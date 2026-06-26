"""
report_history.py

スクリーニング結果（上位銘柄）を保存し、次回実行時に「前回レポートの検証」
（前回上位銘柄が、その後どう推移したかの機械的な集計）を行うモジュール。

- 保存先: data/report_history.csv
- 保存項目: 実行日, 証券コード, 銘柄名, 評価点, 実行時株価, 順位
- これは過去の抽出結果の追跡であり、売買の成果や推奨を示すものではありません。

設計方針:
  - 保存・読み込みに失敗しても全体を止めない（例外は握りつぶして警告）。
  - GitHub Actions など実行環境が毎回リセットされる場合は、CSVをリポジトリに
    コミットして永続化する（ワークフロー側で対応）。
"""

import csv
import os
from datetime import datetime


DEFAULT_PATH = os.path.join("data", "report_history.csv")
FIELDS = ["run_date", "code", "name", "score", "price", "rank"]


def _read_rows(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] レポート履歴の読み込みに失敗しました: {e}")
        return []


def save_report(scored_stocks, path=DEFAULT_PATH, run_date=None):
    """
    今回のスクリーニング上位銘柄を履歴CSVへ追記する。成功で True。
    """
    if not scored_stocks:
        return False
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        exists = os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                writer.writeheader()
            for rank, s in enumerate(scored_stocks, start=1):
                writer.writerow({
                    "run_date": run_date,
                    "code": s["code"],
                    "name": s["name"],
                    "score": f"{s['score']:.1f}",
                    "price": f"{s['price']:.1f}",
                    "rank": rank,
                })
        print(f"[履歴] 今回の上位{len(scored_stocks)}銘柄を {path} に保存しました。")
        return True
    except Exception as e:
        print(f"[警告] レポート履歴の保存に失敗しました: {e}")
        return False


def load_previous(path=DEFAULT_PATH, before_date=None):
    """
    履歴の中で最も新しい1回分（before_date 指定時はそれより前の最新）を返す。

    戻り値:
        {"run_date": "YYYY-MM-DD", "entries": [{code,name,score,price,rank}, ...]}
        無ければ None。
    """
    rows = _read_rows(path)
    if not rows:
        return None
    dates = sorted({(r.get("run_date") or "").strip() for r in rows if r.get("run_date")})
    if before_date:
        dates = [d for d in dates if d < before_date]
    if not dates:
        return None
    target = dates[-1]
    entries = [r for r in rows if (r.get("run_date") or "").strip() == target]
    try:
        entries.sort(key=lambda r: int(r.get("rank") or 0))
    except Exception:
        pass
    return {"run_date": target, "entries": entries}


def benchmark_return(benchmark_df, since_date_str):
    """
    ベンチマーク(TOPIX連動ETF)の since_date 以降〜最新の騰落率(%)を返す。
    計算できなければ None。
    """
    if benchmark_df is None or "Close" not in benchmark_df:
        return None
    try:
        close = benchmark_df["Close"].dropna()
        if close.empty:
            return None
        target = datetime.strptime(since_date_str, "%Y-%m-%d").date()
        past = [float(v) for ts, v in zip(close.index, close.values)
                if ts.date() <= target]
        if not past:
            return None
        base = past[-1]
        latest = float(close.values[-1])
        if base <= 0:
            return None
        return (latest - base) / base * 100
    except Exception:
        return None


def build_validation(previous, current_prices, benchmark_pct=None):
    """
    前回上位銘柄について、前回株価と今回株価から騰落を集計する。

    引数:
        previous: load_previous の戻り値
        current_prices: {証券コード: 今回株価} の辞書
        benchmark_pct: 同期間のベンチマーク騰落率(%)（任意）

    戻り値:
        {
          "run_date", "count", "avg_return", "benchmark_return",
          "wins", "losses", "best": {name, return}, "worst": {name, return},
        }
        集計できなければ None。
    """
    if not previous or not previous.get("entries"):
        return None

    results = []
    for e in previous["entries"]:
        code = (e.get("code") or "").strip()
        name = (e.get("name") or "").strip()
        try:
            prev_price = float(e.get("price"))
        except (TypeError, ValueError):
            continue
        cur = current_prices.get(code)
        if cur is None or prev_price <= 0:
            continue
        results.append({"name": name, "code": code,
                        "return": (cur - prev_price) / prev_price * 100})

    if not results:
        return None

    avg = sum(r["return"] for r in results) / len(results)
    wins = sum(1 for r in results if r["return"] > 0)
    losses = len(results) - wins
    best = max(results, key=lambda r: r["return"])
    worst = min(results, key=lambda r: r["return"])
    return {
        "run_date": previous["run_date"],
        "count": len(results),
        "avg_return": avg,
        "benchmark_return": benchmark_pct,
        "wins": wins,
        "losses": losses,
        "best": best,
        "worst": worst,
    }
