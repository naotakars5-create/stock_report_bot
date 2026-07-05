"""
stock_insights.py

スコアリング済みの銘柄データ（metrics）・カレンダー・市場指数から、
「毎朝3分で今日の見どころが分かる」ための分析ビューを組み立てるモジュール。

重要（表現方針・コンプライアンス）:
  本サービスは **投資助言ではなく、公開データに基づく機械的なスクリーニング** です。
  そのため、ここで生成する文言は一貫して「非投資助言」の枠で表現します。
    - 「押し目買い/利確/損切り」→ 「テクニカル節目（下値メド/上値メド・参考値）」
    - 「強気/弱気/リスクオフ」    → 「市場環境（リスク選好〜リスク回避）の機械的判定」
    - 「期待値/短期期待」        → 「スクリーニング適合度（条件一致の強さ）」
  価格の節目・出来高・ボラティリティなど **客観的な事実／統計** のみを提示し、
  売買の推奨・価格予想は行いません。

データの正確性:
  実データが取得できない項目（決算の市場予想比・信用需給・指数組入予定など）は
  **捏造せず「データ未対応」と明示** します。ここで扱う決算/配当の「日程」は
  yfinance のカレンダーから best-effort で取得したもので、欠損があり得ます。

すべての関数は純粋関数（副作用なし）で、metrics 欠損時は中立表現へフォールバックします。
"""

from datetime import date, datetime


# ====== 表示補助 ======
def stars(n, total=5):
    """整数 n（0〜total）を ★☆ の文字列にする。"""
    n = max(0, min(total, int(round(n))))
    return "★" * n + "☆" * (total - n)


def _fmt_price(v):
    """株価を読みやすく整形（100円未満は小数1桁、以上は整数＋カンマ）。"""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{v:,.1f}円" if v < 100 else f"{v:,.0f}円"


def _m(s, key, default=None):
    return (s.get("metrics") or {}).get(key, default)


# ====== ① 需給・機関投資家視点（機械的推定） ======
def institutional_view(s):
    """
    出来高・値動き・ボラティリティから、需給と資金の性格を機械的に推定する。

    戻り値: {"volume": str, "money": str, "supply": str}
    いずれも「機械的推定」であり、実際の投資主体を特定するものではない。
    """
    vr = _m(s, "vol_ratio")
    surge = _m(s, "surge_5")
    vol_pct = _m(s, "volatility")
    gap = _m(s, "gap_5_25")
    rel = _m(s, "rel_strength")
    price = s.get("price")
    sma25, sma75 = _m(s, "sma25"), _m(s, "sma75")

    # 出来高の読み
    if vr is None:
        volume = "出来高データは限定的（平常圏とみなす）"
    elif vr >= 1.5:
        volume = f"出来高は20日平均の約{vr:.1f}倍。関心が急速に高まっている"
    elif vr >= 1.1:
        volume = f"出来高は20日平均の約{vr:.1f}倍とやや増加"
    elif vr >= 0.9:
        volume = "出来高は平常圏（20日平均並み）"
    else:
        volume = "出来高は20日平均を下回り、関心はやや低下"

    # 資金の性格（短期／中長期）の機械的推定
    uptrend = (price is not None and sma25 is not None and sma75 is not None
               and price > sma25 > sma75)
    short_money = (surge is not None and surge >= 8) and (vol_pct is not None and vol_pct >= 3.0)
    long_money = uptrend and (vol_pct is not None and vol_pct < 2.5) and (rel is not None and rel > 0)
    if short_money and not long_money:
        money = "急騰・高ボラで、短期資金の回転が中心と推定"
    elif long_money and not short_money:
        money = "低ボラで相対的に強く、中長期資金の流入が続く形と推定"
    elif uptrend:
        money = "上昇基調で、短期・中長期の資金が混在すると推定"
    else:
        money = "資金の方向性は中立的と推定"

    # 需給
    if surge is not None and surge >= 12:
        supply = "短期上昇が大きく、需給は過熱ぎみ"
    elif gap is not None and gap > 0 and vr is not None and vr >= 1.1:
        supply = "上昇局面で出来高が伴い、需給は良好な傾向"
    elif surge is not None and surge > 0 and vr is not None and vr < 0.9:
        supply = "上値で出来高が細り、上昇の勢いは鈍りぎみ"
    else:
        supply = "需給は概ね中立"

    return {"volume": volume, "money": money, "supply": supply}


