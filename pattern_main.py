"""
pattern_main.py

上昇チャートパターンの日次バッチ（株価のみ・J-Quants不要・当日終値で完結）。

処理の流れ:
  0. 休場日ガード（土日祝・年末年始）
  1. ユニバース構築（jpx_listed_companies.csv・対象市場）
  2. yfinance で日足を取得（既存データ層を流用）
  3. patterns.screen でチャートパターンを検出し全体で上位Nを選定
  4. 全銘柄検出CSV（検証用）＋計算過程ログ
  5. LINE Flex カルーセル＋補足テキスト（必須免責つき）を配信

これは投資助言ではなく、公開データに基づく機械的なパターン検出です。

環境変数: LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID（既存流用）。DRY_RUN=1 で表示のみ。
"""

import csv
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import data_fetcher
import line_sender
import market_calendar

from patterns import config as C
from patterns import screen as PS
from patterns import delivery as PD

UNIVERSE_CSV = "jpx_listed_companies.csv"
TARGET_MARKETS = ("プライム", "スタンダード")


def _load_universe(path=UNIVERSE_CSV, max_stocks=None):
    out = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                code = (r.get("code") or "").strip()
                market = (r.get("market") or "").strip()
                if not code or not any(t in market for t in TARGET_MARKETS):
                    continue
                out.append({"code": code, "name": (r.get("name") or "").strip(),
                            "sector": (r.get("sector") or "").strip()})
    except FileNotFoundError:
        print(f"[上昇パターン] ユニバースCSVが見つかりません: {path}")
    if max_stocks:
        out = out[:max_stocks]
    return out


def _fetch_ohlcv(code):
    """yfinance日足を patterns 用の数値列 dict に変換。取得不可は None。"""
    ticker = code if "." in code else f"{code}.T"
    df = data_fetcher._download_history(ticker, period="1y")
    if df is None or "Close" not in df or len(df) < C.MIN_ROWS:
        return None
    def col(name):
        return [float(x) for x in df[name].fillna(method="ffill").tolist()] \
            if name in df else []
    close = col("Close")
    if not close:
        return None
    return {"close": close, "high": col("High") or close,
            "low": col("Low") or close, "volume": col("Volume") or [0] * len(close)}


def _save_scores_csv(all_rows, today_str):
    path = os.path.join("data", f"pattern_scores_{today_str}.csv")
    fields = ["code", "name", "sector", "price", "avg_turnover", "surge_5",
              "above_25ma", "vol_ratio", "pattern_labels", "score",
              "in_universe", "reasons"]
    try:
        os.makedirs("data", exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in all_rows:
                w.writerow({k: r.get(k, "") for k in fields})
        print(f"[上昇パターン] 全銘柄検出CSVを保存: {path}（{len(all_rows)}行）")
    except Exception as e:
        print(f"[上昇パターン] CSV保存に失敗: {e}")


def _log_calc(ranked):
    print(f"[計算] 検出上位 {len(ranked)} 銘柄:")
    for i, r in enumerate(ranked, 1):
        print(f"  {i}. {r['code']} {r['name']} score={r['score']} "
              f"[{r['pattern_labels']}] 出来高比="
              f"{round(r['vol_ratio'],2) if r.get('vol_ratio') else '—'} "
              f"25日乖離={round(r['above_25ma'],1) if r.get('above_25ma') is not None else '—'}%")


def main(dry_run=False, max_stocks=None):
    dry_run = dry_run or (os.environ.get("DRY_RUN") or "").strip() in ("1", "true", "yes")
    if max_stocks is None:
        raw = (os.environ.get("MAX_STOCKS") or "").strip()
        max_stocks = int(raw) if raw.isdigit() else None
    today = market_calendar.today_jst()
    today_str = today.strftime("%Y-%m-%d")
    print("=" * 60)
    print(f"上昇チャートパターン日次バッチ  {today_str}"
          + ("  [DRY-RUN]" if dry_run else ""))
    print("=" * 60)

    is_open, reason = market_calendar.is_market_open(today)
    if not is_open:
        print(f"休場日({reason})のため配信をスキップします。")
        return 0

    universe = _load_universe(max_stocks=max_stocks)
    if not universe:
        print("[中断] ユニバースが空です。")
        return 1
    print(f"ユニバース: {len(universe)} 銘柄。日足を取得します...")

    stocks, total = [], len(universe)
    for i, u in enumerate(universe, 1):
        o = _fetch_ohlcv(u["code"])
        if o:
            stocks.append({**u, "ohlcv": o})
        if i % 100 == 0 or i == total:
            print(f"  取得 {i}/{total}（有効 {len(stocks)}）")

    if not stocks:
        line_sender.send_admin_alert(
            f"[上昇パターン] {today_str} 株価取得に失敗したため配信を中止しました。",
            dry_run=dry_run)
        return 0

    ranked, all_rows = PS.score_all(stocks)
    if not dry_run:
        _save_scores_csv(all_rows, today_str)
    _log_calc(ranked)

    basis_label = f"データ基準日：{today.month}月{today.day}日 大引け時点"
    flex_messages, summary = PD.build_delivery(ranked, basis_label=basis_label)
    print("\n--- 配信テキスト（補足・必須免責つき）---")
    print(summary)
    try:
        line_sender.send_report(summary, flex_messages=flex_messages,
                                fallback_text=summary, dry_run=dry_run)
    except Exception as e:
        print(f"[上昇パターン] LINE送信で予期せぬエラー（処理継続）: {e}")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="上昇チャートパターン日次バッチ")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-stocks", type=int, default=None)
    args = ap.parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run, max_stocks=args.max_stocks))
    except Exception:
        import traceback
        print("[上昇パターン] 予期せぬ例外:")
        traceback.print_exc()
        sys.exit(1)
