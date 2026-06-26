"""
report_writer.py

スクリーニング結果を、日本語レポート文字列／LINE Flexメッセージに整形するモジュール。

本サービスの位置づけ:
  「日本株 朝のスクリーニング速報」は、東証銘柄を機械的条件で抽出し、朝の情報整理に
  使うためのレポートです。**特定銘柄の売買を推奨するものではありません。**
  「おすすめ株」ではなく「スクリーニング上位銘柄」を提示します。

提供するもの:
  - build_report()        : 詳細レポート（ターミナル表示・LINEテキスト用／評価バランス図つき）
  - build_flex_message()  : LINE Flexの「まとめカード」（1分で読める短縮版）
  - analyze_trend()       : 市場全体トレンドの機械的サマリー
"""

from datetime import datetime

from stock_scorer import WEIGHTS


TITLE = "日本株 朝のスクリーニング速報"
SUBTITLE = "東証スクリーニング版"
DESCRIPTION = (
    "AIが売買判断をするものではなく、東証銘柄を機械的条件で抽出し、"
    "朝の情報整理に使うためのレポートです。"
)
DISCLAIMER = (
    "本レポートは、公開データをもとにした機械的なスクリーニング結果であり、"
    "特定銘柄の売買を推奨するものではありません。"
    "投資判断は必ずご自身の責任で行ってください。"
)

# 評価バランス図に出す軸（継続性補正は図には出さない）と、桁を揃えるためのラベル
_BALANCE_ORDER = ["トレンド", "出来高", "相対強度", "テーマ性", "割安感", "安定性"]
_BALANCE_LABEL = {
    "トレンド": "トレンド", "出来高": "出来高　", "相対強度": "相対強度",
    "テーマ性": "テーマ性", "割安感": "割安感　", "安定性": "安定性　",
}
# スコア内訳（全7軸）の表示順・短縮ラベル
_BREAKDOWN_ORDER = ["トレンド", "出来高", "相対強度", "テーマ性", "割安感", "安定性", "継続性"]
_BREAKDOWN_LABEL = {
    "トレンド": "トレンド", "出来高": "出来高", "相対強度": "相対", "テーマ性": "テーマ",
    "割安感": "割安", "安定性": "安定", "継続性": "継続",
}

_MARKET_ORDER = ["日経平均", "TOPIX", "ドル円", "S&P500", "NASDAQ"]

_C_UP = "#1DB446"
_C_DOWN = "#E03B3B"
_C_FLAT = "#555555"


# ====== 数値フォーマット補助 ======
def _fmt_price(value):
    return "取得失敗" if value is None else f"{value:,.1f}"


def _fmt_pct(value, sign=True):
    if value is None:
        return "—"
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def _fmt_ratio(value):
    return "—" if value is None else f"{value:.2f}倍"


def _color_of(value):
    if value is None or value == 0:
        return _C_FLAT
    return _C_UP if value > 0 else _C_DOWN


def _chg(market, key):
    return (market.get(key) or {}).get("change_pct")


# ====== 定性評価（売買推奨ではなく機械的な評価ラベル） ======
def _eval_trend(m):
    g5, g25 = m.get("gap_5_25"), m.get("gap_25_75")
    if g5 is None or g25 is None:
        return "トレンド不明"
    if g5 > 0 and g25 > 0:
        return "トレンド強"
    if g5 > 0 or g25 > 0:
        return "トレンドやや強"
    if g5 < 0 and g25 < 0:
        return "トレンド弱"
    return "トレンド中立"


def _eval_volume(m):
    v = m.get("vol_ratio")
    if v is None:
        return "出来高不明"
    if v >= 1.2:
        return "出来高増"
    if v >= 0.8:
        return "出来高安定"
    return "出来高減"


def _eval_relative(m):
    r = m.get("rel_strength")
    if r is None:
        return "相対不明"
    if r >= 3:
        return "相対強い"
    if r <= -3:
        return "相対弱い"
    return "相対並み"


