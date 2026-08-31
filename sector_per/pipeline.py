"""
sector_per/pipeline.py

データ組み立て（財務キャッシュの入出力・EPS履歴→予想PER系列・銘柄レコード合成）。

配信(delivery)・純粋計算(ranking)からは独立した「データ準備層」。
ネットワーク非依存の変換関数を中心にし、単体テストできるようにする。

財務キャッシュ data/fundamentals_cache.csv:
  J-Quants /fins/statements を開示日単位で取り込み、銘柄ごとに最新財務＋予想EPS履歴を
  蓄積する。無料枠の遅延を明示するため asof（最新開示日）も保持する。
"""

import csv
import os
from datetime import datetime, timedelta

from . import jquants_client as J


CACHE_PATH = os.path.join("data", "fundamentals_cache.csv")
CACHE_FIELDS = ["code", "forecast_eps", "roe", "equity_ratio",
                "revenue_or_profit_growth", "no_downward_revision",
                "eps_history", "asof"]


# ===== EPS履歴の直列化 =====
def serialize_eps_history(eps_hist):
    """[(date, eps), ...] → "date:eps|date:eps"。"""
    return "|".join(f"{d}:{e}" for d, e in (eps_hist or []) if e is not None)


def parse_eps_history(raw):
    """"date:eps|..." → [(date, float), ...]（昇順）。"""
    out = []
    for part in (raw or "").split("|"):
        if ":" not in part:
            continue
        d, e = part.rsplit(":", 1)
        try:
            out.append((d, float(e)))
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    return out


# ===== 財務キャッシュ行の生成（純粋関数） =====
def build_cache_row(latest, eps_hist, today_str, revision_window_days=365):
    """
    parse_statement 済みの最新レコードと予想EPS履歴から、キャッシュ1行を作る。

    - 増収 or 増益予想: 予想売上>実績売上 または 予想利益>実績利益
    - 直近1年下方修正なし: 予想EPS履歴の直近1年に下げ転換が無い
    """
    if not latest:
        return None
    growth = False
    fs, ns = latest.get("forecast_net_sales"), latest.get("net_sales")
    fp, pr = latest.get("forecast_profit"), latest.get("profit")
    if fs is not None and ns is not None and fs > ns:
        growth = True
    if fp is not None and pr is not None and fp > pr:
        growth = True
    within = None
    try:
        within = (datetime.strptime(today_str, "%Y-%m-%d").date()
                  - timedelta(days=revision_window_days)).isoformat()
    except Exception:
        within = None
    no_rev = not J.has_downward_revision(eps_hist, within_from=within)
    return {
        "code": latest.get("code"),
        "forecast_eps": latest.get("forecast_eps"),
        "roe": J.roe_from(latest),
        "equity_ratio": latest.get("equity_ratio"),
        "revenue_or_profit_growth": "1" if growth else "0",
        "no_downward_revision": "1" if no_rev else "0",
        "eps_history": serialize_eps_history(eps_hist),
        "asof": latest.get("disclosed_date") or "",
    }


# ===== 予想PER系列（自己過去比用・純粋関数） =====
def build_per_history(close_pairs, eps_history):
    """
    日次終値系列 [(date, close), ...] と 予想EPS履歴 [(date, eps), ...] から、
    各日の「その時点で有効な予想EPS」で予想PER系列を作る（ステップ関数）。

    その日以前で最も新しい予想EPSを採用。EPS が無い/<=0 の日はスキップ。
    戻り値: [float(PER), ...]
    """
    eps_hist = sorted([(d, e) for d, e in (eps_history or []) if e and e > 0],
                      key=lambda x: x[0])
    if not eps_hist or not close_pairs:
        return []
    out = []
    j = 0
    eff = None
    for d, c in sorted(close_pairs, key=lambda x: x[0]):
        while j < len(eps_hist) and eps_hist[j][0] <= d:
            eff = eps_hist[j][1]
            j += 1
        if eff and eff > 0 and c and c > 0:
            out.append(c / eff)
    return out


# ===== キャッシュ I/O =====
def load_cache(path=CACHE_PATH):
    """財務キャッシュを {code: row} で返す。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return {r["code"]: r for r in csv.DictReader(f) if r.get("code")}
    except Exception as e:
        print(f"[業種PER] 財務キャッシュ読み込み失敗: {e}")
        return {}


def save_cache(rows_by_code, path=CACHE_PATH):
    """{code: row} を CSV に保存。"""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
            w.writeheader()
            for code in sorted(rows_by_code):
                w.writerow({k: rows_by_code[code].get(k, "") for k in CACHE_FIELDS})
        return True
    except Exception as e:
        print(f"[業種PER] 財務キャッシュ保存失敗: {e}")
        return False


def ingest_statements(cache, statements_by_code, today_str):
    """
    銘柄ごとの statements リスト（生JSON）をキャッシュへ取り込む（最新で上書き）。

    statements_by_code: {code: [生statementレコード, ...]}
    戻り値: 更新後の {code: row}
    """
    cache = dict(cache or {})
    for code, stmts in (statements_by_code or {}).items():
        latest, eps_hist = J.latest_and_history(stmts)
        row = build_cache_row(latest, eps_hist, today_str)
        if row and row.get("code"):
            cache[row["code"]] = row
    return cache


# ===== 銘柄レコード合成（ranking へ渡す形） =====
def _f(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def assemble_records(universe, cache, price_info):
    """
    ユニバース(メタ) × 財務キャッシュ × 価格情報 を結合し、ranking.score_all 用の
    銘柄レコード群にする。

    universe: [{code,name,market,sector33}, ...]（jpx_listed_companies 由来）
    cache: {code: 財務row}
    price_info: {code: {close, avg_turnover, listing_years}}（yfinance 由来）
      ※per_history は close × eps_history から本関数内で合成する場合は price_info に
        close_pairs を含める（無ければ空）。
    戻り値: [ranking 用 dict, ...]
    """
    records = []
    for u in universe:
        code = u.get("code")
        fin = cache.get(code) or {}
        pi = (price_info or {}).get(code) or {}
        eps_hist = parse_eps_history(fin.get("eps_history"))
        per_hist = build_per_history(pi.get("close_pairs") or [], eps_hist)
        records.append({
            "code": code, "name": u.get("name"),
            "sector33": u.get("sector33") or u.get("sector"),
            "market": u.get("market"),
            "close": pi.get("close"),
            "avg_turnover": pi.get("avg_turnover"),
            "listing_years": pi.get("listing_years"),
            "market_cap": pi.get("market_cap"),
            "forecast_eps": _f(fin.get("forecast_eps")),
            "roe": _f(fin.get("roe")),
            "equity_ratio": _f(fin.get("equity_ratio")),
            "revenue_or_profit_growth": str(fin.get("revenue_or_profit_growth")) == "1",
            "no_downward_revision": str(fin.get("no_downward_revision")) == "1",
            "per_history": per_hist,
            "fundamentals_asof": fin.get("asof", ""),
        })
    return records
