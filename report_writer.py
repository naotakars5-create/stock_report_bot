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

import macro_analyzer
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
_BALANCE_ORDER = ["トレンド", "出来高", "相対強度", "テーマ性", "ニュース", "割安感", "安定性"]
_BALANCE_LABEL = {
    "トレンド": "トレンド", "出来高": "出来高　", "相対強度": "相対強度",
    "テーマ性": "テーマ性", "ニュース": "ニュース", "割安感": "割安感　", "安定性": "安定性　",
}
# 銘柄カード内のバランス図で使う短縮ラベル（LINEで崩れにくいように）
_CARD_AXIS_LABEL = {
    "トレンド": "トレンド", "出来高": "出来高", "相対強度": "相対", "テーマ性": "テーマ",
    "ニュース": "ニュース", "割安感": "割安", "安定性": "安定",
}
# スコア内訳（全8軸）の表示順・短縮ラベル
_BREAKDOWN_ORDER = ["トレンド", "出来高", "相対強度", "テーマ性", "ニュース",
                    "割安感", "安定性", "継続性"]
_BREAKDOWN_LABEL = {
    "トレンド": "トレンド", "出来高": "出来高", "相対強度": "相対", "テーマ性": "テーマ",
    "ニュース": "ニュース", "割安感": "割安", "安定性": "安定", "継続性": "継続",
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


def _axis_ratio(s, axis):
    """ある評価軸の「獲得点 ÷ 満点」（0〜1）。"""
    w = WEIGHTS.get(axis)
    return (s.get("details", {}).get(axis, 0) / w) if w else 0.0


def _first_meaningful_risk(s):
    """プレースホルダー以外の最初のリスクメモを返す（無ければ None）。"""
    for r in (s.get("risks") or []):
        if r and "目立ったリスク" not in r:
            return r
    return None


def _reason_technical(s):
    """テクニカル面の1行（数値があれば反映）。"""
    m = s.get("metrics", {})
    tr = _axis_ratio(s, "トレンド")
    g = m.get("gap_5_25")
    if tr >= 0.6:
        if g is not None:
            return f"短期・中期トレンドが上向き（5日線が25日線を{g:+.1f}%）"
        return "移動平均が上向きでトレンド評価は高め"
    if tr <= 0.35:
        return "移動平均の並びは整っておらずトレンドは限定的"
    return "トレンドは中立圏で方向感は限定的"


def _reason_volume(s):
    """出来高・流動性の1行（数値があれば反映）。"""
    vr = s.get("metrics", {}).get("vol_ratio")
    if vr is None:
        return "出来高は平常圏"
    if vr >= 1.2:
        return f"出来高が20日平均を上回る（約{vr:.1f}倍）で関心は上昇傾向"
    if vr < 0.8:
        return "出来高はやや低調で市場の関心は限定的"
    return "出来高は平常圏"


def _reason_news(s):
    """ニュース・テーマ性の1行。銘柄固有の macro_reason を優先。"""
    mr = s.get("macro_reason")
    if mr:
        return mr
    tags = s.get("theme_tags") or []
    if tags:
        return f"テーマ（{'・'.join(tags[:3])}）はあるが、本日のニュースとの強い関連は限定的"
    return "本日のニュースとの明確な関連は限定的"


def _reason_caution(s):
    """注意点の1行。macro_caution → リスクメモ の順で採用。"""
    return s.get("macro_caution") or _first_meaningful_risk(s) or "短期的な過熱感には注意"


def generate_score_reason(s, macro_context=None):
    """
    評価点の根拠を4要素（テクニカル／出来高／ニュース・テーマ／注意点）で生成する。

    「買い」「上昇予想」等の売買推奨表現は使わず、銘柄ごとに数値・固有テーマを反映する。
    戻り値: 文字列のリスト（カードでは「・」付きで箇条書き表示。最大4行）。
    """
    return [
        _reason_technical(s),
        _reason_volume(s),
        _reason_news(s),
        "一方で、" + _reason_caution(s),
    ]


def generate_score_reason_text(s):
    """詳細テキスト用の評価理由（3〜5文の散文）。銘柄ごとに内容を変える。"""
    m = s.get("metrics", {})
    parts = []

    g5 = m.get("gap_5_25")
    if _axis_ratio(s, "トレンド") >= 0.6:
        gap = f"（5日-25日 {g5:+.1f}%）" if g5 is not None else ""
        parts.append(f"短期移動平均が中期移動平均を上回り{gap}、トレンド評価は高め。")
    else:
        parts.append("移動平均の並びは発展途上で、トレンドは中立寄り。")

    vr, rel = m.get("vol_ratio"), m.get("rel_strength")
    vb = []
    if vr is not None:
        vb.append(f"出来高は20日平均比 約{vr:.1f}倍")
    if rel is not None:
        vb.append(f"市場平均(TOPIX)に対し20日で{rel:+.1f}pt")
    if vb:
        parts.append("、".join(vb) + "。")

    mr = s.get("macro_reason")
    if mr:
        parts.append(f"ニュース面では、{mr}。")

    parts.append("注意点として、" + _reason_caution(s) + "。")
    return " ".join(parts)


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


def format_stock_section(scored_stocks, macro_context=None):
    """スクリーニング上位銘柄の詳細（事業内容・タグ・ニュース環境・評価バランス図・評価理由・リスク）。"""
    lines = [
        "（評価点の目安: 9.0+ 条件多数／8.0+ 複数条件で強い／7.0+ 一部良い／6.0+ 監視候補）",
    ]
    for rank, s in enumerate(scored_stocks, start=1):
        en, ja = _rank_label(s["score"])
        lines.append("")
        lines.append(f"■{rank}位 {s['name']}（{s['code']}） 評価点 {s['score']:.1f}/10（{en}・{ja}）")
        lines.append(f"業種：{s.get('sector') or '—'}")
        lines.append(f"事業：{s.get('business_summary') or '—'}")
        lines.append(f"テーマタグ：{_theme_line(s)}")
        lines.append("")
        lines.append(_balance_figure(s))
        lines.append("")
        lines.append(f"スコア内訳：{_breakdown_inline(s.get('details', {}))}")
        lines.append(f"ニュース環境との関連：{s.get('news_detail') or s.get('news_line') or '本日は明確なニューステーマは限定的'}")
        lines.append(f"評価理由：{generate_score_reason_text(s)}")
        lines.append(f"リスクメモ：{_risk_memo(s)}")
    return "\n".join(lines)


def format_macro_section(macro_context):
    """今日の世界情勢・経済ニュース（市場テーマ・各種マクロコメント・主要見出し）。"""
    mc = macro_context or {}
    lines = []
    if mc.get("note"):
        lines.append(f"  {mc['note']}")
    themes = mc.get("major_themes") or mc.get("summary_themes") or []
    lines.append(f"  市場テーマ：{' / '.join(themes) if themes else '—'}")
    lines.append(f"  日本株への影響メモ：{mc.get('market_summary') or '本日の主要テーマは限定的です。'}")
    for label, key in [("為替", "fx_comment"), ("米国市場", "us_market_comment"),
                       ("金利", "rates_comment"), ("商品・資源", "commodity_comment"),
                       ("地政学", "geopolitical_comment")]:
        val = mc.get(key)
        if val:
            lines.append(f"  {label}：{val}")
    heads = mc.get("raw_headlines") or []
    if heads:
        lines.append("  主要見出し（要約・抜粋）：")
        for h in heads[:5]:
            # 著作権配慮で長い見出しは要約的に丸める
            lines.append(f"   ・{h if len(h) <= 42 else h[:42] + '…'}")
    else:
        lines.append("  主要見出し：取得できませんでした（ニュース評価は中立扱い）")
    return "\n".join(lines)


def format_theme_view(macro_context):
    """ニュースから抽出されたテーマのみ、見方を1行ずつ表示する。"""
    mc = macro_context or {}
    intensity = mc.get("theme_intensity") or {}
    if not intensity:
        return "  抽出された明確なテーマは限定的です。"
    ordered = sorted(intensity.items(), key=lambda kv: kv[1], reverse=True)
    lines = []
    for theme, _ in ordered[:6]:
        env = macro_analyzer.NEWS_ENV.get(theme, f"{theme}関連の報道が増える局面")
        lines.append(f"  ・{theme}：{env}では関連テーマが意識されやすい")
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


def build_report(market, scored_stocks, stats=None, validation=None, macro_context=None):
    """詳細レポート全文（ターミナル表示・LINEテキスト用）。"""
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    parts = [f"【{TITLE}】", SUBTITLE, now, "", DESCRIPTION, ""]

    parts.append("■ 市場概況")
    parts.append(format_market_section(market))
    parts.append(f"  市場コメント：{market_comment(market, scored_stocks, stats)}")
    parts.append("")

    parts.append("■ 今日の世界情勢・経済ニュース")
    parts.append(format_macro_section(macro_context))
    parts.append("")

    parts.append("■ テーマ別の見方")
    parts.append(format_theme_view(macro_context))
    parts.append("")

    parts.append("■ 本日のスクリーニング結果")
    parts.extend(format_screen_stats(stats))
    parts.append("")

    parts.append(f"■ スクリーニング上位{len(scored_stocks)}銘柄")
    if scored_stocks:
        parts.append(format_stock_section(scored_stocks, macro_context))
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


def build_flex_message(market, scored_stocks, stats=None, validation=None,
                       macro_context=None, now_str=None):
    """LINEの「まとめカード」（集計/市場概況/世界情勢/上位5/前回検証/注意書き）。"""
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    t = analyze_trend(market, scored_stocks, stats)

    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "14px", "contents": [
            _flex_text(TITLE, color="#FFFFFF", weight="bold", size="lg"),
            _flex_text(f"{SUBTITLE} ・ {now_str}", color="#C5D2F0", size="xs", margin="sm"),
        ],
    }

    body = []

    # スクリーニング集計（分析対象／取得成功／一次通過／最終抽出）
    body.append(_flex_text("スクリーニング", size="xs", color="#888888"))
    for label, key in [("分析対象", "universe"), ("取得成功", "primary_fetched"),
                       ("一次通過", "primary_passed"), ("最終抽出", "final")]:
        body.append({
            "type": "box", "layout": "horizontal", "contents": [
                _flex_text(label, size="sm", color="#666666", flex=4),
                _flex_text(f"{(stats or {}).get(key, 0):,}銘柄", size="sm",
                           align="end", color="#333333", flex=6),
            ],
        })

    body.append({"type": "separator", "margin": "md"})

    # 市場概況
    body.append(_flex_text("市場概況", size="xs", color="#888888", margin="md"))
    for nm in _MARKET_ORDER:
        if nm in market:
            body.append(_flex_market_row(nm, market[nm]))
    body.append(_flex_text(t["headline"] + "。", size="xs", color="#555555",
                           wrap=True, margin="sm"))

    body.append({"type": "separator", "margin": "md"})

    # 今日の世界情勢・マクロテーマ
    mc = macro_context or {}
    body.append(_flex_text("今日の主要テーマ", size="xs", color="#888888", margin="md"))
    themes = mc.get("major_themes") or mc.get("summary_themes") or []
    body.append(_flex_text(" / ".join(themes) if themes else "本日は明確なテーマは限定的",
                           size="sm", color="#1A3D7C", weight="bold", wrap=True))
    body.append(_flex_text("マクロ環境：" + (mc.get("market_summary")
                           or "本日の主要テーマは限定的です。"),
                           size="xs", color="#555555", wrap=True, margin="sm"))
    pos = mc.get("positive_theme_tags") or []
    cau = mc.get("caution_theme_tags") or []
    if pos:
        body.append(_flex_text("関連が意識されやすい: " + " / ".join(pos[:6]),
                               size="xxs", color="#2F6E4E", wrap=True, margin="sm"))
    if cau:
        body.append(_flex_text("注意したいテーマ: " + " / ".join(cau[:6]),
                               size="xxs", color="#A9772F", wrap=True, margin="xs"))
    if mc.get("note"):
        body.append(_flex_text(mc["note"], size="xxs", color="#A9772F", wrap=True, margin="xs"))

    body.append({"type": "separator", "margin": "md"})

    # スクリーニング上位（簡易ランキング。各銘柄の詳細は後続の横スライドカード）
    body.append(_flex_text(f"スクリーニング上位{len(scored_stocks)}銘柄",
                           size="xs", color="#888888", margin="md"))
    if scored_stocks:
        for i, s in enumerate(scored_stocks, start=1):
            body.append({
                "type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                    _flex_text(f"{i}. {s['name']}（{s['code']}）", size="sm",
                               color="#111111", flex=7, wrap=False),
                    _flex_text(f"{s['score']:.1f}", size="sm", weight="bold",
                               align="end", color="#0B3D91", flex=2),
                ],
            })
        body.append(_flex_text("↓ 各銘柄の詳細は次のカードを横スライドでご覧いただけます",
                               size="xxs", color="#999999", wrap=True, margin="sm"))
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


