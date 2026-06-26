"""
report_writer.py

スクリーニング結果を、日本語のレポート文字列／LINE Flexメッセージに整形するモジュール。

本サービスの位置づけ:
  「日本株 朝のスクリーニング速報」は、東証銘柄を機械的条件で抽出し、
  朝の情報整理に使うためのレポートです。**特定銘柄の売買を推奨するものではありません。**
  「おすすめ株」ではなく「スクリーニング上位銘柄」を提示します。

提供するもの:
  - build_report()          : 詳細レポート（ターミナル表示・LINEテキスト用）
  - build_flex_message()    : LINE Flexの「まとめカード」（1分で読める短縮版）
  - build_detail_carousels(): 銘柄別の評価カード（横スワイプ・スコアをバーで可視化）
  - analyze_trend()         : 市場全体トレンドの機械的サマリー
"""

from collections import Counter
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

# スコア内訳の表示順と短縮ラベル
_SCORE_ORDER = ["短期トレンド", "中期トレンド", "出来高", "相対強さ", "過熱回避", "安定性"]
_SHORT_LABEL = {
    "短期トレンド": "短期", "中期トレンド": "中期", "出来高": "出来高",
    "相対強さ": "相対", "過熱回避": "過熱", "安定性": "安定",
}

# 市場概況に表示する指標の順序
_MARKET_ORDER = ["日経平均", "TOPIX", "ドル円", "S&P500", "NASDAQ"]

# Flex の配色
_C_UP = "#1DB446"
_C_DOWN = "#E03B3B"
_C_FLAT = "#555555"
_C_AMBER = "#F5A623"


# ====== 数値フォーマット補助 ======
def _fmt_price(value):
    if value is None:
        return "取得失敗"
    return f"{value:,.1f}"


def _fmt_pct(value, sign=True):
    if value is None:
        return "—"
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def _fmt_ratio(value):
    if value is None:
        return "—"
    return f"{value:.2f}倍"


def _fmt_signed(value, suffix="%"):
    if value is None:
        return "—"
    return f"{value:+.1f}{suffix}"


def _fmt_plain(value, suffix="%"):
    if value is None:
        return "—"
    return f"{value:.1f}{suffix}"


def _color_of(value):
    if value is None or value == 0:
        return _C_FLAT
    return _C_UP if value > 0 else _C_DOWN


def _avg(values):
    xs = [v for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def _chg(market, key):
    return (market.get(key) or {}).get("change_pct")


# ====== 定性評価（売買推奨ではなく機械的なトレンド/出来高/相対/過熱の評価） ======
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


def _risk_memo(s):
    risks = s.get("risks") or []
    return "／".join(risks) if risks else "目立ったリスクシグナルは検出されず"


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


def _breakdown_inline(details):
    """6観点の獲得点を1行（短縮ラベル＋取得点/満点）にまとめて返す。"""
    return " ".join(
        f"{_SHORT_LABEL[k]}{details.get(k, 0):.1f}/{WEIGHTS[k]:.1f}" for k in _SCORE_ORDER
    )


def _metrics_inline(m):
    if not m:
        return ""
    return " / ".join([
        f"5-25日{_fmt_signed(m.get('gap_5_25'))}",
        f"25-75日{_fmt_signed(m.get('gap_25_75'))}",
        f"出来高{_fmt_ratio(m.get('vol_ratio'))}",
        f"対TOPIX{_fmt_signed(m.get('rel_strength'), 'pt')}",
        f"5日{_fmt_signed(m.get('surge_5'))}",
        f"ボラ{_fmt_plain(m.get('volatility'))}",
    ])


# ====== 市場概況・トレンド ======
def format_market_section(market):
    """市場概況の指標を3つずつインラインで返す（見出しは付けない）。"""
    parts = []
    names = [n for n in _MARKET_ORDER if n in market] + \
            [n for n in market if n not in _MARKET_ORDER]
    for name in names:
        data = market.get(name, {})
        price = data.get("price")
        chg = data.get("change_pct")
        if price is None:
            parts.append(f"{name} 取得失敗")
        elif chg is None:
            parts.append(f"{name} {price:,.2f}")
        else:
            parts.append(f"{name} {price:,.2f}({chg:+.2f}%)")
    lines = []
    for i in range(0, len(parts), 3):
        lines.append("  " + " ｜ ".join(parts[i:i + 3]))
    return "\n".join(lines)


def _trend_word(p, up, down, flat, th=0.3):
    if p is None:
        return flat
    if p > th:
        return up
    if p < -th:
        return down
    return flat


def analyze_trend(market, scored_stocks, stats):
    """市場全体を機械的に要約。戻り値 {"headline": str, "bullets": [str,...]}。"""
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

    bullets = []
    bullets.append(f"国内: TOPIX連動ETF 前日比 {_fmt_pct(topix)}")
    sp, nq = _chg(market, "S&P500"), _chg(market, "NASDAQ")
    if sp is not None or nq is not None:
        bullets.append(f"米国(前日): S&P500 {_fmt_pct(sp)} / NASDAQ {_fmt_pct(nq)}")
    return {"headline": head, "bullets": bullets}


def market_comment(market, scored_stocks, stats):
    """市場概況に添える1〜2文の機械的コメント。"""
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
        f"  分析対象銘柄数          : {stats.get('universe', 0):,} 件",
        f"  データ取得成功数        : {stats.get('primary_fetched', 0):,} 件",
        f"  一次スクリーニング通過数: {stats.get('primary_passed', 0)} 件",
        f"  最終抽出銘柄数          : {stats.get('final', 0)} 件",
    ]


