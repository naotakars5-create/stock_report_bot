"""
stock_scorer.py

取得した株価データ等から、各銘柄に 0.0〜10.0 点の評価点をつけるモジュール。

評価軸と配点（合計 10.0）:
  1. トレンド評価          : 1.7  （5日・25日・75日移動平均の並び、株価との位置）
  2. 出来高・流動性評価    : 1.3  （出来高増加率、売買代金による流動性）
  3. 相対強度評価          : 1.3  （市場平均(TOPIX連動ETF)に対する強さ）
  4. 業種テーマ性評価      : 1.3  （半導体/防衛/AI等のテーマタグ）
  5. ニュース環境・マクロ  : 1.0  （世界情勢・経済ニュースと銘柄テーマの関連）
  6. バリュエーション評価  : 0.9  （PER/PBR/配当利回り。取得できなければ中立）
  7. 安定性・過熱回避      : 1.3  （急騰しすぎ・高ボラのペナルティ）
  8. 前回検証・継続性補正  : 1.2  （前回上位の継続性。過熱が強ければ減点）

注意: これは「売買推奨」ではなく、公開データに基づく機械的なスクリーニングです。
"""

import statistics

import pandas as pd

import macro_analyzer
from profile_loader import HIGH_ATTENTION_THEMES


# 評価グラフ（バランス図）に出す軸。表示は相対スケールで強弱を出す。
DISPLAY_AXES = ["トレンド", "出来高", "相対強度", "テーマ性", "ニュース", "割安感", "安定性"]


# 各評価軸の配点（合計 = 10.0）
WEIGHTS = {
    "トレンド": 1.7,
    "出来高": 1.3,
    "相対強度": 1.3,
    "テーマ性": 1.3,
    "ニュース": 1.0,
    "割安感": 0.9,
    "安定性": 1.3,
    "継続性": 1.2,
}
MAX_RAW = sum(WEIGHTS.values())  # 10.0

# 最終抽出の評価点の下限（これ未満は「今回は除外」）
MIN_FINAL_SCORE = 6.0

# 一次スクリーニングの既定パラメータ
PRIMARY_MIN_AVG_VOLUME = 50000   # 直近20日平均出来高の下限（流動性フィルタ）
PRIMARY_MAX_SURGE_5D = 20.0      # 直近5日の上昇率がこれを超えたら過熱とみなす


# ====== 一次スクリーニング（軽量・高速） ======
def primary_screen(history, min_avg_volume=PRIMARY_MIN_AVG_VOLUME):
    """一次スクリーニング。全条件を満たすか判定し、並べ替え用スコアを返す。"""
    close = history["Close"].dropna()
    volume = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)
    if len(close) < 25:
        return None

    price = float(close.iloc[-1])
    sma5 = _sma(close, 5)
    sma25 = _sma(close, 25)
    if sma5 is None or sma25 is None or sma25 == 0:
        return None

    avg_volume = None
    if len(volume) >= 20:
        avg_volume = float(volume.rolling(20).mean().iloc[-1])
    liquidity_ok = avg_volume is not None and avg_volume >= min_avg_volume

    above_25ma = price > sma25
    short_up = sma5 > sma25

    vol_up = False
    vol_ratio = 1.0
    if len(volume) >= 25:
        vol5 = float(volume.rolling(5).mean().iloc[-1])
        vol25 = float(volume.rolling(25).mean().iloc[-1])
        if vol25 > 0:
            vol_ratio = vol5 / vol25
            vol_up = vol_ratio > 1.0

    surge5 = _pct_change(close, 5)
    not_overheated = surge5 is None or surge5 < PRIMARY_MAX_SURGE_5D

    passed = bool(liquidity_ok and above_25ma and short_up and vol_up and not_overheated)

    above_pct = (price - sma25) / sma25 * 100
    gap_pct = (sma5 - sma25) / sma25 * 100
    momentum = surge5 if surge5 is not None else 0.0
    primary_score = (
        above_pct * 0.3
        + gap_pct * 0.4
        + (vol_ratio - 1.0) * 100 * 0.2
        - max(0.0, momentum - 10.0) * 0.5
    )

    return {
        "passed": passed,
        "primary_score": primary_score,
        "price": price,
        "avg_volume": avg_volume if avg_volume is not None else 0.0,
    }


