"""
tests/test_tracking.py

機能拡張1〜3のユニットテスト。

  機能拡張1: recommendation_tracker（推奨記録・多期間追跡・月次集計・移行）
  機能拡張2: followup（イベント判定・二重通知ガード・文面のNG語チェック）
  機能拡張3: personalize（価格帯フィルタ・配信グループ化）／subscriber_store

一時ディレクトリのCSVを使い、リポジトリの data/ を汚さない。
実行: python tests/test_tracking.py  （または pytest tests/）
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import followup                     # noqa: E402
import personalize                  # noqa: E402
import recommendation_tracker as rt  # noqa: E402
import stock_insights as si         # noqa: E402
import subscriber_store             # noqa: E402
from promo import ng_words          # noqa: E402


def _tmp(name):
    d = tempfile.mkdtemp(prefix="trk_")
    return os.path.join(d, name)


def _series(start_date, prices):
    """営業日連続の [(date, close), ...]（土日スキップ）。"""
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    out = []
    for p in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), float(p)))
        d += timedelta(days=1)
    return out


def _stock(code="9990", name="テスト銘柄", price=1000.0):
    return {"code": code, "name": name, "score": 8.0, "price": price,
            "theme_tags": ["DX"], "macro_reason": "DXタグが関連",
            "metrics": {"vol_ratio": 1.5, "gap_5_25": 2.0, "gap_25_75": 1.0,
                        "rel_strength": 3.0, "surge_5": 4.0, "per": 12.0,
                        "pbr": 0.9, "sma25": price * 0.95, "sma5": price * 0.98,
                        "sma75": price * 0.9, "atr14": price * 0.03,
                        "recent_low_20": price * 0.94,
                        "recent_high_20": price * 1.05,
                        "recent_low_60": price * 0.88,
                        "recent_high_60": price * 1.05}}


# ====== 機能拡張1: recommendation_tracker ======
def test_record_today_saves_basis_and_levels():
    """推奨記録に該当条件と目安レベル（数値）が保存される。"""
    path = _tmp("recs.csv")
    ok = rt.record_today([_stock()], run_date="2026-07-01", path=path)
    assert ok
    rows = rt.load_recommendations(path)
    assert len(rows) == 1
    r = rows[0]
    assert r["basis_conditions"], "該当条件が空"
    assert "出来高" in r["basis_conditions"]
    assert float(r["ref_upper"]) > 1000.0, "上値メドが数値で保存されていない"
    assert float(r["ref_lower"]) < 1000.0, "参考下値ラインが数値で保存されていない"
    assert r["status"] == "open"


def test_update_tracks_multi_horizon_and_nikkei():
    """1d/3d/5d/20d の各期間が銘柄自身の日足から確定し、日経の同窓リターンが付く。"""
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    rt.record_today([_stock(price=100.0)], run_date="2026-06-01", path=recs_path)
    prices = [100 + i for i in range(25)]           # 100,101,...124（21本以上）
    series = _series("2026-06-01", prices)
    nikkei = _series("2026-06-01", [1000 + i * 2 for i in range(25)])
    added = rt.update_tracks("2026-07-10", lambda code: series,
                             nikkei_series=nikkei,
                             recs_path=recs_path, tracks_path=tracks_path)
    assert added == 4, f"4期間確定するはず: {added}"
    tracks = rt.load_tracks(tracks_path)
    by_h = {t["horizon"]: t for t in tracks}
    assert abs(float(by_h["1d"]["return_pct"]) - 1.0) < 1e-6
    assert abs(float(by_h["5d"]["return_pct"]) - 5.0) < 1e-6
    assert abs(float(by_h["20d"]["return_pct"]) - 20.0) < 1e-6
    assert by_h["5d"]["nikkei_return_pct"] != "", "日経の同窓リターンが無い"
    # 再実行しても追記されない（冪等）
    again = rt.update_tracks("2026-07-10", lambda code: series,
                             nikkei_series=nikkei,
                             recs_path=recs_path, tracks_path=tracks_path)
    assert again == 0


def test_close_expired_after_5d():
    """5dトラック確定後に open→closed（満了）となり、成績集計は継続する。"""
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    rt.record_today([_stock(price=100.0)], run_date="2026-06-01", path=recs_path)
    series = _series("2026-06-01", [100, 101, 102, 103, 104, 110, 111])
    rt.update_tracks("2026-07-10", lambda code: series,
                     recs_path=recs_path, tracks_path=tracks_path)
    closed = rt.close_expired("2026-07-10", recs_path=recs_path,
                              tracks_path=tracks_path)
    assert len(closed) == 1
    assert abs(closed[0]["return_pct"] - 10.0) < 1e-6
    assert rt.open_recommendations(recs_path) == []


def test_monthly_summary_uses_5d_as_official():
    """月次集計は 5d トラックを正式基準として performance と同一ロジックで出る。"""
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    rt.record_today([_stock(price=100.0)], run_date="2026-06-01", path=recs_path)
    series = _series("2026-06-01", [100, 101, 102, 103, 104, 110, 111])
    nikkei = _series("2026-06-01", [1000, 1001, 1002, 1003, 1004, 1010, 1011])
    rt.update_tracks("2026-07-10", lambda code: series, nikkei_series=nikkei,
                     recs_path=recs_path, tracks_path=tracks_path)
    msum = rt.monthly_summary(tracks_path=tracks_path, recs_path=recs_path)
    p = msum["performance"]
    assert p["available"]
    assert abs(p["cum_return"] - 10.0) < 1e-6
    assert "5d" in msum["horizons"] and "1d" in msum["horizons"]


def test_migrate_from_pick_ledger_idempotent():
    """pick_ledger からの移行が冪等（2回実行しても増えない）。"""
    ledger_path = _tmp("ledger.csv")
    import performance
    performance._write_csv(ledger_path, performance.LEDGER_FIELDS, [{
        "run_date": "2026-06-01", "code": "1111", "name": "旧銘柄", "rank": "1",
        "entry_price": "100.00", "exit_date": "2026-06-08", "exit_price": "105.00",
        "return_pct": "5.0", "bench_return_pct": "1.0", "status": "closed"}])
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    r1, t1 = rt.migrate_from_pick_ledger(ledger_path, recs_path, tracks_path)
    r2, t2 = rt.migrate_from_pick_ledger(ledger_path, recs_path, tracks_path)
    assert (r1, t1) == (1, 1)
    assert (r2, t2) == (0, 0)


# ====== 機能拡張2: followup ======
def _open_rec(code="9990", entry=1000.0, upper=1050.0, lower=940.0):
    return {"run_date": "2026-07-16", "code": code, "name": "テスト銘柄",
            "entry_price": f"{entry}", "ref_upper": f"{upper}",
            "ref_lower": f"{lower}", "status": "open"}


def test_detect_upper_and_lower_events():
    recs = [_open_rec()]
    up = followup.detect_events(recs, {"9990": 1055.0}, today_str="2026-07-20",
                                existing_events=[])
    assert len(up) == 1 and up[0]["event_type"] == "upper_hit"
    down = followup.detect_events(recs, {"9990": 935.0}, today_str="2026-07-20",
                                  existing_events=[])
    assert len(down) == 1 and down[0]["event_type"] == "lower_break"
    none = followup.detect_events(recs, {"9990": 1000.0}, today_str="2026-07-20",
                                  existing_events=[])
    assert none == []


def test_detect_events_dedupe():
    """同じイベントは既存記録があれば再検知しない（二重通知ガード）。"""
    recs = [_open_rec()]
    existing = [{"run_date": "2026-07-16", "code": "9990",
                 "event_type": "upper_hit"}]
    out = followup.detect_events(recs, {"9990": 1060.0}, today_str="2026-07-21",
                                 existing_events=existing)
    assert out == []


def test_followup_texts_are_ng_clean():
    """フォローアップ文面（朝・即時・満了）が禁止語を含まない。"""
    events = [
        {"event_date": "2026-07-20", "run_date": "2026-07-16", "code": "4432",
         "name": "サンプルＡ", "event_type": "upper_hit", "price": "1055",
         "ref_value": "1050", "return_pct": "3.2", "notified": ""},
        {"event_date": "2026-07-20", "run_date": "2026-07-16", "code": "2471",
         "name": "サンプルＢ", "event_type": "lower_break", "price": "935",
         "ref_value": "940", "return_pct": "-6.5", "notified": ""},
        {"event_date": "2026-07-20", "run_date": "2026-07-13", "code": "9990",
         "name": "サンプルＣ", "event_type": "expired", "price": "1100",
         "ref_value": "", "return_pct": "10.0", "notified": ""},
    ]
    for e in events:
        line = followup.event_line(e)
        assert ng_words.check_ng(line) == [], f"{line} -> {ng_words.check_ng(line)}"
    instant = followup.build_instant_text(events)
    assert ng_words.check_ng(instant) == [], ng_words.check_ng(instant)
    assert "投資助言ではありません" in instant


def test_morning_section_marks_notified():
    """朝セクション生成でイベントに morning マークが付き、翌日は再掲されない。"""
    path = _tmp("events.csv")
    ev = [{"event_date": "2026-07-20", "run_date": "2026-07-16", "code": "4432",
           "name": "サンプルＡ", "event_type": "upper_hit", "price": "1055",
           "ref_value": "1050", "return_pct": "3.2", "notified": ""}]
    followup.record_events(ev, path=path)
    lines1 = followup.build_morning_section(path=path)
    assert any("サンプルＡ" in ln for ln in lines1)
    lines2 = followup.build_morning_section(path=path)
    assert not any("サンプルＡ" in ln for ln in lines2), "二重掲載されている"


# ====== 機能拡張3: personalize / subscriber_store ======
def test_filter_by_cap():
    stocks = [_stock("1111", price=500.0),    # 単元5万
              _stock("2222", price=1500.0),   # 単元15万
              _stock("3333", price=4000.0)]   # 単元40万
    assert len(personalize.filter_by_cap(stocks, None)) == 3
    assert [s["code"] for s in personalize.filter_by_cap(stocks, 100000)] == ["1111"]
    assert [s["code"] for s in personalize.filter_by_cap(stocks, 200000)] == ["1111", "2222"]


def test_group_users_minimizes_requests():
    """同じフィルタ結果の読者が1グループにまとまり、デフォルト組が先頭に来る。"""
    stocks = [_stock("1111", price=500.0), _stock("2222", price=1500.0)]
    users = [
        {"user_id": "U1", "price_cap": None, "active": True},
        {"user_id": "U2", "price_cap": None, "active": True},
        {"user_id": "U3", "price_cap": 100000.0, "active": True},
        {"user_id": "U4", "price_cap": 100000.0, "active": True},
        {"user_id": "U5", "price_cap": 100000.0, "active": False},  # ブロック済み
    ]
    groups = personalize.group_users(users, stocks)
    assert len(groups) == 2
    assert groups[0]["is_default"] and sorted(groups[0]["user_ids"]) == ["U1", "U2"]
    assert sorted(groups[1]["user_ids"]) == ["U3", "U4"]
    note = personalize.filtered_note(groups[1], len(stocks))
    assert note and "価格帯" in note
    assert ng_words.check_ng(note) == []
    assert personalize.filtered_note(groups[0], len(stocks)) is None


def test_watchers_for_codes():
    settings = {"users": [{"user_id": "U1", "price_cap": None, "active": True}],
                "watchers": {"4432": {"interest": ["U1"], "holding": []},
                             "2471": {"interest": [], "holding": ["U2"]}}}
    assert subscriber_store.watchers_for_codes(["4432"], settings) == ["U1"]
    assert subscriber_store.watchers_for_codes(["2471"], settings) == ["U2"]
    assert subscriber_store.watchers_for_codes(["4432", "2471"], settings) == ["U1", "U2"]
    assert subscriber_store.watchers_for_codes(["9999"], settings) == []


def test_monthly_report_text_is_ng_clean():
    """月次レポート（集計あり/なし両方）が禁止語を含まない。"""
    import report_writer as rw
    empty = rw.build_monthly_report_text({"performance": {"available": False}},
                                         "2026年7月度まで")
    assert ng_words.check_ng(empty) == [], ng_words.check_ng(empty)
    msum = {"performance": {
        "available": True, "cum_return": 4.5, "cum_nikkei": 2.9,
        "cum_vs_nikkei": 1.6, "cohort_win_rate": 62.5, "pick_win_rate": 60.0,
        "chain_cohorts": 8, "pick_count": 40, "max_drawdown": -2.2,
        "avg_cohort_return": 0.5, "first_date": "2026-05-25",
        "last_date": "2026-07-13",
        "monthly": [{"month": "2026-06", "cohorts": 5, "month_return": 1.6,
                     "cum_return": 2.6, "cum_vs_nikkei": 1.0,
                     "cohort_win_rate": 60.0, "avg_cohort_return": 0.3,
                     "max_drawdown": -2.2}]},
        "horizons": {"1d": {"n": 40, "avg_return": 0.2, "win_rate": 55.0,
                            "avg_vs_nikkei": 0.1},
                     "5d": {"n": 40, "avg_return": 0.6, "win_rate": 60.0,
                            "avg_vs_nikkei": 0.3}},
        "rec_count": 40, "open_count": 5}
    text = rw.build_monthly_report_text(msum, "2026年7月度まで")
    assert ng_words.check_ng(text) == [], ng_words.check_ng(text)
    assert "最大ドローダウン" in text and "超過" in text and "勝率" in text


# ====== 改善1: 起点バーの一貫性＋検証ビューの一本化 ======
def test_exit_anchor_uses_bar_before_run_date():
    """
    本番同様「run_dateより前のバー＝記録entry価格のバー」を起点にする。
    朝実行では entry_price=前営業日終値のため、後日取得した日足に run_date 当日
    バーがあっても、起点が1本後ろにずれてはいけない。
    """
    import performance
    # 6/1(月)〜: 前営業日 6/5 に entry(=104)、run_date 6/8(月)
    series = _series("2026-06-01", [100, 101, 102, 103, 104,   # 6/1-6/5
                                    105, 106, 107, 108, 109, 110])  # 6/8-
    ex = performance._exit_after_sessions(series, "2026-06-08", sessions=5)
    assert ex is not None
    _ed, ep = ex
    # 起点=6/5(104) → 5本後=6/12(109)。当日バー起点なら110になってしまう。
    assert ep == 109.0, f"起点が前営業日バーになっていない: {ep}"
    # ベンチマーク窓も同じ起点（6/5基準）
    r = performance._window_return(series, "2026-06-08", _ed)
    assert abs(r - (109 - 104) / 104 * 100) < 1e-9, f"窓リターンの起点が不一致: {r}"


def test_validation_views_from_tracks():
    """検証ビュー（前回=1d/3営業日前=3d/1週間前=5d）が price_tracks から作られる。"""
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    # 3回分の掲載（1・3・5立会い日前）
    recs, tracks = [], []
    for rd, ret1, ret3, ret5 in [
        ("2026-07-22", 1.0, None, None),    # 前回（1営業日前）
        ("2026-07-20", 0.5, 2.0, None),     # 3営業日前
        ("2026-07-16", 0.2, 1.0, 4.0),      # 1週間前（5営業日前）
    ]:
        recs.append({"run_date": rd, "code": f"C{rd[-2:]}", "name": f"銘柄{rd[-2:]}",
                     "rank": 1, "score": "8.0", "entry_price": "1000.00",
                     "basis_conditions": "", "basis_count": "", "basis_total": "",
                     "ref_upper": "", "ref_lower": "", "ref_hold": 5,
                     "status": "open", "close_date": "", "close_reason": ""})
        for h, ret in (("1d", ret1), ("3d", ret3), ("5d", ret5)):
            if ret is None:
                continue
            tracks.append({"run_date": rd, "code": f"C{rd[-2:]}", "horizon": h,
                           "snap_date": "2026-07-23", "price": "1010.00",
                           "return_pct": f"{ret:.4f}",
                           "nikkei_return_pct": f"{ret - 0.5:.4f}"})
    rt._write_csv(recs_path, rt.REC_FIELDS, recs)
    rt._write_csv(tracks_path, rt.TRACK_FIELDS, tracks)

    views = rt.validation_views("2026-07-23", recs_path=recs_path,
                                tracks_path=tracks_path)
    by_label = {v["label"]: v for v in views}
    assert "前回" in by_label and by_label["前回"]["run_date"] == "2026-07-22"
    assert abs(by_label["前回"]["avg_return"] - 1.0) < 1e-6
    assert abs(by_label["前回"]["vs_nikkei"] - 0.5) < 1e-6
    assert by_label["3営業日前"]["run_date"] == "2026-07-20"
    assert abs(by_label["3営業日前"]["avg_return"] - 2.0) < 1e-6  # 3dトラックの値
    assert by_label["1週間前"]["run_date"] == "2026-07-16"
    assert abs(by_label["1週間前"]["avg_return"] - 4.0) < 1e-6   # 5dトラックの値


def test_validation_views_empty_when_no_tracks():
    """トラック未確定なら空リスト（呼び出し側が従来検証へフォールバック）。"""
    recs_path, tracks_path = _tmp("recs.csv"), _tmp("tracks.csv")
    rt.record_today([_stock()], run_date="2026-07-22", path=recs_path)
    assert rt.validation_views("2026-07-23", recs_path=recs_path,
                               tracks_path=tracks_path) == []


# ====== 改善2: message_budget ======
def test_message_budget_record_and_usage():
    import message_budget as mb
    path = _tmp("usage.csv")
    mb.record("朝配信", 200, date_str="2026-07-01", path=path)
    mb.record("朝配信", 200, date_str="2026-07-23", path=path)
    mb.record("即時通知", 3, date_str="2026-07-23", path=path)
    u = mb.month_usage(month="2026-07", path=path)
    assert u["total"] == 403
    # 今日分（today_jstに依存しないよう手動集計で確認）
    rows = mb._read_rows(path)
    assert sum(int(r["recipients"]) for r in rows if r["date"] == "2026-07-23") == 203


def test_message_budget_threshold_alert(monkeypatch=None):
    import message_budget as mb
    path = _tmp("usage.csv")
    today = __import__("market_calendar").today_jst().strftime("%Y-%m-%d")
    month = today[:7]
    # 上限5000の80% = 4000 を今日の送信で跨ぐ: 3900(過去) + 200(今日) = 4100
    mb.record("朝配信", 3900, date_str=f"{month}-01", path=path)
    assert mb.threshold_alert(path=path) is None or f"{month}-01" == today  # 過去分のみでは発火しない
    mb.record("朝配信", 200, date_str=today, path=path)
    alert = mb.threshold_alert(path=path)
    assert alert is not None and "80%" in alert, alert
    from promo import ng_words
    assert ng_words.check_ng(alert) == []


# ====== 改善3: 業種PER中央値 ======
def test_sector_medians_and_fallback():
    import sector_valuation as sv
    path = _tmp("per_cache.csv")
    vals = {f"C{i}": {"per": 10.0 + i} for i in range(6)}       # 10..15 → 中央値12.5
    sectors = {f"C{i}": "電気機器" for i in range(6)}
    sectors["C5"] = "サービス業"                                  # 1件だけ → サンプル不足
    vals["C5"] = {"per": 30.0}
    n = sv.update_cache(vals, sectors, today_str="2026-07-23", path=path)
    assert n == 6
    med = sv.sector_medians(path=path)
    assert "電気機器" in med and abs(med["電気機器"]["median"] - 12.0) < 1e-6
    assert "サービス業" not in med, "サンプル不足の業種は中央値を出さない"

    # selection_basis: 中央値ありなら「業種中央値以下」で判定
    s = _stock()
    s["metrics"]["per"] = 11.0
    s["sector"] = "電気機器"
    sv.attach_sector_median([s], med)
    basis = si.selection_basis(s)
    joined = " / ".join(basis["items"])
    assert "業種中央値" in joined, joined
    # 中央値より高PERなら不一致（項目に出ない）
    s2 = _stock(code="9991")
    s2["metrics"]["per"] = 25.0
    s2["sector"] = "電気機器"
    sv.attach_sector_median([s2], med)
    assert "業種中央値" not in " / ".join(si.selection_basis(s2)["items"])
    # 中央値が無い業種は従来の絶対水準にフォールバック
    s3 = _stock(code="9992")
    s3["metrics"]["per"] = 12.0
    s3["sector"] = "サービス業"
    sv.attach_sector_median([s3], med)
    assert "割安圏" in " / ".join(si.selection_basis(s3)["items"])


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