def _stock_oneline(rank, s):
    """LINE/カード用の1行表記。例: 1. 三菱重工 8.6 トレンド強 / 出来高増 / 過熱やや注意"""
    m = s.get("metrics", {})
    tags = f"{_eval_trend(m)} / {_eval_volume(m)} / {_eval_overheat(m)}"
    return f"{rank}. {s['name']} {s['score']:.1f} {tags}"


def format_stock_section(scored_stocks):
    """スクリーニング上位銘柄（詳細・定性評価＋スコア内訳＋リスクメモ）。"""
    lines = [
        "（評価点の目安: 9.0+ 条件多数／8.0+ 複数条件で強い／7.0+ 一部良い／6.0+ 監視候補）",
    ]
    for rank, s in enumerate(scored_stocks, start=1):
        m = s.get("metrics", {})
        sector = s.get("sector", "")
        sec = f"・{sector}" if sector else ""
        lines.append("")
        lines.append(
            f"■{rank}位 {s['name']}({s['code']}){sec}  "
            f"株価 {_fmt_price(s['price'])}円  評価点 {s['score']:.1f}/10"
        )
        lines.append(
            f"  トレンド評価: {_eval_trend(m)} ｜ 出来高評価: {_eval_volume(m)} ｜ "
            f"相対強度: {_eval_relative(m)} ｜ 過熱感: {_eval_overheat(m)}"
        )
        lines.append(f"  スコア内訳: {_breakdown_inline(s.get('details', {}))}")
        lines.append(f"  リスクメモ: {_risk_memo(s)}")
    return "\n".join(lines)


def format_validation_section(validation):
    """前回レポートの検証セクション（テキスト）。"""
    if not validation:
        return "  前回データがないため、検証は次回以降に表示します"
    v = validation
    lines = [
        f"  前回({v['run_date']})上位{v['count']}銘柄の平均騰落率: {_fmt_pct(v['avg_return'])}",
    ]
    if v.get("benchmark_return") is not None:
        lines.append(f"  同期間の市場平均(TOPIX): {_fmt_pct(v['benchmark_return'])}")
    lines.append(f"  結果: {v['wins']}勝{v['losses']}敗")
    lines.append(f"  最も上昇: {v['best']['name']} {_fmt_pct(v['best']['return'])}")
    lines.append(f"  最も下落: {v['worst']['name']} {_fmt_pct(v['worst']['return'])}")
    return "\n".join(lines)


def _validation_summary(validation):
    """カード用の1行サマリー。"""
    if not validation:
        return "前回データなし（次回以降に表示）"
    v = validation
    bench = ""
    if v.get("benchmark_return") is not None:
        bench = f"（市場 {_fmt_pct(v['benchmark_return'])}）"
    return f"平均 {_fmt_pct(v['avg_return'])}{bench}・{v['wins']}勝{v['losses']}敗"


