"""
report_writer.py

スクリーニング結果を、日本語レポート文字列／LINE Flexメッセージに整形するモジュール。

本サービスの位置づけ:
  「日本株 朝のスクリーニング速報」は、東証銘柄を機械的条件で抽出し、朝の情報整理に
  使うためのレポートです。**特定銘柄の売買を推奨するものではありません。**
  「おすすめ株」ではなく「スクリーニング上位銘柄」を提示します。

LINE配信は「カード中心」の構成です:
  1. build_flex_message()  : サマリーカード（集計・市場概況・テーマ・上位5・検証）
  2. build_stock_cards()   : 上位5銘柄の横スライドカード（銘柄詳細はここに集約）
  3. build_followup_text() : 短い補足テキスト（ニュース／テーマ／検証のみ・最大1500字）

提供するもの:
  - build_flex_message()   : LINE Flexの「サマリーカード」（1分で読める短縮版）
  - build_stock_cards()    : 銘柄別の横スライドカルーセル（銘柄詳細を集約）
  - build_followup_text()  : カードと重複しない短い補足テキスト（LINE用）
  - build_fallback_text()  : Flex送信失敗時のみ使う短縮テキスト（銘柄概要＋補足）
  - build_report()         : 長文の詳細レポート。**LINEでは送らず**、ターミナル表示
                             および将来のWeb版／PDF版用に残している
  - analyze_trend()        : 市場全体トレンドの機械的サマリー
"""

from datetime import datetime

import macro_analyzer
import stock_insights as si
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
    return " / ".join(tags[:3]) if tags else "—"


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


def _disp(s, axis):
    """表示用の相対スコア(0〜1)。無ければ絶対値(獲得点÷満点)。"""
    d = s.get("display_ratios") or {}
    return d.get(axis, _axis_ratio(s, axis))


# 評価軸ごとの加点・減点フレーズ
_AXIS_POS = {
    "トレンド": "短期・中期トレンドが上向き",
    "出来高": "出来高が20日平均を上回る",
    "相対強度": "市場平均(TOPIX)に対して相対的に強い",
    "テーマ性": "業種テーマ性が評価されている",
    "ニュース": "ニュース環境との接点がある",
    "割安感": "PER/PBR等から割高感は限定的",
    "安定性": "値動きが比較的安定している",
}
_AXIS_NEG = {
    "トレンド": "移動平均の並びが整っていない",
    "出来高": "出来高は低調で市場の関心は限定的",
    "相対強度": "市場平均(TOPIX)に対して見劣りする",
    "テーマ性": "業種テーマ性は限定的",
    "ニュース": "本日のニュースとの関連は限定的",
    "割安感": "バリュエーションは割高感がある",
    "安定性": "安定性スコアが低い（値動きが荒い）",
}
_POS_ORDER = ["トレンド", "出来高", "相対強度", "テーマ性", "ニュース", "割安感", "安定性"]


def generate_watch_reason(s, macro_context=None):
    """カード用「今日見る理由」（事業/テーマ＋テクニカル/出来高を最低2要素・1文）。"""
    tags = s.get("theme_tags") or []
    m = s.get("metrics", {})
    vr = m.get("vol_ratio")
    trend_strong = _disp(s, "トレンド") >= 0.58
    rel_strong = _disp(s, "相対強度") >= 0.58
    vol_strong = vr is not None and vr >= 1.2

    if tags:
        head = f"{'・'.join(tags[:3])}テーマとの接点があり"
    elif s.get("sector"):
        head = f"{s['sector']}領域の事業を背景に"
    else:
        head = "スクリーニング条件を背景に"

    if vol_strong and (trend_strong or rel_strong):
        tail = "出来高増を伴って" + ("短期トレンドが強い" if trend_strong else "相対強度が高い")
    elif trend_strong:
        tail = "短期トレンドが強い"
    elif rel_strong:
        tail = "相対強度が高い"
    elif vol_strong:
        tail = "出来高が増加している"
    else:
        tail = "複数の評価軸で条件がそろう"

    news = "、ニュース環境とも関連" if s.get("macro_reason") else ""
    return f"{head}、{tail}{news}。"


