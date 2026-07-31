"""
tests/test_features.py

改善4機能のユニットテスト。

  機能1: selection_basis（選定根拠チェックリスト）
  機能2: technical_levels の下値ライン(ATR)・目安保有期間、強い免責のNG語チェック
  機能3: macro_state（前日類似度・数値変化の鮮度判定）
  機能4: performance（5立会い日保有・週次非重複・等加重複利連鎖の累積成績）

実行: python tests/test_features.py  （または pytest tests/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import macro_state          # noqa: E402
import performance          # noqa: E402
import stock_insights as si  # noqa: E402
from promo import ng_words  # noqa: E402


# ============ 機能4: performance ============
def _series(start_date, prices):
    """[(date, close), ...] 形式の連続営業日系列を作る（土日は概念上無視・立会い日扱い）。"""
    from datetime import datetime, timedelta
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    out, i = [], 0
    for p in prices:
        # 立会い日だけを積む（土日を飛ばす）
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), float(p)))
        d += timedelta(days=1)
        i += 1
    return out


def test_exit_after_sessions():
    """5立会い日後の終値を、銘柄自身の日足から正しく取り出す。"""
    ser = _series("2026-06-01", [100, 101, 102, 103, 104, 110, 111])  # index0=6/1
    ex = performance._exit_after_sessions(ser, "2026-06-01", sessions=5)
    assert ex is not None, "5本先があるのに None"
    _ed, ep = ex
    assert ep == 110.0, f"exit終値が想定と異なる: {ep}"


def test_exit_after_sessions_immature():
    """5立会い日先が無ければ未成熟(None)。"""
    ser = _series("2026-06-01", [100, 101, 102])  # 3本しかない
    assert performance._exit_after_sessions(ser, "2026-06-01", sessions=5) is None


def test_window_return():
    ser = _series("2026-06-01", [100, 101, 102, 103, 104, 110])
    r = performance._window_return(ser, "2026-06-01", ser[5][0])
    assert abs(r - 10.0) < 1e-6, f"窓リターンが想定外: {r}"


def test_cohorts_and_chain_and_dd():
    """コホート集計→非重複チェーン→累積・最大DDが一貫基準で出る。"""
    # 2コホート（5立会い日以上離す→非重複で両方採用）
    ledger = [
        # コホートA (6/1) 平均 +10%
        {"run_date": "2026-06-01", "code": "1", "rank": "1", "entry_price": "100",
         "exit_date": "2026-06-08", "exit_price": "120", "return_pct": "20.0",
         "bench_return_pct": "5.0", "status": "closed"},
        {"run_date": "2026-06-01", "code": "2", "rank": "2", "entry_price": "100",
         "exit_date": "2026-06-08", "exit_price": "100", "return_pct": "0.0",
         "bench_return_pct": "5.0", "status": "closed"},
        # コホートB (6/09, 6営業日後) 平均 -5%
        {"run_date": "2026-06-09", "code": "3", "rank": "1", "entry_price": "100",
         "exit_date": "2026-06-16", "exit_price": "90", "return_pct": "-10.0",
         "bench_return_pct": "-2.0", "status": "closed"},
        {"run_date": "2026-06-09", "code": "4", "rank": "2", "entry_price": "100",
         "exit_date": "2026-06-16", "exit_price": "100", "return_pct": "0.0",
         "bench_return_pct": "-2.0", "status": "closed"},
    ]
    cohorts = performance.cohorts_from_ledger(ledger)
    assert len(cohorts) == 2
    assert abs(cohorts[0]["cohort_return"] - 10.0) < 1e-6
    assert abs(cohorts[1]["cohort_return"] - (-5.0)) < 1e-6

    summary = performance.summarize(ledger)
    assert summary["available"]
    assert summary["chain_cohorts"] == 2
    # 複利連鎖: (1.10)*(0.95) - 1 = 0.045 → +4.5%
    assert abs(summary["cum_return"] - 4.5) < 1e-6, summary["cum_return"]
    # 日経: (1.05)*(0.98) - 1 = 0.029 → +2.9%
    assert abs(summary["cum_nikkei"] - 2.9) < 1e-6, summary["cum_nikkei"]
    # 最大DD: ピーク1.10 → 1.10*0.95=1.045 → (1.045-1.10)/1.10 = -5.0%
    assert abs(summary["max_drawdown"] - (-5.0)) < 1e-6, summary["max_drawdown"]
    # 勝率: コホート 1/2=50%、銘柄 1勝(+20)/4=25%
    assert abs(summary["cohort_win_rate"] - 50.0) < 1e-6
    assert abs(summary["pick_win_rate"] - 25.0) < 1e-6
    # 月次は 2026-06 の1件に集約
    assert len(summary["monthly"]) == 1
    assert summary["monthly"][0]["month"] == "2026-06"


def test_nonoverlap_skips_overlapping_cohorts():
    """保有が重なる（5立会い日未満しか離れていない）コホートは非重複チェーンで除外。"""
    ledger = []
    # 連続する3営業日にコホート（重複）→ 非重複チェーンは1つだけ採用されるはず
    for i, rd in enumerate(["2026-06-01", "2026-06-02", "2026-06-03"]):
        ledger.append({"run_date": rd, "code": str(i), "rank": "1",
                       "entry_price": "100", "exit_date": "2026-06-10",
                       "exit_price": "110", "return_pct": "10.0",
                       "bench_return_pct": "1.0", "status": "closed"})
    cohorts = performance.cohorts_from_ledger(ledger)
    chain = performance.nonoverlap_chain(cohorts, holding=5)
    assert len(chain) == 1, f"重複コホートを二重計上している: {len(chain)}"


def test_summarize_empty():
    assert performance.summarize([])["available"] is False


# ============ 機能3: macro_state ============
def test_ngram_jaccard_identical_and_different():
    a = "米国株は堅調。半導体・グロース関連が連想されやすい。"
    assert macro_state.ngram_jaccard(a, a) == 1.0
    b = "本日は円高が進み、輸出関連の採算に注意したい局面。"
    assert macro_state.ngram_jaccard(a, b) < 0.3


def test_freshness_drops_similar_previous_comment():
    """前日と酷似のコメントは stale（使い回し）として落とす。"""
    text = "米国株は堅調。半導体・グロース関連が連想されやすい。"
    macro = {"us_market_comment": text, "market_summary": "別の要約文です。"}
    prev = {"comments": {"us_market_comment": text}, "market": {}}
    fr = macro_state.evaluate_freshness(macro, market={}, prev_state=prev)
    assert "us_market_comment" in fr["stale_keys"]
    assert not macro_state.is_fresh(fr, "us_market_comment")
    assert macro_state.is_fresh(fr, "market_summary")


def test_freshness_drops_unchanged_market_direction():
    """為替が前日から同方向・ほぼ同値なら『変化なし』で落とす。"""
    macro = {"fx_comment": "ドル円は円安方向。輸出・海外売上が意識されやすい。"}
    prev = {"comments": {"fx_comment": "（前日は別文言）"},
            "market": {"ドル円": 0.30}}
    market = {"ドル円": {"change_pct": 0.33}}  # 前日0.30 とほぼ同じ→変化なし
    fr = macro_state.evaluate_freshness(macro, market=market, prev_state=prev)
    assert fr["reasons"].get("fx_comment") == "unchanged"


def test_freshness_keeps_changed_market():
    """為替が前日から反転（円安→円高）していれば fresh として残す。"""
    macro = {"fx_comment": "ドル円は円高方向。輸出関連の採算に留意したい。"}
    prev = {"comments": {"fx_comment": "（前日は円安の文言）"},
            "market": {"ドル円": 0.40}}
    market = {"ドル円": {"change_pct": -0.35}}  # 反転
    fr = macro_state.evaluate_freshness(macro, market=market, prev_state=prev)
    assert "fx_comment" in fr["fresh_keys"]


def test_freshness_no_previous_state_passes_all():
    macro = {"fx_comment": "ドル円は円安方向。", "market_summary": "要約。"}
    fr = macro_state.evaluate_freshness(macro, market={}, prev_state=None)
    assert fr["stale_keys"] == []
    assert macro_state.is_fresh(fr, "fx_comment")


# ============ 機能1: selection_basis ============
def _stock(metrics, **kw):
    base = {"price": 1000.0, "score": 8.0, "theme_tags": [], "metrics": metrics}
    base.update(kw)
    return base


def test_selection_basis_counts_matches():
    """一致した条件だけが具体値付きで列挙され、count/total が整合する。"""
    metrics = {"vol_ratio": 1.9, "gap_5_25": 2.0, "gap_25_75": 1.0,
               "rel_strength": 3.0, "surge_5": 4.0, "per": 12.0, "pbr": 0.9,
               "sma25": 950.0}
    s = _stock(metrics, theme_tags=["DX", "AI"], macro_reason="DXタグがAI関連と関連")
    basis = si.selection_basis(s)
    assert basis["count"] >= 6, basis
    assert basis["count"] <= basis["total"]
    # 具体値（出来高比・テーマ）が根拠文に入っている
    joined = " / ".join(basis["items"])
    assert "出来高" in joined and "テーマ該当" in joined
    assert "DX" in joined


def test_selection_basis_excludes_unevaluable():
    """データが無い条件は total から除外され、捏造しない。"""
    metrics = {"sma25": None}  # ほぼ全条件が評価不能
    s = _stock(metrics)
    basis = si.selection_basis(s)
    # 評価可能なのはテーマ該当・ニュース接点の2条件（データ不要）のみ
    assert basis["total"] == 2, basis
    assert basis["count"] == 0


# ============ 機能2: technical_levels + 免責 ============
def test_technical_levels_downside_and_holding():
    """ATR・直近安値ベースの下値ラインと目安保有期間が算出される。"""
    metrics = {"price": 1000.0, "sma5": 990.0, "sma25": 950.0, "sma75": 900.0,
               "recent_low_20": 940.0, "recent_high_20": 1050.0,
               "recent_low_60": 880.0, "recent_high_60": 1100.0, "atr14": 30.0}
    s = _stock(metrics, price=1000.0)
    t = si.technical_levels(s)
    assert t["downside"] is not None, "下値ラインが算出されない"
    assert t["downside_note"] and "根拠" in t["downside_note"]
    assert t["holding"] and "立会い日" in t["holding"]
    # 目安保有期間は正式集計(5立会い日)と一致
    assert str(performance.HOLDING_SESSIONS) in t["holding"]


def test_disclaimers_are_ng_clean():
    """強い免責・選定根拠・成績テキストが禁止語を含まないこと（NG語チェック）。"""
    import report_writer as rw
    for label, text in [("head", rw.STRONG_DISCLAIMER_HEAD),
                        ("tail", rw.STRONG_DISCLAIMER_TAIL)]:
        assert ng_words.check_ng(text) == [], f"{label}: {ng_words.check_ng(text)}"

    # 成績カード・補足テキスト・選定根拠の生成物もNG語を含まないこと
    perf = {"available": True, "cum_return": 4.5, "cum_nikkei": 2.9,
            "cum_vs_nikkei": 1.6, "cohort_win_rate": 50.0, "pick_win_rate": 25.0,
            "chain_cohorts": 2, "pick_count": 4, "max_drawdown": -5.0,
            "avg_cohort_return": 2.5,
            "monthly": [{"month": "2026-06", "cohorts": 2, "month_return": 4.5,
                         "cum_return": 4.5, "cum_vs_nikkei": 1.6,
                         "cohort_win_rate": 50.0, "avg_cohort_return": 2.5,
                         "max_drawdown": -5.0}]}
    for line in rw._performance_card_lines(perf) + rw._performance_followup_lines(perf):
        assert ng_words.check_ng(line) == [], f"perf: {line} -> {ng_words.check_ng(line)}"

    metrics = {"vol_ratio": 1.9, "gap_5_25": 2.0, "gap_25_75": 1.0,
               "rel_strength": 3.0, "surge_5": 4.0, "per": 12.0, "pbr": 0.9,
               "sma25": 950.0, "price": 1000.0, "sma5": 990.0, "sma75": 900.0,
               "recent_low_20": 940.0, "recent_high_20": 1050.0, "atr14": 30.0}
    s = _stock(metrics, theme_tags=["DX"], macro_reason="DXタグがAI関連と関連")
    for it in si.selection_basis(s)["items"]:
        assert ng_words.check_ng(it) == [], f"basis: {it} -> {ng_words.check_ng(it)}"
    t = si.technical_levels(s)
    for v in (t["downside_note"], t["holding_note"]):
        assert ng_words.check_ng(v or "") == [], f"tech: {v} -> {ng_words.check_ng(v or '')}"


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