# ====== 銘柄カード（LINE Flex・横スライドカルーセル） ======
# 評価点に応じた落ち着いた配色（深い青＝強い／青緑＝標準／グレー＝注意）。
# 基調は 青・紺・グレー・白。警戒要素は薄いオレンジ/グレーで表現する。
def _card_palette(score):
    if score >= 8.5:    # 深い青系
        return {"bg": "#13335A", "accent": "#3D6FA5", "sub": "#A9C2E0",
                "risk_bg": "#FBF3EB", "risk_fg": "#9A5A22"}
    if score >= 7.5:    # 緑／青緑系
        return {"bg": "#1C5D52", "accent": "#2F8A78", "sub": "#AFD8CE",
                "risk_bg": "#FBF3EB", "risk_fg": "#9A5A22"}
    return {"bg": "#3F454D", "accent": "#6E7884", "sub": "#C5CAD1",   # グレー系
            "risk_bg": "#F1F2F4", "risk_fg": "#6B7077"}


def _score_bar10(score):
    """総合評価(0〜10)を 10ブロックのバーにする。例: 8.6 → █████████░。"""
    blocks = max(0, min(10, int(round(score))))
    return "█" * blocks + "░" * (10 - blocks)


# 評価ランク（英語表記＋日本語。投資判断に見える語は使わない）
_RANK_LABELS = [
    (8.5, "High Fit", "条件一致度が高い"),
    (7.5, "Good Fit", "複数条件が一致"),
    (6.5, "Watch", "監視候補"),
    (0.0, "Neutral", "中立"),
]


