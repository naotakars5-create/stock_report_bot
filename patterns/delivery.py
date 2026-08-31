"""
patterns/delivery.py

上昇パターン（全体上位N）の配信整形（LINE Flex カルーセル＋補足テキスト）。

計算(screen)から独立。必須免責を構造的に強制（末尾に無ければ例外）、禁止語チェック。
"""

from . import config as C

try:
    from promo import ng_words
except Exception:  # pragma: no cover
    ng_words = None

MAX_BUBBLES = 10


def _yen(v):
    return "—" if v is None else f"{float(v):,.0f}円"


def _pct(v, sign=True, nd=1):
    if v is None:
        return "—"
    return f"{float(v):+.{nd}f}%" if sign else f"{float(v):.{nd}f}%"


def _flex_text(text, size="sm", color="#333333", weight=None, wrap=True,
               align=None, flex=None):
    o = {"type": "text", "text": str(text), "size": size, "color": color, "wrap": wrap}
    if weight:
        o["weight"] = weight
    if align:
        o["align"] = align
    if flex is not None:
        o["flex"] = flex
    return o


def _kv(label, value):
    return {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        _flex_text(label, size="xs", color="#8A8F98", flex=4),
        _flex_text(value, size="sm", color="#2D3540", flex=6, align="end")]}


def stock_bubble(rank, row):
    """1銘柄＝1バブル（検出パターン・終値・出来高比・25日線乖離・5日騰落）。"""
    vr = row.get("vol_ratio")
    vr_txt = f"{vr:.1f}倍" if vr is not None else "—"
    pats = row.get("patterns") or []
    body = [_flex_text("検出パターン", size="xs", color="#8A8F98", weight="bold")]
    for p in pats:
        body.append(_flex_text(f"✓ {p['label']}", size="sm", color="#1B5E20",
                               weight="bold"))
        body.append(_flex_text(p["note"], size="xxs", color="#6B7077"))
    body.append({"type": "separator", "margin": "md"})
    body.append(_kv("終値", _yen(row.get("price"))))
    body.append(_kv("出来高（20日平均比）", vr_txt))
    body.append(_kv("25日線からの乖離", _pct(row.get("above_25ma"))))
    body.append(_kv("直近5日騰落", _pct(row.get("surge_5"))))
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#12332A",
                   "paddingAll": "14px", "contents": [
                       _flex_text(f"No.{rank} ・ 上昇チャートパターン検出", size="xxs",
                                  color="#8FD3BC"),
                       _flex_text(f"{row.get('name','')}（{row.get('code','')}）",
                                  size="lg", weight="bold", color="#FFFFFF"),
                       _flex_text(row.get("sector") or "", size="xxs", color="#8FD3BC")]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                 "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px",
                   "contents": [_flex_text(
                       "※チャート形状の事実提示です（売買推奨ではありません）",
                       size="xxs", color="#9AA0A6")]},
    }


def build_carousels(ranked):
    """上位N銘柄をカルーセル化（最大10バブル/メッセージ）。"""
    messages = []
    for i in range(0, len(ranked), MAX_BUBBLES):
        chunk = ranked[i:i + MAX_BUBBLES]
        bubbles = [stock_bubble(i + j + 1, r) for j, r in enumerate(chunk)]
        alt = "上昇チャートパターン検出（" + "・".join(
            r.get("name", "") for r in chunk[:3]) + " ほか）"
        messages.append((alt, {"type": "carousel", "contents": bubbles}))
    return messages


def build_summary_text(ranked, basis_label=None):
    """補足テキスト（上位一覧＋必須免責）。末尾は必ず必須免責。"""
    lines = ["【上昇チャートパターン 検出銘柄】"]
    if basis_label:
        lines.append(basis_label)
    lines.append("")
    if ranked:
        lines.append(f"■ 検出 {len(ranked)} 銘柄（全体・パターン強度順）")
        for i, r in enumerate(ranked, 1):
            lines.append(f"{i}. {r.get('name','')}（{r.get('code','')}）："
                         f"{r.get('pattern_labels','')}")
    else:
        lines.append("本日、条件を満たす上昇チャートパターンの検出はありませんでした。")
    lines.append("")
    lines.append(C.REQUIRED_DISCLAIMER)
    return "\n".join(lines)


def build_delivery(ranked, basis_label=None):
    """
    配信一式を組み立てる。戻り値: (flex_messages, summary_text)。

    必須免責を構造的に保証（末尾に無ければ例外＝省略不可）。禁止語混入も例外。
    """
    flex_messages = build_carousels(ranked)
    summary = build_summary_text(ranked, basis_label)
    if not summary.rstrip().endswith(C.REQUIRED_DISCLAIMER):
        raise ValueError("必須免責が配信末尾にありません（省略不可）。")
    if ng_words is not None:
        found = ng_words.check_ng(summary)
        import json
        for _alt, car in flex_messages:
            found += ng_words.check_ng(json.dumps(car, ensure_ascii=False))
        if found:
            raise ValueError(f"配信文面に禁止語が含まれます: {sorted(set(found))}")
    return flex_messages, summary
