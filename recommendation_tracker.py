"""
recommendation_tracker.py

推奨履歴の蓄積と多期間リターン追跡（機能拡張1の中核・機能2/3の土台）。

━━ 設計原則 ━━
  1. 生データと集計を分離する。
     - recommendations.csv … 推奨の生記録（追記のみ・消さない）
     - price_tracks.csv    … 期間ごとの株価スナップショット（生・消さない）
     - 集計（月次サマリー等）は毎回生データから再計算する（後から検証・再集計できる）
  2. 集計基準は一貫させる。各期間のリターンは「推奨日終値 → N立会い日後の終値」で
     確定し、同じ窓の日経平均リターンを常に併記する（対ベンチマーク比較）。
     期間の取り方で良く見せる操作が入り込む余地を作らない。
  3. これは過去の抽出結果の追跡であり、売買の成果・推奨を示すものではない。

━━ データ構造 ━━
recommendations.csv（1推奨=1行・生データ）:
  run_date, code, name, rank, score, entry_price,
  basis_conditions（該当条件を | 区切りで保存＝「なぜこの銘柄か」の記録）,
  basis_count, basis_total,
  ref_upper（上値メド・数値）, ref_lower（参考下値ライン・数値）,
  ref_hold（目安保有・立会い日数）, status(open/closed), close_date, close_reason

price_tracks.csv（1推奨×1期間=1行・生データ）:
  run_date, code, horizon(1d/3d/5d/20d), snap_date, price,
  return_pct, nikkei_return_pct

既存 pick_ledger.csv（5営業日固定）の発展形。5d の値は pick_ledger と同一基準なので、
performance.py の累積集計（週次非重複・複利連鎖）は 5d トラックから同じ結果が得られる。
"""

import csv
import os

import market_calendar
import performance


RECS_PATH = os.path.join("data", "recommendations.csv")
TRACKS_PATH = os.path.join("data", "price_tracks.csv")

REC_FIELDS = ["run_date", "code", "name", "rank", "score", "entry_price",
              "basis_conditions", "basis_count", "basis_total",
              "ref_upper", "ref_lower", "ref_hold",
              "status", "close_date", "close_reason"]
TRACK_FIELDS = ["run_date", "code", "horizon", "snap_date", "price",
                "return_pct", "nikkei_return_pct"]

# 追跡する期間（ラベル: 立会い日数）。1ヶ月=20立会い日。
HORIZONS = {"1d": 1, "3d": 3, "5d": 5, "20d": 20}
# 正式な目安保有期間（成績集計・満了クローズの基準。performance と一致）
HOLD_SESSIONS = performance.HOLDING_SESSIONS  # = 5

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


# ====== CSV I/O（失敗しても全体を止めない） ======
def _read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] {path} の読み込みに失敗しました: {e}")
        return []


def _write_csv(path, fields, rows):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in fields})
        return True
    except Exception as e:
        print(f"[警告] {path} の書き込みに失敗しました: {e}")
        return False


def _f(row, key):
    try:
        v = row.get(key)
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_recommendations(path=RECS_PATH):
    return _read_csv(path)


def load_tracks(path=TRACKS_PATH):
    return _read_csv(path)


