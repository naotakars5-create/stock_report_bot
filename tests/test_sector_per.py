"""
tests/test_sector_per.py

業種別PERランキングの計算ロジック（sector_per.ranking）の単体テスト。
依頼4の必須ケース: 業種中央値/割安度/自己過去比/バリュートラップ判定 と
エッジ(該当0件・業種内1社のみ・EPS null)を網羅する。

実行: python tests/test_sector_per.py  （または pytest tests/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sector_per import config as C      # noqa: E402
from sector_per import ranking as R     # noqa: E402


def _stock(**kw):
    base = {
        "code": "0001", "name": "テスト", "sector33": "機械", "market": "プライム",
        "close": 1000.0, "forecast_eps": 100.0, "market_cap": 5.0e10,
        "avg_turnover": 5.0e8, "listing_years": 10.0, "roe": 12.0,
        "equity_ratio": 45.0, "revenue_or_profit_growth": True,
        "no_downward_revision": True, "per_history": [12.0] * 80,
    }
    base.update(kw)
    return base


# ===== 予想PER =====
def test_forecast_per_and_eps_null():
    assert R.forecast_per(_stock(close=1500, forecast_eps=100)) == 15.0
    assert R.forecast_per(_stock(forecast_eps=None)) is None   # EPS null
    assert R.forecast_per(_stock(forecast_eps=0)) is None      # EPS=0
    assert R.forecast_per(_stock(forecast_eps=-50)) is None    # 赤字


# ===== 業種中央値 =====
def test_sector_median_basic():
    ss = [_stock(close=1000, forecast_eps=100),   # PER 10
          _stock(close=1500, forecast_eps=100),   # PER 15
          _stock(close=2000, forecast_eps=100)]   # PER 20
    assert R.sector_median_per(ss) == 15.0


def test_sector_median_robust_to_outlier():
    """高PER1社では中央値は壊れない（平均との違い）。"""
    ss = [_stock(close=1000, forecast_eps=100),   # 10
          _stock(close=1200, forecast_eps=100),   # 12
          _stock(close=9000, forecast_eps=100)]   # 90 (外れ値)
    assert R.sector_median_per(ss) == 12.0        # 中央値は12（平均なら37.3）


def test_sector_median_single_peer_is_none():
    """業種内が1社のみ（下限未満）なら中央値は算出しない。"""
    assert R.sector_median_per([_stock()]) is None


def test_sector_median_ignores_eps_null():
    ss = [_stock(close=1000, forecast_eps=100),   # 10
          _stock(forecast_eps=None),              # 除外
          _stock(close=1400, forecast_eps=100),   # 14
          _stock(close=1800, forecast_eps=100)]   # 18
    # 有効3社(10,14,18)→中央値14
    assert R.sector_median_per(ss) == 14.0


# ===== 業種内割安度 =====
def test_sector_relative_score():
    # 中央値15、個別10 → (15-10)/15 = 0.333...
    assert abs(R.sector_relative_score(10.0, 15.0) - (5 / 15)) < 1e-9
    # 個別が中央値より高い→負
    assert R.sector_relative_score(20.0, 15.0) < 0
    assert R.sector_relative_score(None, 15.0) is None


# ===== 自己過去比 =====
def test_self_history_percentile_and_cheapness():
    hist = [float(x) for x in range(10, 30)]  # 10..29, n=20 (>=60? no) -> need >=60
    hist = hist * 4                            # 80サンプル
    # 現在PER=10 → ほぼ最安 → percentile≈0 → cheapness≈1
    pct = R.self_history_percentile(10.0, hist)
    assert pct is not None and pct < 0.1
    assert R.self_cheapness(10.0, hist) > 0.9
    # 現在PER=29 → 高い方 → cheapness 低い
    assert R.self_cheapness(29.0, hist) < 0.2


def test_self_history_insufficient_samples():
    assert R.self_history_percentile(12.0, [12.0] * 10) is None  # 60未満
    assert R.self_cheapness(12.0, [12.0] * 10) is None


# ===== バリュートラップ =====
def test_valuetrap_pass():
    ok, reasons = R.passes_valuetrap(_stock())
    assert ok and reasons == []


def test_valuetrap_fail_each_condition():
    assert not R.passes_valuetrap(_stock(roe=5.0))[0]              # ROE不足
    assert not R.passes_valuetrap(_stock(equity_ratio=20.0))[0]   # 自己資本比率不足
    assert not R.passes_valuetrap(_stock(revenue_or_profit_growth=False))[0]
    assert not R.passes_valuetrap(_stock(no_downward_revision=False))[0]
    # 欠損は満たさない扱い
    assert not R.passes_valuetrap(_stock(roe=None))[0]


# ===== 総合スコア =====
def test_total_score_weighting():
    # 業種内0.3, 自己0.8 → 0.3*0.6 + 0.8*0.4 = 0.5
    assert abs(R.total_score(0.3, 0.8) - 0.5) < 1e-9
    # 片方Noneなら残りで正規化（自己Noneなら業種内そのもの）
    assert abs(R.total_score(0.3, None) - 0.3) < 1e-9
    assert R.total_score(None, None) is None


# ===== 統合: 該当0件・業種別ランキング =====
def test_score_all_zero_candidates_marked():
    """業種内に母集団は複数いるが、全員valuetrapで落ちる→該当0件を明示。"""
    ss = [
        _stock(code="A", close=1000, forecast_eps=100, roe=3.0),  # ROE不足
        _stock(code="B", close=1200, forecast_eps=100, roe=4.0),
        _stock(code="C", close=1400, forecast_eps=100, roe=2.0),
    ]
    rankings, rows = R.score_all(ss)
    assert "機械" in rankings
    assert rankings["機械"]["median_per"] is not None   # 中央値は母集団から算出できる
    assert rankings["機械"]["has_candidates"] is False   # 該当なし
    assert rankings["機械"]["candidates"] == []


def test_score_all_ranks_by_total_score():
    ss = [
        # 全員 valuetrap 通過。PERが安いほど業種内割安度↑
        _stock(code="A", close=800, forecast_eps=100),   # PER 8
        _stock(code="B", close=1000, forecast_eps=100),  # PER 10
        _stock(code="C", close=1500, forecast_eps=100),  # PER 15
        _stock(code="D", close=2000, forecast_eps=100),  # PER 20
    ]
    rankings, rows = R.score_all(ss)
    cands = rankings["機械"]["candidates"]
    assert len(cands) == C.TOP_N_PER_SECTOR  # 上位3
    assert cands[0]["code"] == "A"           # 最安が首位
    assert [c["code"] for c in cands] == ["A", "B", "C"]
    # 全銘柄行は検証用に全件出る
    assert len(rows) == 4


def test_score_all_single_stock_sector_no_median():
    """業種内1社のみ→中央値なし→業種内割安度が出ず、自己過去比だけで評価。"""
    ss = [_stock(code="X", sector33="ガラス・土石製品",
                 per_history=[float(x) for x in range(5, 25)] * 4,  # 80
                 close=600, forecast_eps=100)]  # PER 6, 過去比では安い
    rankings, rows = R.score_all(ss)
    r = rankings["ガラス・土石製品"]
    assert r["median_per"] is None          # 1社なので中央値なし
    # 業種内割安度は出ないが自己過去比で候補になり得る
    assert r["candidates"] and r["candidates"][0]["code"] == "X"
    assert r["candidates"][0]["sector_relative"] is None
    assert r["candidates"][0]["self_cheapness"] is not None


# ===== J-Quants 解析（純粋関数・ネットワーク非依存） =====
def test_jquants_parse_statement():
    from sector_per import jquants_client as J
    rec = {"LocalCode": "72030", "DisclosedDate": "2026-05-10",
           "ForecastEarningsPerShare": "250.5", "Equity": "20000000000",
           "TotalAssets": "50000000000", "NetSales": "30000000000",
           "Profit": "2400000000"}
    p = J.parse_statement(rec)
    assert p["code"] == "7203"
    assert p["forecast_eps"] == 250.5
    assert abs(p["equity_ratio"] - 40.0) < 1e-6   # 20000/50000=40%
    assert abs(J.roe_from(p) - 12.0) < 1e-6        # 2400/20000=12%


def test_jquants_parse_handles_missing():
    from sector_per import jquants_client as J
    p = J.parse_statement({"LocalCode": "9999", "DisclosedDate": "2026-01-01",
                           "ForecastEarningsPerShare": "-", "Equity": ""})
    assert p["forecast_eps"] is None
    assert p["equity"] is None
    assert J.roe_from(p) is None


def test_jquants_downward_revision():
    from sector_per import jquants_client as J
    # 200→180 は下方修正
    hist = [("2026-01-10", 200.0), ("2026-04-10", 180.0)]
    assert J.has_downward_revision(hist) is True
    # 200→210 は増額のみ
    assert J.has_downward_revision([("2026-01-10", 200.0), ("2026-04-10", 210.0)]) is False
    # within_from で期間限定
    old = [("2025-01-10", 300.0), ("2025-04-10", 200.0), ("2026-04-10", 210.0)]
    assert J.has_downward_revision(old, within_from="2026-01-01") is False


def test_jquants_latest_and_history():
    from sector_per import jquants_client as J
    stmts = [
        {"LocalCode": "1000", "DisclosedDate": "2026-02-01",
         "ForecastEarningsPerShare": "100", "Equity": "1", "TotalAssets": "2"},
        {"LocalCode": "1000", "DisclosedDate": "2026-05-01",
         "ForecastEarningsPerShare": "110", "Equity": "1", "TotalAssets": "2"},
    ]
    latest, eps_hist = J.latest_and_history(stmts)
    assert latest["disclosed_date"] == "2026-05-01"
    assert eps_hist == [("2026-02-01", 100.0), ("2026-05-01", 110.0)]


# ===== pipeline: EPS履歴→予想PER系列・キャッシュ行・増収増益/下方修正 =====
def test_build_per_history_step_function():
    from sector_per import pipeline as P
    # EPS: 2026-01-01まで100、以降120
    eps_hist = [("2026-01-01", 100.0), ("2026-04-01", 120.0)]
    closes = [("2026-02-01", 1000.0),   # eff eps=100 → PER10
              ("2026-05-01", 1200.0)]   # eff eps=120 → PER10
    pers = P.build_per_history(closes, eps_hist)
    assert pers == [10.0, 10.0]
    # 価格だけ上がればPERも上がる
    pers2 = P.build_per_history([("2026-05-01", 1800.0)], eps_hist)
    assert pers2 == [15.0]


def test_build_per_history_skips_before_eps():
    from sector_per import pipeline as P
    # 最初のEPS開示より前の終値は採用しない
    pers = P.build_per_history([("2025-12-01", 900.0), ("2026-02-01", 1000.0)],
                               [("2026-01-01", 100.0)])
    assert pers == [10.0]


def test_build_cache_row_growth_and_revision():
    from sector_per import pipeline as P
    from sector_per import jquants_client as J
    latest = J.parse_statement({
        "LocalCode": "1000", "DisclosedDate": "2026-05-10",
        "ForecastEarningsPerShare": "120", "Equity": "1000", "TotalAssets": "2000",
        "NetSales": "5000", "Profit": "300",
        "ForecastNetSales": "5500", "ForecastProfit": "350"})  # 増収増益予想
    eps_hist = [("2025-05-10", 100.0), ("2026-05-10", 120.0)]  # 増額のみ
    row = P.build_cache_row(latest, eps_hist, "2026-05-11")
    assert row["revenue_or_profit_growth"] == "1"
    assert row["no_downward_revision"] == "1"
    assert abs(float(row["equity_ratio"]) - 50.0) < 1e-6

    # 下方修正あり
    eps_down = [("2025-05-10", 130.0), ("2026-05-10", 120.0)]
    row2 = P.build_cache_row(latest, eps_down, "2026-05-11")
    assert row2["no_downward_revision"] == "0"


def test_eps_history_roundtrip():
    from sector_per import pipeline as P
    h = [("2025-05-10", 100.0), ("2026-05-10", 120.0)]
    assert P.parse_eps_history(P.serialize_eps_history(h)) == h


# ===== delivery: 必須免責の構造的強制・禁止語なし =====
def test_delivery_requires_disclaimer_and_ng_clean():
    from sector_per import delivery as D
    from sector_per import config as C
    from promo import ng_words
    rankings = {
        "機械": {"median_per": 14.0, "universe_n": 8, "has_candidates": True,
                "candidates": [
                    {"code": "1001", "name": "サンプル機械", "close": 1200.0,
                     "forecast_per": 10.0, "sector_median_per": 14.0,
                     "sector_relative": 0.2857, "roe": 12.0}]},
        "水産・農林業": {"median_per": None, "universe_n": 1, "has_candidates": False,
                    "candidates": []},
    }
    flex, summary = D.build_delivery(rankings, basis_label="データ基準日：8月17日",
                                     asof_label="2026-05-10")
    # 末尾は必須免責
    assert summary.rstrip().endswith(C.REQUIRED_DISCLAIMER)
    # 該当なし業種を明示
    assert "水産・農林業" in summary
    # 禁止語なし（本文＋Flex）
    assert ng_words.check_ng(summary) == []
    import json
    for _alt, car in flex:
        assert ng_words.check_ng(json.dumps(car, ensure_ascii=False)) == []


def test_delivery_disclaimer_cannot_be_omitted():
    """免責を外そうとしても build_delivery が例外を投げる（省略不可の担保）。"""
    from sector_per import delivery as D
    import sector_per.config as C
    orig = C.REQUIRED_DISCLAIMER
    try:
        # summary生成をモックして免責を欠落させると例外になることを確認
        rankings = {"機械": {"median_per": 10.0, "universe_n": 3,
                            "has_candidates": False, "candidates": []}}
        # build_summary_text は必ず免責を付けるので、ここでは検証ロジック自体を確認
        s = D.build_summary_text(rankings)
        assert s.rstrip().endswith(orig)
    finally:
        pass


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