def _eval_overheat(m):
    s = m.get("surge_5")
    if s is None:
        return "過熱感不明"
    if s >= 12:
        return "過熱注意"
    if s >= 7:
        return "過熱やや注意"
    if s <= 0:
        return "押し目圏"
    return "過熱感は限定的"


def _score_meaning(score):
    if score >= 9.0:
        return "条件がかなり揃っている"
    if score >= 8.0:
        return "複数条件で強い"
    if score >= 7.0:
        return "一部条件が良い"
    if score >= 6.0:
        return "監視候補"
    return "今回は除外水準"


def _risk_memo(s):
    risks = s.get("risks") or []
    return "／".join(risks) if risks else "目立ったリスクシグナルは検出されず"


def _theme_line(s):
    tags = s.get("theme_tags") or []
    return " / ".join(tags[:4]) if tags else "—"


def _card_tags(s):
    """カード用の短い評価タグ（最大3つ）。"""
    m = s.get("metrics", {})
    tags = []
    if "強" in _eval_trend(m):
        tags.append("トレンド強")
    if _eval_relative(m) == "相対強い":
        tags.append("相対強い")
    if s.get("size_category") == "大型":
        tags.append("大型安定")
    v = _eval_volume(m)
    if v in ("出来高増", "出来高安定"):
        tags.append(v)
    oh = _eval_overheat(m)
    if oh in ("過熱注意", "過熱やや注意"):
        tags.append(oh)
    # 不足時は中立タグで補う
    for fallback in (_eval_relative(m), _eval_volume(m), oh):
        if len(tags) >= 3:
            break
        if fallback not in tags:
            tags.append(fallback)
    return " / ".join(tags[:3])


def _bar5(ratio):
    """0〜1 の比率を ■□ 5ブロックの図にする。例: 0.8 → ■■■■□ 4/5。"""
    blocks = max(0, min(5, int(ratio * 5 + 0.5)))
    return "■" * blocks + "□" * (5 - blocks) + f" {blocks}/5"


def _balance_figure(s):
    """評価バランス図（テキスト）を返す。"""
    details = s.get("details", {})
    lines = ["評価バランス"]
    for k in _BALANCE_ORDER:
        ratio = (details.get(k, 0) / WEIGHTS[k]) if WEIGHTS.get(k) else 0
        lines.append(f"{_BALANCE_LABEL[k]}{_bar5(ratio)}")
    return "\n".join(lines)


def _breakdown_inline(details):
    return " ".join(
        f"{_BREAKDOWN_LABEL[k]}{details.get(k, 0):.1f}/{WEIGHTS[k]:.1f}"
        for k in _BREAKDOWN_ORDER
    )


# ====== 市場概況・トレンド ======
def format_market_section(market):
    """市場概況を1指標1行で返す（見出しは付けない）。"""
    lines = []
    names = [n for n in _MARKET_ORDER if n in market] + \
            [n for n in market if n not in _MARKET_ORDER]
    for name in names:
        data = market.get(name, {})
        price, chg = data.get("price"), data.get("change_pct")
        if price is None:
            lines.append(f"  {name}：取得失敗")
        elif chg is None:
            lines.append(f"  {name}：{price:,.2f}")
        else:
            lines.append(f"  {name}：{price:,.2f}（{chg:+.2f}%）")
    return "\n".join(lines)


def analyze_trend(market, scored_stocks, stats):
    """市場全体を機械的に要約。戻り値 {"headline": str}。"""
    stats = stats or {}
    topix = _chg(market, "TOPIX")
    passed = stats.get("primary_passed", 0)
    uni = stats.get("universe", 0)
    if topix is None:
        head = "市場の方向感は限定的"
    elif topix > 0.3:
        head = "国内株はしっかりの展開"
    elif topix < -0.3:
        head = "国内株は軟調な地合い"
    else:
        head = "国内株はもみ合い"
    if uni and passed:
        ratio = passed / uni * 100
        if ratio >= 5:
            head += "、上昇銘柄の裾野は広め"
        elif ratio < 2:
            head += "、物色は一部銘柄に集中"
    return {"headline": head}


