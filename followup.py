"""
followup.py

推奨銘柄のフォローアップ（機能拡張2）。

過去に推奨した銘柄（recommendation_tracker の open 推奨）を監視し、
以下のイベントを検知して配信へ織り込む:

  - upper_hit    : 上値メド（推奨時に機械算出した参考値）に到達
  - lower_break  : 参考下値ライン（直近安値・ATRベースの参考値）を下回った
  - expired      : 目安保有期間（5立会い日）の満了（クローズ報告）

表現方針（コンプライアンス・promo/ng_words 準拠）:
  - フォローアップは **事実の報告に徹し**、売買を促す表現は使わない。
    「エントリー/損切り/利確/撤退」等の語は使わず、
    「上値メド到達」「参考下値ラインを下回る」「目安保有期間の満了」で統一する。
  - 各文面に「機械的に算出した参考値であり投資助言ではない」旨の免責を添える。

二重通知ガード:
  followup_events.csv に (run_date, code, event_type) を記録し、同じイベントは
  一度しか通知しない。場中の即時通知と翌朝の織り込みは notified 列で区別する
  （場中に通知済みでも、翌朝の全体配信には「昨日の出来事」として1回だけ載せる）。
"""

import csv
import os

import market_calendar
import recommendation_tracker as rt


EVENTS_PATH = os.path.join("data", "followup_events.csv")
EVENT_FIELDS = ["event_date", "run_date", "code", "name", "event_type",
                "price", "ref_value", "return_pct", "notified"]

EVENT_UPPER = "upper_hit"
EVENT_LOWER = "lower_break"
EVENT_EXPIRED = "expired"

# notified 列の値: instant=場中に即時通知済み / morning=朝配信に掲載済み / both
_N_INSTANT = "instant"
_N_MORNING = "morning"
_N_BOTH = "both"

FOLLOWUP_DISCLAIMER = "※機械的に算出した参考値に基づく事実の報告であり、投資助言ではありません。"


def _read_events(path=EVENTS_PATH):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] {path} の読み込みに失敗しました: {e}")
        return []


def _write_events(rows, path=EVENTS_PATH):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=EVENT_FIELDS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in EVENT_FIELDS})
        return True
    except Exception as e:
        print(f"[警告] {path} の書き込みに失敗しました: {e}")
        return False


