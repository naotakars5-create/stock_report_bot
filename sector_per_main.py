"""
sector_per_main.py

業種別PERランキングの日次バッチ（大引け後17:30・当日終値ベース）。

処理の流れ:
  0. 休場日ガード（土日祝・年末年始はスキップ）
  1. ユニバース構築（jpx_listed_companies.csv・対象市場のみ）
  2. J-Quants から財務を差分取得し fundamentals_cache を更新
     → 取得失敗時は「前日データで配信しない」。配信をスキップし管理者へ通知する
  3. yfinance から当日終値・売買代金・上場年数・過去終値を取得（既存データ層を流用）
  4. 銘柄レコード合成 → sector_per.ranking で業種別ランキング算出
  5. 全銘柄スコアCSV（検証用）＋計算過程ログを出力
  6. LINE Flex カルーセル＋補足テキスト（必須免責つき）を配信

これは投資助言ではなく、公開データに基づく機械的な相対評価です。

環境変数:
  JQUANTS_MAILADDRESS / JQUANTS_PASSWORD（または JQUANTS_REFRESH_TOKEN）
  LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID（既存の配信層を流用）
  実行制御: DRY_RUN=1 で送信・保存せず表示のみ。
"""

import csv
import os
import sys
from datetime import timedelta

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import data_fetcher
import line_sender
import market_calendar

from sector_per import config as C
from sector_per import jquants_client as J
from sector_per import pipeline as P
from sector_per import ranking as R
from sector_per import delivery as D


UNIVERSE_CSV = "jpx_listed_companies.csv"
SCORES_CSV_DIR = "data"
STATEMENTS_LOOKBACK_DAYS = 10   # 財務差分取得で遡る開示日数（休配明けの取りこぼし対策）


def _load_universe(path=UNIVERSE_CSV):
    """対象市場（プライム・スタンダード）の普通株ユニバースを読み込む。"""
    out = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                market = (r.get("market") or "").strip()
                sector = (r.get("sector") or "").strip()
                code = (r.get("code") or "").strip()
                if not code or sector in ("", "-"):
                    continue
                if not any(t in market for t in C.TARGET_MARKETS):
                    continue
                out.append({"code": code, "name": (r.get("name") or "").strip(),
                            "market": market, "sector33": sector})
    except FileNotFoundError:
        print(f"[業種PER] ユニバースCSVが見つかりません: {path}")
    return out


def _update_fundamentals(today, dry_run=False):
    """
    J-Quants から直近開示日ぶんの財務を取得し、財務キャッシュを更新して返す。

    取得に失敗（認証・通信）した場合は None を返す（＝配信中止のシグナル）。
    """
    try:
        id_token = J.get_id_token()
    except J.JQuantsError as e:
        print(f"[業種PER][中止] J-Quants 認証に失敗: {e}")
        return None
    cache = P.load_cache()
    got_any = False
    for i in range(STATEMENTS_LOOKBACK_DAYS):
        d = (today - timedelta(days=i))
        if not market_calendar.is_trading_day(d):
            continue
        ds = d.strftime("%Y-%m-%d")
        try:
            stmts = J.fetch_statements_by_date(id_token, ds)
        except J.JQuantsError as e:
            print(f"[業種PER][中止] statements 取得失敗（{ds}）: {e}")
            return None
        if not stmts:
            continue
        by_code = {}
        for s in stmts:
            p = J.parse_statement(s)
            by_code.setdefault(p["code"], []).append(s)
        cache = P.ingest_statements(cache, by_code, today.strftime("%Y-%m-%d"))
        got_any = True
    if not dry_run:
        P.save_cache(cache)
    print(f"[業種PER] 財務キャッシュ更新: {len(cache)} 銘柄（新規開示{'あり' if got_any else 'なし'}）")
    return cache


def _price_info(codes):
    """yfinance から当日終値・20日平均売買代金・上場年数・過去終値系列を取得。"""
    info = {}
    total = len(codes)
    for i, code in enumerate(codes, 1):
        ticker = code if "." in code else f"{code}.T"
        df = data_fetcher._download_history(ticker, period="3y")
        if df is None or "Close" not in df or len(df) < C.TURNOVER_WINDOW:
            continue
        close = df["Close"].dropna()
        if close.empty:
            continue
        price = float(close.iloc[-1])
        vol = df["Volume"].dropna() if "Volume" in df else None
        turnover = None
        if vol is not None and len(vol) >= C.TURNOVER_WINDOW:
            turnover = float((close * vol).rolling(C.TURNOVER_WINDOW).mean().iloc[-1])
        listing_years = (close.index[-1] - close.index[0]).days / 365.25
        close_pairs = [(ts.date().isoformat(), float(v))
                       for ts, v in zip(close.index, close.values)]
        val = data_fetcher.get_valuation(ticker)
        info[code] = {"close": price, "avg_turnover": turnover,
                      "listing_years": listing_years, "close_pairs": close_pairs,
                      "market_cap": (val or {}).get("market_cap")}
        if i % 50 == 0 or i == total:
            print(f"  価格取得 {i}/{total}")
    return info


