"""
tests/test_patterns.py

上昇パターン検出（patterns.detectors）と全体top N選定（patterns.screen）、
配信の必須免責・禁止語チェックの単体テスト。合成価格列でネットワーク非依存。

実行: python tests/test_patterns.py  （または pytest tests/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patterns import detectors as D    # noqa: E402
from patterns import screen as S       # noqa: E402
from patterns import config as C       # noqa: E402
from promo import ng_words             # noqa: E402


def _ohlcv(close, high=None, low=None, volume=None):
    n = len(close)
    return {
        "close": close,
        "high": high or [c * 1.01 for c in close],
        "low": low or [c * 0.99 for c in close],
        "volume": volume or [100000] * n,
    }


# ===== ゴールデンクロス =====
def test_golden_cross_detects():
    # 前半下降で25<75、後半上昇でクロスさせる
    # 80日下降 → 30日の明確な上昇（末尾で25日線が75日線を明確に上抜け）
    close = [100 - i * 0.3 for i in range(80)] + [76 + i * 1.6 for i in range(30)]
    r = D.golden_cross(_ohlcv(close), cross_lookback=25)
    assert r is not None and r["key"] == "golden_cross"
    assert 0 <= r["strength"] <= 1
    assert ng_words.check_ng(r["label"] + r["note"]) == []


def test_golden_cross_absent_in_flat():
    close = [100 + (i % 3) for i in range(120)]  # ほぼ横ばい・クロスなし
    assert D.golden_cross(_ohlcv(close)) is None


# ===== 新高値ブレイク =====
def test_new_high_breakout():
    close = [100 + (i % 5) for i in range(120)] + [130]  # 最後に高値更新
    high = [c * 1.005 for c in close]
    vol = [100000] * 120 + [300000]                       # 出来高急増
    r = D.new_high_breakout(_ohlcv(close, high=high, volume=vol))
    assert r is not None and r["key"] == "new_high_breakout"
    assert ng_words.check_ng(r["note"]) == []


def test_new_high_breakout_absent():
    close = [130 - (i % 4) for i in range(120)]  # 高値更新していない
    assert D.new_high_breakout(_ohlcv(close)) is None


# ===== 三角保ち合い上放れ =====
def test_triangle_breakout():
    # 収束: 高値切り下げ・安値切り上げ → 最後に上放れ
    base = 100
    highs, lows, closes = [], [], []
    for i in range(35):
        highs.append(base + 15 - i * 0.3)
        lows.append(base - 15 + i * 0.3)
        closes.append(base + (i % 2))
    closes[-1] = max(highs[:-1]) + 3   # 最終日レンジ上抜け
    vol = [100000] * 34 + [250000]
    r = D.triangle_breakout(_ohlcv(closes, high=highs, low=lows, volume=vol))
    assert r is not None and r["key"] == "triangle_breakout"
    assert "上放れ" in r["note"] or "上抜け" in r["note"]


# ===== 移動平均接近後の反発（押し目の語を使わない） =====
def test_ma_pullback_bounce():
    # 上昇基調(25>75) → 25日線付近まで調整 → 反発
    close = [80 + i * 0.5 for i in range(90)]  # 上昇基調でsma25>sma75
    s25 = sum(close[-25:]) / 25
    close += [s25 * 1.005, s25 * 0.995, s25 * 1.02]  # 接近して反発
    r = D.ma_pullback_bounce(_ohlcv(close))
    assert r is not None and r["key"] == "ma_pullback_bounce"
    # 禁止語「押し目」を使っていない
    assert "押し目" not in (r["label"] + r["note"])
    assert ng_words.check_ng(r["label"] + r["note"]) == []


# ===== detect_all =====
def test_detect_all_sorted_and_ng_clean():
    close = [100 + (i % 5) for i in range(120)] + [130]
    fired = D.detect_all(_ohlcv(close, volume=[100000] * 120 + [300000]))
    assert isinstance(fired, list)
    for p in fired:
        assert ng_words.check_ng(p["label"] + p["note"]) == []
    # strength 降順
    assert fired == sorted(fired, key=lambda p: p["strength"], reverse=True)


# ===== screen: 母集団・過熱除外・全体top N =====
def test_screen_overheating_excluded():
    m = {"price": 100, "avg_turnover": 5e8, "surge_5": 30.0, "above_25ma": 5}
    ok, reasons = S.pass_universe(m)
    assert not ok and any("過熱" in r for r in reasons)


def test_screen_low_turnover_excluded():
    m = {"price": 100, "avg_turnover": 1e7, "surge_5": 2.0, "above_25ma": 3}
    assert not S.pass_universe(m)[0]


def test_score_all_ranks_overall_top_n():
    # ブレイク銘柄（該当）と横ばい銘柄（非該当）
    # MIN_ROWS(130)以上・売買代金下限超・緩やかに新高値更新（過熱にならない範囲）
    bc = [100 + (i % 5) for i in range(130)] + [106, 108, 110, 111, 112]
    breakout = _ohlcv(bc, high=bc, low=bc,
                      volume=[2_000_000] * 134 + [6_000_000])
    flat = _ohlcv([100 + (i % 2) for i in range(135)], volume=[2_000_000] * 135)
    stocks = [
        {"code": "0001", "name": "ブレイク", "sector": "機械", "ohlcv": breakout},
        {"code": "0002", "name": "横ばい", "sector": "化学", "ohlcv": flat},
    ]
    ranked, all_rows = S.score_all(stocks)
    assert len(all_rows) == 2
    assert ranked and ranked[0]["code"] == "0001"
    assert ranked[0]["pattern_labels"]      # 検出パターン名が入る
    # 横ばいは検出なし＝ランキング外
    assert all(r["code"] != "0002" for r in ranked)


def test_score_all_insufficient_data():
    stocks = [{"code": "X", "name": "短命", "sector": "機械",
               "ohlcv": _ohlcv([100] * 50)}]  # MIN_ROWS未満
    ranked, all_rows = S.score_all(stocks)
    assert ranked == []
    assert all_rows[0]["in_universe"] is False


# ===== delivery: 必須免責の構造的強制・禁止語なし =====
def test_delivery_requires_disclaimer_and_ng_clean():
    from patterns import delivery as PD
    from patterns import config as PC
    ranked = [{"code": "1111", "name": "サンプル", "sector": "機械",
               "price": 1200.0, "surge_5": 3.2, "above_25ma": 4.1, "vol_ratio": 1.8,
               "pattern_labels": "新高値ブレイク・ゴールデンクロス",
               "patterns": [{"label": "新高値ブレイク", "note": "直近60日の高値を更新"},
                            {"label": "ゴールデンクロス", "note": "25日線が75日線を上抜け"}]}]
    flex, summary = PD.build_delivery(ranked, basis_label="データ基準日：8月17日")
    assert summary.rstrip().endswith(PC.REQUIRED_DISCLAIMER)
    assert ng_words.check_ng(summary) == []
    import json
    for _alt, car in flex:
        assert ng_words.check_ng(json.dumps(car, ensure_ascii=False)) == []


def test_delivery_empty_is_ng_clean_with_disclaimer():
    from patterns import delivery as PD
    from patterns import config as PC
    _flex, summary = PD.build_delivery([], basis_label="データ基準日：8月17日")
    assert summary.rstrip().endswith(PC.REQUIRED_DISCLAIMER)
    assert "検出はありませんでした" in summary
    assert ng_words.check_ng(summary) == []


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
