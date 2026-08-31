"""
sector_per/delivery.py

業種別PERランキングの配信整形（LINE Flex カルーセル＋補足テキスト）。

計算ロジック(ranking)からは独立。ここは「見せ方」だけを担う。

表現方針（依頼5・必須）:
  - 「買い/推奨/狙い目/注目」等の売買推奨・示唆語を使わない（ng_words で機械チェック）。
    事実の提示（業種内で予想PERが相対的に低い）に留める。
  - 配信の末尾に必須免責(config.REQUIRED_DISCLAIMER)を **必ず** 入れる。
    build_delivery() が構造的に付与し、欠落時は例外を投げる（省略不可）。
"""

from . import config as C

try:
    from promo import ng_words
except Exception:  # pragma: no cover
    ng_words = None

# LINEカルーセルは1メッセージ最大12バブル。余裕をもって10で分割する。
MAX_BUBBLES = 10


# ===== 表示補助 =====
def _yen(v):
    return "—" if v is None else f"{float(v):,.0f}円"


def _mult(v):
    return "—" if v is None else f"{float(v):.1f}倍"


def _pct(v, nd=1):
    return "—" if v is None else f"{float(v):.{nd}f}%"


def _flex_text(text, size="sm", color="#333333", weight=None, wrap=True,
               align=None, flex=None, margin=None):
    o = {"type": "text", "text": str(text), "size": size, "color": color, "wrap": wrap}
    if weight:
        o["weight"] = weight
    if align:
        o["align"] = align
    if flex is not None:
        o["flex"] = flex
    if margin:
        o["margin"] = margin
    return o


def _kv(label, value):
    return {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        _flex_text(label, size="xs", color="#8A8F98", flex=4),
        _flex_text(value, size="sm", color="#2D3540", flex=6, align="end"),
    ]}


# ===== 銘柄行・業種バブル =====
def stock_lines(row):
    """1銘柄の表示項目（依頼3の7項目）を KV ボックスのリストで返す。"""
    rel = row.get("sector_relative")
    rel_txt = "—" if rel is None else f"業種中央値比 {rel * 100:+.0f}%"
    return [
        _flex_text(f"{row.get('name','')}（{row.get('code','')}）",
                   size="md", weight="bold", color="#13335A"),
        _kv("終値", _yen(row.get("close"))),
        _kv("予想PER", _mult(row.get("forecast_per"))),
        _kv("業種中央値PER", _mult(row.get("sector_median_per"))),
        _kv("割安度", rel_txt),
        _kv("ROE", _pct(row.get("roe"))),
    ]


def sector_bubble(sector, data, asof_label=None):
    """1業種＝1バブル（上位銘柄を縦に並べる）。"""
    body = [_flex_text(f"予想PER 業種中央値 {_mult(data.get('median_per'))}"
                       f"（母集団{data.get('universe_n', 0)}銘柄）",
                       size="xxs", color="#8A8F98")]
    for i, row in enumerate(data.get("candidates") or []):
        if i > 0:
            body.append({"type": "separator", "margin": "md"})
        for c in stock_lines(row):
            body.append(c)
    footer_txt = "※業種内で予想PERが相対的に低い銘柄の事実提示です（売買推奨ではありません）"
    if asof_label:
        footer_txt = f"財務データ基準：{asof_label}\n" + footer_txt
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#13335A",
                   "paddingAll": "14px", "contents": [
                       _flex_text("東証33業種 予想PER 割安ランキング", size="xxs",
                                  color="#A9C2E0"),
                       _flex_text(sector, size="lg", weight="bold", color="#FFFFFF")]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                 "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px",
                   "contents": [_flex_text(footer_txt, size="xxs", color="#9AA0A6")]},
    }


# ===== カルーセル・補足テキスト・統合 =====
def build_carousels(rankings, asof_label=None):
    """
    候補のある業種を業種名順にカルーセル化する（最大10バブル/メッセージ）。

    戻り値: [(alt_text, carousel_contents), ...]
    """
    sectors = [s for s in sorted(rankings) if rankings[s].get("has_candidates")]
    messages = []
    for i in range(0, len(sectors), MAX_BUBBLES):
        chunk = sectors[i:i + MAX_BUBBLES]
        bubbles = [sector_bubble(s, rankings[s], asof_label) for s in chunk]
        alt = "東証33業種 予想PER割安ランキング（" + "・".join(chunk[:3]) + " ほか）"
        messages.append((alt, {"type": "carousel", "contents": bubbles}))
    return messages


def build_summary_text(rankings, basis_label=None, asof_label=None):
    """
    補足テキスト（該当なし業種の明示＋必須免責）。

    依頼3「該当0件の業種は『該当なし』と明示」を満たす。末尾は必ず必須免責。
    """
    with_c = [s for s in sorted(rankings) if rankings[s].get("has_candidates")]
    none_c = [s for s in sorted(rankings) if not rankings[s].get("has_candidates")]
    lines = ["【東証33業種 予想PER 割安ランキング】"]
    if basis_label:
        lines.append(basis_label)
    if asof_label:
        lines.append(f"財務データ基準：{asof_label}")
    lines.append("")
    lines.append(f"■ 該当ありの業種（{len(with_c)}）：" + ("、".join(with_c) if with_c else "なし"))
    lines.append("")
    lines.append(f"■ 本日 該当なしの業種（{len(none_c)}）")
    lines.append("、".join(none_c) if none_c else "（なし）")
    lines.append("")
    lines.append(C.REQUIRED_DISCLAIMER)   # 末尾に必須免責
    return "\n".join(lines)


def build_delivery(rankings, basis_label=None, asof_label=None):
    """
    配信一式を組み立てる。戻り値: (flex_messages, summary_text)。

    必須免責を構造的に保証する（末尾に無ければ例外＝省略不可）。
    禁止語が混入した場合も例外（ng_words 利用可能時）。
    """
    flex_messages = build_carousels(rankings, asof_label)
    summary = build_summary_text(rankings, basis_label, asof_label)

    # --- 必須免責の強制（省略できない実装） ---
    if not summary.rstrip().endswith(C.REQUIRED_DISCLAIMER):
        raise ValueError("必須免責が配信末尾にありません（省略不可）。")

    # --- 禁止語チェック（事実提示に留める・依頼5） ---
    if ng_words is not None:
        found = ng_words.check_ng(summary)
        for _alt, carousel in flex_messages:
            import json
            found += ng_words.check_ng(json.dumps(carousel, ensure_ascii=False))
        if found:
            raise ValueError(f"配信文面に禁止語が含まれます: {sorted(set(found))}")

    return flex_messages, summary