# ====== 推奨の記録（毎朝・上位N銘柄） ======
def record_today(scored_stocks, run_date=None, path=RECS_PATH):
    """
    今回の上位銘柄を recommendations.csv に記録する（機能拡張1）。

    各銘柄について、スコアだけでなく **該当条件（selection_basis）** と
    **目安レベル（上値メド／参考下値ライン・数値）** をセットで保存する。
    → 「なぜ推奨したか」「どの水準を目安としたか」が後から検証できる。

    同じ run_date の既存行は置き換える（同日再実行の重複防止）。追記のみで
    過去の行は変更しない（生データの保全）。
    """
    if not scored_stocks:
        return False
    import stock_insights as si
    run_date = run_date or market_calendar.today_jst().strftime("%Y-%m-%d")
    rows = [r for r in _read_csv(path)
            if (r.get("run_date") or "").strip() != run_date]
    for rank, s in enumerate(scored_stocks[:5], start=1):
        basis = si.selection_basis(s)
        tech = si.technical_levels(s)
        rows.append({
            "run_date": run_date,
            "code": (s.get("code") or "").strip(),
            "name": (s.get("name") or "").strip(),
            "rank": rank,
            "score": f"{s.get('score', 0):.1f}",
            "entry_price": f"{s.get('price', 0):.2f}",
            "basis_conditions": "|".join(basis["items"]),
            "basis_count": basis["count"],
            "basis_total": basis["total"],
            "ref_upper": (f"{tech['resistance_value']:.2f}"
                          if tech.get("resistance_value") else ""),
            "ref_lower": (f"{tech['downside_value']:.2f}"
                          if tech.get("downside_value") else ""),
            "ref_hold": HOLD_SESSIONS,
            "status": STATUS_OPEN,
            "close_date": "",
            "close_reason": "",
        })
    rows.sort(key=lambda r: ((r.get("run_date") or ""),
                             int(r.get("rank") or 99) if str(r.get("rank") or "").isdigit() else 99))
    ok = _write_csv(path, REC_FIELDS, rows)
    if ok:
        print(f"[推奨記録] {run_date} の上位{min(len(scored_stocks), 5)}銘柄を"
              f"条件・目安レベル込みで保存しました。")
    return ok


# ====== 多期間リターンの追跡（1日/3営/1週/1ヶ月・対日経） ======
def update_tracks(today_str, price_history_provider, nikkei_series=None,
                  recs_path=RECS_PATH, tracks_path=TRACKS_PATH):
    """
    各推奨について、確定した期間（1d/3d/5d/20d）のスナップショットを追記する。

    - 「N立会い日後の終値」は **その銘柄自身の日足** で entry の N 本後を採用
      （performance と同一ロジック＝集計基準の一貫性）。
    - 同じ窓の日経平均リターンを nikkei_return_pct に併記（対ベンチマーク）。
    - 既に記録済みの (run_date, code, horizon) はスキップ（追記のみ・書き換えない）。
    - 未成熟（N本先の終値がまだ無い）期間は何もしない（次回以降に確定）。

    戻り値: 新規追記した行数。
    """
    recs = _read_csv(recs_path)
    tracks = _read_csv(tracks_path)
    done = {(t.get("run_date"), t.get("code"), t.get("horizon")) for t in tracks}

    # 必要な銘柄だけ履歴を1回ずつ取得（未確定期間が残っている銘柄のみ）
    pending_codes = set()
    for r in recs:
        rd, code = (r.get("run_date") or "").strip(), (r.get("code") or "").strip()
        if not rd or not code or rd >= today_str:
            continue
        if any((rd, code, h) not in done for h in HORIZONS):
            pending_codes.add(code)

    cache = {}
    for code in sorted(pending_codes):
        try:
            cache[code] = price_history_provider(code)
        except Exception as e:
            print(f"[警告] 価格追跡: {code} の履歴取得に失敗（保留）: {e}")
            cache[code] = None

    bench_pairs = performance._as_pairs(nikkei_series)
    added = 0
    for r in recs:
        rd, code = (r.get("run_date") or "").strip(), (r.get("code") or "").strip()
        entry = _f(r, "entry_price")
        if not rd or not code or rd >= today_str or not entry or entry <= 0:
            continue
        series = cache.get(code)
        if series is None:
            continue
        for label, sessions in HORIZONS.items():
            if (rd, code, label) in done:
                continue
            ex = performance._exit_after_sessions(series, rd, sessions)
            if ex is None:
                continue  # 未成熟
            snap_date, snap_price = ex
            ret = (snap_price - entry) / entry * 100
            nk = (performance._window_return(bench_pairs, rd, snap_date)
                  if bench_pairs else None)
            tracks.append({
                "run_date": rd, "code": code, "horizon": label,
                "snap_date": snap_date, "price": f"{snap_price:.2f}",
                "return_pct": f"{ret:.4f}",
                "nikkei_return_pct": f"{nk:.4f}" if nk is not None else "",
            })
            done.add((rd, code, label))
            added += 1

    if added:
        tracks.sort(key=lambda t: ((t.get("run_date") or ""), (t.get("code") or ""),
                                   HORIZONS.get(t.get("horizon"), 99)))
        _write_csv(tracks_path, TRACK_FIELDS, tracks)
        print(f"[価格追跡] {added} 件の期間スナップショットを確定しました。")
    return added