def positive_reasons(s, macro_context=None):
    """加点理由（2〜3個）。相対的に強い軸＋ニュース接点から、数値を添えて生成。"""
    m = s.get("metrics", {})
    ranked = sorted(((a, _disp(s, a)) for a in _POS_ORDER), key=lambda x: x[1], reverse=True)
    items = []
    for a, v in ranked:
        if v < 0.58:
            continue
        phrase = _AXIS_POS[a]
        if a == "トレンド" and m.get("gap_5_25") is not None:
            phrase += f"（5日線が25日線を{m['gap_5_25']:+.1f}%）"
        elif a == "出来高" and m.get("vol_ratio") is not None:
            phrase += f"（約{m['vol_ratio']:.1f}倍）"
        elif a == "相対強度" and m.get("rel_strength") is not None:
            phrase += f"（20日{m['rel_strength']:+.1f}pt）"
        items.append(phrase)
        if len(items) >= 3:
            break
    if s.get("macro_reason") and len(items) < 3:
        items.append(s["macro_reason"])
    return items[:3] or ["総合スコアが抽出水準を満たす"]


def negative_reasons(s):
    """減点理由（1〜2個）。相対的に弱い軸＋リスク・注意点から生成。無ければ限定的と表示。"""
    m = s.get("metrics", {})
    ranked = sorted(((a, _disp(s, a)) for a in _POS_ORDER), key=lambda x: x[1])
    items = []
    for a, v in ranked:
        if v > 0.42:
            continue
        phrase = _AXIS_NEG[a]
        if a == "安定性" and m.get("volatility") is not None:
            phrase += f"（日次ボラ{m['volatility']:.1f}%）"
        items.append(phrase)
        if len(items) >= 2:
            break
    if len(items) < 2:
        r = _first_meaningful_risk(s)
        if r and r not in items:
            items.append(r)
    if s.get("macro_caution") and len(items) < 2:
        items.append(s["macro_caution"])
    return items[:2] or ["大きな減点要素は限定的"]


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
    """評価バランス図（テキスト）。相対スコア(display_ratios)で強弱を出す。"""
    disp = s.get("display_ratios") or {}
    details = s.get("details", {})
    lines = ["評価バランス（候補内の相対評価）"]
    for k in _BALANCE_ORDER:
        ratio = disp.get(k, (details.get(k, 0) / WEIGHTS[k]) if WEIGHTS.get(k) else 0)
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
    """スクリーニング上位銘柄の詳細（今日見る理由・事業・ニュース環境・加点/減点・内訳・リスク）。"""
    lines = [
        "（評価点の目安: 9.0+ 条件多数／8.0+ 複数条件で強い／7.0+ 一部良い／6.0+ 監視候補）",
    ]
    for rank, s in enumerate(scored_stocks, start=1):
        en, ja = _rank_label(s["score"])
        lines.append("")
        lines.append(f"■{rank}位 {s['name']}（{s['code']}） 評価点 {s['score']:.1f}/10（{en}・{ja}）")
        lines.append(f"業種：{s.get('sector') or '—'}")
        lines.append(f"今日見る理由：{generate_watch_reason(s, macro_context)}")
        lines.append(f"事業：{s.get('business_summary') or '—'}")
        lines.append(f"テーマタグ：{_theme_line(s)}")
        lines.append(f"ニュース環境との関連：{s.get('news_detail') or s.get('news_line') or '本日は明確なニューステーマは限定的'}")
        lines.append("")
        lines.append(_balance_figure(s))
        lines.append("")
        lines.append(f"スコア内訳：{_breakdown_inline(s.get('details', {}))}")
        lines.append("加点理由：")
        for r in positive_reasons(s, macro_context):
            lines.append(f"  ＋ {r}")
        lines.append("減点理由：")
        for r in negative_reasons(s):
            lines.append(f"  － {r}")
        lines.append(f"リスクメモ：{_risk_memo(s)}")
    return "\n".join(lines)