def _rank_label(score):
    for thr, en, ja in _RANK_LABELS:
        if score >= thr:
            return en, ja
    return "Neutral", "中立"


def _flex_bar(ratio, accent, track="#E9ECF1", flex=6):
    """0〜1 の比率を Flex の横棒グラフ（薄いグレーのトラック＋色付きバー）にする。"""
    pct = max(3, min(100, int(round(ratio * 100))))
    return {
        "type": "box", "layout": "vertical", "backgroundColor": track,
        "height": "8px", "cornerRadius": "4px", "flex": flex, "contents": [
            {"type": "box", "layout": "vertical", "backgroundColor": accent,
             "height": "8px", "width": f"{pct}%", "cornerRadius": "4px",
             "contents": [{"type": "filler"}]},
        ],
    }


def _card_business(s, limit=46):
    """カード用の事業内容（短縮）。業種から自動生成した場合は簡易分類と明示する。"""
    b = (s.get("business_summary") or "").strip()
    if s.get("profile_source") == "auto" or not b:
        sector = s.get("sector") or "業種不明"
        return f"{sector}・業種情報をもとにした簡易分類"
    return b if len(b) <= limit else b[:limit] + "…"


def _card_news(s):
    """カード用の「ニュース環境」1行。"""
    nl = (s.get("news_line") or "").strip()
    return nl or "本日は明確なニューステーマは限定的"


