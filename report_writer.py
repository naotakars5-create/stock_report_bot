"""
report_writer.py

スコアリング結果を、日本語のレポート文字列／LINE Flexメッセージに整形するモジュール。

提供するもの:
  - build_report()        : ターミナル表示・LINEテキスト用の全文レポート
  - build_flex_message()  : LINE Flexメッセージ（リッチなまとめカード）
  - analyze_trend()       : 市場全体トレンドの機械的サマリー
"""

from collections import Counter
from datetime import datetime

from stock_scorer import WEIGHTS


DISCLAIMER = (
    "※これは投資助言ではなく、情報整理・スクリーニング支援です。\n"
    "  記載の評価点・想定上昇率は過去の株価データに基づく機械的な計算結果であり、\n"
    "  将来の値動きを保証するものではありません。投資判断はご自身の責任で行ってください。"
)

# スコア内訳の表示順
_SCORE_ORDER = ["短期トレンド", "中期トレンド", "出来高", "相対強さ", "過熱回避", "安定性"]

# Flex の配色
_C_UP = "#1DB446"     # プラス（緑）
_C_DOWN = "#E03B3B"   # マイナス（赤）
_C_FLAT = "#555555"   # 中立


# ====== 数値フォーマット補助 ======
def _fmt_price(value):
    if value is None:
        return "取得失敗"
    return f"{value:,.1f}"


def _fmt_pct(value, sign=True):
    if value is None:
        return "—"
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def _fmt_signed(value, suffix="%"):
    if value is None:
        return "—"
    return f"{value:+.1f}{suffix}"


def _fmt_plain(value, suffix="%"):
    if value is None:
        return "—"
    return f"{value:.1f}{suffix}"


def _fmt_ratio(value):
    if value is None:
        return "—"
    return f"{value:.2f}倍"


def _color_of(value):
    if value is None:
        return _C_FLAT
    if value > 0:
        return _C_UP
    if value < 0:
        return _C_DOWN
    return _C_FLAT


