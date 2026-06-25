"""
stock_scorer.py

取得した株価データから、各銘柄に 0.0〜10.0 点の評価点をつけるモジュール。

スコアリング観点（各観点に配点を割り当て、合計を 10 点満点に正規化）:
  1. 5日移動平均と25日移動平均の関係（短期トレンド）
  2. 25日移動平均と75日移動平均の関係（中期トレンド）
  3. 直近出来高が平均出来高より増えているか（人気・関心）
  4. 市場平均(TOPIX連動ETF)に対して相対的に強いか（相対強さ）
  5. 直近で急騰しすぎていないか（過熱の回避）
  6. ボラティリティが高すぎないか（安定性）

注意: これは投資助言ではなく、機械的なスクリーニングです。
"""

import pandas as pd


# 各観点の配点（合計 = 満点）
WEIGHTS = {
    "短期トレンド": 2.0,    # 5日 vs 25日
    "中期トレンド": 2.0,    # 25日 vs 75日
    "出来高": 1.5,          # 出来高増加
    "相対強さ": 2.0,        # 対日経平均
    "過熱回避": 1.5,        # 急騰しすぎていないか
    "安定性": 1.0,          # ボラティリティ
}
MAX_RAW = sum(WEIGHTS.values())  # 10.0


# 一次スクリーニングの既定パラメータ
PRIMARY_MIN_AVG_VOLUME = 50000   # 直近20日平均出来高の下限（流動性フィルタ）
PRIMARY_MAX_SURGE_5D = 20.0      # 直近5日の上昇率がこれを超えたら「急騰しすぎ」


def primary_screen(history, min_avg_volume=PRIMARY_MIN_AVG_VOLUME):
    """
    一次スクリーニング（軽量・高速）。

    判定条件:
        - 流動性: 直近20日の平均出来高が一定以上
        - 25日移動平均より株価が上にある
        - 5日移動平均が25日移動平均を上回っている
        - 直近出来高が増えている（5日平均 > 25日平均）
        - 急騰しすぎていない（直近5日の上昇率が閾値未満）

    戻り値:
        {
          "passed": bool,           # 全条件を満たしたか
          "primary_score": float,   # 通過銘柄を並べ替えるための簡易スコア
          "price": float,
          "avg_volume": float,
        }
        計算に必要なデータが無ければ None。
    """
    close = history["Close"].dropna()
    volume = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)

    if len(close) < 25:
        return None

    price = float(close.iloc[-1])
    sma5 = _sma(close, 5)
    sma25 = _sma(close, 25)
    if sma5 is None or sma25 is None or sma25 == 0:
        return None

    # 流動性（直近20日平均出来高）
    avg_volume = None
    if len(volume) >= 20:
        avg_volume = float(volume.rolling(20).mean().iloc[-1])
    liquidity_ok = avg_volume is not None and avg_volume >= min_avg_volume

    # 各条件
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

    # 通過銘柄の並べ替え用スコア（高いほど一次評価が良い）
    above_pct = (price - sma25) / sma25 * 100
    gap_pct = (sma5 - sma25) / sma25 * 100
    momentum = surge5 if surge5 is not None else 0.0
    primary_score = (
        above_pct * 0.3
        + gap_pct * 0.4
        + (vol_ratio - 1.0) * 100 * 0.2
        - max(0.0, momentum - 10.0) * 0.5  # 過熱はマイナス
    )

    return {
        "passed": passed,
        "primary_score": primary_score,
        "price": price,
        "avg_volume": avg_volume if avg_volume is not None else 0.0,
    }


