"""
tests/test_edge.py

edge_analysis のユニットテスト（リスク調整指標・判定ロジック・正直さのゲート）。
実行: python tests/test_edge.py  （または pytest tests/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edge_analysis as ea  # noqa: E402


def test_metrics_basic():
    xs = [1.0, -1.0, 2.0, -2.0, 1.0]
    m = ea._metrics(xs, ppy=252)
    assert m["n"] == 5
    assert abs(m["mean"] - 0.2) < 1e-9
    assert m["vol"] > 0
    assert m["win_rate_periods"] == 60.0  # 3/5 > 0
    # 最大DD は負（下落局面がある系列）
    assert m["max_dd"] < 0


def test_max_drawdown_monotonic_up_is_zero():
    assert abs(ea._max_drawdown([1.0, 1.0, 1.0])) < 1e-9


def test_small_sample_is_not_sold():
    """観測が MIN_HINT 未満なら、数字が良く見えても SAMPLE_TOO_SMALL。"""
    series = {"label": "daily",
              "strat": [2.0, 1.5, 3.0, 2.5, 2.0],   # 明らかにプラスでも…
              "bench": [-1.0, -0.5, -1.0, 0.0, -0.5],
              "wins": 20, "losses": 5, "dates": ["d1", "d5"]}
    r = ea.analyze(series)
    assert r["verdict"] == "SAMPLE_TOO_SMALL", r["verdict"]
    assert r["n"] == 5


def test_regime_warning_when_bench_negative():
    series = {"label": "daily",
              "strat": [0.1] * 25, "bench": [-0.5] * 25,
              "wins": 60, "losses": 40, "dates": ["a", "b"]}
    r = ea.analyze(series)
    assert r["regime_warn"] is True


def test_significant_positive_excess_flags_edge():
    """十分な観測数で、超過が安定してプラス（低分散）なら EDGE_LIKELY。"""
    n = 50
    series = {"label": "daily",
              "strat": [0.6] * n, "bench": [0.1] * n,  # 超過+0.5・分散ゼロに近い
              "wins": 160, "losses": 90, "dates": ["a", "b"]}
    r = ea.analyze(series)
    # 分散ゼロだと t 計算不能なので僅かに揺らす
    series["strat"] = [0.6 + (0.01 if i % 2 else -0.01) for i in range(n)]
    r = ea.analyze(series)
    assert r["excess"]["mean_excess"] > 0
    assert r["verdict"] in ("EDGE_LIKELY", "WEAK_RISK_EDGE")


def test_no_edge_when_strategy_worse():
    n = 45
    series = {"label": "daily",
              "strat": [-0.3 + (0.05 if i % 2 else -0.05) for i in range(n)],
              "bench": [0.2 + (0.05 if i % 2 else -0.05) for i in range(n)],
              "wins": 80, "losses": 130, "dates": ["a", "b"]}
    r = ea.analyze(series)
    assert r["verdict"] == "NO_EDGE", r["verdict"]
    assert r["winning_axes"] == [] or all(not a[1] for a in r["axes"])


def test_report_builds_and_is_readable():
    series = ea.load_from_daily_stats.__wrapped__ if hasattr(
        ea.load_from_daily_stats, "__wrapped__") else None
    # レポート生成が例外なく回ること（合成データで）
    s = {"label": "daily", "strat": [0.1, -0.2, 0.3], "bench": [0.0, -0.1, 0.1],
         "wins": 8, "losses": 7, "dates": ["2026-07-06", "2026-07-08"]}
    text = ea.build_report(ea.analyze(s))
    assert "エッジ検証レポート" in text and "リスク調整指標" in text


# ====== 改善A: scoring_profiles（balanced==現行 / defensive は別配点） ======
def test_profiles_sum_to_ten():
    import scoring_profiles as sp
    for p in sp.PROFILES.values():
        assert abs(sum(p["weights"].values()) - 10.0) < 1e-9


def test_get_profile_defaults_balanced():
    import scoring_profiles as sp
    assert sp.get_profile(None)["name"] == "balanced"
    assert sp.get_profile("unknown")["name"] == "balanced"
    assert sp.get_profile("defensive")["name"] == "defensive"


# ====== 改善B: stock_query（コード抽出・文面のNG語・免責） ======
def test_parse_code():
    import stock_query as sq
    assert sq.parse_code("7203") == "7203"
    assert sq.parse_code("評価 7203") == "7203"
    assert sq.parse_code("トヨタ(7203)はどう？") == "7203"
    assert sq.parse_code("130A") == "130A"
    assert sq.parse_code("こんにちは") is None


def test_query_answer_format_is_ng_clean():
    import stock_query as sq
    from promo import ng_words
    # evaluate をモックして format_answer 単体を検証（ネットワーク不要）
    evalr = {
        "ok": True, "code": "7203", "name": "サンプル自動車", "score": 7.4,
        "price": 3120.0,
        "basis": {"summary": "該当7/9件",
                  "items": ["出来高が5日平均で25日平均比 +40%", "25日線を上回って推移（+3.1%）",
                            "テーマ該当：自動車・DX"]},
        "technical": {"support": "3,040円（5日線）", "resistance": "3,180円（直近20日高値）",
                      "downside": "2,964円", "holding": "5立会い日（約1週間）"},
        "risks": ["直近高値圏で上値抵抗を意識しやすい", "日次ボラティリティ3.6%と高め"],
        "fit": {"label": "複数条件が一致"},
    }
    text = sq.format_answer(evalr)
    assert ng_words.check_ng(text) == [], ng_words.check_ng(text)
    assert "投資助言では" in text
    assert "7203" in text and "総合スコア" in text


def test_query_error_answer_is_ng_clean():
    import stock_query as sq
    from promo import ng_words
    text = sq.format_answer({"ok": False, "code": "9999", "error": "データ不足です。"})
    assert ng_words.check_ng(text) == []
    assert "9999" in text


def test_query_answer_text_no_code():
    import stock_query as sq
    out = sq.answer_text("こんにちは")
    assert "証券コード" in out


# ====== 改善②: catalyst（決算跨ぎ回避＋PEAD近似） ======
def _cat_stock(code, surge=None, vr=None, score=7.0):
    return {"code": code, "name": f"銘柄{code}", "score": score,
            "metrics": {"surge_5": surge, "vol_ratio": vr}}


def test_catalyst_penalizes_earnings_cross():
    import catalyst
    from datetime import date
    today = date(2026, 7, 22)
    cal = {"earnings_date": date(2026, 7, 27)}  # 5日後＝保有期間内
    d, notes = catalyst.catalyst_adjustment(_cat_stock("1000"), cal, today)
    assert d < 0 and any("保有期間内に決算" in n for n in notes)


def test_catalyst_bonus_pead():
    import catalyst
    from datetime import date
    today = date(2026, 7, 22)
    cal = {"earnings_date": date(2026, 7, 15)}  # 7日前＝直近決算通過
    d, notes = catalyst.catalyst_adjustment(
        _cat_stock("1000", surge=3.0, vr=1.4), cal, today)
    assert d > 0 and any("ドリフト" in n for n in notes)


def test_catalyst_fade_penalty():
    import catalyst
    from datetime import date
    today = date(2026, 7, 22)
    cal = {"earnings_date": date(2026, 7, 15)}
    d, _n = catalyst.catalyst_adjustment(
        _cat_stock("1000", surge=-5.0, vr=1.0), cal, today)
    assert d < 0


def test_catalyst_no_calendar_is_neutral():
    import catalyst
    from datetime import date
    d, notes = catalyst.catalyst_adjustment(_cat_stock("1000", surge=2, vr=1.3),
                                            {}, date(2026, 7, 22))
    assert d == 0.0 and notes == []


def test_catalyst_rerank_promotes_and_demotes():
    import catalyst
    from datetime import date
    today = date(2026, 7, 22)
    cands = [
        _cat_stock("A", score=7.5),                 # 決算跨ぎ → 減点
        _cat_stock("B", surge=3.0, vr=1.4, score=7.0),  # PEAD → 加点
        _cat_stock("C", score=7.2),                 # 中立
    ]
    cals = {"A": {"earnings_date": date(2026, 7, 24)},
            "B": {"earnings_date": date(2026, 7, 15)},
            "C": {}}
    ranked = catalyst.rerank(cands, cals, today=today, top_n=3)
    # B(7.0+0.8=7.8)がA(7.5-1.0=6.5)より上位に来る
    assert ranked[0]["code"] == "B"
    codes = [r["code"] for r in ranked]
    assert codes.index("B") < codes.index("A")
    assert all("catalyst_delta" in r for r in ranked)


def test_shadow_paths_distinct():
    import recommendation_tracker as rt
    dr, dt = rt.shadow_paths("defensive")
    cr, ct = rt.shadow_paths("catalyst")
    assert dr != cr and dt != ct
    assert "defensive" in dr and "catalyst" in cr


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
