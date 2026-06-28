"""
report_history.py

スクリーニング結果（上位銘柄）を保存し、過去レポートの「検証」
（上位銘柄がその後どう推移したかの機械的な集計）を行うモジュール。

- 保存先: data/report_history.csv
- 保存項目: 実行日, 証券コード, 銘柄名, 順位, 評価点, 実行時株価, 業種, テーマタグ
- これは過去の抽出結果の追跡であり、売買の成果や推奨を示すものではありません。

検証は **その回の上位5銘柄だけ** を対象にし、勝敗数は最大5件になります。
前回（直近）に加え、3営業日前・1週間前（5営業日前）の回も、データがある範囲で検証します。

設計方針:
  - 保存・読み込みに失敗しても全体を止めない（例外は握りつぶして警告）。
  - **同じ実行日の重複保存は避ける**（同日に複数回実行しても最後の1回だけ残す）。
  - GitHub Actions など実行環境が毎回リセットされる場合は、CSVをリポジトリに
    コミットして永続化する（ワークフロー側で対応）。
"""

import csv
import os
from datetime import datetime, date


DEFAULT_PATH = os.path.join("data", "report_history.csv")
FIELDS = ["run_date", "code", "name", "rank", "score", "price", "sector", "theme_tags"]


def _read_rows(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] レポート履歴の読み込みに失敗しました: {e}")
        return []


def _join_tags(tags):
    return "|".join(tags or [])


def _split_tags(raw):
    return [t.strip() for t in (raw or "").replace("、", "|").split("|") if t.strip()]


def save_report(scored_stocks, path=DEFAULT_PATH, run_date=None):
    """
    今回のスクリーニング上位銘柄（最大5件）を履歴CSVへ保存する。

    同じ run_date の既存行は削除してから書き込むため、同日に複数回実行しても
    重複は残らない（最後の1回だけが残る）。成功で True。
    """
    if not scored_stocks:
        return False
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # 既存行から「今日の run_date」を除外（同日重複を防ぐ）
        rows = [r for r in _read_rows(path)
                if (r.get("run_date") or "").strip() != run_date]

        for rank, s in enumerate(scored_stocks[:5], start=1):
            rows.append({
                "run_date": run_date,
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "rank": rank,
                "score": f"{s.get('score', 0):.1f}",
                "price": f"{s.get('price', 0):.1f}",
                "sector": s.get("sector", ""),
                "theme_tags": _join_tags(s.get("theme_tags")),
            })

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in FIELDS})
        print(f"[履歴] 今回の上位{min(len(scored_stocks), 5)}銘柄を {path} に保存しました"
              f"（同日重複は排除）。")
        return True
    except Exception as e:
        print(f"[警告] レポート履歴の保存に失敗しました: {e}")
        return False


def load_runs(path=DEFAULT_PATH, before_date=None):
    """
    履歴を実行日ごとにまとめ、新しい順のリストで返す。

    戻り値: [{"run_date", "entries": [{code,name,rank,score,price,sector,theme_tags}, ...]}, ...]
    before_date を指定すると、それより前（当日を含めない）の回だけを返す。
    """
    rows = _read_rows(path)
    if not rows:
        return []
    by_date = {}
    for r in rows:
        d = (r.get("run_date") or "").strip()
        if not d:
            continue
        if before_date and d >= before_date:
            continue
        by_date.setdefault(d, []).append(r)

    runs = []
    for d in sorted(by_date, reverse=True):
        entries = by_date[d]
        try:
            entries.sort(key=lambda r: int(r.get("rank") or 99))
        except Exception:
            pass
        runs.append({"run_date": d, "entries": entries[:5]})
    return runs


def load_previous(path=DEFAULT_PATH, before_date=None):
    """直近1回分（before_date 指定時はそれより前の最新）。無ければ None。"""
    runs = load_runs(path, before_date=before_date)
    return runs[0] if runs else None


def _business_days_between(d_from, d_to):
    """d_from〜d_to の営業日数（平日カウント・概算。祝日は考慮しない）。"""
    try:
        a = datetime.strptime(d_from, "%Y-%m-%d").date()
        b = d_to if isinstance(d_to, date) else datetime.strptime(d_to, "%Y-%m-%d").date()
    except Exception:
        return None
    if a > b:
        a, b = b, a
    days, cur = 0, a
    from datetime import timedelta
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def select_horizon_runs(runs, today=None, targets=(1, 3, 5)):
    """
    検証する回を、営業日距離が targets に近い順で選ぶ（重複は除く）。

    戻り値: [(label, run, age_business_days), ...]
      label は "前回" / "3営業日前" / "1週間前" のような表示名。
    """
    if not runs:
        return []
    today = today or datetime.now().strftime("%Y-%m-%d")
    labels = {1: "前回", 3: "3営業日前", 5: "1週間前"}
    chosen, used_dates = [], set()
    for tgt in targets:
        best, best_diff = None, None
        for run in runs:
            if run["run_date"] in used_dates:
                continue
            age = _business_days_between(run["run_date"], today)
            if age is None:
                continue
            diff = abs(age - tgt)
            if best is None or diff < best_diff:
                best, best_diff, best_age = run, diff, age
        if best is not None:
            used_dates.add(best["run_date"])
            chosen.append((labels.get(tgt, f"{tgt}営業日前"), best, best_age))
    return chosen


def benchmark_return(price_df, since_date_str):
    """price_df(Close を持つ DataFrame)の since_date 以降〜最新の騰落率(%)。無ければ None。"""
    if price_df is None or "Close" not in price_df:
        return None
    try:
        close = price_df["Close"].dropna()
        if close.empty:
            return None
        target = datetime.strptime(since_date_str, "%Y-%m-%d").date()
        past = [float(v) for ts, v in zip(close.index, close.values) if ts.date() <= target]
        if not past:
            return None
        base = past[-1]
        latest = float(close.values[-1])
        if base <= 0:
            return None
        return (latest - base) / base * 100
    except Exception:
        return None


def build_validation(run, current_prices, nikkei_pct=None, topix_pct=None, label="前回"):
    """
    ある回の上位5銘柄について、当時株価と現在株価から騰落を集計する。

    戻り値: {label, run_date, evaluated, total, avg_return, wins, losses,
             best, worst, nikkei_return, topix_return, vs_nikkei, vs_topix}
    集計できなければ None。
    """
    if not run or not run.get("entries"):
        return None
    entries = run["entries"][:5]
    results = []
    for e in entries:
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
    return {
        "label": label,
        "run_date": run["run_date"],
        "evaluated": len(results),
        "total": len(entries),
        "avg_return": avg,
        "wins": wins,
        "losses": losses,
        "best": max(results, key=lambda r: r["return"]),
        "worst": min(results, key=lambda r: r["return"]),
        "nikkei_return": nikkei_pct,
        "topix_return": topix_pct,
        "vs_nikkei": (avg - nikkei_pct) if nikkei_pct is not None else None,
        "vs_topix": (avg - topix_pct) if topix_pct is not None else None,
    }
