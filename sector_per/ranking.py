"""
sector_per/ranking.py

業種別PERランキングの計算ロジック（純粋関数・配信/取得に非依存）。

1銘柄の入力レコード(dict)想定キー:
  code, name, sector33, market,
  close(当日終値), forecast_eps(会社予想EPS), market_cap(時価総額),
  avg_turnover(20日平均売買代金), listing_years(上場年数),
  roe(%), equity_ratio(自己資本比率%),
  revenue_or_profit_growth(bool: 増収 or 増益予想), no_downward_revision(bool),
  per_history(list[float]: 過去の予想PER系列),
  fundamentals_asof(str: 財務データの基準日。無料枠の遅延を明示するため)

数値が欠損(None)する項目は「判定不能＝除外」に倒す（捏造しない）。
"""

import statistics

from . import config as C


# ===== 予想PER =====
def forecast_per(stock):
    """当日終値 ÷ 会社予想EPS。EPS<=0 や欠損は None（PER算出不能）。"""
    eps = stock.get("forecast_eps")
    close = stock.get("close")
    if eps is None or close is None:
        return None
    try:
        eps = float(eps)
        close = float(close)
    except (TypeError, ValueError):
        return None
    if eps <= C.MIN_FORECAST_EPS or close <= 0:
        return None
    return close / eps


# ===== 2-1. 母集団フィルタ =====
def pass_universe(stock, cfg=C):
    """
    母集団の絞り込み条件を満たすか。戻り値: (bool, [除外理由, ...])。

    予想EPS>0 / 時価総額 / 売買代金 / 上場年数 / 対象市場 をすべて満たすこと。
    """
    reasons = []
    if forecast_per(stock) is None:
        reasons.append("予想EPSが正でない/取得不可")
    mc = stock.get("market_cap")
    if mc is None or float(mc) < cfg.MIN_MARKET_CAP:
        reasons.append("時価総額が下限未満")
    tv = stock.get("avg_turnover")
    if tv is None or float(tv) < cfg.MIN_AVG_TURNOVER:
        reasons.append("売買代金が下限未満")
    ly = stock.get("listing_years")
    if ly is None or float(ly) < cfg.MIN_LISTING_YEARS:
        reasons.append("上場から一定年数未満")
    mk = (stock.get("market") or "")
    if not any(t in mk for t in cfg.TARGET_MARKETS):
        reasons.append("対象市場外")
    return (len(reasons) == 0, reasons)


# ===== 2-2. 業種内相対評価 =====
def sector_median_per(stocks, cfg=C):
    """
    同一業種の母集団通過銘柄の予想PER中央値。銘柄数が下限未満なら None。

    平均でなく中央値（高PER1社で壊れないため）。
    """
    pers = [forecast_per(s) for s in stocks]
    pers = [p for p in pers if p is not None]
    if len(pers) < cfg.MIN_PEERS_FOR_MEDIAN:
        return None
    return statistics.median(pers)


def sector_relative_score(per, median):
    """(業種中央値 − 個別PER) ÷ 業種中央値。正が大きいほど業種内で割安。"""
    if per is None or median is None or median == 0:
        return None
    return (median - per) / median


def self_history_percentile(current_per, history, cfg=C):
    """
    自銘柄の過去予想PER系列の中で、現在値のパーセンタイル順位(0〜1)。

    低いほど「その銘柄として歴史的に安い」。サンプルが下限未満なら None。
    戻り値は「順位」(0=最安)。割安度に変換するには self_cheapness を使う。
    """
    if current_per is None:
        return None
    hist = [h for h in (history or []) if h is not None and h > 0]
    if len(hist) < cfg.MIN_HISTORY_SAMPLES:
        return None
    below = sum(1 for h in hist if h < current_per)
    return below / len(hist)


def self_cheapness(current_per, history, cfg=C):
    """自己過去比の割安度(0〜1・高いほど歴史的に安い)。= 1 − パーセンタイル順位。"""
    pct = self_history_percentile(current_per, history, cfg)
    return None if pct is None else (1.0 - pct)


# ===== 2-3. バリュートラップ除外 =====
def passes_valuetrap(stock, cfg=C):
    """
    バリュートラップ除外条件をすべて満たすか。戻り値: (bool, [除外理由, ...])。

    ROE / 自己資本比率 / (増収 or 増益予想) / 直近1年下方修正なし。
    数値欠損・真偽欠損は「満たさない」に倒す（安全側）。
    """
    reasons = []
    roe = stock.get("roe")
    if roe is None or float(roe) < cfg.MIN_ROE:
        reasons.append("ROEが下限未満/不明")
    er = stock.get("equity_ratio")
    if er is None or float(er) < cfg.MIN_EQUITY_RATIO:
        reasons.append("自己資本比率が下限未満/不明")
    if not stock.get("revenue_or_profit_growth"):
        reasons.append("増収も増益予想も確認できない")
    if not stock.get("no_downward_revision"):
        reasons.append("直近1年以内に予想下方修正の疑い")
    return (len(reasons) == 0, reasons)