def _avg(values):
    xs = [v for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def _chg(market, key):
    return (market.get(key) or {}).get("change_pct")


# ====== 市場概況・サマリー ======
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


# ====== #2 全体トレンド ======
def _trend_word(p, up, down, flat, th=0.3):
    if p is None:
        return flat
    if p > th:
        return up
    if p < -th:
        return down
    return flat


def analyze_trend(market, scored_stocks, stats):
    """
    市場全体のトレンドを機械的に要約する。

    戻り値:
        {"headline": "一言サマリー", "bullets": ["箇条書き", ...]}
    """
    stats = stats or {}
    bullets = []

    topix = _chg(market, "TOPIX(連動ETF)")
    sp = _chg(market, "S&P500")
    nq = _chg(market, "NASDAQ")
    usd = market.get("ドル円") or {}
    usd_price, usd_chg = usd.get("price"), usd.get("change_pct")

    # 国内（基準は安定して取れる TOPIX連動ETF）
    dom = _trend_word(topix, "上昇し堅調", "下落し軟調", "ほぼ横ばい")
    bullets.append(f"国内市場: TOPIX連動ETFは前日比 {_fmt_pct(topix)} で{dom}。")

    # 海外（前日の米国市場）
    us = []
    if sp is not None:
        us.append(f"S&P500 {_fmt_pct(sp)}")
    if nq is not None:
        us.append(f"NASDAQ {_fmt_pct(nq)}")
    if us:
        usdir = _trend_word(_avg([sp, nq]), "堅調", "軟調", "まちまち")
        bullets.append(f"海外市場: {' / '.join(us)}（前日の米国市場は{usdir}）。")

    # 為替
    if usd_price is not None:
        fx = _trend_word(usd_chg, "やや円安方向", "やや円高方向", "横ばい圏", th=0.1)
        bullets.append(f"為替: ドル円 {usd_price:,.2f}（前日比 {_fmt_pct(usd_chg)}）。{fx}。")

    # 物色の広がり（一次スクリーニング通過率）
    uni = stats.get("universe", 0)
    passed = stats.get("primary_passed", 0)
    if uni:
        ratio = passed / uni * 100
        breadth = "幅広い物色" if ratio >= 5 else ("中程度の物色" if ratio >= 2 else "選別色の強い")
        bullets.append(
            f"物色の広がり: 全{uni:,}銘柄中、上昇条件を満たしたのは{passed}銘柄"
            f"({ratio:.1f}%)で、{breadth}地合い。"
        )

    # 注目業種・TOP10平均
    if scored_stocks:
        secs = Counter(s.get("sector", "") for s in scored_stocks if s.get("sector"))
        if secs:
            top = "、".join(f"{k}{v}" for k, v in secs.most_common(3))
            bullets.append(f"注目業種(TOP10内): {top}。")
        avg_score = _avg([s["score"] for s in scored_stocks])
        avg_up = _avg([s["expected_up_pct"] for s in scored_stocks])
        if avg_score is not None:
            bullets.append(
                f"TOP10平均: 評価点 {avg_score:.1f} / 想定上昇率 {_fmt_pct(avg_up)}。"
            )

    # 一言サマリー
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

    return {"headline": head, "bullets": bullets}


def format_trend_section(market, scored_stocks, stats):
    """全体トレンドのセクションを文字列で返す。"""
    t = analyze_trend(market, scored_stocks, stats)
    lines = ["【本日の全体トレンド】", f"  {t['headline']}。"]
    for b in t["bullets"]:
        lines.append(f"  ・{b}")
    return "\n".join(lines)


# ====== #1 銘柄ごとの詳細解説 ======
def _format_breakdown(details):
    """6観点の獲得点を『観点 a/b』形式で2行に整形して返す。"""
    parts = [f"{k} {details.get(k, 0):.1f}/{WEIGHTS[k]:.1f}" for k in _SCORE_ORDER]
    return [" ｜ ".join(parts[:3]), " ｜ ".join(parts[3:])]


def _stock_comment(s):
    """評価点と観点の強弱から、機械的な総合コメントを生成する。"""
    details = s.get("details", {})
    ratios = {k: (details.get(k, 0) / WEIGHTS[k] if WEIGHTS[k] else 0) for k in WEIGHTS}
    strong = [k for k in _SCORE_ORDER if ratios.get(k, 0) >= 0.8]
    weak = [k for k in _SCORE_ORDER if ratios.get(k, 0) <= 0.45]

    score = s["score"]
    if score >= 8.5:
        msg = "総合評価は高水準。"
    elif score >= 7.5:
        msg = "総合評価は良好。"
    else:
        msg = "総合評価は中位。"
    if strong:
        msg += f"特に「{'・'.join(strong)}」が強み。"
    if weak:
        msg += f"一方で「{'・'.join(weak)}」は弱め。"
    elif strong:
        msg += "目立った弱点は見られない。"
    return msg


def _format_metrics(metrics):
    """テクニカル指標の生値を3行に整形して返す。"""
    if not metrics:
        return []
    return [
        f"5日線−25日線: {_fmt_signed(metrics.get('gap_5_25'))}"
        f"   25日線−75日線: {_fmt_signed(metrics.get('gap_25_75'))}",
        f"出来高比(5日/25日): {_fmt_ratio(metrics.get('vol_ratio'))}"
        f"   対TOPIX(20日): {_fmt_signed(metrics.get('rel_strength'), 'pt')}",
        f"5日騰落: {_fmt_signed(metrics.get('surge_5'))}"
        f"   日次ボラ: {_fmt_plain(metrics.get('volatility'))}",
    ]


def format_stock_section(scored_stocks):
    """注目銘柄セクション（詳細版）を文字列で返す。"""
    lines = ["【本日の注目銘柄 TOP{}】".format(len(scored_stocks))]
    for rank, s in enumerate(scored_stocks, start=1):
        sector = s.get("sector", "")
        sector_str = f"   業種: {sector}" if sector else ""
        lines.append("")
        lines.append(f"─── 第{rank}位 ────────────────────────────────")
        lines.append(f"  銘柄名     : {s['name']}（{s['code']}）{sector_str}")
        lines.append(f"  現在株価   : {_fmt_price(s['price'])} 円")
        lines.append(
            f"  評価点     : {s['score']:.1f} / 10.0    "
            f"想定上昇率 {_fmt_pct(s['expected_up_pct'])}"
        )

        lines.append("  ▼ スコア内訳（各観点の獲得点）")
        for bl in _format_breakdown(s.get("details", {})):
            lines.append(f"      {bl}")

        metric_lines = _format_metrics(s.get("metrics", {}))
        if metric_lines:
            lines.append("  ▼ テクニカル指標")
            for ml in metric_lines:
                lines.append(f"      {ml}")

        lines.append("  ▼ 注目理由")
        for r in s["reasons"]:
            lines.append(f"      ・{r}")
        lines.append("  ▼ リスク")
        for risk in s["risks"]:
            lines.append(f"      ・{risk}")

        lines.append("  ▼ 総合コメント")
        lines.append(f"      {_stock_comment(s)}")
    return "\n".join(lines)


def build_report(market, scored_stocks, stats=None):
    """
    レポート全文を組み立てて返す（ターミナル表示・LINEテキスト用）。
    """
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    header = (
        "============================================================\n"
        "       毎朝の日本株 AIレポート（東証スクリーニング版）\n"
        f"       生成日時: {now}\n"
        "============================================================"
    )

    parts = [header, "", format_market_section(market)]

    # #2 全体トレンド（市場概況のすぐ後）
    parts.append("")
    parts.append(format_trend_section(market, scored_stocks, stats))

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
        parts.append("  ネットワーク接続や銘柄一覧をご確認ください。")

    parts.append("")
    parts.append("------------------------------------------------------------")
    parts.append(DISCLAIMER)
    parts.append("------------------------------------------------------------")

    return "\n".join(parts)


# ====== #3 LINE Flexメッセージ ======
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


def _flex_stock_row(rank, s):
    up = s["expected_up_pct"]
    return {
        "type": "box", "layout": "horizontal", "contents": [
            _flex_text(rank, size="sm", color="#AAAAAA", flex=1),
            _flex_text(s["name"], size="sm", color="#111111", flex=6, wrap=False),
            _flex_text(f"{s['score']:.1f}", size="sm", align="end",
                       color="#333333", flex=2),
            _flex_text(f"{up:+.1f}%", size="sm", align="end",
                       color=_color_of(up), flex=3),
        ],
    }


def build_flex_message(market, scored_stocks, stats=None, now_str=None):
    """
    LINE Flexメッセージ（まとめカード）を作る。

    戻り値:
        (alt_text, contents)  contents は Flex の bubble 辞書
    """
    now_str = now_str or datetime.now().strftime("%Y/%m/%d %H:%M")
    trend = analyze_trend(market, scored_stocks, stats)

    header = {
        "type": "box", "layout": "vertical", "backgroundColor": "#0B3D91",
        "paddingAll": "14px", "contents": [
            _flex_text("日本株 AIレポート", color="#FFFFFF", weight="bold", size="lg"),
            _flex_text(f"東証スクリーニング版 ・ {now_str}",
                       color="#C5D2F0", size="xs", margin="sm"),
        ],
    }

    body_contents = [
        _flex_text(trend["headline"] + "。", size="sm", weight="bold",
                   color="#333333", wrap=True),
        {"type": "separator", "margin": "md"},
        _flex_text("市場概況", size="xs", color="#888888", margin="md"),
    ]
    for nm in ["TOPIX(連動ETF)", "日経平均", "ドル円", "S&P500", "NASDAQ"]:
        if nm in market:
            body_contents.append(_flex_market_row(nm, market[nm]))

    body_contents.append({"type": "separator", "margin": "md"})
    if scored_stocks:
        body_contents.append(
            _flex_text(f"注目銘柄 TOP{len(scored_stocks)}（評価点 / 想定上昇率）",
                       size="xs", color="#888888", margin="md")
        )
        for i, s in enumerate(scored_stocks, start=1):
            body_contents.append(_flex_stock_row(i, s))
    else:
        body_contents.append(
            _flex_text("本日は条件を満たす銘柄がありませんでした。",
                       size="sm", color="#555555", wrap=True, margin="md")
        )

    body = {
        "type": "box", "layout": "vertical", "paddingAll": "14px",
        "spacing": "sm", "contents": body_contents,
    }

    footer = {
        "type": "box", "layout": "vertical", "paddingAll": "10px", "contents": [
            _flex_text(
                "※投資助言ではなく機械的なスクリーニング結果です。投資判断はご自身の責任で。",
                size="xxs", color="#AAAAAA", wrap=True),
        ],
    }

    bubble = {"type": "bubble", "size": "giga",
              "header": header, "body": body, "footer": footer}
    alt = f"本日の注目銘柄 TOP{len(scored_stocks)}（{now_str}）"
    return alt, bubble
