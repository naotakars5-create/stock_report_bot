"""
promo/text_builder.py

X投稿の本文生成（LINE配信の生成コードとは分離）。

ポリシー:
  - **個別銘柄名は出さない**。触れる場合は「電気機器の中型株」のような
    業種＋規模の **ぼかし表記** にして、「銘柄名はLINEで」の導線にする。
    （例外は適時開示速報のみ。あちらは公開事実なので銘柄名可）
  - 売買推奨と受け取られる表現は使わない（`promo.ng_words` でチェック）。
  - 本文の末尾に必ず LINE友だち追加URL（環境変数 LINE_ADD_FRIEND_URL）を付ける。
  - 勝った日も負けた日も同じトーンで淡々と事実を書く（負けを隠さない）。

注記: 本サービスは「AIが銘柄を選ぶ」ものではなく、公開データによる機械的な
スクリーニングです。そのため文面では「AI抽出」ではなく「スクリーニング上位」
と表記します（サービスの位置づけと矛盾させないため）。
"""

import os

from . import ng_words

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

DISCLAIMER = "※機械的スクリーニングの結果です（売買推奨ではありません）"


def add_friend_url():
    """LINE友だち追加URL（未設定なら空文字）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return (os.environ.get("LINE_ADD_FRIEND_URL") or "").strip()


def fmt_date(d):
    """2026-07-06 → 7/6(月)"""
    return f"{d.month}/{d.day}({_WEEKDAY_JA[d.weekday()]})"


def blur(sector, size_category=None):
    """
    銘柄名を伏せた業種＋規模のぼかし表記を作る。
    例: ("電気機器","大型") → 「電気機器大手」 / ("情報・通信業","中型") → 「情報・通信業の中型株」
    """
    sector = (sector or "").strip() or "その他業種"
    size = (size_category or "").strip()
    if size == "大型":
        return f"{sector}大手"
    if size in ("中型", "小型"):
        return f"{sector}の{size}株"
    return f"{sector}の銘柄"


def _market_line(market):
    """市況1行（日経・ドル円）。取得できない項目は省く。"""
    parts = []
    nk = (market or {}).get("日経平均") or {}
    if nk.get("price") is not None:
        chg = f"({nk['change_pct']:+.1f}%)" if nk.get("change_pct") is not None else ""
        parts.append(f"日経{nk['price']:,.0f}{chg}")
    fx = (market or {}).get("ドル円") or {}
    if fx.get("price") is not None:
        parts.append(f"ドル円{fx['price']:,.1f}")
    return "・".join(parts)


def _line_cta(label):
    url = add_friend_url()
    return f"{label}はLINEで(無料) ▶ {url}" if url else f"{label}はLINEで(無料)"


def build_morning_digest(today, market, temperature, theme_ranking):
    """
    【機能1-a】朝ダイジェスト（毎営業日・LINE配信の直後に投稿）。

    構成: 日付＋市況1行 / 今日の温度感＋理由 / 強いテーマ上位3 / LINE導線。
    銘柄名は出さない。目安140〜230字。
    """
    lines = []
    head = fmt_date(today)
    mkt = _market_line(market)
    lines.append(f"{head} {mkt}" if mkt else head)

    temp = temperature or {}
    if temp.get("level"):
        reason = temp.get("reason") or ""
        lines.append(f"本日の温度感：{temp['level']}"
                     + (f" — {reason}" if reason else ""))

    themes = [t["theme"] for t in (theme_ranking or [])[:3]]
    if themes:
        lines.append("強いテーマ：" + " / ".join(themes))

    lines.append(DISCLAIMER)
    lines.append(_line_cta("スクリーニング上位5銘柄の詳細"))
    text = "\n".join(lines)
    ng_words.assert_clean(text, "morning_digest")
    return text


def build_close_result(today, result):
    """
    【機能1-b】引け後の答え合わせ（毎営業日・引け後に投稿）。

    result: {wins, losses, avg_return, vs_nikkei, best, worst} を想定。
      best/worst は {"blur": 業種+規模のぼかし表記, "return": 騰落率}。
    勝ちの日も負けの日も同じトーンで、事実のみを淡々と書く。銘柄名は出さない。
    """
    r = result or {}
    lines = [f"{fmt_date(today)} 引け後の答え合わせ"]

    total = r.get("wins", 0) + r.get("losses", 0)
    core = f"今朝のスクリーニング上位{total}銘柄：{r.get('wins', 0)}勝{r.get('losses', 0)}敗"
    if r.get("avg_return") is not None:
        core += f"／平均{r['avg_return']:+.2f}%"
    if r.get("vs_nikkei") is not None:
        core += f"（日経比{r['vs_nikkei']:+.2f}pt）"
    lines.append(core)

    # 一言（事実のみ・銘柄名は伏せる）
    bits = []
    if r.get("best") and r["best"].get("return") is not None:
        bits.append(f"最も上昇したのは{r['best'].get('blur', '—')}で{r['best']['return']:+.1f}%")
    if r.get("worst") and r["worst"].get("return") is not None:
        bits.append(f"最も下落したのは{r['worst'].get('blur', '—')}で{r['worst']['return']:+.1f}%")
    if bits:
        lines.append("、".join(bits) + "。")

    lines.append(DISCLAIMER)
    lines.append(_line_cta("明日のスクリーニング上位"))
    text = "\n".join(lines)
    ng_words.assert_clean(text, "close_result")
    return text