def _card_risk(s, limit=52):
    """カード用のリスクメモ（最大2件・短縮）。"""
    risks = s.get("risks") or []
    if not risks:
        return "目立ったリスクシグナルは検出されず"
    txt = "／".join(risks[:2])
    return txt if len(txt) <= limit else txt[:limit] + "…"


def _flex_kv(label, value, value_color="#333333"):
    """ラベル＋値の横並び1行（見出し整理用）。"""
    return {
        "type": "box", "layout": "horizontal", "contents": [
            _flex_text(label, size="xs", color="#8A8F98", flex=3),
            _flex_text(value, size="sm", color=value_color, flex=7, wrap=True),
        ],
    }


def _flex_section_label(text):
    return _flex_text(text, size="xs", color="#8A8F98", weight="bold", margin="md")


def _flex_balance_rows(s, accent):
    """評価バランス図（7軸・ニュース含む）を Flex の横棒グラフ行（ラベル＋バー＋数値）で返す。"""
    details = s.get("details", {})
    rows = []
    for k in _BALANCE_ORDER:
        ratio = (details.get(k, 0) / WEIGHTS[k]) if WEIGHTS.get(k) else 0
        ratio = max(0.0, min(1.0, ratio))
        rows.append({
            "type": "box", "layout": "horizontal", "alignItems": "center",
            "spacing": "sm", "margin": "sm", "contents": [
                _flex_text(_CARD_AXIS_LABEL.get(k, k), size="xs", color="#6B7280", flex=3),
                _flex_bar(ratio, accent, flex=6),
                _flex_text(f"{ratio * 5:.1f}", size="xs", color="#4A5568",
                           align="end", flex=2),
            ],
        })
    return rows