def format_theme_ranking_text(theme_ranking):
    """今日強いテーマ＋テーマ別スクリーニング上位（詳細テキスト用）。"""
    if not theme_ranking:
        return "  テーマ集計は蓄積中です。"
    lines = ["  今日強いテーマ（スクリーニング上位の集計）："]
    for i, t in enumerate(theme_ranking[:5], start=1):
        lines.append(f"   {i}. {t['theme']}（該当{t['count']}銘柄・平均{t['avg_score']:.1f}点）")
    lines.append("  テーマ別スクリーニング上位（確認銘柄）：")
    for t in theme_ranking[:5]:
        names = "、".join(f"{x['name']}（{x['code']}）" for x in t["stocks"][:3])
        lines.append(f"   ・{t['theme']}：{names or '—'}")
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


def format_validation_section(validations):
    """検証結果（前回・3営業日前・1週間前を、データがある範囲で）。"""
    validations = [v for v in (validations or []) if v]
    if not validations:
        return "  検証データは蓄積中です（次回以降に表示します）。"
    lines = []
    for v in validations:
        head = f"  {v['label']}（{v['run_date']}）上位{v['total']}銘柄"
        if v["evaluated"] < v["total"]:
            head += f"（取得可能な{v['evaluated']}銘柄のみで検証）"
        lines.append(head + "：")
        lines.append(f"    {v['wins']}勝{v['losses']}敗・平均騰落率 {_fmt_pct(v['avg_return'])}")
        comps = []
        if v.get("vs_nikkei") is not None:
            comps.append(f"日経平均比 {v['vs_nikkei']:+.2f}pt")
        if v.get("vs_topix") is not None:
            comps.append(f"TOPIX比 {v['vs_topix']:+.2f}pt")
        if comps:
            lines.append("    " + "・".join(comps))
        lines.append(f"    最も上昇：{v['best']['name']} {_fmt_pct(v['best']['return'])}"
                     f"／最も下落：{v['worst']['name']} {_fmt_pct(v['worst']['return'])}")
    return "\n".join(lines)


def _validation_summary_lines(validations):
    """まとめカード用の前回検証（前回＝直近の1回を短く）。"""
    validations = [v for v in (validations or []) if v]
    if not validations:
        return ["検証データは蓄積中です（次回以降に表示）"]
    v = validations[0]
    out = [f"前回上位{v['total']}銘柄：{v['wins']}勝{v['losses']}敗"]
    out.append(f"平均騰落率：{_fmt_pct(v['avg_return'])}")
    if v.get("vs_nikkei") is not None:
        out.append(f"日経平均比：{v['vs_nikkei']:+.2f}pt")
    elif v.get("vs_topix") is not None:
        out.append(f"TOPIX比：{v['vs_topix']:+.2f}pt")
    return out


# ====== 短い補足テキスト（LINE用・カードと重複しない） ======
def _clip_text(text, limit):
    """LINEで読みやすいよう、上限文字数で安全に丸める。"""
    if text is None:
        return ""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _followup_macro_bullets(market, scored_stocks, stats, macro_context):
    """補足テキスト用のマクロ環境（2〜4行）。重複（部分一致含む）を避け、短い文だけを採る。"""
    mc = macro_context or {}
    bullets = []

    def _add(v):
        v = (v or "").strip()
        if not v:
            return
        # 既存の文に含まれる／既存の文を含む場合は重複とみなしてスキップ。
        # （market_summary が us/fx コメントを連結していることがあるため）
        for b in bullets:
            if v in b or b in v:
                return
        bullets.append(v)

    _add(mc.get("market_summary"))
    for key in ("us_market_comment", "fx_comment", "commodity_comment",
                "geopolitical_comment"):
        _add(mc.get(key))
    if not bullets:
        _add(analyze_trend(market, scored_stocks, stats)["headline"] + "。")
    return bullets[:4]