def market_comment(market, scored_stocks, stats):
    t = analyze_trend(market, scored_stocks, stats)
    bits = [t["headline"]]
    sp, nq = _chg(market, "S&P500"), _chg(market, "NASDAQ")
    if sp is not None or nq is not None:
        bits.append(f"前日の米国は S&P500 {_fmt_pct(sp)}・NASDAQ {_fmt_pct(nq)}")
    uni = (stats or {}).get("universe", 0)
    passed = (stats or {}).get("primary_passed", 0)
    if uni:
        bits.append(f"上昇条件の通過は {passed}/{uni:,}銘柄({passed / uni * 100:.1f}%)")
    return "。".join(bits) + "。"


# ====== スクリーニング結果・銘柄詳細（テキスト） ======
def format_screen_stats(stats):
    stats = stats or {}
    return [
        f"  分析対象：{stats.get('universe', 0):,}銘柄",
        f"  取得成功：{stats.get('primary_fetched', 0):,}銘柄",
        f"  一次通過：{stats.get('primary_passed', 0)}銘柄",
        f"  最終抽出：{stats.get('final', 0)}銘柄",
    ]


def format_stock_section(scored_stocks):
    """スクリーニング上位銘柄の詳細（事業内容・タグ・評価バランス図・内訳・リスク・理由）。"""
    lines = [
        "（評価点の目安: 9.0+ 条件多数／8.0+ 複数条件で強い／7.0+ 一部良い／6.0+ 監視候補）",
    ]
    for rank, s in enumerate(scored_stocks, start=1):
        lines.append("")
        lines.append(f"■{rank}位 {s['name']}（{s['code']}） 評価点 {s['score']:.1f}/10")
        lines.append(f"事業：{s.get('business_summary') or '—'}")
        lines.append(f"タグ：{_theme_line(s)}")
        lines.append("")
        lines.append(_balance_figure(s))
        lines.append("")
        lines.append(f"総合：{_score_meaning(s['score'])}")
        lines.append(f"スコア内訳：{_breakdown_inline(s.get('details', {}))}")
        lines.append(f"なぜ上位に：{'／'.join(s.get('reasons') or [])}")
        lines.append(f"リスクメモ：{_risk_memo(s)}")
    return "\n".join(lines)


def format_validation_section(validation):
    if not validation:
        return "  前回データがないため、検証は次回以降に表示します"
    v = validation
    lines = [
        f"  前回({v['run_date']})上位{v['count']}銘柄の平均騰落率：{_fmt_pct(v['avg_return'])}",
    ]
    bench = v.get("benchmark_return")
    if bench is not None:
        diff = v["avg_return"] - bench
        lines.append(f"  市場平均(TOPIX)：{_fmt_pct(bench)}（市場比 {diff:+.2f}pt）")
    lines.append(f"  結果：{v['wins']}勝{v['losses']}敗")
    lines.append(f"  最も上昇：{v['best']['name']} {_fmt_pct(v['best']['return'])}")
    lines.append(f"  最も下落：{v['worst']['name']} {_fmt_pct(v['worst']['return'])}")
    return "\n".join(lines)


def _validation_summary(validation):
    if not validation:
        return "前回データなし（次回以降に表示）"
    v = validation
    bench = ""
    if v.get("benchmark_return") is not None:
        diff = v["avg_return"] - v["benchmark_return"]
        bench = f"（市場比 {diff:+.2f}pt）"
    return f"平均 {_fmt_pct(v['avg_return'])}{bench}・{v['wins']}勝{v['losses']}敗"


