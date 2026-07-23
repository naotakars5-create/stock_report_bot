"""
stock_query.py

銘柄Q&A（改善B）: 読者が送った証券コードを、配信と同じスコアリングエンジンで
その場で機械的に評価し、LINE向けの文面にする。

価値の源泉:
  「当てる」ではなく「読者が指定した銘柄を、根拠つきで機械的に読む」対話性。
  無料スクリーナーには無く、既存のスコアリング・選定根拠・テクニカル節目を
  そのまま流用できるため実装が軽い。

表現方針（既存と一貫）:
  投資助言ではなく機械的な評価。売買を促す語は使わない（promo/ng_words 準拠）。
  データが取れない項目は「取得できませんでした」と正直に返す。

注意:
  本モジュールは Python（データ取得＝yfinance）が必要なため、常時稼働の
  Cloudflare Worker では実行できない。Worker は問い合わせを D1 にキューし、
  バッチ（query_worker.py）が本モジュールで回答を生成して push する構成にする。
"""

import re

import data_fetcher
import stock_scorer
import stock_insights as si


QUERY_DISCLAIMER = ("※本回答は公開データに基づく機械的な評価であり、投資助言では"
                    "ありません。表示の価格・期間は機械的に算出した参考値です。"
                    "投資はご自身の判断と責任で行ってください。")

# 東証コードは4桁数字（例:7203）に加え、新形式の英数字4文字（例:130A）も混在する。
_CODE_RE = re.compile(r"([0-9]{3}[0-9A-Z])")


def parse_code(text):
    """入力テキストから証券コード（4桁＋任意1文字）を抽出する。無ければ None。"""
    if not text:
        return None
    m = _CODE_RE.search(str(text))
    return m.group(1) if m else None


def evaluate(code, benchmark_df=None, valuation=None, name=None):
    """
    1銘柄を評価する（配信と同じ score_stock を使用）。

    戻り値: dict（score/selection_basis/technical/risks/…）またはエラー dict。
      失敗時: {"ok": False, "code", "error"}
    """
    code = (code or "").strip()
    if not code:
        return {"ok": False, "code": code, "error": "証券コードが読み取れませんでした。"}
    ticker = code if "." in code else f"{code}.T"
    df = data_fetcher._download_history(ticker, period="6mo")
    if df is None or len(df) < 75:
        return {"ok": False, "code": code,
                "error": "株価データを十分に取得できませんでした（新規上場・低流動性・"
                         "コード誤りの可能性）。"}
    if benchmark_df is None:
        benchmark_df = data_fetcher.get_benchmark_history()
    benchmark_close = benchmark_df["Close"] if (benchmark_df is not None
                                                and "Close" in benchmark_df) else None
    if valuation is None:
        try:
            valuation = data_fetcher.get_valuation(ticker)
        except Exception:
            valuation = None

    result = stock_scorer.score_stock(
        df, benchmark_close=benchmark_close, valuation=valuation)
    if result is None:
        return {"ok": False, "code": code,
                "error": "評価を計算できませんでした（データ不足）。"}
    # selection_basis / technical_levels は s 形式（metrics 等）を期待するので合わせる
    s = {"code": code, "name": name or code, "price": result["price"],
         "score": result["score"], "theme_tags": result.get("theme_tags") or [],
         "metrics": result.get("metrics") or {}, "macro_reason": None,
         "risks": result.get("risks") or [],
         "size_category": result.get("size_category") or ""}
    return {"ok": True, "code": code, "name": name or code,
            "score": result["score"], "price": result["price"],
            "basis": si.selection_basis(s), "technical": si.technical_levels(s),
            "risks": si.risk_flags(s), "fit": si.expectation_rating(s)}


def format_answer(evalr):
    """evaluate() の結果を LINE 向けテキストにする（NG語なし・免責つき）。"""
    if not evalr.get("ok"):
        return (f"【銘柄評価: {evalr.get('code','')}】\n"
                f"{evalr.get('error','評価できませんでした。')}\n\n{QUERY_DISCLAIMER}")
    b, t = evalr["basis"], evalr["technical"]
    fit = evalr["fit"]
    lines = [f"【銘柄評価: {evalr['name']}（{evalr['code']}）】",
             f"総合スコア {evalr['score']:.1f}/10（{fit['label']}）",
             f"現在値 {evalr['price']:,.0f}円",
             "",
             f"■ 条件一致（{b['summary']}）"]
    for it in b["items"][:6]:
        lines.append(f"・{it}")
    if not b["items"]:
        lines.append("・機械的条件の明確な一致は限定的")
    lines.append("")
    lines.append("■ テクニカル節目（参考値）")
    lines.append(f"・下値メド {t['support']}")
    if t.get("resistance"):
        lines.append(f"・上値メド {t['resistance']}")
    if t.get("downside"):
        lines.append(f"・参考下値ライン {t['downside']}")
    lines.append(f"・目安保有期間 {t.get('holding','—')}")
    lines.append("")
    lines.append("■ 留意点")
    for r in evalr["risks"][:3]:
        lines.append(f"・{r}")
    lines.append("")
    lines.append(QUERY_DISCLAIMER)
    return "\n".join(lines)


def answer_text(text_or_code, benchmark_df=None):
    """テキスト（またはコード）から回答文を作るワンショット関数。"""
    code = parse_code(text_or_code)
    if not code:
        return ("証券コード（4桁）を送ってください。例: 7203\n\n" + QUERY_DISCLAIMER)
    return format_answer(evaluate(code, benchmark_df=benchmark_df))


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "7203"
    print(answer_text(q))