# ====== ② テクニカル節目（参考・売買推奨ではない） ======
def technical_levels(s):
    """
    移動平均・直近スイング高値/安値から、価格の節目を機械的に算出する。

    戻り値: {"support": str, "resistance": str, "range": str, "note": str}
    ここでの「下値メド／上値メド」は客観的な価格の節目であり、
    「押し目買い価格・利確・損切り」といった売買の指示ではない。
    """
    price = s.get("price")
    sma5, sma25, sma75 = _m(s, "sma5"), _m(s, "sma25"), _m(s, "sma75")
    low20, high20 = _m(s, "recent_low_20"), _m(s, "recent_high_20")
    low60, high60 = _m(s, "recent_low_60"), _m(s, "recent_high_60")

    if price is None:
        return {"support": "—", "resistance": "—", "range": "—",
                "note": "価格データが取得できませんでした"}

    # 下値メド: 価格より下の節目のうち最も近いもの
    below = [("5日線", sma5), ("25日線", sma25), ("75日線", sma75),
             ("直近20日安値", low20), ("直近60日安値", low60)]
    below = [(lbl, v) for lbl, v in below if v is not None and v < price]
    if below:
        lbl, v = max(below, key=lambda x: x[1])
        support = f"{_fmt_price(v)}（{lbl}）"
    else:
        support = f"{_fmt_price(low60 or low20)}（直近安値）"

    # 上値メド: 価格より上の節目のうち最も近いもの。無ければ None（＝行ごと非表示）。
    above = [("直近20日高値", high20), ("直近60日高値", high60)]
    above = [(lbl, v) for lbl, v in above if v is not None and v > price]
    if above:
        lbl, v = min(above, key=lambda x: x[1])
        resistance = f"{_fmt_price(v)}（{lbl}）"
    else:
        resistance = None  # 直近高値を更新中 → 上値メドは非表示

    rng = None
    if low20 is not None and high20 is not None:
        rng = f"{_fmt_price(low20)}〜{_fmt_price(high20)}（直近20日）"
    note = "機械的に算出した価格の節目です（参考・売買推奨ではありません）"
    return {"support": support, "resistance": resistance, "range": rng, "note": note}


# ====== ③ 決算評価（取得可能な日程のみ・予想比は未対応） ======
def _days_until(d, today):
    if not isinstance(d, date):
        return None
    return (d - today).days


def earnings_view(s, calendar=None, today=None):
    """
    決算まわりの表示。日程は best-effort、市場予想比の評価は「データ未対応」と明示。

    戻り値: {"date_line": str, "beat_line": str, "soon": bool}
    """
    today = today or datetime.now().date()
    cal = calendar or {}
    ed = cal.get("earnings_date")
    du = _days_until(ed, today)
    soon = du is not None and 0 <= du <= 14
    # 決算日が取得できない場合は date_line=None（カードでは行ごと非表示）。
    # 市場予想比（売上/利益/ガイダンス）はデータ源が無いため、ここでは扱わない
    # （＝「データ未対応」の行はカードに出さず、非表示にする方針）。
    if ed is None:
        date_line = None
    elif du is not None and du < 0:
        date_line = f"直近決算 {ed:%m/%d}（発表済み）"
    elif soon:
        date_line = f"次回決算 {ed:%m/%d}（あと{du}日・決算跨ぎに注意）"
    else:
        date_line = f"次回決算 {ed:%m/%d} 予定"
    return {"date_line": date_line, "soon": soon}


