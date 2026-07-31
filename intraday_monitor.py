"""
intraday_monitor.py

場中モニタ（機能拡張2×3）: 監視中の推奨銘柄の現在値を軽量に取得し、
イベント（上値メド到達／参考下値ライン割れ）を検知したら、その銘柄を
「気になる/保有」登録している読者にのみ即時通知する。

起動: 外部cron（cron-job.org 等）から repository_dispatch(intraday-monitor) で
      場中（9:00〜15:30 JST）に30分毎を想定。GitHub schedule は遅延が大きいため
      使わない（朝配信と同じ方針）。

設計:
  - 監視対象は open 推奨のみ（最大 5銘柄/日 × 5営業日 ≒ 25銘柄）→ 取得は軽量。
  - 即時通知の宛先は **登録読者のみ**（メッセージ予算の制約。全体には翌朝の
    配信で「追跡」セクションとして必ず報告される＝情報格差は1日以内）。
  - 検知したイベントは登録読者の有無に関わらず followup_events.csv に記録し、
    翌朝の配信に織り込む（notified 列で二重通知を防ぐ）。
  - 場外・休場日は何もせず正常終了する（FORCE_RUN=true で上書き可・検証用）。

実行: python intraday_monitor.py [--dry-run]
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
import followup
import line_sender
import market_calendar
import recommendation_tracker as rt
import subscriber_store


MARKET_OPEN_HHMM = (9, 0)
MARKET_CLOSE_HHMM = (15, 30)


def _in_market_hours(now=None):
    now = now or market_calendar.now_jst()
    d = now.date()
    if not market_calendar.is_trading_day(d):
        return False, "not a trading day"
    open_dt = now.replace(hour=MARKET_OPEN_HHMM[0], minute=MARKET_OPEN_HHMM[1],
                          second=0, microsecond=0)
    close_dt = now.replace(hour=MARKET_CLOSE_HHMM[0], minute=MARKET_CLOSE_HHMM[1],
                           second=0, microsecond=0)
    if not (open_dt <= now <= close_dt):
        return False, "outside market hours"
    return True, ""


def _fetch_current_prices(codes):
    """監視銘柄の現在値（当日足の最新Close）を軽量取得する。取得不可はスキップ。"""
    prices = {}
    for code in codes:
        ticker = code if "." in str(code) else f"{code}.T"
        df = data_fetcher._download_history(ticker, period="1d")
        try:
            close = df["Close"].dropna()
            if len(close):
                prices[code] = float(close.iloc[-1])
        except Exception:
            pass
    return prices


def main(dry_run=False):
    force = (os.environ.get("FORCE_RUN") or "").strip().lower() in ("1", "true", "yes", "on")
    ok, reason = _in_market_hours()
    if not ok and not force:
        print(f"[場中モニタ] スキップ: {reason}")
        return 0

    open_recs = rt.open_recommendations()
    if not open_recs:
        print("[場中モニタ] 監視中の推奨はありません。")
        return 0

    codes = sorted({(r.get("code") or "").strip() for r in open_recs})
    print(f"[場中モニタ] 監視 {len(codes)} 銘柄の現在値を取得します...")
    prices = _fetch_current_prices(codes)
    if not prices:
        print("[場中モニタ] 現在値を取得できませんでした（何もせず終了）。")
        return 0

    today_str = market_calendar.today_jst().strftime("%Y-%m-%d")
    events = followup.detect_events(open_recs, prices, today_str=today_str)
    if not events:
        print("[場中モニタ] 新規イベントはありません。")
        return 0
    print(f"[場中モニタ] {len(events)} 件のイベントを検知:")
    for e in events:
        print("  - " + followup.event_line(e))

    # 即時通知は「そのイベント銘柄を登録している読者」にのみ送る（予算制約）。
    settings = subscriber_store.load_settings()
    event_codes = sorted({e["code"] for e in events})
    recipients = subscriber_store.watchers_for_codes(event_codes, settings)

    notified = ""
    if recipients:
        # 読者ごとに関係する銘柄のイベントだけを届ける（自分に関係ない通知を送らない）
        sent_any = False
        for uid in recipients:
            my_codes = {c for c in event_codes
                        if uid in subscriber_store.watchers_for_codes([c], settings)}
            my_events = [e for e in events if e["code"] in my_codes]
            if not my_events:
                continue
            text = followup.build_instant_text(my_events)
            status = line_sender.send_multicast(
                [{"type": "text", "text": text}], [uid],
                dry_run=dry_run, label=f"即時通知({uid[:6]}…)")
            sent_any = sent_any or status in ("sent", "dry-run")
        notified = "instant" if sent_any else ""
        if not sent_any:
            print("[場中モニタ] 即時通知の送信対象はいましたが送信できませんでした"
                  "（イベントは記録し、翌朝の配信で報告されます）。")
    else:
        print("[場中モニタ] このイベント銘柄の登録読者はいません"
              "（翌朝の配信で全体に報告されます）。")

    # イベントを記録（dry-run では書き込まない）
    if dry_run:
        print("[場中モニタ] dry-run のためイベントは保存しません。")
    else:
        followup.record_events(events, notified=notified)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="場中モニタ（掲載銘柄の追跡）")
    parser.add_argument("--dry-run", action="store_true",
                        help="通知・保存を行わず判定のみ表示")
    args = parser.parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run))
    except Exception:
        import traceback
        print("[場中モニタ] 予期せぬ例外:")
        traceback.print_exc()
        sys.exit(1)
