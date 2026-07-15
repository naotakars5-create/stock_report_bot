"""
close_report.py

【機能1-b】引け後の答え合わせジョブ（毎営業日 16:10 JST）。

その日の朝に抽出した上位5銘柄が、当日どう動いたかを機械的に集計し、
X(旧Twitter)へ淡々と投稿する（勝った日も負けた日も同じトーン）。

処理:
  1. 休場日ガード（土日・祝日・年末年始はスキップして正常終了）
  2. 当日の朝の抽出結果（data/report_history.csv の当日分）を読み込む
  3. 各銘柄の当日終値を取得し、朝の基準価格（前営業日終値）からの騰落率を集計
     → これは「今朝の抽出銘柄が本日どう動いたか」＝当日の値動き
  4. 日経平均の当日騰落率と比較（日経比）
  5. X へ投稿（銘柄名は出さず、業種＋規模のぼかし表記）

データ鮮度ガード（重要）:
  16:10 時点で当日の終値がまだ取得できない（yfinance の反映遅延等）場合、
  前日データで「答え合わせ」をすると誤った成績を公表してしまう。
  そのため **取得データの最新バーが当日でなければ投稿せず中止** し、管理者へ通知する。

使い方:
  python close_report.py [--dry-run]
"""

import argparse
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
import report_history
from promo import promo_posts, text_builder, x_client


def _today_close_map(entries):
    """当日終値 {code: close} と、取得データの最新バー日付を返す。"""
    stocks = [{"code": (e.get("code") or "").strip(),
               "name": e.get("name", "")}
              for e in entries if (e.get("code") or "").strip()]
    histories = data_fetcher.fetch_histories(
        stocks, period="5d", min_rows=1, stage_label="引け後の答え合わせ")
    prices, basis = {}, None
    for item in histories:
        try:
            close = item["history"]["Close"].dropna()
            if not len(close):
                continue
            prices[item["code"]] = float(close.iloc[-1])
            d = close.index[-1].date()
            if basis is None or d > basis:
                basis = d
        except Exception:
            continue
    return prices, basis


def _nikkei_today_pct():
    """日経平均の当日騰落率(%)。取得できなければ None。"""
    df = data_fetcher.get_nikkei_history()
    try:
        close = df["Close"].dropna()
        if len(close) < 2:
            return None
        return (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    except Exception:
        return None


def main(dry_run=False):
    today = market_calendar.today_jst()
    is_open, reason = market_calendar.is_market_open(today)
    force_run = (os.environ.get("FORCE_RUN") or "").strip().lower() in ("1", "true", "yes", "on")
    if not is_open and not force_run:
        print(f"Market closed: {today} ({reason})")
        print("休場日のため答え合わせをスキップして正常終了します。")
        return 0

    today_str = today.strftime("%Y-%m-%d")
    if promo_posts.already_posted("close_result", today_str):
        print("[X] 本日の答え合わせは投稿済みのためスキップします。")
        return 0

    # 当日朝の抽出結果を読み込む（before_date を指定せず、当日分を探す）
    runs = report_history.load_runs()
    today_run = next((r for r in runs if r["run_date"] == today_str), None)
    if not today_run:
        print(f"[情報] 本日({today_str})の抽出結果が履歴にないため、答え合わせをスキップします。")
        return 0

    entries = today_run["entries"][:5]
    prices, basis = _today_close_map(entries)
    if not prices:
        print("[情報] 当日終値が取得できなかったため、答え合わせをスキップします。")
        return 0

    # 鮮度ガード: 最新バーが当日でなければ、前日データでの誤った答え合わせを防ぐため中止
    if basis != today:
        msg = (f"[Close Report] 当日終値が未反映のため答え合わせを中止しました。\n"
               f"データ最新日: {basis} / 本日(JST): {today}\n"
               f"→ 誤った成績の公表を防ぐためスキップしました。")
        print("[中止] " + msg.replace("\n", " / "))
        line_sender.send_admin_alert(msg, dry_run=dry_run)
        return 0

    # 集計（朝の基準価格＝前営業日終値 → 当日終値 の騰落率）
    results = []
    for e in entries:
        code = (e.get("code") or "").strip()
        cur = prices.get(code)
        try:
            base = float(e.get("price"))
        except (TypeError, ValueError):
            continue
        if cur is None or base <= 0:
            continue
        results.append({
            "blur": text_builder.blur(e.get("sector"), _size_of(e)),
            "return": (cur - base) / base * 100,
        })
    if not results:
        print("[情報] 集計できる銘柄がないため、答え合わせをスキップします。")
        return 0

    avg = sum(r["return"] for r in results) / len(results)
    wins = sum(1 for r in results if r["return"] > 0)
    losses = sum(1 for r in results if r["return"] < 0)
    nk = _nikkei_today_pct()
    result = {
        "wins": wins,
        "losses": losses,
        "avg_return": avg,
        "vs_nikkei": (avg - nk) if nk is not None else None,
        "best": max(results, key=lambda r: r["return"]),
        "worst": min(results, key=lambda r: r["return"]),
    }
    summary = f"[集計] {wins}勝{losses}敗 / 平均 {avg:+.2f}%"
    if result["vs_nikkei"] is not None:
        summary += f" / 日経比 {result['vs_nikkei']:+.2f}pt"
    print(summary)

    text = text_builder.build_close_result(today, result)
    x_client.post_tweet(text, "close_result", dry_run=dry_run, post_date=today_str)
    return 0


def _size_of(entry):
    """履歴に保存された規模区分（大型/中型/小型）。古い行では空になり業種のみのぼかしになる。"""
    return (entry.get("size_category") or "").strip()


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="引け後の答え合わせ（X投稿）")
    p.add_argument("--dry-run", action="store_true",
                   help="Xに投稿せず、投稿予定文の表示のみ行う")
    return p.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    try:
        code = main(dry_run=_args.dry_run)
    except KeyboardInterrupt:
        print("\n[中断] ユーザーによって処理が中断されました。")
        code = 130
    except Exception:
        import traceback
        print("\n[致命的エラー] 予期せぬ例外が発生しました:")
        traceback.print_exc()
        code = 1
    sys.exit(code)
