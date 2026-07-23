"""
catalyst.py

カタリスト（イベント）連動スコアリング（改善②・シャドウ検証用）。

狙い:
  純粋なテクニカルより優位が出やすいとされる「イベント前後」を取り込む。
  リテールで再現しやすい2つの効果を、既存の決算カレンダー(get_calendar_events)と
  metrics だけで機械的に近似する:

  1. 決算跨ぎリスクの回避（守り）
     目安保有期間（5立会い日）内に決算を控える銘柄は、決算ギャップで想定外の
     値動きになりやすい。→ 減点（イベントリスクを避ける）。

  2. 決算後ドリフト＝PEAD の近似（攻め）
     直近に決算を通過し、その後も出来高を伴って上昇が続く銘柄は、
     ポジティブサプライズのドリフトが続きやすい（Post-Earnings-Announcement Drift）。
     決算サプライズの実数はyfinanceで安定取得できないため、
     「直近決算通過 × 出来高増 × 上昇」を代理シグナルにする。→ 加点。
     逆に直近決算後に失速している銘柄は減点。

重要:
  これは「エッジがある」と決めつけた変更ではなく、**検証すべき仮説**。
  本番配信(balanced)は変えず、catalyst で並べ替えた上位5を「シャドウ」で記録・追跡し、
  edge_analysis で他アームと同一基準・サンプル数ゲート付きで比較して初めて採否を判断する。

すべて純粋関数（副作用なし・calendar/metricsが欠けても中立に振る舞う）。
"""

from datetime import date, datetime, timedelta


HOLDING_TRADING_DAYS = 5       # 目安保有期間（立会い日）。決算跨ぎ判定の窓。
# 立会い日→暦日の概算（5立会い日 ≈ 7〜8暦日）。カレンダー日付との比較に使う。
HOLDING_CALENDAR_DAYS = 8
RECENT_EARNINGS_CALENDAR_DAYS = 14  # 「直近決算」とみなす過去日数（≈10立会い日）

# 加減点（0〜10スケール上の増減）。控えめに設定し、上位の並べ替えに効かせる。
PENALTY_EARNINGS_CROSS = 1.0   # 保有期間内に決算 → 減点
BONUS_PEAD = 0.8               # 決算後ドリフト近似 → 加点
PENALTY_POST_EARNINGS_FADE = 0.6  # 決算後に失速 → 減点


def _to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def earnings_in_holding_window(calendar, today, window_days=HOLDING_CALENDAR_DAYS):
    """目安保有期間内（today〜today+window_days）に決算予定があるか。"""
    ed = _to_date((calendar or {}).get("earnings_date"))
    if ed is None:
        return False
    delta = (ed - today).days
    return 0 <= delta <= window_days


def recent_earnings(calendar, today, days=RECENT_EARNINGS_CALENDAR_DAYS):
    """直近 days 日以内に決算を通過済みか（today より前）。"""
    ed = _to_date((calendar or {}).get("earnings_date"))
    if ed is None:
        return False
    delta = (ed - today).days
    return -days <= delta < 0


def catalyst_adjustment(stock, calendar, today=None):
    """
    1銘柄のカタリスト加減点と理由を返す。

    戻り値: (delta: float, notes: [str, ...])
    """
    today = today or date.today()
    metrics = stock.get("metrics") or {}
    surge = metrics.get("surge_5")
    vr = metrics.get("vol_ratio")
    delta, notes = 0.0, []

    if earnings_in_holding_window(calendar, today):
        delta -= PENALTY_EARNINGS_CROSS
        notes.append("目安保有期間内に決算予定（イベントリスク回避で減点）")
        return delta, notes  # 跨ぎ回避を優先（ドリフト加点とは併用しない）

    if recent_earnings(calendar, today):
        strong = (surge is not None and surge > 0) and (vr is not None and vr >= 1.1)
        fading = surge is not None and surge <= -3
        if strong:
            delta += BONUS_PEAD
            notes.append("直近決算を通過し出来高を伴い堅調（決算後ドリフト近似で加点）")
        elif fading:
            delta -= PENALTY_POST_EARNINGS_FADE
            notes.append("直近決算後に失速（減点）")
    return delta, notes


def rerank(candidates, calendars, today=None, top_n=5, min_score=0.0):
    """
    balanced でスコアリング済みの候補群にカタリスト加減点を適用し、上位を並べ替える。

    引数:
        candidates: score_all の戻り（score/metrics を持つ dict のリスト・多めに渡す）
        calendars: {code: get_calendar_events の戻り}
    戻り値: カタリスト調整後スコアで降順ソートした上位 top_n（各要素に
            catalyst_delta / catalyst_notes / score(調整後) を付与したコピー）。
    """
    today = today or date.today()
    adjusted = []
    for s in candidates or []:
        cal = (calendars or {}).get(s.get("code")) or {}
        delta, notes = catalyst_adjustment(s, cal, today)
        base = float(s.get("score", 0.0))
        new_score = max(0.0, min(10.0, base + delta))
        c = dict(s)
        c["catalyst_delta"] = round(delta, 2)
        c["catalyst_notes"] = notes
        c["base_score"] = base
        c["score"] = round(new_score, 1)
        adjusted.append(c)
    adjusted = [c for c in adjusted if c["score"] >= min_score]
    adjusted.sort(key=lambda x: x["score"], reverse=True)
    return adjusted[:top_n]