def _stock_bubble(rank, s, macro_context=None):
    """1銘柄を1枚の Flex バブル（カード）にする。評価グラフ・評価理由・ニュース環境を必ず掲載。"""
    pal = _card_palette(s["score"])
    en, ja = _rank_label(s["score"])

    # Header: 順位・銘柄名・証券コード/業種・総合評価（大きく）・評価ランク
    header = {
        "type": "box", "layout": "vertical", "backgroundColor": pal["bg"],
        "paddingAll": "18px", "spacing": "sm", "contents": [
            _flex_text(f"No.{rank} ・ スクリーニング上位銘柄", color=pal["sub"],
                       size="xs", weight="bold"),
            _flex_text(s["name"], color="#FFFFFF", size="xl", weight="bold", wrap=True),
            _flex_text(f"{s['code']} ・ {s.get('sector') or '—'}",
                       color=pal["sub"], size="xs"),
            {"type": "box", "layout": "baseline", "margin": "lg", "contents": [
                _flex_text(f"{s['score']:.1f}", color="#FFFFFF", size="3xl", weight="bold"),
                _flex_text("/ 10", color=pal["sub"], size="sm", margin="md"),
            ]},
            _flex_text("SCREENING SCORE", color=pal["sub"], size="xxs"),
            _flex_text(_score_bar10(s["score"]), color="#FFFFFF", size="sm", margin="sm"),
            {"type": "box", "layout": "baseline", "margin": "sm", "contents": [
                _flex_text(en, color="#FFFFFF", size="sm", weight="bold"),
                _flex_text("・" + ja, color=pal["sub"], size="xs", margin="sm"),
            ]},
        ],
    }

    # Body: 1 事業内容 → 2 ニュース環境 → 3 評価グラフ → 4 評価理由 → 5 リスクメモ
    body_contents = [
        _flex_section_label("事業内容"),
        _flex_text(_card_business(s), size="sm", color="#2D3540", wrap=True, margin="xs"),
        _flex_text("テーマ：" + _theme_line(s), size="xs", color="#7A828C",
                   wrap=True, margin="sm"),
        {"type": "separator", "margin": "lg"},

        _flex_section_label("ニュース環境"),
        _flex_text(_card_news(s), size="sm", color="#2D3540", wrap=True, margin="xs"),
        {"type": "separator", "margin": "lg"},

        _flex_section_label("評価グラフ"),
    ]
    body_contents.extend(_flex_balance_rows(s, pal["accent"]))
    body_contents.append({"type": "separator", "margin": "lg"})

    body_contents.append(_flex_section_label("評価理由"))
    for line in generate_score_reason(s, macro_context):
        body_contents.append(
            _flex_text("・" + line, size="xs", color="#4A5568", wrap=True, margin="xs")
        )

    # リスクメモ（薄い背景の注意ボックス）
    body_contents.append({
        "type": "box", "layout": "vertical", "backgroundColor": pal["risk_bg"],
        "cornerRadius": "8px", "paddingAll": "12px", "margin": "lg", "spacing": "xs",
        "contents": [
            _flex_text("リスクメモ", size="xxs", color=pal["risk_fg"], weight="bold"),
            _flex_text(_card_risk(s), size="xs", color=pal["risk_fg"], wrap=True),
        ],
    })

    body = {"type": "box", "layout": "vertical", "paddingAll": "18px",
            "spacing": "md", "contents": body_contents}

    footer = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "spacing": "xs",
        "backgroundColor": "#FAFAFB", "contents": [
            _flex_text("※売買推奨ではありません（公開データをもとにした機械的な抽出結果）",
                       size="xxs", color="#9AA0A6", wrap=True),
            _flex_text("詳細はテキストレポートをご参照ください",
                       size="xxs", color="#B0B5BB", wrap=True),
        ],
    }
    return {"type": "bubble", "size": "mega", "header": header,
            "body": body, "footer": footer}


def build_stock_cards(scored_stocks, macro_context=None, now_str=None):
    """
    スクリーニング上位銘柄を横スライドできる Flex カルーセルにする。

    1銘柄＝1カード（最大5枚）。各カードに 順位／銘柄名／コード／業種／事業内容／
    テーマタグ／ニュース環境／総合評価・評価ランク／評価バランス図（7軸）／
    評価理由／リスクメモ／注意書きを掲載する。
    戻り値: (alt_text, carousel_contents)。銘柄が無ければ None。
    """
    if not scored_stocks:
        return None
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    bubbles = [_stock_bubble(i, s, macro_context) for i, s in enumerate(scored_stocks, start=1)]
    carousel = {"type": "carousel", "contents": bubbles}
    alt = f"スクリーニング上位{len(bubbles)}銘柄カード（{now_str}）"
    return alt, carousel