def _save_scores_csv(all_rows, today_str):
    """全銘柄スコア一覧をCSVに出力（依頼4・ロジック検証用）。"""
    path = os.path.join(SCORES_CSV_DIR, f"sector_per_scores_{today_str}.csv")
    fields = ["code", "name", "sector33", "market", "close", "forecast_eps",
              "forecast_per", "sector_median_per", "sector_relative",
              "self_percentile", "self_cheapness", "roe", "equity_ratio",
              "total_score", "in_universe", "passes_valuetrap", "reasons",
              "fundamentals_asof"]
    try:
        os.makedirs(SCORES_CSV_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in all_rows:
                w.writerow({k: r.get(k, "") for k in fields})
        print(f"[業種PER] 全銘柄スコアCSVを保存: {path}（{len(all_rows)}行）")
    except Exception as e:
        print(f"[業種PER] スコアCSV保存に失敗: {e}")


def _log_calc(rankings):
    """業種中央値・スコアの計算過程をログ出力（後から再現できるように）。"""
    for sector in sorted(rankings):
        d = rankings[sector]
        head = (f"[計算] {sector}: 中央値PER="
                f"{'—' if d['median_per'] is None else round(d['median_per'],2)} "
                f"母集団={d['universe_n']} 候補={len(d['candidates'])}")
        print(head)
        for r in d["candidates"]:
            print(f"    {r['code']} {r['name']}: PER="
                  f"{round(r['forecast_per'],2) if r['forecast_per'] else '—'} "
                  f"業種内割安={round(r['sector_relative'],3) if r['sector_relative'] is not None else '—'} "
                  f"自己過去比={round(r['self_cheapness'],3) if r['self_cheapness'] is not None else '—'} "
                  f"総合={round(r['total_score'],3)}")


def main(dry_run=False):
    dry_run = dry_run or (os.environ.get("DRY_RUN") or "").strip() in ("1", "true", "yes")
    today = market_calendar.today_jst()
    today_str = today.strftime("%Y-%m-%d")
    print("=" * 60)
    print(f"業種別PERランキング日次バッチ  {today_str}"
          + ("  [DRY-RUN]" if dry_run else ""))
    print("=" * 60)

    # 0. 休場日ガード
    is_open, reason = market_calendar.is_market_open(today)
    if not is_open:
        print(f"休場日({reason})のため配信をスキップします。")
        return 0

    # 1. ユニバース
    universe = _load_universe()
    if not universe:
        print("[中断] ユニバースが空です。")
        return 1
    print(f"ユニバース（対象市場）: {len(universe)} 銘柄")

    # 2. 財務（J-Quants）。失敗時は前日データで配信せず中止＋管理者通知。
    cache = _update_fundamentals(today, dry_run=dry_run)
    if cache is None:
        line_sender.send_admin_alert(
            f"[業種別PERランキング] {today_str} 財務データ(J-Quants)の取得に失敗したため、"
            "本日の配信を中止しました（前日データでは配信しません）。", dry_run=dry_run)
        return 0

    # 3. 価格（yfinance）。財務がある銘柄だけに絞って取得（負荷軽減）。
    codes = [u["code"] for u in universe if (cache.get(u["code"]) or {}).get("forecast_eps")]
    print(f"財務のある銘柄の価格を取得: {len(codes)} 銘柄")
    price_info = _price_info(codes)
    if not price_info:
        line_sender.send_admin_alert(
            f"[業種別PERランキング] {today_str} 株価の取得に失敗したため配信を中止しました。",
            dry_run=dry_run)
        return 0

    # 4. 合成 → ランキング
    records = P.assemble_records(universe, cache, price_info)
    rankings, all_rows = R.score_all(records)

    # 5. 検証用CSV＋計算過程ログ
    if not dry_run:
        _save_scores_csv(all_rows, today_str)
    _log_calc(rankings)

    # 6. 配信（必須免責つき）。財務の基準日（最も新しい asof）を明示。
    asofs = [r.get("fundamentals_asof") for r in records if r.get("fundamentals_asof")]
    asof_label = max(asofs) if asofs else None
    basis_label = f"データ基準日：{today.month}月{today.day}日 大引け時点"
    flex_messages, summary = D.build_delivery(rankings, basis_label=basis_label,
                                              asof_label=asof_label)
    print("\n--- 配信テキスト（補足・必須免責つき）---")
    print(summary)
    try:
        line_sender.send_report(summary, flex_messages=flex_messages,
                                fallback_text=summary, dry_run=dry_run)
    except Exception as e:
        print(f"[業種PER] LINE送信で予期せぬエラー（処理継続）: {e}")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="業種別PERランキング日次バッチ")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run))
    except Exception:
        import traceback
        print("[業種PER] 予期せぬ例外:")
        traceback.print_exc()
        sys.exit(1)