def _followup_validation_text(validations):
    """補足テキスト用の検証結果（最大3期間・勝敗／平均／市場比のみ・銘柄別は出さない）。"""
    validations = [v for v in (validations or []) if v]
    if not validations:
        return "検証データは蓄積中です（次回以降に表示します）。"
    blocks = []
    for v in validations[:3]:
        if v["label"] == "前回":
            head = f"前回上位{v['total']}銘柄：{v['wins']}勝{v['losses']}敗"
        else:
            head = f"{v['label']}：{v['wins']}勝{v['losses']}敗"
        lines = [head, f"平均騰落率：{_fmt_pct(v['avg_return'])}"]
        if v.get("vs_nikkei") is not None:
            lines.append(f"日経平均比：{v['vs_nikkei']:+.2f}pt")
        elif v.get("vs_topix") is not None:
            lines.append(f"TOPIX比：{v['vs_topix']:+.2f}pt")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _followup_sections(market, scored_stocks, stats, validations,
                       macro_context, theme_ranking):
    """補足テキストの本文セクション（ニュース／今日強いテーマ／テーマ別確認銘柄／検証）。"""
    lines = []
    lines.append("■ 今日のニュース・マクロ環境")
    for b in _followup_macro_bullets(market, scored_stocks, stats, macro_context):
        lines.append(f"・{b}")
    lines.append("")

    lines.append("■ 今日強いテーマ")
    if theme_ranking:
        for i, t in enumerate(theme_ranking[:5], start=1):
            lines.append(f"{i}. {t['theme']}")
    else:
        lines.append("（テーマ集計は蓄積中です）")
    lines.append("")

    lines.append("■ テーマ別確認銘柄")
    rows = []
    for t in (theme_ranking or [])[:5]:
        names = "、".join(x["name"] for x in t["stocks"][:3])
        if names:
            rows.append(f"{t['theme']}：{names}")
    lines.extend(rows or ["（集計は蓄積中です）"])
    lines.append("")

    lines.append("■ 検証結果")
    lines.append(_followup_validation_text(validations))
    return lines


def build_followup_text(market, scored_stocks, stats=None, validations=None,
                        macro_context=None, theme_ranking=None, now_str=None,
                        basis_label=None):
    """
    カードの後に送る「短い補足テキスト」（最大1500字・目安1000字以内）。

    銘柄ごとの詳細（評価グラフ・加点/減点・ニュース環境・リスクメモ）は
    すべて横スライドカードに集約しているため、ここでは繰り返さない。
    含めるのは: 今日のニュース・マクロ環境／今日強いテーマ／テーマ別確認銘柄／検証結果。
    """
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    head2 = basis_label or f"{SUBTITLE} ・ {now_str}"
    parts = ["【補足レポート】", head2, ""]
    parts.extend(_followup_sections(
        market, scored_stocks, stats, validations, macro_context, theme_ranking))
    parts.append("")
    parts.append("※本レポートは公開データをもとにした機械的なスクリーニング結果であり、"
                 "特定銘柄の売買を推奨するものではありません。")
    return _clip_text("\n".join(parts), 1500)


def build_fallback_text(market, scored_stocks, stats=None, validations=None,
                        macro_context=None, theme_ranking=None, now_str=None,
                        basis_label=None):
    """
    Flexカードの送信に失敗したときだけ使う短縮テキスト。

    カードが届かないため、上位銘柄を1行ずつ（名称・コード・評価点・テーマ）だけ補い、
    続けて補足テキスト本文を載せる。長文にはせず、最大1500字に収める。
    """
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    head2 = basis_label or f"{SUBTITLE} ・ {now_str}"
    parts = [f"【{TITLE}】（カード表示の代替・短縮版）", head2, ""]
    parts.append(f"■ スクリーニング上位{len(scored_stocks)}銘柄")
    if scored_stocks:
        for i, s in enumerate(scored_stocks, start=1):
            _en, ja = _rank_label(s["score"])
            parts.append(f"{i}. {s['name']}（{s['code']}） {s['score']:.1f}/10・{ja}")
            tags = _theme_line(s)
            if tags and tags != "—":
                parts.append(f"   テーマ：{tags}")
    else:
        parts.append("条件を満たす銘柄は今回ありませんでした。")
    parts.append("")
    parts.extend(_followup_sections(
        market, scored_stocks, stats, validations, macro_context, theme_ranking))
    parts.append("")
    parts.append("※公開データをもとにした機械的なスクリーニング結果であり、"
                 "特定銘柄の売買を推奨するものではありません。")
    return _clip_text("\n".join(parts), 1500)