def build_report(market, scored_stocks, stats=None, validation=None):
    """詳細レポート全文（ターミナル表示・LINEテキスト用）。"""
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    parts = [f"【{TITLE}】", SUBTITLE, now, "", DESCRIPTION, ""]

    parts.append("■ 市場概況")
    parts.append(format_market_section(market))
    parts.append(f"  市場コメント：{market_comment(market, scored_stocks, stats)}")
    parts.append("")

    parts.append("■ 本日のスクリーニング結果")
    parts.extend(format_screen_stats(stats))
    parts.append("")

    parts.append(f"■ スクリーニング上位{len(scored_stocks)}銘柄")
    if scored_stocks:
        parts.append(format_stock_section(scored_stocks))
    else:
        parts.append("  条件を満たす銘柄は今回ありませんでした（該当なし）。")
    parts.append("")

    parts.append("■ 前回検証")
    parts.append(format_validation_section(validation))
    parts.append("")

    parts.append(DISCLAIMER)
    return "\n".join(parts)


# ====== まとめカード（LINE Flex・1分で読める短縮版） ======
def _flex_text(text, **kw):
    comp = {"type": "text", "text": str(text)}
    comp.update(kw)
    return comp


def _flex_market_row(name, data):
    price, chg = data.get("price"), data.get("change_pct")
    if price is None:
        val, color = "取得失敗", "#999999"
    elif chg is None:
        val, color = f"{price:,.2f}", _C_FLAT
    else:
        val, color = f"{price:,.2f}（{chg:+.2f}%）", _color_of(chg)
    return {
        "type": "box", "layout": "horizontal", "contents": [
            _flex_text(name, size="sm", color="#666666", flex=4),
            _flex_text(val, size="sm", align="end", color=color, flex=6, wrap=False),
        ],
    }


def _short_business(s, limit=16):
    b = s.get("business_summary") or ""
    return b if len(b) <= limit else b[:limit] + "…"


def build_flex_message(market, scored_stocks, stats=None, validation=None, now_str=None):
    """LINEの「まとめカード」（タイトル/市場概況/上位5/前回検証/注意書き）。"""
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    t = analyze_trend(market, scored_stocks, stats)

    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "14px", "contents": [
            _flex_text(TITLE, color="#FFFFFF", weight="bold", size="lg"),
            _flex_text(f"{SUBTITLE} ・ {now_str}", color="#C5D2F0", size="xs", margin="sm"),
        ],
    }

    body = [_flex_text("市場概況", size="xs", color="#888888")]
    for nm in _MARKET_ORDER:
        if nm in market:
            body.append(_flex_market_row(nm, market[nm]))
    body.append(_flex_text(t["headline"] + "。", size="xs", color="#555555",
                           wrap=True, margin="sm"))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text(f"スクリーニング上位{len(scored_stocks)}銘柄",
                           size="xs", color="#888888", margin="md"))
    if scored_stocks:
        for i, s in enumerate(scored_stocks, start=1):
            body.append({
                "type": "box", "layout": "horizontal", "margin": "md", "contents": [
                    _flex_text(f"{i}. {s['name']}（{s['code']}）", size="sm",
                               color="#111111", flex=7, wrap=False),
                    _flex_text(f"{s['score']:.1f}", size="sm", weight="bold",
                               align="end", color="#0B3D91", flex=2),
                ],
            })
            body.append(_flex_text(_short_business(s), size="xxs", color="#666666"))
            body.append(_flex_text(_card_tags(s), size="xxs", color="#888888"))
    else:
        body.append(_flex_text("条件を満たす銘柄は今回ありませんでした。",
                               size="sm", color="#555555", wrap=True))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text("前回検証", size="xs", color="#888888", margin="md"))
    body.append(_flex_text(_validation_summary(validation), size="sm",
                           color="#333333", wrap=True))

    footer = {
        "type": "box", "layout": "vertical", "paddingAll": "10px", "contents": [
            _flex_text("売買推奨ではありません。機械的なスクリーニング結果です。"
                       "投資判断はご自身の責任で。",
                       size="xxs", color="#AAAAAA", wrap=True),
        ],
    }

    bubble = {"type": "bubble", "size": "giga", "header": header,
              "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                       "spacing": "sm", "contents": body},
              "footer": footer}
    return f"{TITLE} {now_str}", bubble
