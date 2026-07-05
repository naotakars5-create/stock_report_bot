"""
market_calendar.py

東証（日本株）の営業日・休場日を判定する共通ユーティリティ。

配信ジョブは「休場日には配信しない」「取得データの鮮度を確認する」ために、
本モジュールの `is_market_open()` / `expected_data_date()` を利用する。

休場日の定義:
  - 土曜・日曜
  - 国民の祝日（`jpholiday` を使用。未導入・取得失敗時は祝日除外を行わず警告）
  - 年末年始（12/31〜1/3）※東証の大納会・大発会に合わせた固定休場

時刻はすべて日本時間(JST)で扱う。GitHub Actions は UTC で動くため、
「今日」の判定は必ず `today_jst()` を使うこと（UTC の日付とはずれる）。
"""

import os
from datetime import date, datetime, timedelta, timezone

# jpholiday は best-effort。未導入でも土日・年末年始の判定は動く。
try:
    import jpholiday
except Exception:  # pragma: no cover
    jpholiday = None

JST = timezone(timedelta(hours=9))

# テスト用の日付上書きフック（本番では未設定）。受け入れテストで
# 「土曜日付でジョブを実行」等を再現するために使う。SIMULATE_DATE=YYYY-MM-DD。
_SIMULATE_ENV = "SIMULATE_DATE"

# 東証の大引けは 15:00（2024/11以降は15:30）。日中データの確定に余裕を持たせ、
# 「本日のデータが基準日として確定する」時刻を 15:30 JST とみなす。
MARKET_CLOSE = (15, 30)


def now_jst():
    """
    現在時刻（JST, tz-aware）。

    テスト時に環境変数 SIMULATE_DATE=YYYY-MM-DD が設定されていれば、その日付の
    現在時刻（JST）を返す（受け入れテストで休場日などを再現するため）。
    """
    sim = os.environ.get(_SIMULATE_ENV)
    real = datetime.now(JST)
    if sim:
        try:
            d = datetime.strptime(sim.strip(), "%Y-%m-%d").date()
            # 本番は朝8:15の寄り付き前に走るため、テストも 08:15 JST を再現する
            # （実時刻に依存せず、決定的に「寄り付き前・前営業日基準」を検証できる）。
            return real.replace(year=d.year, month=d.month, day=d.day,
                                hour=8, minute=15, second=0, microsecond=0)
        except ValueError:
            pass
    return real


def today_jst():
    """今日の日付（JST）。UTC とずれるため配信ジョブは必ずこれを使う。"""
    return now_jst().date()


def _is_year_end_holiday(d):
    """年末年始（12/31〜1/3）の固定休場か。"""
    return (d.month == 12 and d.day == 31) or (d.month == 1 and d.day in (1, 2, 3))


def is_market_open(d=None):
    """
    東証がその日に開いているか（営業日か）を判定する。

    戻り値: (open: bool, reason: str)
      open=False のとき reason に理由（"weekend" / "holiday: 元日" /
      "year-end holiday" / など）を入れる。open=True のとき reason は ""。
    """
    d = d or today_jst()
    if d.weekday() >= 5:  # 5=土, 6=日
        return False, "weekend"
    if _is_year_end_holiday(d):
        return False, "year-end holiday"
    if jpholiday is not None:
        try:
            name = jpholiday.is_holiday_name(d)
            if name:
                return False, f"holiday: {name}"
        except Exception:
            pass  # 判定失敗時は祝日除外をスキップ（土日・年末年始のみで判定）
    return True, ""


def is_trading_day(d=None):
    """その日が営業日か（bool のみ）。"""
    return is_market_open(d)[0]


def previous_trading_day(d=None):
    """d より前で最も近い営業日を返す（d 自身は含めない）。"""
    d = d or today_jst()
    cur = d - timedelta(days=1)
    for _ in range(30):  # 連休を考慮しても十分な上限
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return cur


def next_trading_day(d=None):
    """d より後で最も近い営業日を返す（d 自身は含めない）。"""
    d = d or today_jst()
    cur = d + timedelta(days=1)
    for _ in range(30):
        if is_trading_day(cur):
            return cur
        cur += timedelta(days=1)
    return cur


def expected_data_date(now=None):
    """
    「いま時点で確定している最新の立会い日（大引け済みの営業日）」を返す。

    - 朝の寄り付き前（8:15配信など）は本日場が未了なので **前営業日** を返す。
    - 大引け後（15:30 JST 以降）に本日が営業日なら **本日** を返す。
    - 休場日に実行した場合（手動実行など）は直近の過去営業日を返す。

    取得データの鮮度チェックで「実データの基準日がこれと一致するか」に使う。
    """
    now = now or now_jst()
    d = now.date()
    close_dt = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1],
                           second=0, microsecond=0)
    if is_trading_day(d) and now >= close_dt:
        return d
    return previous_trading_day(d)