def event_view(s, calendar=None, today=None, horizon_days=14):
    """
    今後 horizon_days 日以内の株価変動要因のうち、**決算日以外**（＝配当権利日など）。

    決算日は earnings_view が持つため、ここでは重複させない。株主総会・指数組入・
    展示会・政策・IR はデータ未対応。該当が無ければ「イベントなし」を明示する。

    戻り値: {"items": [str, ...], "has_event": bool}
    """
    today = today or datetime.now().date()
    cal = calendar or {}
    items = []
    xd = cal.get("ex_dividend_date")
    dx = _days_until(xd, today)
    if dx is not None and 0 <= dx <= horizon_days:
        items.append(f"{xd:%m/%d} 配当権利落ち（あと{dx}日）")
    if items:
        return {"items": items, "has_event": True}
    return {"items": ["直近1〜2週間の決算・配当イベントなし"], "has_event": False}


# ====== ④ スクリーニング適合度（＝旧「期待値」を非投資助言で言い換え） ======
def fit_stars(score):
    """総合スコア(0〜10)を 1〜5 の★段階にする。"""
    if score >= 9.0:
        return 5
    if score >= 8.0:
        return 4
    if score >= 7.0:
        return 3
    if score >= 6.0:
        return 2
    return 1


def expectation_rating(s):
    """
    スクリーニング適合度（何段階の条件が一致しているか）を★と理由で表す。
    「期待値／上昇期待」ではなく、あくまで条件一致の強さを機械的に示す。

    戻り値: {"stars_n": int, "stars": str, "label": str, "reason": str}
    """
    score = s.get("score", 0)
    n = fit_stars(score)
    labels = {5: "条件一致度が非常に高い", 4: "複数条件が一致",
              3: "一部条件が一致", 2: "監視水準", 1: "中立"}
    # 理由: 相対的に強い軸を1〜2個、数値を添えて
    disp = s.get("display_ratios") or {}
    order = ["トレンド", "相対強度", "出来高", "テーマ性", "ニュース", "割安感", "安定性"]
    strong = sorted(((a, disp.get(a, 0)) for a in order), key=lambda x: x[1], reverse=True)
    phrases = {
        "トレンド": "移動平均が上向き", "相対強度": "市場平均(TOPIX)より強い",
        "出来高": "出来高が増加", "テーマ性": "テーマ性が明確",
        "ニュース": "ニュース環境と接点", "割安感": "割高感が限定的",
        "安定性": "値動きが安定",
    }
    picks = [phrases[a] for a, v in strong[:2] if v >= 0.5]
    reason = "・".join(picks) if picks else "際立った強みは限定的だが大きな崩れもない"
    return {"stars_n": n, "stars": stars(n), "label": labels[n], "reason": reason}


# ====== ⑥ リスク（機械的フラグ・必ず1つ以上返す） ======
def risk_flags(s, calendar=None, today=None):
    """metrics から機械的なリスク要因を列挙する。該当が無ければ中立メモを返す。"""
    flags = []
    surge = _m(s, "surge_5")
    vol_pct = _m(s, "volatility")
    vr = _m(s, "vol_ratio")
    price = s.get("price")
    high60 = _m(s, "recent_high_60")
    ev = earnings_view(s, calendar, today)

    if ev["soon"]:
        flags.append("決算跨ぎに注意（発表が近い）")
    if surge is not None and surge >= 12:
        flags.append(f"テーマ過熱ぎみ（直近5日 +{surge:.0f}%）")
    if vr is not None and vr < 0.9:
        flags.append("出来高が減少ぎみ")
    if price is not None and high60 is not None and price >= high60 * 0.98:
        flags.append("直近高値圏で上値抵抗を意識しやすい")
    if vol_pct is not None and vol_pct >= 3.5:
        flags.append(f"日次ボラティリティ{vol_pct:.1f}%と高め")
    if s.get("size_category") == "小型":
        flags.append("小型株で値動きが大きくなりやすい")

    # スコア側で拾った固有リスク（プレースホルダー以外）を1件だけ補完
    for r in (s.get("risks") or []):
        if r and "目立ったリスク" not in r and r not in flags:
            flags.append(r)
            break

    if not flags:
        flags.append("機械的な警戒シグナルは限定的（ただし相場変動リスクは常にあり）")
    return flags[:4]