# ===== 2-4. 総合スコア =====
def total_score(sector_relative, self_ch, cfg=C):
    """
    総合スコア = 業種内割安度 × W_sector + 自己過去比割安度 × W_self。

    どちらかが None（算出不能）の場合は、算出できた側だけで重み再正規化する
    （例: 過去比が未算出でも業種内割安度だけで評価する）。両方 None は None。
    """
    parts, weights = [], []
    if sector_relative is not None:
        parts.append(sector_relative)
        weights.append(cfg.WEIGHT_SECTOR_RELATIVE)
    if self_ch is not None:
        parts.append(self_ch)
        weights.append(cfg.WEIGHT_SELF_HISTORY)
    if not parts:
        return None
    wsum = sum(weights)
    return sum(p * w for p, w in zip(parts, weights)) / wsum if wsum else None


# ===== 統合: 全銘柄スコアリングと業種別ランキング =====
def score_all(stocks, cfg=C):
    """
    全銘柄をスコアリングし、(業種別ランキング, 全銘柄スコア行) を返す。

    戻り値:
      rankings: {sector33: {
                   "median_per": float|None,
                   "universe_n": int,          # 母集団通過数（中央値の母数）
                   "candidates": [row, ...],   # valuetrap通過＋スコア上位 top_n
                   "has_candidates": bool,
                 }, ...}
      all_rows: [row, ...]  # 検証用CSV向け（全銘柄・各指標・通過可否・理由）

    row 主要キー: code,name,sector33,close,forecast_per,sector_median_per,
      sector_relative,self_percentile,self_cheapness,roe,equity_ratio,
      total_score,in_universe,passes_valuetrap,reasons,fundamentals_asof
    """
    # 業種ごとに母集団通過銘柄を集め、中央値を算出
    by_sector = {}
    for s in stocks:
        by_sector.setdefault(s.get("sector33") or "未分類", []).append(s)

    medians, universe_pass = {}, {}
    for sector, members in by_sector.items():
        passed = [m for m in members if pass_universe(m, cfg)[0]]
        universe_pass[sector] = passed
        medians[sector] = sector_median_per(passed, cfg)

    all_rows = []
    rankings = {}
    for sector, members in by_sector.items():
        median = medians[sector]
        cand_rows = []
        for s in members:
            per = forecast_per(s)
            in_uni, uni_reasons = pass_universe(s, cfg)
            vt_ok, vt_reasons = passes_valuetrap(s, cfg)
            rel = sector_relative_score(per, median) if in_uni else None
            pct = self_history_percentile(per, s.get("per_history"), cfg) if in_uni else None
            ch = None if pct is None else (1.0 - pct)
            score = total_score(rel, ch, cfg) if in_uni else None
            row = {
                "code": s.get("code"), "name": s.get("name"),
                "sector33": sector, "market": s.get("market"),
                "close": s.get("close"), "forecast_eps": s.get("forecast_eps"),
                "forecast_per": per, "sector_median_per": median,
                "sector_relative": rel, "self_percentile": pct,
                "self_cheapness": ch, "roe": s.get("roe"),
                "equity_ratio": s.get("equity_ratio"),
                "total_score": score,
                "in_universe": in_uni,
                "passes_valuetrap": vt_ok,
                "reasons": "; ".join(uni_reasons + vt_reasons),
                "fundamentals_asof": s.get("fundamentals_asof", ""),
            }
            all_rows.append(row)
            if in_uni and vt_ok and score is not None:
                # スコア下限（設定時）。既定 None＝依頼通り上位N無条件。
                if (cfg.MIN_TOTAL_SCORE_FOR_CANDIDATE is None
                        or score >= cfg.MIN_TOTAL_SCORE_FOR_CANDIDATE):
                    cand_rows.append(row)
        cand_rows.sort(key=lambda r: r["total_score"], reverse=True)
        top = cand_rows[:cfg.TOP_N_PER_SECTOR]
        rankings[sector] = {
            "median_per": median,
            "universe_n": len(universe_pass[sector]),
            "candidates": top,
            "has_candidates": len(top) > 0,
        }
    return rankings, all_rows