def _f(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_date(d):
    """"2026-07-16" → "7/16"（文面用の短い日付）。"""
    try:
        _y, m, day = d.split("-")
        return f"{int(m)}/{int(day)}"
    except Exception:
        return d


# ====== イベント判定（純粋関数・テスト可能） ======
def detect_events(open_recs, current_prices, today_str=None, existing_events=None):
    """
    監視中の推奨と現在価格から、新規イベントを検知する。

    引数:
        open_recs: recommendation_tracker.open_recommendations() の行
        current_prices: {code: 現在値}
        existing_events: 既存イベント行（二重検知の除外に使用）
    戻り値: 新規イベントの行リスト（followup_events.csv 形式・notified は空）
    """
    today_str = today_str or market_calendar.today_jst().strftime("%Y-%m-%d")
    seen = {((e.get("run_date") or "").strip(), (e.get("code") or "").strip(),
             (e.get("event_type") or "").strip())
            for e in (existing_events if existing_events is not None
                      else _read_events())}
    out = []
    for r in open_recs:
        rd = (r.get("run_date") or "").strip()
        code = (r.get("code") or "").strip()
        name = (r.get("name") or "").strip()
        entry = _f(r.get("entry_price"))
        price = current_prices.get(code)
        if not rd or not code or price is None or not entry or entry <= 0:
            continue
        ret = (price - entry) / entry * 100

        upper = _f(r.get("ref_upper"))
        if upper and price >= upper and (rd, code, EVENT_UPPER) not in seen:
            out.append({"event_date": today_str, "run_date": rd, "code": code,
                        "name": name, "event_type": EVENT_UPPER,
                        "price": f"{price:.2f}", "ref_value": f"{upper:.2f}",
                        "return_pct": f"{ret:.2f}", "notified": ""})
            seen.add((rd, code, EVENT_UPPER))

        lower = _f(r.get("ref_lower"))
        if lower and price <= lower and (rd, code, EVENT_LOWER) not in seen:
            out.append({"event_date": today_str, "run_date": rd, "code": code,
                        "name": name, "event_type": EVENT_LOWER,
                        "price": f"{price:.2f}", "ref_value": f"{lower:.2f}",
                        "return_pct": f"{ret:.2f}", "notified": ""})
            seen.add((rd, code, EVENT_LOWER))
    return out


def record_events(new_events, notified=None, path=EVENTS_PATH):
    """新規イベントを保存する。notified を渡すと通知済みマークを付けて保存。"""
    if not new_events:
        return []
    rows = _read_events(path)
    for e in new_events:
        e = dict(e)
        if notified:
            e["notified"] = notified
        rows.append(e)
    rows.sort(key=lambda r: ((r.get("event_date") or ""), (r.get("code") or "")))
    _write_events(rows, path)
    return new_events


def record_expirations(closed_list, today_str=None, path=EVENTS_PATH):
    """満了クローズ（recommendation_tracker.close_expired の結果）をイベントとして記録。"""
    if not closed_list:
        return []
    today_str = today_str or market_calendar.today_jst().strftime("%Y-%m-%d")
    rows = _read_events(path)
    seen = {((e.get("run_date") or "").strip(), (e.get("code") or "").strip(),
             (e.get("event_type") or "").strip()) for e in rows}
    added = []
    for c in closed_list:
        key = (c["run_date"], c["code"], EVENT_EXPIRED)
        if key in seen:
            continue
        row = {"event_date": today_str, "run_date": c["run_date"], "code": c["code"],
               "name": c.get("name", ""), "event_type": EVENT_EXPIRED,
               "price": (f"{c['exit_price']:.2f}" if c.get("exit_price") else ""),
               "ref_value": "",
               "return_pct": (f"{c['return_pct']:.2f}"
                              if c.get("return_pct") is not None else ""),
               "notified": ""}
        rows.append(row)
        added.append(row)
        seen.add(key)
    if added:
        rows.sort(key=lambda r: ((r.get("event_date") or ""), (r.get("code") or "")))
        _write_events(rows, path)
    return added


# ====== 文面生成（事実の報告に徹する・NG語なし） ======
def event_line(e):
    """イベント1件を1行の事実報告にする。"""
    name, code = e.get("name", ""), e.get("code", "")
    rd = _fmt_date(e.get("run_date", ""))
    ret = _f(e.get("return_pct"))
    ret_txt = f"{ret:+.1f}%" if ret is not None else "—"
    et = e.get("event_type")
    if et == EVENT_UPPER:
        return (f"{rd}掲載の{name}({code})：{ret_txt}。"
                f"上値メド（機械算出の参考値）に到達")
    if et == EVENT_LOWER:
        return (f"{rd}掲載の{name}({code})：{ret_txt}。"
                f"参考下値ラインを下回りました（機械的な下値目安の水準）")
    if et == EVENT_EXPIRED:
        nk = _f(e.get("nikkei_return_pct"))
        vs = f"・同期間の日経 {nk:+.1f}%" if nk is not None else ""
        return (f"{rd}掲載の{name}({code})：目安保有期間（5立会い日）が満了。"
                f"期間リターン {ret_txt}{vs}")
    return f"{rd}掲載の{name}({code})：{ret_txt}"


def build_morning_section(events_today=None, open_recs=None, current_prices=None,
                          path=EVENTS_PATH, max_open_lines=5, persist=True):
    """
    朝配信に織り込む「追跡」セクションの行リストを作る（機能拡張2）。

    構成:
      1. 前回配信以降のイベント（上値メド到達／下値ライン割れ／満了）
      2. 監視中銘柄の現況（経過リターンのみ・最大 max_open_lines 行）
      3. 免責1行
    未通知(morning未済)のイベントを載せ、掲載済みマークを付ける。
    """
    lines = []
    rows = _read_events(path)
    pending = [e for e in rows
               if (e.get("notified") or "") in ("", _N_INSTANT)]
    if events_today:
        have = {((e.get("run_date") or ""), (e.get("code") or ""),
                 (e.get("event_type") or "")) for e in pending}
        for e in events_today:
            key = ((e.get("run_date") or ""), (e.get("code") or ""),
                   (e.get("event_type") or ""))
            if key not in have:
                pending.append(e)

    if pending:
        lines.append("■ 掲載銘柄の追跡（前回配信以降の出来事）")
        for e in pending[:8]:
            lines.append(f"・{event_line(e)}")

    # 監視中銘柄の現況（イベントが無くても経過を1行ずつ）
    if open_recs and current_prices:
        cur_lines = []
        for r in open_recs[:max_open_lines * 2]:
            code = (r.get("code") or "").strip()
            entry = _f(r.get("entry_price"))
            price = current_prices.get(code)
            if price is None or not entry or entry <= 0:
                continue
            ret = (price - entry) / entry * 100
            cur_lines.append(f"・{_fmt_date(r.get('run_date',''))}掲載 "
                             f"{r.get('name','')}({code})：{ret:+.1f}%（経過観察中）")
            if len(cur_lines) >= max_open_lines:
                break
        if cur_lines:
            lines.append("■ 監視中の掲載銘柄（目安保有期間内）")
            lines.extend(cur_lines)

    if lines:
        lines.append(FOLLOWUP_DISCLAIMER)

    # 掲載済みマーク（morning）を付けて保存（dry-run 時は persist=False で書かない）
    if pending and persist:
        marked = {((e.get("run_date") or ""), (e.get("code") or ""),
                   (e.get("event_type") or "")) for e in pending}
        changed = False
        for e in rows:
            key = ((e.get("run_date") or ""), (e.get("code") or ""),
                   (e.get("event_type") or ""))
            if key in marked:
                prev = e.get("notified") or ""
                e["notified"] = _N_BOTH if prev == _N_INSTANT else _N_MORNING
                changed = True
        if changed:
            _write_events(rows, path)
    return lines


def build_instant_text(events, now_str=None):
    """
    場中の即時通知テキスト（登録読者向け・機能拡張2×3）。

    複数イベントは1通にまとめる。事実の報告＋免責のみ。
    """
    if not events:
        return None
    now_str = now_str or market_calendar.now_jst().strftime("%H:%M")
    lines = [f"【掲載銘柄の追跡通知】{now_str} 時点"]
    for e in events[:6]:
        lines.append(f"・{event_line(e)}")
    lines.append("")
    lines.append(FOLLOWUP_DISCLAIMER)
    lines.append("※本通知は「気になる/保有」に登録された銘柄のイベントのみお送りしています。")
    return "\n".join(lines)