# ====== トップページ：今日の相場判定（非投資助言・市場環境の機械判定） ======
def _chg(market, key):
    return (market.get(key) or {}).get("change_pct")


def _bucket(v, hi=0.5, lo=0.1):
    if v is None:
        return 0.0
    if v >= hi:
        return 2.0
    if v >= lo:
        return 1.0
    if v <= -hi:
        return -2.0
    if v <= -lo:
        return -1.0
    return 0.0


def market_judgment(market, stats=None):
    """
    国内指数・前日米国・スクリーニング通過率から、今日の市場環境を5段階で機械判定。

    「強気/弱気」ではなく「リスク選好〜リスク回避」の機械的な地合い判定として表現する。
    戻り値: {"stars_n", "stars", "label", "reasons": [str, ...]}
    """
    market = market or {}
    stats = stats or {}
    topix = _chg(market, "TOPIX")
    nikkei = _chg(market, "日経平均")
    sp = _chg(market, "S&P500")
    nq = _chg(market, "NASDAQ")
    us = None
    us_vals = [x for x in (sp, nq) if x is not None]
    if us_vals:
        us = sum(us_vals) / len(us_vals)

    breadth = None
    uni, passed = stats.get("universe", 0), stats.get("primary_passed", 0)
    if uni:
        breadth = passed / uni * 100

    raw = _bucket(topix if topix is not None else nikkei) + _bucket(us) * 0.8
    if breadth is not None:
        raw += 1.0 if breadth >= 5 else (0.0 if breadth >= 2 else -1.0)

    if raw >= 2.5:
        n, label = 5, "リスク選好（地合い良好）"
    elif raw >= 1.0:
        n, label = 4, "やや選好（地合いは支えられている）"
    elif raw >= -1.0:
        n, label = 3, "中立（方向感は限定的）"
    elif raw >= -2.5:
        n, label = 2, "やや慎重（上値は重い地合い）"
    else:
        n, label = 1, "リスク回避（地合いは慎重）"

    reasons = []
    dom = topix if topix is not None else nikkei
    if dom is not None:
        base = "TOPIX" if topix is not None else "日経平均"
        word = "上昇" if dom > 0.1 else ("下落" if dom < -0.1 else "横ばい")
        reasons.append(f"国内は{base}が{dom:+.2f}%で{word}")
    if us is not None:
        word = "高い" if us > 0.1 else ("軟調" if us < -0.1 else "横ばい")
        reasons.append(f"前日の米国株は{word}（S&P500/NASDAQ平均 {us:+.2f}%）")
    if breadth is not None:
        level = "広め" if breadth >= 5 else ("平常" if breadth >= 2 else "限定的")
        reasons.append(f"スクリーニング通過は全体の{breadth:.1f}%と物色の裾野は{level}")
    if not reasons:
        reasons.append("指数データが限定的なため中立とみなしています")
    return {"stars_n": n, "stars": stars(n), "label": label, "reasons": reasons}