def build_report(market, scored_stocks, stats=None, validations=None,
                 macro_context=None, theme_ranking=None, basis_label=None):
    """
    長文の詳細レポート全文（ターミナル表示・将来のWeb版／PDF版用）。

    注意: **LINE配信では使用しない**。LINEはカード中心（サマリーカード＋銘柄カルーセル
    ＋短い補足テキスト build_followup_text()）で構成し、銘柄詳細はカードに集約する。
    この関数はターミナルでの確認用、および将来の別チャネル（Web/PDF）向けに残している。
    """
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    parts = [f"【{TITLE}】", SUBTITLE, basis_label or "", f"配信 {now}", "", DESCRIPTION, ""]

    parts.append("■ 今日の市場概況")
    parts.append(format_market_section(market))
    parts.append(f"  市場コメント：{market_comment(market, scored_stocks, stats)}")
    parts.append("")

    parts.append("■ 今日の世界情勢・経済ニュース")
    parts.append(format_macro_section(macro_context))
    parts.append("")

    parts.append("■ 今日強いテーマ・テーマ別スクリーニング上位")
    parts.append(format_theme_ranking_text(theme_ranking))
    parts.append("")

    parts.append("■ テーマ別の見方")
    parts.append(format_theme_view(macro_context))
    parts.append("")

    parts.append("■ 本日のスクリーニング結果")
    parts.extend(format_screen_stats(stats))
    parts.append("")

    parts.append(f"■ スクリーニング上位{len(scored_stocks)}銘柄の詳細")
    if scored_stocks:
        parts.append(format_stock_section(scored_stocks, macro_context))
    else:
        parts.append("  条件を満たす銘柄は今回ありませんでした（該当なし）。")
    parts.append("")

    parts.append("■ 検証結果（過去のスクリーニング上位5銘柄の追跡）")
    parts.append(format_validation_section(validations))
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


# 相場判定・結論の★ボックス配色（段階が高いほど落ち着いた緑、低いほど暖色寄り）
def _judgment_palette(n):
    return {
        5: {"bg": "#EAF5EF", "fg": "#1C6B4A", "star": "#1C6B4A"},
        4: {"bg": "#EDF6F1", "fg": "#2F7D5A", "star": "#2F7D5A"},
        3: {"bg": "#EEF2F8", "fg": "#3A5578", "star": "#3A5578"},
        2: {"bg": "#FBF4E7", "fg": "#9A6B1E", "star": "#C8912F"},
        1: {"bg": "#FAEFEB", "fg": "#9A4B33", "star": "#B65C40"},
    }.get(n, {"bg": "#EEF2F8", "fg": "#3A5578", "star": "#3A5578"})


def _flex_star_box(title, stars_str, label, lines):
    """『今日の相場判定』『今日の結論』用の目立つ★ボックス。lines は説明の箇条書き。"""
    n = stars_str.count("★")
    pal = _judgment_palette(n)
    contents = [
        _flex_text(title, size="xs", color=pal["fg"], weight="bold"),
        {"type": "box", "layout": "baseline", "margin": "sm", "contents": [
            _flex_text(stars_str, size="lg", color=pal["star"], flex=0),
            _flex_text(label, size="sm", color=pal["fg"], weight="bold", margin="md", wrap=True),
        ]},
    ]
    for ln in lines:
        contents.append(_flex_text(ln, size="xxs", color="#5A6472", wrap=True, margin="xs"))
    return {
        "type": "box", "layout": "vertical", "backgroundColor": pal["bg"],
        "cornerRadius": "10px", "paddingAll": "14px", "spacing": "xs",
        "margin": "md", "contents": contents,
    }