def build_report(market, scored_stocks, stats=None, validation=None):
    """詳細レポート全文を組み立てて返す（ターミナル表示・LINEテキスト用）。"""
    now = datetime.now().strftime("%Y/%m/%d %H:%M")

    parts = [f"【{TITLE}】", f"{SUBTITLE}  {now}", "", DESCRIPTION, ""]

    parts.append("■ 今日の市場概況")
    parts.append(format_market_section(market))
    parts.append(f"  市場コメント: {market_comment(market, scored_stocks, stats)}")
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

    parts.append("■ 前回レポートの検証")
    parts.append(format_validation_section(validation))
    parts.append("")

    parts.append(DISCLAIMER)
    return "\n".join(parts)


# ====== Flex 共通部品 ======
def _flex_text(text, **kw):
    comp = {"type": "text", "text": str(text)}
    comp.update(kw)
    return comp


def _flex_market_row(name, data):
    price = data.get("price")
    chg = data.get("change_pct")
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


# ====== #6 まとめカード（1分で読める短縮版） ======
def build_flex_message(market, scored_stocks, stats=None, validation=None, now_str=None):
    """LINEの「まとめカード」（タイトル/市場概況/上位10/前回検証/注意書き）。"""
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    t = analyze_trend(market, scored_stocks, stats)

    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "14px", "contents": [
            _flex_text(TITLE, color="#FFFFFF", weight="bold", size="lg"),
            _flex_text(f"{SUBTITLE} ・ {now_str}", color="#C5D2F0",
                       size="xs", margin="sm"),
        ],
    }

    body = [
        _flex_text("今日の市場概況", size="xs", color="#888888"),
    ]
    for nm in _MARKET_ORDER:
        if nm in market:
            body.append(_flex_market_row(nm, market[nm]))
    body.append(_flex_text(t["headline"] + "。", size="xs", color="#555555",
                           wrap=True, margin="sm"))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text(f"スクリーニング上位{len(scored_stocks)}銘柄", size="xs",
                           color="#888888", margin="md"))
    if scored_stocks:
        for i, s in enumerate(scored_stocks, start=1):
            m = s.get("metrics", {})
            body.append({
                "type": "box", "layout": "horizontal", "contents": [
                    _flex_text(f"{i}. {s['name']}", size="sm", color="#111111",
                               flex=7, wrap=False),
                    _flex_text(f"{s['score']:.1f}", size="sm", weight="bold",
                               align="end", color="#0B3D91", flex=2),
                ],
            })
            body.append(_flex_text(
                f"{_eval_trend(m)} / {_eval_volume(m)} / {_eval_overheat(m)}",
                size="xxs", color="#888888"))
    else:
        body.append(_flex_text("条件を満たす銘柄は今回ありませんでした。",
                               size="sm", color="#555555", wrap=True))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text("前回レポートの検証", size="xs", color="#888888", margin="md"))
    body.append(_flex_text(_validation_summary(validation), size="sm",
                           color="#333333", wrap=True))

    footer = {
        "type": "box", "layout": "vertical", "paddingAll": "10px", "contents": [
            _flex_text("売買推奨ではありません。機械的なスクリーニング結果です。"
                       "投資判断はご自身の責任で。",
                       size="xxs", color="#AAAAAA", wrap=True),
        ],
    }

    bubble = {"type": "bubble", "size": "giga",
              "header": header,
              "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                       "spacing": "sm", "contents": body},
              "footer": footer}
    alt = f"{TITLE} {now_str}"
    return alt, bubble


# ====== 銘柄ごとの評価カード（カルーセル：スコアをバーで可視化） ======
def _bar_color(ratio):
    if ratio >= 0.7:
        return _C_UP
    if ratio >= 0.4:
        return _C_AMBER
    return _C_DOWN