def screen_primary(stock_histories, min_avg_volume=PRIMARY_MIN_AVG_VOLUME, top_n=50):
    """
    一次スクリーニングを全銘柄に適用し、
    (通過銘柄リスト, 絞り込み前の条件合致数) のタプルを返す。

    通過銘柄リストは二次スクリーニングに回す **上位 top_n 件だけ**
    （primary_score 降順）。ただし「通過率（物色の裾野）」の指標には
    **絞り込み前の実際の通過数**が必要。top_n で切った後の件数を使うと常に
    top_n（＝一定値）になり、通過率も相場判定・温度感・パーセンタイル(P1-3)も
    すべて意味を失うため、絞り込み前の件数を第2要素として返す。
    """
    passed = []
    for item in stock_histories:
        result = primary_screen(item["history"], min_avg_volume=min_avg_volume)
        if result is None or not result["passed"]:
            continue
        entry = {k: v for k, v in item.items() if k != "history"}
        entry["primary_score"] = result["primary_score"]
        entry["price"] = result["price"]
        passed.append(entry)

    raw_passed = len(passed)
    passed.sort(key=lambda x: x["primary_score"], reverse=True)
    return passed[:top_n], raw_passed


# ====== 補助 ======
def _sma(series, window):
    s = series.dropna()
    if len(s) < window:
        return None
    return float(s.rolling(window).mean().iloc[-1])


def _pct_change(series, days):
    s = series.dropna()
    if len(s) <= days:
        return None
    past = float(s.iloc[-1 - days])
    latest = float(s.iloc[-1])
    if past == 0:
        return None
    return (latest - past) / past * 100


def _daily_volatility(series, days=20):
    s = series.dropna()
    if len(s) <= days:
        return None
    returns = s.pct_change().dropna().iloc[-days:]
    if returns.empty:
        return None
    return float(returns.std() * 100)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _valuation_ratio(valuation):
    """PER/PBR/配当利回りから 0〜1 の割安感スコアを作る。取得不可は中立(0.5)。"""
    if not valuation:
        return 0.5
    parts = []
    per = valuation.get("per")
    pbr = valuation.get("pbr")
    dy = valuation.get("div_yield")
    if per and per > 0:
        parts.append(1.0 if per <= 15 else 0.6 if per <= 25 else 0.3 if per <= 40 else 0.15)
    if pbr and pbr > 0:
        parts.append(1.0 if pbr <= 1 else 0.6 if pbr <= 2 else 0.3 if pbr <= 4 else 0.15)
    if dy is not None and dy > 0:
        parts.append(1.0 if dy >= 3.5 else 0.7 if dy >= 2 else 0.5)
    return sum(parts) / len(parts) if parts else 0.5


def _size_from_mktcap(valuation):
    """時価総額から規模区分を推定（取得不可は空）。"""
    mc = (valuation or {}).get("market_cap")
    if not mc:
        return ""
    if mc >= 1e12:
        return "大型"
    if mc >= 3e11:
        return "中型"
    return "小型"