def build_flex_message(market, scored_stocks, stats=None, validations=None,
                       macro_context=None, theme_ranking=None, now_str=None,
                       basis_label=None):
    """
    LINEの「サマリーカード」（トップページ）。1分で全体像がつかめる構成。

    今日の相場判定（★・非投資助言の市場環境判定）→ 集計 → 市場概況 →
    今日の主要テーマ＋最重要テーマの理由 → 今日強いテーマ → 本日のスクリーニング傾向
    → 上位5ランキング → 検証結果 → 今日の結論（★）→ 注意書き。

    basis_label には「データ基準日：M月D日 大引け時点」を渡す（データ鮮度の明示）。
    """
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    t = analyze_trend(market, scored_stocks, stats)
    judgment = si.market_judgment(market, stats)
    conclusion = si.daily_conclusion(scored_stocks, stats, judgment)
    strategy = si.daily_strategy(scored_stocks, theme_ranking, market, stats)
    top_theme = si.top_theme_reason(theme_ranking, macro_context)

    header_contents = [_flex_text(TITLE, color="#FFFFFF", weight="bold", size="lg")]
    if basis_label:
        header_contents.append(_flex_text(basis_label, color="#FFFFFF", size="sm",
                                          weight="bold", margin="sm"))
    header_contents.append(
        _flex_text(f"{SUBTITLE} ・ 配信 {now_str}", color="#C5D2F0", size="xxs", margin="xs"))
    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "14px", "contents": header_contents,
    }

    body = []

    # 【今日の相場判定】（★・非投資助言の市場環境判定。1分で全体像がつかめるよう最上部に）
    body.append(_flex_star_box("今日の相場判定", judgment["stars"],
                               judgment["label"], judgment["reasons"]))

    # スクリーニング集計（分析対象／取得成功／一次通過／最終抽出）
    body.append(_flex_text("スクリーニング", size="xs", color="#888888", margin="md"))
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
    # 今日最重要テーマ：順位だけでなく「なぜそのテーマか」を100字以内で
    if top_theme:
        body.append(_flex_text(f"◎ 最重要テーマ：{top_theme['theme']}", size="xs",
                               color="#1A3D7C", weight="bold", margin="sm"))
        body.append(_flex_text(top_theme["reason"], size="xxs", color="#5A6472",
                               wrap=True, margin="xs"))
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

    # 今日強いテーマ（スクリーニング集計）
    if theme_ranking:
        body.append({"type": "separator", "margin": "md"})
        body.append(_flex_text("今日強いテーマ（スクリーニング）", size="xs",
                               color="#888888", margin="md"))
        body.append(_flex_text(
            " / ".join(f"{t2['theme']}({t2['count']})" for t2 in theme_ranking[:5]),
            size="sm", color="#1A3D7C", wrap=True))

    # 本日のスクリーニング傾向（機械的な観察・売買指示ではない）
    if strategy:
        body.append({"type": "separator", "margin": "md"})
        body.append(_flex_text("本日のスクリーニング傾向", size="xs",
                               color="#888888", margin="md"))
        for line in strategy:
            body.append(_flex_bullet(line))

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
    body.append(_flex_text("検証結果（前回上位5銘柄）", size="xs", color="#888888", margin="md"))
    for vl in _validation_summary_lines(validations):
        body.append(_flex_text(vl, size="sm", color="#333333", wrap=True))

    # 【今日の結論】（★・非投資助言。スクリーニング環境として一言で締める）
    body.append(_flex_star_box("今日の結論", conclusion["stars"],
                               conclusion["label"], [conclusion["reason"]]))

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


def _card_business(s, limit=52):
    """カード用の事業内容（短縮）。固有の事業説明をできるだけ自然に表示する。"""
    b = (s.get("business_summary") or "").strip()
    if not b:
        sector = s.get("sector") or "—"
        return f"{sector}領域で事業を展開する企業"
    return b if len(b) <= limit else b[:limit] + "…"


def _card_news(s, limit=44):
    """カード用の「ニュース環境」1行（長すぎる場合は短縮）。"""
    nl = (s.get("news_line") or "").strip() or "本日は明確なニューステーマは限定的"
    return nl if len(nl) <= limit else nl[:limit] + "…"


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


def _flex_icon_head(icon, text, color="#5A6472"):
    """アイコン付きの小見出し（📊 需給・資金 のような1行・軽量な単一テキスト）。"""
    return _flex_text(f"{icon} {text}", size="xs", color=color, weight="bold", margin="lg")


def _flex_bullet(text, color="#3C4450", dot="・"):
    """箇条書き1行（先頭に中黒・軽量な単一テキスト）。読みやすさのため wrap 有効。"""
    return _flex_text(dot + text, size="xs", color=color, wrap=True, margin="xs")


def _flex_kv_line(label, value, label_color="#8A8F98", value_color="#2D3540"):
    """ラベル（左・固定幅）＋値（右・可変）の1行。テクニカル節目などに使用。"""
    return {
        "type": "box", "layout": "baseline", "spacing": "sm", "margin": "xs",
        "contents": [
            _flex_text(label, size="xs", color=label_color, flex=2),
            _flex_text(value, size="xs", color=value_color, flex=5, wrap=True),
        ],
    }