def _flex_score_bar(label, val, maxv):
    ratio = (val / maxv) if maxv else 0.0
    pct = max(3, min(100, round(ratio * 100)))
    bar = {
        "type": "box", "layout": "vertical", "height": "8px",
        "backgroundColor": "#E8E8E8", "cornerRadius": "4px", "contents": [
            {"type": "box", "layout": "vertical", "height": "8px",
             "width": f"{pct}%", "backgroundColor": _bar_color(ratio),
             "cornerRadius": "4px", "contents": [{"type": "filler"}]},
        ],
    }
    return {
        "type": "box", "layout": "horizontal", "alignItems": "center",
        "spacing": "sm", "contents": [
            _flex_text(label, flex=3, size="xs", color="#666666"),
            {"type": "box", "layout": "vertical", "flex": 6, "contents": [bar]},
            _flex_text(f"{val:.1f}/{maxv:g}", flex=3, size="xs",
                       align="end", color="#333333"),
        ],
    }


def _eval_row(label, value):
    return {
        "type": "box", "layout": "horizontal", "contents": [
            _flex_text(label, size="xs", color="#888888", flex=4),
            _flex_text(value, size="xs", color="#333333", align="end", flex=6),
        ],
    }


def build_detail_bubble(rank, s):
    """1銘柄の評価カード（bubble）。売買推奨ではなく機械的評価の可視化。"""
    sector = s.get("sector", "")
    sec = f"・{sector}" if sector else ""
    m = s.get("metrics", {})

    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "12px", "contents": [
            {"type": "box", "layout": "horizontal", "alignItems": "center",
             "contents": [
                 _flex_text(f"第{rank}位", color="#C5D2F0", size="sm", flex=4,
                            gravity="center"),
                 _flex_text(f"{s['score']:.1f}/10", color="#FFFFFF", size="xl",
                            weight="bold", align="end", flex=6),
             ]},
            _flex_text(f"{s['name']}({s['code']}){sec}", color="#FFFFFF",
                       size="md", weight="bold", wrap=True, margin="sm"),
            _flex_text(_score_meaning(s["score"]), color="#C5D2F0", size="xxs",
                       margin="sm"),
        ],
    }

    body = [
        _flex_text(f"株価 {_fmt_price(s['price'])}円", size="sm", color="#333333"),
        {"type": "separator", "margin": "md"},
        _flex_text("スコア内訳（取得点/満点）", size="xs", color="#888888", margin="md"),
    ]
    details = s.get("details", {})
    for k in _SCORE_ORDER:
        body.append(_flex_score_bar(_SHORT_LABEL[k], details.get(k, 0), WEIGHTS[k]))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text("評価", size="xs", color="#888888", margin="md"))
    body.append(_eval_row("トレンド評価", _eval_trend(m)))
    body.append(_eval_row("出来高評価", _eval_volume(m)))
    body.append(_eval_row("相対強度", _eval_relative(m)))
    body.append(_eval_row("過熱感", _eval_overheat(m)))

    metrics = _metrics_inline(m)
    if metrics:
        body.append(_flex_text("参考指標: " + metrics, size="xxs",
                               color="#999999", wrap=True, margin="md"))

    body.append({"type": "separator", "margin": "md"})
    body.append(_flex_text("リスクメモ", size="xs", color="#888888", margin="md"))
    for r in (s.get("risks") or ["特記事項なし"]):
        body.append(_flex_text("・" + r, size="xs", color="#B3261E", wrap=True))

    return {
        "type": "bubble", "size": "mega", "header": header,
        "body": {"type": "box", "layout": "vertical", "paddingAll": "12px",
                 "spacing": "sm", "contents": body},
    }


def build_detail_carousels(scored_stocks, per_carousel=6):
    """
    銘柄別の評価カードを、LINEのサイズ上限(50KB/メッセージ)に収まるよう
    複数のカルーセル（横スワイプ）に分割して返す。

    戻り値: [(alt_text, contents), ...]  銘柄が無ければ []。
    """
    if not scored_stocks:
        return []
    stocks = scored_stocks[:12]
    carousels = []
    for start in range(0, len(stocks), per_carousel):
        chunk = stocks[start:start + per_carousel]
        bubbles = [build_detail_bubble(start + i + 1, s) for i, s in enumerate(chunk)]
        alt = f"スクリーニング上位の評価カード（{start + 1}〜{start + len(chunk)}位・横スワイプ）"
        carousels.append((alt, {"type": "carousel", "contents": bubbles}))
    return carousels
