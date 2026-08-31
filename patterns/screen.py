"""
patterns/screen.py

全銘柄にチャートパターン検出を適用し、全体（業種問わず）で上位N銘柄を選ぶ。

入力 stock: {code, name, sector, ohlcv:{close,high,low,volume}}
純粋関数（ネットワーク非依存）。母集団は軽い流動性フィルタ＋過熱除外のみ。
"""

from . import config as C
from . import detectors as D


def _sma_last(vals, w):
    if len(vals) < w:
        return None
    seg = vals[-w:]
    return sum(seg) / w


def metrics(ohlcv, cfg=C):
    """終値・平均売買代金・5日騰落・25日線乖離を算出。"""
    close = ohlcv.get("close") or []
    vol = ohlcv.get("volume") or []
    if len(close) < 6:
        return None
    price = close[-1]
    turnover = None
    w = cfg.TURNOVER_WINDOW
    if len(vol) >= w and len(close) >= w:
        turnover = sum(close[i] * vol[i] for i in range(len(close) - w, len(close))) / w
    surge5 = (price / close[-6] - 1) * 100 if close[-6] else None
    s25 = _sma_last(close, 25)
    above25 = ((price / s25 - 1) * 100) if s25 else None
    return {"price": price, "avg_turnover": turnover,
            "surge_5": surge5, "above_25ma": above25}


def pass_universe(m, cfg=C):
    """流動性＋過熱除外。戻り値: (bool, [理由])。"""
    reasons = []
    if m is None:
        return (False, ["データ不足"])
    if m["avg_turnover"] is None or m["avg_turnover"] < cfg.MIN_AVG_TURNOVER:
        reasons.append("売買代金が下限未満")
    if m["surge_5"] is not None and m["surge_5"] > cfg.MAX_SURGE_5D:
        reasons.append(f"直近5日 +{m['surge_5']:.0f}%で過熱")
    if m["above_25ma"] is not None and m["above_25ma"] > cfg.MAX_ABOVE_25MA:
        reasons.append("25日線からの上方乖離が大きい（過熱）")
    return (len(reasons) == 0, reasons)


def _score(patterns):
    """検出パターン群から総合スコア。最強パターン＋複数確認のボーナス。"""
    if not patterns:
        return 0.0
    best = max(p["strength"] for p in patterns)
    return min(1.0, best + 0.08 * (len(patterns) - 1))


def score_all(stocks, cfg=C):
    """
    全銘柄をパターン検出し、(全体上位N, 全銘柄行) を返す。

    戻り値:
      ranked: [row, ...]  # in_universe かつパターン検出ありを score 降順で top_n
      all_rows: [row, ...] # 検証用CSV向け（全銘柄・指標・検出・通過可否）
    """
    all_rows, fired_rows = [], []
    for s in stocks:
        o = s.get("ohlcv") or {}
        if len(o.get("close") or []) < cfg.MIN_ROWS:
            all_rows.append({"code": s.get("code"), "name": s.get("name"),
                             "sector": s.get("sector"), "in_universe": False,
                             "patterns": "", "score": 0.0, "reasons": "データ不足"})
            continue
        m = metrics(o, cfg)
        in_uni, reasons = pass_universe(m, cfg)
        patterns = D.detect_all(o) if in_uni else []
        score = _score(patterns)
        row = {
            "code": s.get("code"), "name": s.get("name"), "sector": s.get("sector"),
            "price": m["price"] if m else None,
            "avg_turnover": m["avg_turnover"] if m else None,
            "surge_5": m["surge_5"] if m else None,
            "above_25ma": m["above_25ma"] if m else None,
            "vol_ratio": D._vol_ratio(o.get("volume") or [], cfg.TURNOVER_WINDOW),
            "patterns": patterns,
            "pattern_labels": "・".join(p["label"] for p in patterns),
            "score": round(score, 3),
            "in_universe": in_uni,
            "reasons": "; ".join(reasons),
        }
        all_rows.append(row)
        if in_uni and patterns:
            fired_rows.append(row)
    fired_rows.sort(key=lambda r: r["score"], reverse=True)
    return fired_rows[:cfg.TOP_N], all_rows