# ====== 満了クローズ（機能拡張2: 監視対象を増やしすぎない） ======
def close_expired(today_str, recs_path=RECS_PATH, tracks_path=TRACKS_PATH):
    """
    5d トラックが確定した open 推奨を closed にする（想定保有期間の満了）。

    クローズは監視（フォローアップ）の対象から外すだけで、価格追跡（20d）と
    成績集計は継続する。戻り値: クローズした推奨のリスト（フォローアップ文面用）。
      [{run_date, code, name, entry_price, exit_price, return_pct, nikkei_return_pct}]
    """
    recs = _read_csv(recs_path)
    tracks = _read_csv(tracks_path)
    five = {(t.get("run_date"), t.get("code")): t
            for t in tracks if t.get("horizon") == "5d"}
    closed = []
    changed = False
    for r in recs:
        if (r.get("status") or "").strip() != STATUS_OPEN:
            continue
        key = ((r.get("run_date") or "").strip(), (r.get("code") or "").strip())
        t = five.get(key)
        if not t:
            continue
        r["status"] = STATUS_CLOSED
        r["close_date"] = (t.get("snap_date") or today_str)
        r["close_reason"] = "expired"
        changed = True
        closed.append({
            "run_date": key[0], "code": key[1], "name": (r.get("name") or "").strip(),
            "entry_price": _f(r, "entry_price"),
            "exit_price": _f(t, "price"),
            "return_pct": _f(t, "return_pct"),
            "nikkei_return_pct": _f(t, "nikkei_return_pct"),
        })
    if changed:
        _write_csv(recs_path, REC_FIELDS, recs)
        print(f"[追跡] 目安保有期間の満了で {len(closed)} 件をクローズしました"
              "（価格追跡・成績集計は継続）。")
    return closed


def open_recommendations(recs_path=RECS_PATH):
    """監視中（open）の推奨行を返す（フォローアップ・場中モニタの対象）。"""
    return [r for r in _read_csv(recs_path)
            if (r.get("status") or "").strip() == STATUS_OPEN]


# ====== 集計（生データから毎回再計算・performance と同一基準） ======
def tracks_as_ledger(tracks=None, horizon="5d", tracks_path=TRACKS_PATH,
                     recs_path=RECS_PATH):
    """
    price_tracks の指定期間を performance.summarize が読める台帳形式に変換する。

    5d を渡せば既存の累積集計（5営業日保有・週次非重複・複利連鎖）がそのまま使える。
    集計は常にこの生データからの再計算であり、途中結果を上書き保存しない。
    """
    tracks = tracks if tracks is not None else _read_csv(tracks_path)
    recs = {((r.get("run_date") or "").strip(), (r.get("code") or "").strip()): r
            for r in _read_csv(recs_path)}
    ledger = []
    for t in tracks:
        if t.get("horizon") != horizon:
            continue
        key = ((t.get("run_date") or "").strip(), (t.get("code") or "").strip())
        rec = recs.get(key, {})
        ledger.append({
            "run_date": key[0], "code": key[1],
            "name": (rec.get("name") or "").strip(),
            "rank": (rec.get("rank") or "").strip(),
            "entry_price": rec.get("entry_price", ""),
            "exit_date": t.get("snap_date", ""),
            "exit_price": t.get("price", ""),
            "return_pct": t.get("return_pct", ""),
            "bench_return_pct": t.get("nikkei_return_pct", ""),
            "status": "closed",
        })
    return ledger