def _flex_balance_rows(s, accent):
    """評価グラフ（7軸）を Flex の横棒グラフ行で返す。相対スコアで強弱を出す。"""
    disp = s.get("display_ratios") or {}
    details = s.get("details", {})
    rows = []
    for k in _BALANCE_ORDER:
        ratio = disp.get(k, (details.get(k, 0) / WEIGHTS[k]) if WEIGHTS.get(k) else 0)
        ratio = max(0.0, min(1.0, ratio))
        rows.append({
            "type": "box", "layout": "horizontal", "alignItems": "center",
            "spacing": "sm", "margin": "sm", "contents": [
                _flex_text(_CARD_AXIS_LABEL.get(k, k), size="xs", color="#6B7280", flex=2),
                _flex_bar(ratio, accent, flex=6),
            ],
        })
    return rows


def _stock_bubble(rank, s, macro_context=None, include_graph=True):
    """
    1銘柄を1枚の Flex バブル（カード）にする。銘柄詳細をここに集約し、重複を排除する。

    include_graph=False のときは評価グラフ（相対7軸バー）を省く。カルーセル全体が
    LINEの50KB上限に近づいた場合に、確実に配信するためのサイズ保護に使う。

    構成（アイコン＋色分け＋箇条書きでスキャンしやすく）:
      ヘッダー（銘柄名/コード・業種/総合評価＋★/適合ランク）
      → 事業・テーマ・ニュース（各1行）
      → 📊 需給・資金（機関投資家視点）
      → 📅 決算・イベント（取得可能な日程／予想比はデータ未対応）
      → 📈 テクニカル節目（参考・売買推奨ではない）
      → 評価グラフ（相対7軸）
      → ⭐ スクリーニング適合度（＝旧「期待値」を非投資助言で表現）
      → ⚠️ リスク
    """
    pal = _card_palette(s["score"])
    en, ja = _rank_label(s["score"])
    cal = s.get("calendar")
    inst = si.institutional_view(s)
    tech = si.technical_levels(s)
    ev = si.earnings_view(s, cal)
    events = si.event_view(s, cal)
    fit = si.expectation_rating(s)
    star_str = si.stars(si.fit_stars(s["score"]))

    # Header: 順位・銘柄名・コード・業種・総合評価（大きく＋★）・適合ランク
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
                _flex_text(star_str, color="#FFD479", size="sm", align="end"),
            ]},
            {"type": "box", "layout": "baseline", "margin": "sm", "contents": [
                _flex_text("SCREENING SCORE", color=pal["sub"], size="xxs", flex=0),
                _flex_text(f"{en}・{ja}", color="#FFFFFF", size="xs", align="end", wrap=True),
            ]},
        ],
    }

    body_contents = []

    # 事業・テーマ・ニュース（重複を避け、各1行に集約）
    body_contents.append(_flex_text(_card_business(s), size="sm", color="#2D3540", wrap=True))
    body_contents.append(_flex_kv_line("テーマ", _theme_line(s)))
    body_contents.append(_flex_kv_line("ニュース", _card_news(s)))

    # 📊 需給・資金（機関投資家視点・機械的推定）
    body_contents.append(_flex_icon_head("📊", "需給・資金（機械的推定）"))
    body_contents.append(_flex_bullet(inst["volume"]))
    body_contents.append(_flex_bullet(inst["money"]))
    body_contents.append(_flex_bullet(inst["supply"]))

    # 📅 決算・イベント（取得可能な日程のみ）
    body_contents.append(_flex_icon_head("📅", "決算・イベント"))
    body_contents.append(_flex_bullet(ev["date_line"]))
    body_contents.append(_flex_bullet(ev["beat_line"], color="#8A8F98"))
    for it in events["items"]:
        body_contents.append(_flex_bullet(it, color=("#3C4450" if events["has_event"] else "#8A8F98")))

    # 📈 テクニカル節目（参考・売買推奨ではない）
    body_contents.append(_flex_icon_head("📈", "テクニカル節目（参考）"))
    body_contents.append(_flex_kv_line("下値メド", tech["support"]))
    body_contents.append(_flex_kv_line("上値メド", tech["resistance"]))
    body_contents.append(_flex_text(f"直近レンジ {tech['range']}／{tech['note']}",
                                    size="xxs", color="#AEB4BC", wrap=True, margin="xs"))

    # 評価グラフ（相対7軸）※サイズ保護時は省略
    if include_graph:
        body_contents.append(_flex_icon_head("📐", "評価グラフ（候補内の相対評価）"))
        body_contents.extend(_flex_balance_rows(s, pal["accent"]))

    # ⭐ スクリーニング適合度（＝旧「期待値」を非投資助言で表現）
    body_contents.append(_flex_icon_head("⭐", "スクリーニング適合度"))
    body_contents.append({
        "type": "box", "layout": "baseline", "margin": "xs", "contents": [
            _flex_text(fit["stars"], color="#E8A93B", size="md", flex=0),
            _flex_text(fit["label"], size="xs", color="#3C4450", margin="sm"),
        ],
    })
    body_contents.append(_flex_text("理由：" + fit["reason"], size="xxs",
                                    color="#7A828C", wrap=True, margin="xs"))

    # ⚠️ リスク（薄い背景の注意ボックス）
    risk_box = {
        "type": "box", "layout": "vertical", "backgroundColor": pal["risk_bg"],
        "cornerRadius": "8px", "paddingAll": "12px", "margin": "lg", "spacing": "xs",
        "contents": [_flex_text("⚠️ リスク", size="xxs", color=pal["risk_fg"], weight="bold")],
    }
    for r in si.risk_flags(s, cal)[:3]:
        risk_box["contents"].append(_flex_bullet(r, color=pal["risk_fg"]))
    body_contents.append(risk_box)

    body = {"type": "box", "layout": "vertical", "paddingAll": "18px",
            "spacing": "sm", "contents": body_contents}

    footer = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "spacing": "xs",
        "backgroundColor": "#FAFAFB", "contents": [
            _flex_text("※売買推奨ではありません（公開データをもとにした機械的な抽出結果）",
                       size="xxs", color="#9AA0A6", wrap=True),
            _flex_text("← → 横スライドで他の銘柄も確認できます",
                       size="xxs", color="#B0B5BB", wrap=True),
        ],
    }
    return {"type": "bubble", "size": "mega", "header": header,
            "body": body, "footer": footer}