# ====== 二次スクリーニング（評価点 0〜10） ======
def score_stock(history, benchmark_close=None, theme_tags=None,
                valuation=None, continuity=None, news_ratio=None, news_line=None):
    """
    1銘柄を 8つの評価軸でスコアリングする。

    引数:
        history: yfinance の OHLCV DataFrame
        benchmark_close: 市場平均(TOPIX連動ETF)の Close Series
        theme_tags: 業種テーマ性評価に使うテーマタグのリスト
        valuation: {"per","pbr","div_yield","market_cap"}（取得不可なら None で中立）
        continuity: {"in_previous": bool}（前回上位かどうか。None で中立）
        news_ratio: ニュース環境評価(0〜1)。None で中立(0.5)
        news_line: ニュース環境の説明文（評価理由・カード表示に使う）

    戻り値: dict（score/price/reasons/risks/details/metrics 等）。計算不可なら None。
    注意: これは売買推奨ではなく、機械的なスクリーニング評価です。
    """
    close = history["Close"].dropna()
    volume = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)
    if len(close) < 75:
        return None

    price = float(close.iloc[-1])
    sma5, sma25, sma75 = _sma(close, 5), _sma(close, 25), _sma(close, 75)
    if sma5 is None or sma25 is None or sma75 is None:
        return None

    theme_tags = theme_tags or []
    reasons, risks, details = [], [], {}

    # 1. トレンド評価
    checks = [price > sma25, price > sma5, sma5 > sma25, sma25 > sma75, price > sma75]
    trend_ratio = sum(1 for c in checks if c) / len(checks)
    details["トレンド"] = trend_ratio * WEIGHTS["トレンド"]
    gap_5_25 = (sma5 - sma25) / sma25 * 100 if sma25 else 0.0
    gap_25_75 = (sma25 - sma75) / sma75 * 100 if sma75 else 0.0
    if sma5 > sma25 > sma75 and price > sma5:
        reasons.append("5日線>25日線>75日線で移動平均が上向きに揃っている")
    elif trend_ratio >= 0.6:
        reasons.append("移動平均は概ね上向き")
    else:
        risks.append("移動平均の並びが整っておらずトレンドは不明瞭")

    # 2. 出来高・流動性評価
    vol_ratio = avg_vol = turnover = None
    if len(volume) >= 25:
        vol5 = float(volume.rolling(5).mean().iloc[-1])
        vol25 = float(volume.rolling(25).mean().iloc[-1])
        if vol25 > 0:
            vol_ratio = vol5 / vol25
    if len(volume) >= 20:
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        turnover = avg_vol * price
    base_vol = _clamp((vol_ratio - 0.7) / 0.8) if vol_ratio is not None else 0.5
    liq = 1.0
    if turnover is not None:
        if turnover < 1e8:
            liq = 0.5
        elif turnover < 5e8:
            liq = 0.8
    details["出来高"] = base_vol * liq * WEIGHTS["出来高"]
    if vol_ratio is not None and vol_ratio > 1.2:
        reasons.append(f"直近出来高が20日平均比{vol_ratio:.2f}倍に増加")
    if turnover is not None and turnover < 1e8:
        risks.append("売買代金が小さく流動性が低い")
    if vol_ratio is not None and vol_ratio >= 2.5:
        risks.append("出来高急増が一過性の可能性に注意")

    # 3. 相対強度評価
    stock_20 = _pct_change(close, 20)
    rel = None
    if stock_20 is not None and benchmark_close is not None and len(benchmark_close.dropna()) > 20:
        b20 = _pct_change(benchmark_close, 20)
        if b20 is not None:
            rel = stock_20 - b20
    if rel is not None:
        details["相対強度"] = _clamp((rel + 5) / 10) * WEIGHTS["相対強度"]
        if rel > 0:
            reasons.append(f"市場平均(TOPIX)を20日で{rel:.1f}pt上回る相対的な強さ")
        else:
            risks.append(f"市場平均(TOPIX)を20日で{abs(rel):.1f}pt下回る")
    else:
        details["相対強度"] = 0.5 * WEIGHTS["相対強度"]

    # 4. 業種テーマ性評価
    n = len(theme_tags)
    base_theme = 0.3 + 0.25 * n
    if any(t in HIGH_ATTENTION_THEMES for t in theme_tags):
        base_theme += 0.15
    details["テーマ性"] = _clamp(base_theme) * WEIGHTS["テーマ性"]
    if theme_tags:
        reasons.append("テーマ性: " + "・".join(theme_tags[:3]))

    # 5. ニュース環境・マクロテーマ評価（世界情勢・経済ニュースとの関連。中立は0.5）
    #    詳しい関連理由・注意点・解説は report_writer 側で macro_reason 等から生成する。
    nr = 0.5 if news_ratio is None else _clamp(news_ratio)
    details["ニュース"] = nr * WEIGHTS["ニュース"]

    # 6. バリュエーション評価（取得不可は中立）
    details["割安感"] = _valuation_ratio(valuation) * WEIGHTS["割安感"]

    # 7. 安定性・過熱回避
    surge_5 = _pct_change(close, 5)
    vol_pct = _daily_volatility(close, 20)
    oh = 0.5 if surge_5 is None else (1.0 if surge_5 <= 0 else _clamp(1 - surge_5 / 15))
    stab = 0.5 if vol_pct is None else _clamp((4 - vol_pct) / 3)
    details["安定性"] = (oh + stab) / 2 * WEIGHTS["安定性"]
    if surge_5 is not None and surge_5 >= 12:
        risks.append(f"直近5日で{surge_5:.1f}%上昇しており短期的な過熱感に注意")
    if vol_pct is not None and vol_pct >= 3.5:
        risks.append(f"日次ボラティリティ{vol_pct:.1f}%と高め")

    # 規模に応じたリスクメモ
    size = _size_from_mktcap(valuation)
    if size == "小型":
        risks.append("小型株のため値動きが大きくなりやすい")

    # 8. 前回検証・継続性補正
    if continuity is None or not continuity.get("in_previous"):
        details["継続性"] = 0.5 * WEIGHTS["継続性"]
    else:
        cont_ratio = 0.4 if (surge_5 is not None and surge_5 >= 12) else 0.85
        details["継続性"] = cont_ratio * WEIGHTS["継続性"]
        reasons.append("前回も上位で条件の継続性あり")

    raw = sum(details.values())
    score = max(0.0, min(10.0, raw))

    if not reasons:
        reasons.append("際立った強さは限定的だが大きな崩れはない中立的な状態")
    if not risks:
        risks.append("目立ったリスクシグナルは検出されず")

    # テクニカル節目（サポート/レジスタンス）算出用の直近スイング高値・安値。
    # これらは「機械的に算出した価格の節目（参考値）」であり、売買推奨ではない。
    def _win_high(days):
        w = close.iloc[-days:]
        return float(w.max()) if len(w) else None

    def _win_low(days):
        w = close.iloc[-days:]
        return float(w.min()) if len(w) else None

    metrics = {
        "gap_5_25": gap_5_25,
        "gap_25_75": gap_25_75,
        "vol_ratio": vol_ratio,
        "turnover": turnover,
        "rel_strength": rel,
        "surge_5": surge_5,
        "volatility": vol_pct,
        "per": (valuation or {}).get("per"),
        "pbr": (valuation or {}).get("pbr"),
        "div_yield": (valuation or {}).get("div_yield"),
        "market_cap": (valuation or {}).get("market_cap"),
        "news_ratio": nr,
        "sma5": sma5, "sma25": sma25, "sma75": sma75,
        "recent_high_20": _win_high(20), "recent_low_20": _win_low(20),
        "recent_high_60": _win_high(60), "recent_low_60": _win_low(60),
    }

    return {
        "score": round(score, 1),
        "price": price,
        "reasons": reasons,
        "risks": risks,
        "details": details,
        "metrics": metrics,
        "theme_tags": theme_tags,
        "news_line": news_line,
        "size_category": size,
    }


