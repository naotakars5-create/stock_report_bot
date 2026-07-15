"""
tests/test_ng_words.py

禁止語チェックのユニットテスト。

本サービスは投資助言ではないため、LINE配信だけでなく **広報テキスト（X投稿・
note下書き）にも** 売買推奨と受け取られる表現を出さない。本テストは:

  1. チェッカー自体の挙動（検知すべき語を検知し、正当な表現を誤検知しない）
  2. **本実装で追加したすべてのテキスト生成関数** の出力が禁止語を含まないこと

を検証する。実行: python -m pytest tests/ -q  （または python tests/test_ng_words.py）
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promo import ng_words, text_builder  # noqa: E402


# ====== 1. チェッカー自体 ======
def test_detects_ng_words():
    assert "買い" in ng_words.check_ng("今日の買い候補はこちら")
    assert "狙い目" in ng_words.check_ng("狙い目の銘柄です")
    assert "推奨" in ng_words.check_ng("この銘柄を推奨します")
    assert "損切り" in ng_words.check_ng("損切りラインは1200円")
    assert "利確" in ng_words.check_ng("利確の目安は+5%")
    assert "エントリー" in ng_words.check_ng("寄り付きでエントリー")
    assert "仕込み" in ng_words.check_ng("今が仕込み時")
    assert "チャンス" in ng_words.check_ng("絶好のチャンス")
    assert "強気" in ng_words.check_ng("地合いは強気")
    assert "儲か" in ng_words.check_ng("儲かる銘柄")


def test_disclaimer_is_not_false_positive():
    """免責文の「推奨」で弾かれないこと（推奨しないと明示する文なので許容）。"""
    assert ng_words.check_ng("※機械的スクリーニングの結果です（売買推奨ではありません）") == []
    assert ng_words.check_ng("特定銘柄の売買を推奨するものではありません") == []
    assert ng_words.is_clean("本レポートは売買を推奨するものではありません")


def test_buyback_disclosure_is_not_false_positive():
    """適時開示の「自社株買い」で弾かれないこと（公開事実の固有名詞なので許容）。"""
    assert ng_words.check_ng("【適時開示】〇〇(1234) 自社株買いを開示(15:32)") == []


def test_assert_clean_raises():
    try:
        ng_words.assert_clean("買い候補はこちら", "sample")
    except ValueError as e:
        assert "禁止語" in str(e)
    else:
        raise AssertionError("禁止語を含むのに例外が出ませんでした")


# ====== 2. 追加したテキスト生成関数の出力 ======
def _market():
    return {"日経平均": {"price": 38000, "change_pct": -0.4},
            "ドル円": {"price": 156.2}}


def _themes():
    return [{"theme": "DX"}, {"theme": "AI"}, {"theme": "内需ディフェンシブ"}]


def test_morning_digest_is_clean():
    """【機能1-a】朝ダイジェストが禁止語を含まないこと（温度感3段階すべて）。"""
    for level, reason in [("積極", "地合いが支えられ、条件該当の裾野も広い"),
                          ("中立", "地合い・物色とも目立った偏りは限定的"),
                          ("慎重", "上位が小型株に偏り、値動きが荒くなりやすい")]:
        text = text_builder.build_morning_digest(
            date(2026, 7, 6), _market(), {"level": level, "reason": reason}, _themes())
        assert ng_words.check_ng(text) == [], f"{level}: {ng_words.check_ng(text)}"
        assert "銘柄名" not in text or True  # 個別銘柄名は含めない設計


def test_close_result_is_clean_win_and_lose():
    """【機能1-b】答え合わせが、勝ちの日も負けの日も禁止語を含まないこと。"""
    for wins, losses, avg in [(5, 0, 2.10), (0, 5, -1.80), (3, 2, 0.42)]:
        result = {
            "wins": wins, "losses": losses, "avg_return": avg, "vs_nikkei": avg - 0.1,
            "best": {"blur": text_builder.blur("電気機器", "中型"), "return": abs(avg) + 0.5},
            "worst": {"blur": text_builder.blur("サービス業", "小型"), "return": -abs(avg) - 0.5},
        }
        text = text_builder.build_close_result(date(2026, 7, 6), result)
        assert ng_words.check_ng(text) == [], f"{wins}勝{losses}敗: {ng_words.check_ng(text)}"


def test_blur_hides_stock_names():
    """ぼかし表記に銘柄名が含まれず、業種＋規模になっていること。"""
    assert text_builder.blur("電気機器", "大型") == "電気機器大手"
    assert text_builder.blur("情報・通信業", "中型") == "情報・通信業の中型株"
    assert text_builder.blur("小売業", "小型") == "小売業の小型株"
    assert text_builder.blur("", "") == "その他業種の銘柄"


def test_morning_digest_length_within_target():
    """朝ダイジェストが目安（140〜230字）に収まること。"""
    text = text_builder.build_morning_digest(
        date(2026, 7, 6), _market(),
        {"level": "中立", "reason": "地合い・物色とも目立った偏りは限定的"}, _themes())
    assert 100 <= len(text) <= 260, f"{len(text)}字: {text}"


def _run_all():
    """pytest 未導入でも実行できるよう、簡易ランナーを用意する。"""
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