def build_stock_cards(scored_stocks, macro_context=None, now_str=None):
    """
    スクリーニング上位銘柄を横スライドできる Flex カルーセルにする。

    1銘柄＝1カード（最大5枚）。各カードに 銘柄名／コード／業種／総合評価＋★／
    適合ランク／事業・テーマ・ニュース（各1行）／📊需給・資金（機械的推定）／
    📅決算・イベント（取得可能な日程・予想比は未対応と明示）／📈テクニカル節目
    （参考）／評価グラフ（相対7軸）／⭐スクリーニング適合度／⚠️リスク／注意書き
    を、アイコン・色分け・箇条書きで重複なく集約する。
    戻り値: (alt_text, carousel_contents)。銘柄が無ければ None。
    """
    if not scored_stocks:
        return None
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    bubbles = [_stock_bubble(i, s, macro_context) for i, s in enumerate(scored_stocks, start=1)]
    carousel = {"type": "carousel", "contents": bubbles}

    # サイズ保護: LINE Flex は 1メッセージ 50KB 上限。長い銘柄名等で近づいた場合は、
    # 最も重い評価グラフを外して確実に配信する（グラフはサマリーカードでも代替可能）。
    import json
    if len(json.dumps(carousel, ensure_ascii=False).encode("utf-8")) > 49000:
        print("[カード] カルーセルが50KBに近いため、評価グラフを省いて軽量化します。")
        bubbles = [_stock_bubble(i, s, macro_context, include_graph=False)
                   for i, s in enumerate(scored_stocks, start=1)]
        carousel = {"type": "carousel", "contents": bubbles}

    alt = f"スクリーニング上位{len(bubbles)}銘柄カード（{now_str}）"
    return alt, carousel