def horizon_stats(tracks=None, tracks_path=TRACKS_PATH):
    """
    期間別の簡易統計（平均騰落率・勝率・対日経・件数）。月次レポートの多期間表用。

    戻り値: {horizon: {"n", "avg_return", "win_rate", "avg_vs_nikkei"}}
    """
    tracks = tracks if tracks is not None else _read_csv(tracks_path)
    out = {}
    for label in HORIZONS:
        rows = [t for t in tracks if t.get("horizon") == label]
        rets = [_f(t, "return_pct") for t in rows]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        vs = []
        for t in rows:
            r, nk = _f(t, "return_pct"), _f(t, "nikkei_return_pct")
            if r is not None and nk is not None:
                vs.append(r - nk)
        out[label] = {
            "n": len(rets),
            "avg_return": sum(rets) / len(rets),
            "win_rate": sum(1 for x in rets if x > 0) / len(rets) * 100,
            "avg_vs_nikkei": (sum(vs) / len(vs)) if vs else None,
        }
    return out


def monthly_summary(tracks_path=TRACKS_PATH, recs_path=RECS_PATH):
    """
    月次成績サマリー（機能拡張1の配信用）。5dトラックを正式基準として
    performance.summarize（週次非重複・複利連鎖）で集計し、多期間統計を添える。

    戻り値: {"performance": performance.summarize の結果, "horizons": horizon_stats,
             "rec_count": 総推奨数, "open_count": 監視中数}
    """
    ledger = tracks_as_ledger(horizon="5d", tracks_path=tracks_path,
                              recs_path=recs_path)
    perf = performance.summarize(ledger)
    recs = _read_csv(recs_path)
    return {
        "performance": perf,
        "horizons": horizon_stats(tracks_path=tracks_path),
        "rec_count": len(recs),
        "open_count": sum(1 for r in recs
                          if (r.get("status") or "").strip() == STATUS_OPEN),
    }


# ====== 検証ビュー（前回/3営業日前/1週間前・price_tracks を単一ソースに） ======
# 表示ラベルと (目標経過立会い日数, 使用する期間トラック) の対応。
# 「前回」= 前営業日の掲載（1立会い日経過）→ 1d トラック、のように、
# 経過日数と期間を一致させることで、summary カードの検証と price_tracks の
# 追跡が **同じ生データ・同じ基準** から出る（2系統併走による食い違いの排除）。
VALIDATION_TARGETS = (("前回", 1, "1d"), ("3営業日前", 3, "3d"), ("1週間前", 5, "5d"))


def validation_views(today_str, recs_path=RECS_PATH, tracks_path=TRACKS_PATH,
                     targets=VALIDATION_TARGETS):
    """
    検証ビュー（前回・3営業日前・1週間前）を price_tracks から一貫基準で作る。

    従来の report_history 検証（当時価格 vs 今日の価格）と異なり、各回の成績を
    「経過日数に対応する期間トラックの確定値」で表示する。銘柄・日経とも
    performance の同一起点ロジックで確定した値なので、追跡・累積成績と数字が揃う。

    戻り値: report_writer の検証表示と互換の dict リスト
      [{label, run_date, evaluated, total, avg_return, wins, losses,
        best, worst, nikkei_return, topix_return, vs_nikkei, vs_topix}, ...]
    トラック未確定の回はスキップ（呼び出し側で従来検証にフォールバック可）。
    """
    recs = _read_csv(recs_path)
    tracks = _read_csv(tracks_path)
    by_rd = {}
    for r in recs:
        rd = (r.get("run_date") or "").strip()
        if rd and rd < today_str:
            by_rd.setdefault(rd, []).append(r)
    track_map = {((t.get("run_date") or "").strip(), (t.get("code") or "").strip(),
                  (t.get("horizon") or "").strip()): t for t in tracks}

    out, used = [], set()
    for label, age_target, horizon in targets:
        best_rd, best_diff = None, None
        for rd in by_rd:
            if rd in used:
                continue
            age = performance._business_days_between(rd, today_str)
            if age is None:
                continue
            diff = abs(age - age_target)
            if best_diff is None or diff < best_diff:
                best_rd, best_diff = rd, diff
        if best_rd is None:
            continue
        entries = sorted(by_rd[best_rd],
                         key=lambda r: int(r.get("rank") or 99)
                         if str(r.get("rank") or "").isdigit() else 99)[:5]
        results, nikkeis = [], []
        for e in entries:
            code = (e.get("code") or "").strip()
            t = track_map.get((best_rd, code, horizon))
            ret = _f(t, "return_pct") if t else None
            if ret is None:
                continue
            results.append({"name": (e.get("name") or "").strip(),
                            "code": code, "return": ret})
            nk = _f(t, "nikkei_return_pct")
            if nk is not None:
                nikkeis.append(nk)
        if not results:
            continue  # この回の該当トラックが未確定（データ蓄積待ち）
        used.add(best_rd)
        avg = sum(r["return"] for r in results) / len(results)
        nk_avg = (sum(nikkeis) / len(nikkeis)) if nikkeis else None
        out.append({
            "label": label,
            "run_date": best_rd,
            "evaluated": len(results),
            "total": len(entries),
            "avg_return": avg,
            "wins": sum(1 for r in results if r["return"] > 0),
            "losses": sum(1 for r in results if r["return"] <= 0),
            "best": max(results, key=lambda r: r["return"]),
            "worst": min(results, key=lambda r: r["return"]),
            "nikkei_return": nk_avg,
            "topix_return": None,
            "vs_nikkei": (avg - nk_avg) if nk_avg is not None else None,
            "vs_topix": None,
        })
    return out


