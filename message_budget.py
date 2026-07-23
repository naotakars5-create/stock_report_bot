"""
message_budget.py

LINEメッセージ通数の使用量記録と残枠監視（改善2）。

背景:
  LINE公式アカウントの課金は「送信リクエスト1回×受信者数＝通数」で、プランごとの
  月間上限（ライトプラン5,000通）を超えると配信できなくなる。読者が増えたときに
  「枠切れで配信停止」に気づかない事故を防ぐため、送信のたびに通数を記録し、
  月間使用量が閾値（80%／95%）を跨いだ日に管理者へ警告する。

設計:
  - data/message_usage.csv に1送信=1行で追記（date, kind, recipients）。
    生データを消さない・月間集計は毎回この生データから再計算。
  - 記録は line_sender の送信成功時に呼ばれる（実際に送れた通数のみ数える。
    dry-run は送信しないので記録されない。TEST_MODE は実際に送るので記録される）。
  - 警告判定は「今日の送信で閾値を跨いだか」で決めるため、状態管理なしで
    1閾値につき1回だけ通知される（同日再実行では再通知され得るが許容）。
  - 月間上限は環境変数 LINE_MONTHLY_QUOTA（既定 5000=ライトプラン）。
    無料プランなら 200 を設定する。

失敗しても配信本体を止めない（記録・判定の例外はすべて握りつぶして警告表示）。
"""

import csv
import os

import market_calendar


USAGE_PATH = os.path.join("data", "message_usage.csv")
USAGE_FIELDS = ["date", "kind", "recipients"]

DEFAULT_QUOTA = 5000          # ライトプランの月間通数
WARN_THRESHOLDS = (0.8, 0.95)  # 使用率がこれを跨いだ日に管理者へ警告


def quota():
    """月間上限（環境変数 LINE_MONTHLY_QUOTA・既定はライトプランの5000）。"""
    raw = (os.environ.get("LINE_MONTHLY_QUOTA") or "").strip()
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_QUOTA
    except ValueError:
        return DEFAULT_QUOTA


def _read_rows(path=USAGE_PATH):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[通数] 使用量の読み込みに失敗しました: {e}")
        return []


def record(kind, recipients, date_str=None, path=USAGE_PATH):
    """
    送信1リクエスト分の通数（=受信者数）を追記する。失敗しても止めない。

    line_sender の送信成功時に呼ばれる。recipients<=0 は記録しない。
    """
    try:
        if not recipients or recipients <= 0:
            return False
        date_str = date_str or market_calendar.today_jst().strftime("%Y-%m-%d")
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        exists = os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=USAGE_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerow({"date": date_str, "kind": kind,
                             "recipients": int(recipients)})
        return True
    except Exception as e:
        print(f"[通数] 使用量の記録に失敗しました（処理は継続）: {e}")
        return False


def month_usage(month=None, path=USAGE_PATH):
    """
    指定月（"YYYY-MM"・省略時は今月）の使用通数と本日分を返す。

    戻り値: {"month", "total": 月間通数, "today": 本日通数}
    """
    today = market_calendar.today_jst()
    month = month or today.strftime("%Y-%m")
    today_str = today.strftime("%Y-%m-%d")
    total = today_total = 0
    for r in _read_rows(path):
        d = (r.get("date") or "").strip()
        if not d.startswith(month):
            continue
        try:
            n = int(r.get("recipients") or 0)
        except ValueError:
            continue
        total += n
        if d == today_str:
            today_total += n
    return {"month": month, "total": total, "today": today_total}


def threshold_alert(path=USAGE_PATH):
    """
    今日の送信で警告閾値（80%／95%）を跨いだ場合に、管理者向け警告文を返す。

    跨いでいなければ None。判定は「本日分を除いた使用量 < 閾値 <= 現在の使用量」
    なので、状態ファイルなしで1閾値につき跨いだ日に1回だけ発火する。
    """
    try:
        q = quota()
        u = month_usage(path=path)
        before = u["total"] - u["today"]
        crossed = [th for th in WARN_THRESHOLDS
                   if before < q * th <= u["total"]]
        if not crossed:
            return None
        th = max(crossed)
        remaining = max(0, q - u["total"])
        return (f"[Daily Stock Report] LINE通数が月間上限の{int(th * 100)}%を超えました。\n"
                f"今月({u['month']})の使用: {u['total']:,} / {q:,}通（残り {remaining:,}通）\n"
                f"残枠が尽きると配信が届かなくなります。プランの見直し、または"
                f"配信構成（1リクエストへの束ね・即時通知の対象範囲）をご確認ください。")
    except Exception as e:
        print(f"[通数] 残枠判定に失敗しました（処理は継続）: {e}")
        return None


def usage_line(path=USAGE_PATH):
    """ログ表示用の1行サマリー（毎朝の実行ログに出す）。"""
    try:
        q = quota()
        u = month_usage(path=path)
        pct = u["total"] / q * 100 if q else 0
        return (f"[通数] 今月({u['month']}) {u['total']:,}/{q:,}通"
                f"（{pct:.1f}%・本日 {u['today']:,}通）")
    except Exception:
        return "[通数] 使用量を取得できませんでした"
