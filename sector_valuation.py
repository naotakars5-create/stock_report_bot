"""
sector_valuation.py

業種別バリュエーション（PER中央値）のローリングキャッシュ（改善3）。

課題:
  選定根拠の「割安圏」は従来 PER の絶対水準（≤20倍等）で判定していた。
  本来やりたいのは「PER 業種中央値以下」だが、全銘柄（約3,700）のPERを毎日
  取得するのは yfinance では現実的でない（1銘柄ずつ .info を引く必要があり遅い）。

方式:
  毎日の二次スクリーニングで取得する約50銘柄のバリュエーションを
  data/per_cache.csv に蓄積する（同一銘柄は最新値で上書き）。
  日々の蓄積で業種カバレッジが広がり、鮮度の切れた行（90日超）は集計から除外。
  業種のサンプルが最低数（5銘柄）に満たない間は中央値を返さず、
  呼び出し側（selection_basis）は従来の絶対水準判定にフォールバックする。

  ※このキャッシュは「一次スクリーニングを通過しやすい銘柄」に偏るサンプルであり、
    厳密な業種全体の中央値ではない。配信では「業種中央値（当社集計）」として扱う。
"""

import csv
import os
import statistics
from datetime import datetime, timedelta

import market_calendar


CACHE_PATH = os.path.join("data", "per_cache.csv")
CACHE_FIELDS = ["code", "sector", "per", "updated"]

MAX_AGE_DAYS = 90   # これより古い行は中央値の計算から除外
MIN_SAMPLES = 5     # 業種の最低サンプル数（未満なら中央値を返さない）


def _read_rows(path=CACHE_PATH):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[業種PER] キャッシュの読み込みに失敗しました: {e}")
        return []


def update_cache(valuations, sectors, today_str=None, path=CACHE_PATH):
    """
    今日取得したバリュエーションをキャッシュへ反映する（同一銘柄は最新で上書き）。

    引数:
        valuations: {code: {"per", ...}}（data_fetcher.get_valuation の結果）
        sectors: {code: 業種名}
    戻り値: 反映後の行数。失敗しても止めない。
    """
    today_str = today_str or market_calendar.today_jst().strftime("%Y-%m-%d")
    try:
        rows = {r.get("code"): r for r in _read_rows(path) if r.get("code")}
        for code, val in (valuations or {}).items():
            per = (val or {}).get("per")
            sector = (sectors or {}).get(code, "")
            if per is None or per <= 0 or not sector:
                continue
            rows[code] = {"code": code, "sector": sector,
                          "per": f"{float(per):.2f}", "updated": today_str}
        out = sorted(rows.values(), key=lambda r: (r.get("sector") or "",
                                                   r.get("code") or ""))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
            writer.writeheader()
            for r in out:
                writer.writerow({k: r.get(k, "") for k in CACHE_FIELDS})
        return len(out)
    except Exception as e:
        print(f"[業種PER] キャッシュ更新に失敗しました（処理は継続）: {e}")
        return 0


def sector_medians(path=CACHE_PATH, today=None, max_age_days=MAX_AGE_DAYS,
                   min_samples=MIN_SAMPLES):
    """
    業種ごとの PER 中央値を返す。サンプル不足（min_samples 未満）の業種は含めない。

    戻り値: {sector: {"median": float, "n": int}}
    """
    today = today or market_calendar.today_jst()
    cutoff = (today - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    buckets = {}
    for r in _read_rows(path):
        sector = (r.get("sector") or "").strip()
        updated = (r.get("updated") or "").strip()
        if not sector or (updated and updated < cutoff):
            continue
        try:
            per = float(r.get("per"))
        except (TypeError, ValueError):
            continue
        if per > 0:
            buckets.setdefault(sector, []).append(per)
    out = {}
    for sector, vals in buckets.items():
        if len(vals) >= min_samples:
            out[sector] = {"median": statistics.median(vals), "n": len(vals)}
    return out


def attach_sector_median(scored_stocks, medians=None, path=CACHE_PATH):
    """
    各銘柄に業種PER中央値（s["sector_per_median"]・s["sector_per_n"]）を付与する。

    selection_basis が「PER業種中央値以下」の判定に使う。該当業種の中央値が
    無い場合は付与しない（＝絶対水準判定へのフォールバックが働く）。
    """
    medians = medians if medians is not None else sector_medians(path=path)
    for s in (scored_stocks or []):
        m = medians.get((s.get("sector") or "").strip())
        if m:
            s["sector_per_median"] = m["median"]
            s["sector_per_n"] = m["n"]
    return scored_stocks
