"""
report_writer.py

スコアリング結果を、日本語のレポート文字列に整形するモジュール。
"""

from datetime import datetime


DISCLAIMER = (
    "※これは投資助言ではなく、情報整理・スクリーニング支援です。\n"
    "  記載の評価点・想定上昇率は過去の株価データに基づく機械的な計算結果であり、\n"
    "  将来の値動きを保証するものではありません。投資判断はご自身の責任で行ってください。"
)


def _fmt_price(value):
    if value is None:
        return "取得失敗"
    return f"{value:,.1f}"


def _fmt_pct(value, sign=True):
    if value is None:
        return "—"
    if sign:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def format_market_section(market):
    """市場概況セクションを文字列で返す。"""
    lines = ["【市場概況】"]
    for name, data in market.items():
        price = data.get("price")
        change = data.get("change_pct")
        if price is None:
            lines.append(f"  {name:<8} : 取得失敗")
        else:
            lines.append(f"  {name:<8} : {price:>12,.2f}  （前日比 {_fmt_pct(change)}）")
    return "\n".join(lines)


def format_stats_section(stats):
    """スクリーニング結果サマリーのセクションを返す。"""
    if not stats:
        return ""
    lines = ["【スクリーニング結果サマリー】"]
    lines.append(f"  分析対象銘柄数          : {stats.get('universe', 0)} 件")
    lines.append(f"  一次データ取得成功数    : {stats.get('primary_fetched', 0)} 件")
    lines.append(f"  一次スクリーニング通過数: {stats.get('primary_passed', 0)} 件")
    lines.append(f"  二次データ取得成功数    : {stats.get('detail_fetched', 0)} 件")
    lines.append(f"  最終注目銘柄数          : {stats.get('final', 0)} 件")
    return "\n".join(lines)


def format_stock_section(scored_stocks):
    """注目銘柄セクションを文字列で返す。"""
    lines = ["【本日の注目銘柄 TOP{}】".format(len(scored_stocks))]
    for rank, s in enumerate(scored_stocks, start=1):
        lines.append("")
        lines.append(f"─── 第{rank}位 ────────────────────────────────")
        lines.append(f"  銘柄名     : {s['name']}")
        lines.append(f"  証券コード : {s['code']}")
        lines.append(f"  現在株価   : {_fmt_price(s['price'])} 円")
        lines.append(f"  評価点     : {s['score']:.1f} / 10.0")
        lines.append(f"  想定上昇率 : {_fmt_pct(s['expected_up_pct'])}")
        lines.append(f"  注目理由   :")
        for r in s["reasons"]:
            lines.append(f"      ・{r}")
        lines.append(f"  リスク     :")
        for risk in s["risks"]:
            lines.append(f"      ・{risk}")
    return "\n".join(lines)


def build_report(market, scored_stocks, stats=None):
    """
    レポート全文を組み立てて返す。

    引数:
        market: fetch_market_data の戻り値
        scored_stocks: score_all の戻り値
        stats: スクリーニング結果サマリー（dict、任意）
    """
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    header = (
        "============================================================\n"
        "       毎朝の日本株 AIレポート（東証スクリーニング版）\n"
        f"       生成日時: {now}\n"
        "============================================================"
    )

    parts = [header, "", format_market_section(market)]

    stats_section = format_stats_section(stats)
    if stats_section:
        parts.append("")
        parts.append(stats_section)

    if scored_stocks:
        parts.append("")
        parts.append(format_stock_section(scored_stocks))
    else:
        parts.append("")
        parts.append("【本日の注目銘柄】")
        parts.append("  有効なデータが取得できず、銘柄を選定できませんでした。")
        parts.append("  ネットワーク接続や stocks.csv の証券コードをご確認ください。")

    parts.append("")
    parts.append("------------------------------------------------------------")
    parts.append(DISCLAIMER)
    parts.append("------------------------------------------------------------")

    return "\n".join(parts)