def screen_primary(stock_histories, min_avg_volume=PRIMARY_MIN_AVG_VOLUME,
                   top_n=50):
    """
    一次スクリーニングを全銘柄に適用し、通過銘柄を primary_score 降順で返す。

    引数:
        stock_histories: fetch_histories の戻り値（"history" を含む）
    戻り値:
        通過銘柄リスト（上位 top_n 件）。各要素は元の銘柄情報 + primary_score 等。
        history は二次で取り直すため、ここでは付けずに返す。
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

    passed.sort(key=lambda x: x["primary_score"], reverse=True)
    return passed[:top_n]


def _sma(series, window):
    """単純移動平均（最新値）を返す。計算できなければ None。"""
    s = series.dropna()
    if len(s) < window:
        return None
    return float(s.rolling(window).mean().iloc[-1])


def _pct_change(series, days):
    """days 日前から最新までの騰落率(%)。計算できなければ None。"""
    s = series.dropna()
    if len(s) <= days:
        return None
    past = float(s.iloc[-1 - days])
    latest = float(s.iloc[-1])
    if past == 0:
        return None
    return (latest - past) / past * 100


def _daily_volatility(series, days=20):
    """直近 days 日の日次騰落率の標準偏差(%)。"""
    s = series.dropna()
    if len(s) <= days:
        return None
    returns = s.pct_change().dropna().iloc[-days:]
    if returns.empty:
        return None
    return float(returns.std() * 100)


def score_stock(history, benchmark_close=None):
    """
    1銘柄をスコアリングする。

    引数:
        history: yfinance の OHLCV DataFrame
        benchmark_close: 市場平均(TOPIX連動ETF)の Close Series
                         （相対強さ計算用、無ければ None）

    戻り値:
        {
            "score": 0.0〜10.0,
            "price": 現在株価,
            "expected_up_pct": 想定上昇率(%),
            "reasons": [注目理由の文字列, ...],
            "risks": [リスクの文字列, ...],
            "details": {観点ごとの素点},
            "metrics": {テクニカル指標の生値},
        }
        計算できない場合は None。
    """
    close = history["Close"].dropna()
    volume = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)

    if len(close) < 75:
        return None

    price = float(close.iloc[-1])

    sma5 = _sma(close, 5)
    sma25 = _sma(close, 25)
    sma75 = _sma(close, 75)
    if sma5 is None or sma25 is None or sma75 is None:
        return None

    reasons = []
    risks = []
    details = {}

    # --- 1. 短期トレンド: 5日 > 25日 で上向き ---
    if sma25:
        gap_5_25 = (sma5 - sma25) / sma25 * 100
    else:
        gap_5_25 = 0.0
    # -2%〜+5% を 0〜1 にマッピング
    s1 = _clamp((gap_5_25 + 2) / 7) * WEIGHTS["短期トレンド"]
    details["短期トレンド"] = s1
    if gap_5_25 > 0:
        reasons.append(f"5日線が25日線を{gap_5_25:.1f}%上回り短期上昇基調")
    else:
        risks.append(f"5日線が25日線を{abs(gap_5_25):.1f}%下回り短期は弱含み")

    # --- 2. 中期トレンド: 25日 > 75日 で上向き ---
    if sma75:
        gap_25_75 = (sma25 - sma75) / sma75 * 100
    else:
        gap_25_75 = 0.0
    s2 = _clamp((gap_25_75 + 3) / 10) * WEIGHTS["中期トレンド"]
    details["中期トレンド"] = s2
    if gap_25_75 > 0:
        reasons.append(f"25日線が75日線を{gap_25_75:.1f}%上回り中期トレンドは上向き")
    else:
        risks.append(f"25日線が75日線を下回り中期トレンドは弱い")

    # --- 3. 出来高: 直近5日平均 vs 25日平均 ---
    vol_ratio = None
    if len(volume) >= 25:
        vol5 = float(volume.rolling(5).mean().iloc[-1])
        vol25 = float(volume.rolling(25).mean().iloc[-1])
        if vol25 > 0:
            vol_ratio = vol5 / vol25
    if vol_ratio is not None:
        # 0.7倍〜1.5倍 を 0〜1 にマッピング
        s3 = _clamp((vol_ratio - 0.7) / 0.8) * WEIGHTS["出来高"]
        if vol_ratio > 1.1:
            reasons.append(f"直近出来高が平均比{vol_ratio:.2f}倍に増加し関心が高い")
        elif vol_ratio < 0.8:
            risks.append(f"直近出来高が平均比{vol_ratio:.2f}倍と低調")
    else:
        s3 = WEIGHTS["出来高"] * 0.5  # 不明なら中立
    details["出来高"] = s3

    # --- 4. 相対強さ: 直近20日の対 市場平均(TOPIX連動ETF) ---
    stock_20 = _pct_change(close, 20)
    rel = None
    if stock_20 is not None and benchmark_close is not None and len(benchmark_close.dropna()) > 20:
        bench_20 = _pct_change(benchmark_close, 20)
        if bench_20 is not None:
            rel = stock_20 - bench_20
    if rel is not None:
        # -5%〜+5% を 0〜1 にマッピング
        s4 = _clamp((rel + 5) / 10) * WEIGHTS["相対強さ"]
        if rel > 0:
            reasons.append(f"過去20日で市場平均(TOPIX)を{rel:.1f}ポイント上回る相対的な強さ")
        else:
            risks.append(f"過去20日で市場平均(TOPIX)を{abs(rel):.1f}ポイント下回る")
    else:
        s4 = WEIGHTS["相対強さ"] * 0.5
    details["相対強さ"] = s4

    # --- 5. 過熱回避: 直近5日の急騰をペナルティ ---
    surge_5 = _pct_change(close, 5)
    if surge_5 is not None:
        # +15%以上で過熱とみなし減点。0%〜15%上昇を満点〜0点へ
        if surge_5 <= 0:
            s5 = WEIGHTS["過熱回避"]  # 上げていなければ過熱なし＝満点
        else:
            s5 = _clamp(1 - surge_5 / 15) * WEIGHTS["過熱回避"]
        if surge_5 >= 12:
            risks.append(f"直近5日で{surge_5:.1f}%急騰しており短期過熱に注意")
    else:
        s5 = WEIGHTS["過熱回避"] * 0.5
    details["過熱回避"] = s5

    # --- 6. 安定性: ボラティリティが高すぎないか ---
    vol_pct = _daily_volatility(close, 20)
    if vol_pct is not None:
        # 日次ボラ 1%以下=満点, 4%以上=0点
        s6 = _clamp((4 - vol_pct) / 3) * WEIGHTS["安定性"]
        if vol_pct >= 3.5:
            risks.append(f"日次ボラティリティ{vol_pct:.1f}%と高く値動きが荒い")
    else:
        s6 = WEIGHTS["安定性"] * 0.5
    details["安定性"] = s6

    # --- 合計と正規化（0〜10）---
    raw = s1 + s2 + s3 + s4 + s5 + s6
    score = raw / MAX_RAW * 10.0
    score = max(0.0, min(10.0, score))

    # 想定上昇率: スコアと中期トレンドから簡易推定（あくまで目安）
    expected_up = _estimate_upside(score, gap_25_75)

    # 理由・リスクが空の場合の補完
    if not reasons:
        reasons.append("際立った強さは見られないが大きな崩れもない中立的な状態")
    if not risks:
        risks.append("現時点で目立ったリスクシグナルは検出されず")

    # レポート/Flexで詳しく解説するためのテクニカル指標の生値
    metrics = {
        "gap_5_25": gap_5_25,       # 5日線が25日線を上回る割合(%)
        "gap_25_75": gap_25_75,     # 25日線が75日線を上回る割合(%)
        "vol_ratio": vol_ratio,     # 直近5日平均出来高 / 25日平均（None有）
        "rel_strength": rel,        # 対 市場平均(TOPIX) 20日相対騰落(pt)（None有）
        "surge_5": surge_5,         # 直近5日騰落率(%)（None有）
        "volatility": vol_pct,      # 日次ボラティリティ(%)（None有）
        "sma5": sma5, "sma25": sma25, "sma75": sma75,
    }

    return {
        "score": round(score, 1),
        "price": price,
        "expected_up_pct": expected_up,
        "reasons": reasons,
        "risks": risks,
        "details": details,
        "metrics": metrics,
    }


def _estimate_upside(score, mid_trend_gap):
    """
    想定上昇率(%)の簡易推定。スコアが高く中期トレンドが上向きなほど大きく。
    あくまで機械的な目安であり、予測を保証するものではない。
    """
    base = (score - 5.0) * 1.2          # スコア5を基準に±
    trend = max(-3.0, min(3.0, mid_trend_gap * 0.3))
    upside = base + trend
    return round(max(-5.0, min(15.0, upside)), 1)


def _clamp(x, lo=0.0, hi=1.0):
    """x を [lo, hi] に収める。"""
    return max(lo, min(hi, x))


def score_all(stock_histories, benchmark_df=None, top_n=10):
    """
    全候補銘柄をスコアリングし、上位 top_n 件を返す。

    引数:
        stock_histories: fetch_histories の戻り値
        benchmark_df: 市場平均(TOPIX連動ETF)の履歴 DataFrame（相対強さの基準）
    戻り値:
        スコア降順の銘柄リスト（各要素に code/name/sector/score/metrics 等を含む）
    """
    benchmark_close = None
    if benchmark_df is not None and "Close" in benchmark_df:
        benchmark_close = benchmark_df["Close"]

    scored = []
    for item in stock_histories:
        result = score_stock(item["history"], benchmark_close=benchmark_close)
        if result is None:
            print(f"[警告] スコア計算をスキップ: {item['name']} ({item['code']})")
            continue
        scored.append({
            "code": item["code"],
            "name": item["name"],
            "sector": item.get("sector", ""),
            **result,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]