# ====== 移行（既存 pick_ledger.csv → 新形式・1回だけ） ======
def migrate_from_pick_ledger(ledger_path=performance.LEDGER_PATH,
                             recs_path=RECS_PATH, tracks_path=TRACKS_PATH):
    """
    既存の pick_ledger.csv（5営業日固定）を新形式へ取り込む（初回移行用・冪等）。

    - recommendations.csv に無い (run_date, code) を追加（条件・目安は空＝当時未記録）
    - closed 行の exit を 5d トラックとして price_tracks.csv に追加
    既存行は上書きしない。戻り値: (追加した推奨数, 追加したトラック数)。
    """
    ledger = _read_csv(ledger_path)
    if not ledger:
        return (0, 0)
    recs = _read_csv(recs_path)
    tracks = _read_csv(tracks_path)
    have_rec = {((r.get("run_date") or "").strip(), (r.get("code") or "").strip())
                for r in recs}
    have_trk = {(t.get("run_date"), t.get("code"), t.get("horizon")) for t in tracks}

    added_r = added_t = 0
    for row in ledger:
        rd = (row.get("run_date") or "").strip()
        code = (row.get("code") or "").strip()
        if not rd or not code:
            continue
        if (rd, code) not in have_rec:
            recs.append({
                "run_date": rd, "code": code,
                "name": (row.get("name") or "").strip(),
                "rank": (row.get("rank") or "").strip(),
                "score": "", "entry_price": row.get("entry_price", ""),
                "basis_conditions": "", "basis_count": "", "basis_total": "",
                "ref_upper": "", "ref_lower": "", "ref_hold": HOLD_SESSIONS,
                "status": (STATUS_CLOSED if row.get("status") == "closed"
                           else STATUS_OPEN),
                "close_date": row.get("exit_date", "") if row.get("status") == "closed" else "",
                "close_reason": "expired" if row.get("status") == "closed" else "",
            })
            have_rec.add((rd, code))
            added_r += 1
        if row.get("status") == "closed" and (rd, code, "5d") not in have_trk:
            tracks.append({
                "run_date": rd, "code": code, "horizon": "5d",
                "snap_date": row.get("exit_date", ""),
                "price": row.get("exit_price", ""),
                "return_pct": row.get("return_pct", ""),
                "nikkei_return_pct": row.get("bench_return_pct", ""),
            })
            have_trk.add((rd, code, "5d"))
            added_t += 1

    if added_r:
        recs.sort(key=lambda r: ((r.get("run_date") or ""),
                                 int(r.get("rank") or 99) if str(r.get("rank") or "").isdigit() else 99))
        _write_csv(recs_path, REC_FIELDS, recs)
    if added_t:
        _write_csv(tracks_path, TRACK_FIELDS, tracks)
    if added_r or added_t:
        print(f"[移行] pick_ledger から 推奨{added_r}件・5dトラック{added_t}件を取り込みました。")
    return (added_r, added_t)