def compute_theme_ranking(scored, top_themes=6, per_theme=3):
    """
    候補銘柄を theme_tags 別に集計し、強いテーマのランキングを作る。

    戻り値: [{"theme", "count", "avg_score", "stocks":[{name,code,score}, ...]}, ...]
    （count 降順 → 平均スコア降順）。
    """
    buckets = {}
    for s in scored:
        for t in (s.get("theme_tags") or []):
            buckets.setdefault(t, []).append(s)
    ranking = []
    for theme, members in buckets.items():
        members_sorted = sorted(members, key=lambda x: x.get("score", 0), reverse=True)
        avg = sum(x.get("score", 0) for x in members) / len(members)
        ranking.append({
            "theme": theme,
            "count": len(members),
            "avg_score": avg,
            "stocks": [{"name": x.get("name", ""), "code": x.get("code", ""),
                        "score": x.get("score", 0)} for x in members_sorted[:per_theme]],
        })
    ranking.sort(key=lambda r: (r["count"], r["avg_score"]), reverse=True)
    return ranking[:top_themes]


def score_all(stock_histories, benchmark_df=None, top_n=5, profiles=None,
              valuations=None, previous_codes=None, macro_context=None,
              theme_ranking_out=None):
    """
    全候補銘柄をスコアリングし、評価点 MIN_FINAL_SCORE 以上の上位 top_n 件を返す。

    引数:
        profiles: {code: profile}（profile_loader 由来。theme_tags/business_summary を含む）
        valuations: {code: valuation}（data_fetcher.get_valuation の結果）
        previous_codes: 前回上位の証券コード集合（継続性補正に使用）
        macro_context: macro_analyzer.analyze の結果（ニュース環境評価に使用。None で中立）
    """
    benchmark_close = None
    if benchmark_df is not None and "Close" in benchmark_df:
        benchmark_close = benchmark_df["Close"]
    profiles = profiles or {}
    valuations = valuations or {}
    previous_codes = previous_codes or set()

    scored = []
    for item in stock_histories:
        code = item["code"]
        prof = profiles.get(code) or {}
        tags = prof.get("theme_tags") or []
        sector = item.get("sector", "")
        business = prof.get("business_summary", "")
        # ニュース環境（macro_context が無ければ中立）。銘柄ごとの関連スコア・解説を作る。
        if macro_context:
            rel = macro_analyzer.calculate_macro_relevance_score(
                tags, sector, business, macro_context)
            news_ratio = rel.get("macro_score")
            news_line = macro_analyzer.build_stock_news_comment(
                tags, sector, business, macro_context, detailed=False)
            news_detail = macro_analyzer.build_stock_news_comment(
                tags, sector, business, macro_context, detailed=True)
        else:
            rel = {"macro_reason": None, "macro_caution": None}
            news_ratio = news_line = news_detail = None
        result = score_stock(
            item["history"],
            benchmark_close=benchmark_close,
            theme_tags=tags,
            valuation=valuations.get(code),
            continuity={"in_previous": code in previous_codes},
            news_ratio=news_ratio,
            news_line=news_line,
        )
        if result is None:
            print(f"[警告] スコア計算をスキップ: {item['name']} ({code})")
            continue
        scored.append({
            "code": code,
            "name": item["name"],
            "sector": sector,
            "business_summary": business,
            "profile_source": prof.get("source", ""),
            "macro_reason": rel.get("macro_reason"),
            "macro_caution": rel.get("macro_caution"),
            "news_detail": news_detail,
            "size_category": result.get("size_category") or prof.get("size_category", ""),
            **{k: v for k, v in result.items() if k != "size_category"},
        })

    # 評価グラフ用の相対スコア（候補全体の分布に対する強弱）を付与してから絞り込む
    _attach_display_ratios(scored)

    # テーマ別ランキング（候補全体から集計。呼び出し側が out リストを渡したとき）
    if theme_ranking_out is not None:
        theme_ranking_out.extend(compute_theme_ranking(scored))

    scored = [s for s in scored if s["score"] >= MIN_FINAL_SCORE]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def _attach_display_ratios(scored):
    """
    各銘柄の評価グラフ表示用に、軸ごとの相対スコア(0〜1)を付与する。

    候補全体の平均・標準偏差で標準化し、平均=0.5中心に強弱を広げる。
    総合スコア自体は変えず、内訳グラフの見え方にメリハリを出すための表示用変換。
    分散が無い軸は絶対値（獲得点÷満点）をそのまま使う。
    """
    if not scored:
        return
    stats = {}
    for a in DISPLAY_AXES:
        w = WEIGHTS.get(a) or 1.0
        vals = [s.get("details", {}).get(a, 0) / w for s in scored]
        mean = sum(vals) / len(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        stats[a] = (mean, sd)
    for s in scored:
        details = s.get("details", {})
        disp = {}
        for a in DISPLAY_AXES:
            w = WEIGHTS.get(a) or 1.0
            r = details.get(a, 0) / w
            mean, sd = stats[a]
            if sd < 1e-6:
                disp[a] = max(0.06, min(1.0, r))
            else:
                z = (r - mean) / sd
                disp[a] = max(0.06, min(1.0, 0.5 + z * 0.20))
        s["display_ratios"] = disp