def top_theme_reason(theme_ranking, macro_context=None):
    """
    今日最重要テーマと「なぜそのテーマなのか」を100文字以内で説明する。
    戻り値: {"theme": str, "reason": str} / テーマが無ければ None。
    """
    if not theme_ranking:
        return None
    mc = macro_context or {}
    top = theme_ranking[0]
    theme = top["theme"]
    majors = mc.get("major_themes") or mc.get("summary_themes") or []
    env = mc.get("market_summary") or ""
    if majors:
        reason = f"本日のニュース環境（{ '・'.join(majors[:3]) }）で意識されやすく、スクリーニングでも{top['count']}銘柄が該当。"
    elif env:
        reason = f"{env} こうした地合いで{theme}関連に{top['count']}銘柄が該当。"
    else:
        reason = f"スクリーニング上位で{top['count']}銘柄が該当し、本日は接点が多いテーマ。"
    return {"theme": theme, "reason": reason[:100]}


def daily_temperature(scored_stocks, market=None, stats=None, judgment=None):
    """
    【P1-1】本日の温度感（積極 / 中立 / 慎重 の3段階）＋理由1行。

    その日のスクリーニング環境の「温度感」を機械的に1行で示す（売買推奨ではない）。
    判定ロジック（コード＝README両方に明記）:
      base = 相場判定の段階(1〜5) を中心化: (stars_n - 3) → -2〜+2
      + 通過率(物色の裾野):  ≥5% → +1 / 2〜5% → 0 / <2% → -1
      + 上位の時価総額分布:  小型が3銘柄以上 → -1（値動きが荒くなりやすい）
                              大型が3銘柄以上 → +0.5（相対的に落ち着きやすい）
      + 過熱:                直近5日+12%超が2銘柄以上 → -0.5
      合計 raw を  ≥1.5→積極 / ≤-1.0→慎重 / それ以外→中立 にマッピング。
    理由は最も効いた要因を1文で述べる。

    戻り値: {"level": "積極"/"中立"/"慎重", "reason": str, "tone": "positive"/"neutral"/"caution"}
    """
    judgment = judgment or market_judgment(market, stats)
    scored = scored_stocks or []
    stats = stats or {}
    uni, passed = stats.get("universe", 0), stats.get("primary_passed", 0)
    breadth = (passed / uni * 100) if uni else None
    small = sum(1 for s in scored if s.get("size_category") == "小型")
    big = sum(1 for s in scored if s.get("size_category") == "大型")
    hot = sum(1 for s in scored if (_m(s, "surge_5") or 0) >= 12)
    jn = judgment.get("stars_n", 3)

    raw = (jn - 3)
    if breadth is not None:
        raw += 1 if breadth >= 5 else (0 if breadth >= 2 else -1)
    if scored and small >= 3:
        raw -= 1
    elif scored and big >= 3:
        raw += 0.5
    if hot >= 2:
        raw -= 0.5

    if raw >= 1.5:
        level, tone = "積極", "positive"
    elif raw <= -1.0:
        level, tone = "慎重", "caution"
    else:
        level, tone = "中立", "neutral"

    # 理由: 判定(level)と整合する主因を1文で（積極なら支え要因、慎重なら警戒要因）。
    if tone == "caution":
        if scored and small >= 3:
            reason = "上位が小型株に偏り、値動きが荒くなりやすい"
        elif hot >= 2:
            reason = "短期過熱ぎみの銘柄が多く、値動きが荒くなりやすい"
        elif breadth is not None and breadth < 2:
            reason = f"通過が{passed}銘柄と絞られ、物色は限定的"
        else:
            reason = "地合いが慎重で、上値の重い展開"
    elif tone == "positive":
        if jn >= 4 and (breadth is None or breadth >= 5):
            reason = "地合いが支えられ、条件該当の裾野も広い"
        elif scored and big >= 3:
            reason = "上位は大型株中心で相対的に落ち着きやすい"
        else:
            reason = "地合い・物色とも条件がそろいやすい"
    else:
        if scored and small >= 3:
            reason = "地合いは中立だが、上位に小型株が多く値動きは荒くなりやすい"
        elif breadth is not None and breadth < 2:
            reason = "物色はやや限定的で、方向感は乏しい"
        else:
            reason = "地合い・物色とも目立った偏りは限定的"
    return {"level": level, "reason": reason, "tone": tone}
