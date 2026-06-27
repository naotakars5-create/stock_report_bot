"""
macro_analyzer.py

ニュース見出し（news_fetcher 由来）と市場データ（為替・米国株）を分析し、
「世界情勢・ニュース環境」を機械的に評価するモジュール。

責務:
  - 見出しから市場テーマ（半導体・防衛・円安・金利 等）を抽出して強度を数値化
  - テーマを銘柄の theme_tags / sector / business_summary と照合し、
    **銘柄ごと**のニュース関連スコア・関連理由・注意点・解説コメントを作る
  - サマリー用の主要テーマ・各種マクロコメント（為替/米国市場/金利/商品/地政学）を用意

重要な方針:
  - これは投資助言ではありません。「買い」「買われる」「上がる」「狙い目」「儲かる」等の
    表現は使いません。「意識されやすい」「関連がある」「注意」といった機械的な関連性のみを示します。
  - 失敗してもプログラムを止めない（analyze は常に有効な context を返す）。
"""

from collections import defaultdict


# ニュース見出しから探す市場テーマと、その手掛かりキーワード
NEWS_THEME_KEYWORDS = {
    "半導体": ["半導体", "チップ", "ファウンドリ", "TSMC", "エヌビディア", "NVIDIA", "ラピダス"],
    "AI": ["AI", "人工知能", "生成AI", "ChatGPT", "OpenAI"],
    "データセンター": ["データセンター", "クラウド"],
    "防衛": ["防衛", "ミサイル", "軍事", "安全保障", "防衛費"],
    "地政学リスク": ["地政学", "戦争", "紛争", "中東", "ウクライナ", "台湾有事", "有事"],
    "円安": ["円安", "ドル高"],
    "円高": ["円高", "ドル安"],
    "金利上昇": ["利上げ", "金利上昇", "長期金利", "利回り上昇", "金利高"],
    "金利低下": ["利下げ", "金利低下", "金利低"],
    "銀行": ["銀行", "メガバンク", "金融株", "日銀"],
    "保険": ["保険", "生保", "損保"],
    "商社": ["商社"],
    "資源": ["資源", "鉱物", "レアアース", "非鉄"],
    "原油": ["原油", "WTI", "OPEC", "石油"],
    "LNG": ["LNG", "天然ガス"],
    "電力": ["電力", "電気料金", "送電"],
    "原発": ["原発", "原子力"],
    "インバウンド": ["インバウンド", "訪日", "観光"],
    "中国景気": ["中国経済", "中国景気", "中国市場", "上海", "中国株"],
    "米国株": ["米国株", "NYダウ", "ダウ平均", "S&P"],
    "NASDAQ": ["ナスダック", "NASDAQ"],
    "SOX指数": ["SOX", "フィラデルフィア半導体"],
    "自動車": ["自動車", "EV", "トヨタ", "ホンダ"],
    "機械": ["工作機械", "機械受注", "産業機械", "設備投資"],
    "重工": ["重工", "プラント"],
    "造船": ["造船", "新造船"],
    "建設": ["建設", "ゼネコン"],
    "インフラ": ["インフラ"],
    "医療": ["医療", "製薬", "創薬"],
    "サイバーセキュリティ": ["サイバー", "セキュリティ", "ランサムウェア"],
    "物流": ["物流", "海運", "運賃", "コンテナ"],
    "小売": ["小売", "百貨店", "スーパー", "コンビニ", "個人消費"],
    "不動産": ["不動産", "REIT", "マンション", "オフィス市況"],
}

# ニュース市場テーマ → 銘柄 theme_tags への影響（正＝関連プラス／負＝注意）
# 銘柄タグは profile_loader.THEME_VOCAB の語彙に合わせる。
NEWS_THEME_TO_TAGS = {
    "半導体": {"半導体": 1.0, "AI": 0.6, "データセンター": 0.6, "DX": 0.3},
    "AI": {"AI": 1.0, "半導体": 0.6, "データセンター": 0.6, "DX": 0.4, "サイバーセキュリティ": 0.3},
    "データセンター": {"データセンター": 1.0, "半導体": 0.5, "電力": 0.4, "AI": 0.4},
    "防衛": {"防衛": 1.0, "重工": 0.6, "造船": 0.3},
    "地政学リスク": {"防衛": 0.8, "資源": 0.6, "エネルギー": 0.6, "重工": 0.4},
    "円安": {"円安メリット": 1.0, "重工": 0.4, "商社": 0.3},
    "円高": {"円安メリット": -0.6, "内需ディフェンシブ": 0.3},
    "金利上昇": {"金融": 1.0, "DX": -0.4, "内需ディフェンシブ": -0.2},
    "金利低下": {"金融": -0.4, "DX": 0.4, "内需ディフェンシブ": 0.3},
    "銀行": {"金融": 1.0},
    "保険": {"金融": 1.0},
    "商社": {"商社": 1.0, "資源": 0.5},
    "資源": {"資源": 1.0, "商社": 0.5, "エネルギー": 0.5},
    "原油": {"資源": 0.8, "エネルギー": 0.8, "商社": 0.5, "物流": -0.4, "電力": -0.3},
    "LNG": {"エネルギー": 0.8, "電力": 0.4, "商社": 0.4},
    "電力": {"電力": 1.0, "インフラ": 0.4, "エネルギー": 0.4},
    "原発": {"電力": 0.8, "重工": 0.4, "エネルギー": 0.4},
    "インバウンド": {"インバウンド": 1.0, "内需ディフェンシブ": 0.3, "物流": 0.2},
    "中国景気": {"資源": -0.5, "重工": -0.4, "商社": -0.3},
    "米国株": {"半導体": 0.5, "AI": 0.4, "DX": 0.3},
    "NASDAQ": {"半導体": 0.7, "AI": 0.5, "データセンター": 0.4, "DX": 0.4},
    "SOX指数": {"半導体": 1.0, "AI": 0.4, "データセンター": 0.4},
    "自動車": {"円安メリット": 0.5, "重工": 0.2},
    "機械": {"重工": 0.5, "円安メリット": 0.3},
    "重工": {"重工": 1.0, "防衛": 0.4},
    "造船": {"造船": 1.0, "物流": 0.3},
    "建設": {"建設": 1.0, "インフラ": 0.5},
    "インフラ": {"インフラ": 1.0, "建設": 0.4, "電力": 0.3},
    "医療": {"医療": 1.0},
    "サイバーセキュリティ": {"サイバーセキュリティ": 1.0, "DX": 0.4, "AI": 0.3},
    "物流": {"物流": 1.0},
    "小売": {"内需ディフェンシブ": 0.6, "インバウンド": 0.4},
    "不動産": {"内需ディフェンシブ": 0.4},
}

# テーマ → 「〇〇な局面」という環境の説明（銘柄別コメントの前半に使う）
NEWS_ENV = {
    "半導体": "米NASDAQや半導体関連指数が堅調な局面",
    "AI": "AI・データセンター投資のニュースが増える局面",
    "データセンター": "データセンター投資の話題が増える局面",
    "防衛": "防衛費や地政学リスク関連の報道が増える局面",
    "地政学リスク": "地政学リスクが意識される局面",
    "円安": "円安が進む局面",
    "円高": "円高が進む局面",
    "金利上昇": "長期金利の上昇が意識される局面",
    "金利低下": "金利低下が意識される局面",
    "銀行": "金利や金融政策が話題になる局面",
    "保険": "金利動向が話題になる局面",
    "商社": "資源・エネルギー価格が話題になる局面",
    "資源": "資源価格が意識される局面",
    "原油": "原油価格の上昇が意識される局面",
    "LNG": "エネルギー価格が話題になる局面",
    "電力": "電力・エネルギー政策が話題になる局面",
    "原発": "原子力政策が話題になる局面",
    "インバウンド": "訪日観光の回復が話題になる局面",
    "中国景気": "中国経済の動向が意識される局面",
    "米国株": "米国株が堅調な局面",
    "NASDAQ": "米ハイテク株が堅調な局面",
    "SOX指数": "半導体指数が意識される局面",
    "自動車": "自動車・輸出関連が話題になる局面",
    "機械": "設備投資・機械受注が話題になる局面",
    "重工": "防衛・インフラ関連の報道が増える局面",
    "造船": "海運・造船が話題になる局面",
    "建設": "建設・インフラ投資が話題になる局面",
    "インフラ": "インフラ投資が話題になる局面",
    "医療": "医療・製薬が話題になる局面",
    "サイバーセキュリティ": "サイバーセキュリティが話題になる局面",
    "物流": "物流・海運が話題になる局面",
    "小売": "個人消費・小売が話題になる局面",
    "不動産": "金利と不動産市況が話題になる局面",
}

# 注意（マイナス方向）テーマの解説
_CAUTION_ENV = {
    "円高": "一方で、円高が進む局面では輸出関連の採算に注意したい。",
    "中国景気": "一方で、中国景気の減速懸念が強まると素材・機械関連は影響を受けやすい点に注意。",
    "金利上昇": "一方で、金利上昇局面ではグロース・不動産関連の重しになりやすい点に注意。",
    "原油": "一方で、原油高が進むと運輸・電力などコスト増業種には逆風になりやすい点に注意。",
}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _neutral_context(note=""):
    return {
        "available": False,
        "raw_headlines": [],
        "theme_hits": {},
        "theme_intensity": {},
        "tag_signal": {},
        "major_themes": [],
        "positive_theme_tags": [],
        "caution_theme_tags": [],
        "fx_comment": "",
        "us_market_comment": "",
        "rates_comment": "",
        "commodity_comment": "",
        "geopolitical_comment": "",
        "market_summary": "本日の主要テーマは限定的です。",
        "macro_comment": "本日の主要テーマは限定的です。",  # 後方互換
        "summary_themes": [],                                  # 後方互換
        "note": note,
    }


def analyze(headlines, market=None):
    """ニュース見出しと市場データから macro_context を作る。失敗しても中立contextを返す。"""
    try:
        return _analyze(headlines, market)
    except Exception as e:
        print(f"[マクロ分析] 分析中に例外（ニュース評価は中立扱い）: {e}")
        return _neutral_context("ニュース分析に失敗したため中立扱いにしました。")


def _analyze(headlines, market):
    headlines = headlines or []
    market = market or {}
    available = bool(headlines)

    # 1. 見出しからテーマ出現件数 → 強度（0〜1。3件以上で最大）
    theme_hits = {}
    for theme, kws in NEWS_THEME_KEYWORDS.items():
        c = sum(1 for h in headlines if any(k in h for k in kws))
        if c:
            theme_hits[theme] = c
    theme_intensity = {t: _clamp(c / 3.0) for t, c in theme_hits.items()}

    tag_signal = defaultdict(float)

    # 2. 市場データ由来の方向性（為替・米国株）
    fx_comment = us_comment = ""
    fx = (market.get("ドル円") or {}).get("change_pct")
    if fx is not None:
        if fx >= 0.15:
            theme_intensity["円安"] = max(theme_intensity.get("円安", 0), _clamp(abs(fx)))
            fx_comment = "ドル円は円安方向。輸出・海外売上比率の高い業種が意識されやすい。"
        elif fx <= -0.15:
            theme_intensity["円高"] = max(theme_intensity.get("円高", 0), _clamp(abs(fx)))
            fx_comment = "ドル円は円高方向。輸出関連の採算には留意したい。"
        else:
            fx_comment = "ドル円はおおむね横ばい。"

    us_changes = [(market.get(k) or {}).get("change_pct") for k in ("S&P500", "NASDAQ")]
    us_changes = [v for v in us_changes if v is not None]
    if us_changes:
        avg = sum(us_changes) / len(us_changes)
        if avg >= 0.2:
            us_comment = "米国株は堅調。半導体・グロース関連が連想されやすい。"
            theme_intensity["米国株"] = max(theme_intensity.get("米国株", 0), 0.6)
            for tag, w in {"半導体": 0.5, "AI": 0.4, "データセンター": 0.4, "DX": 0.3}.items():
                tag_signal[tag] += w * 0.5
        elif avg <= -0.2:
            us_comment = "米国株は軟調。米ハイテク株の反落時は値動きが荒くなりやすい。"
            for tag, w in {"半導体": 0.5, "AI": 0.4, "データセンター": 0.4, "DX": 0.3}.items():
                tag_signal[tag] -= w * 0.5
        else:
            us_comment = "米国株は横ばい圏。"

    # 3. テーマ強度 → 銘柄タグのシグナルへ反映
    for theme, inten in theme_intensity.items():
        for tag, w in NEWS_THEME_TO_TAGS.get(theme, {}).items():
            tag_signal[tag] += w * inten * 0.5

    # 4. 主要テーマ（強度順）と、プラス/注意のタグ集合
    ordered = sorted(theme_intensity.items(), key=lambda kv: kv[1], reverse=True)
    major_themes = [t for t, v in ordered if v >= 0.34][:6] or [t for t, _ in ordered[:4]]

    pos_tags, cau_tags = [], []
    for tag, sig in tag_signal.items():
        if sig >= 0.12:
            pos_tags.append((tag, sig))
        elif sig <= -0.12:
            cau_tags.append((tag, sig))
    positive_theme_tags = [t for t, _ in sorted(pos_tags, key=lambda x: x[1], reverse=True)]
    caution_theme_tags = [t for t, _ in sorted(cau_tags, key=lambda x: x[1])]

    # 5. 各種マクロコメント
    rates_comment = _theme_comment(
        theme_intensity, {"金利上昇": "長期金利の上昇が意識される局面では銀行・保険が注目されやすい。",
                          "金利低下": "金利低下局面ではグロース関連が意識されやすい。"})
    commodity_comment = _theme_comment(
        theme_intensity, {"原油": "原油価格の上昇で資源・商社が意識されやすい一方、運輸・電力はコスト増に注意。",
                          "資源": "資源価格の動向が素材・商社関連に波及しやすい。",
                          "LNG": "エネルギー価格の動向が電力・商社に関連しやすい。"})
    geopolitical_comment = _theme_comment(
        theme_intensity, {"地政学リスク": "地政学リスクが意識され、防衛・資源・エネルギー関連に関心が向きやすい。",
                          "防衛": "防衛関連の報道が増え、防衛・重工テーマが意識されやすい。"})

    market_summary = _build_market_summary(
        major_themes, fx_comment, us_comment, geopolitical_comment, available)

    note = "" if available else "ニュース取得が一部制限されています（ニュース評価は中立扱い）。"

    return {
        "available": available,
        "raw_headlines": headlines[:12],
        "theme_hits": theme_hits,
        "theme_intensity": dict(theme_intensity),
        "tag_signal": dict(tag_signal),
        "major_themes": major_themes,
        "positive_theme_tags": positive_theme_tags,
        "caution_theme_tags": caution_theme_tags,
        "fx_comment": fx_comment,
        "us_market_comment": us_comment,
        "rates_comment": rates_comment,
        "commodity_comment": commodity_comment,
        "geopolitical_comment": geopolitical_comment,
        "market_summary": market_summary,
        "macro_comment": market_summary,        # 後方互換
        "summary_themes": major_themes,          # 後方互換
        "note": note,
    }


def _theme_comment(theme_intensity, mapping):
    """強度が立っているテーマがあれば、対応する1文を返す。"""
    best, best_inten = None, 0.0
    for theme, phrase in mapping.items():
        inten = theme_intensity.get(theme, 0)
        if inten > best_inten:
            best, best_inten = phrase, inten
    return best or ""


def _build_market_summary(major_themes, fx_comment, us_comment, geo_comment, available):
    if not available and not major_themes:
        return "本日の主要テーマは限定的です。"
    bits = []
    if us_comment:
        bits.append(us_comment.rstrip("。"))
    if major_themes:
        bits.append("・".join(major_themes[:4]) + "が意識されやすい環境")
    if geo_comment:
        bits.append(geo_comment.rstrip("。"))
    elif fx_comment:
        bits.append(fx_comment.rstrip("。"))
    if not bits:
        return "本日の主要テーマは限定的です。"
    return "。".join(bits) + "。"


# ====== 銘柄ごとのニュース関連（スコア・理由・解説コメント） ======
def _stock_matched_themes(theme_tags, macro_context):
    """銘柄タグに関連する（プラス/マイナス）テーマを効き目順に返す。"""
    tags = set(theme_tags or [])
    intensity = (macro_context or {}).get("theme_intensity") or {}
    pos, cau = [], []
    for theme, inten in intensity.items():
        weights = NEWS_THEME_TO_TAGS.get(theme, {})
        eff_pos = sum(w for t, w in weights.items() if t in tags and w > 0) * inten
        eff_neg = sum(w for t, w in weights.items() if t in tags and w < 0) * inten
        if eff_pos > 0.05:
            pos.append((theme, eff_pos))
        if eff_neg < -0.05:
            cau.append((theme, eff_neg))
    pos.sort(key=lambda x: x[1], reverse=True)
    cau.sort(key=lambda x: x[1])
    return pos, cau


def _business_theme_hits(business_summary, macro_context):
    """business_summary 内の語が、当日テーマのキーワードに一致するものを返す。"""
    if not business_summary:
        return []
    intensity = (macro_context or {}).get("theme_intensity") or {}
    hits = []
    for theme in intensity:
        if any(k in business_summary for k in NEWS_THEME_KEYWORDS.get(theme, [])):
            hits.append(theme)
    return hits


def _related_tags_for_theme(theme_tags, theme):
    """ある当日テーマに関連する、この銘柄のタグを返す。"""
    weights = NEWS_THEME_TO_TAGS.get(theme, {})
    return [t for t in (theme_tags or []) if weights.get(t, 0) > 0]


def calculate_macro_relevance_score(theme_tags, sector, business_summary, macro_context):
    """
    銘柄とニュース環境の関連度をスコア化する。

    戻り値: {macro_score(0〜1), macro_reason(str|None), macro_caution(str|None),
             matched_positive(list), matched_caution(list)}
    """
    if not macro_context or not macro_context.get("available"):
        return {"macro_score": 0.5, "macro_reason": None, "macro_caution": None,
                "matched_positive": [], "matched_caution": []}

    pos, cau = _stock_matched_themes(theme_tags, macro_context)
    biz_hits = _business_theme_hits(business_summary, macro_context)

    score = 0.5
    for _, eff in pos[:3]:
        score += min(0.18, 0.10 + eff * 0.08)
    if biz_hits:
        score += 0.08
    for _, eff in cau[:2]:
        score -= min(0.15, abs(eff) * 0.10 + 0.05)
    score = _clamp(score, 0.25, 1.0)

    macro_reason = None
    if pos:
        themes = "・".join(dict.fromkeys([t for t, _ in pos[:2]]))
        related = []
        for theme, _ in pos[:2]:
            related += _related_tags_for_theme(theme_tags, theme)
        tagstr = "・".join(dict.fromkeys(related))[:40] or "・".join((theme_tags or [])[:2])
        macro_reason = f"{tagstr}タグが、{themes}関連のニュースと関連"
    elif biz_hits:
        macro_reason = f"事業内容が{'・'.join(biz_hits[:2])}関連の話題と接点"

    macro_caution = None
    if cau:
        ct = "・".join([t for t, _ in cau[:2]])
        macro_caution = f"{ct}関連の逆風には注意"
    elif pos:
        macro_caution = "テーマ先行で短期過熱になりやすい点には注意"

    return {"macro_score": round(score, 3), "macro_reason": macro_reason,
            "macro_caution": macro_caution,
            "matched_positive": [t for t, _ in pos], "matched_caution": [t for t, _ in cau]}


def build_stock_news_comment(theme_tags, sector, business_summary, macro_context, detailed=False):
    """
    銘柄ごとの「ニュース環境」解説（2〜3文）。theme_tags・sector・business_summary と
    当日テーマを照合して固有のコメントを作る。detailed=True でやや詳しい版。
    """
    if not macro_context or not macro_context.get("available"):
        return "明確な個別テーマは限定的。現時点では特定のニュースとの強い結びつきは確認しづらい。"

    pos, cau = _stock_matched_themes(theme_tags, macro_context)
    biz_hits = _business_theme_hits(business_summary, macro_context)
    if not pos and not biz_hits:
        return "明確な個別テーマは限定的。本日のニュースとの強い結びつきは確認しづらい。"

    theme = pos[0][0] if pos else biz_hits[0]
    env = NEWS_ENV.get(theme, f"{theme}関連の報道が増える局面")
    related = _related_tags_for_theme(theme_tags, theme) or (theme_tags or [])[:2]
    tagstr = "・".join(dict.fromkeys(related))[:40] or (sector or "同社の事業")

    sentences = [f"{env}では、{tagstr}を持つ同社は市場の関心対象になりやすい。"]
    if detailed and business_summary:
        sentences.append(f"事業は「{business_summary}」で、{theme}関連の話題との接点がある。")
    elif len(pos) > 1:
        sentences.append(f"{pos[1][0]}の動向とも関連しやすい。")

    if cau:
        sentences.append(_CAUTION_ENV.get(
            cau[0][0], f"一方で、{cau[0][0]}関連の逆風時には値動きが荒くなりやすい点に注意。"))
    else:
        sentences.append("一方で、テーマ先行で短期的に値動きが荒くなりやすく、過熱感には注意。")

    return ("" if not detailed else " ").join(sentences)


if __name__ == "__main__":
    import json
    sample = [
        "半導体関連が上昇、エヌビディア決算に注目集まる",
        "防衛費増額の方針、関連銘柄に関心",
        "ドル円、円安進行で輸出関連に関心",
        "長期金利が上昇、銀行株の動向に注目",
        "原油価格が上昇、商社・資源関連が話題",
    ]
    ctx = analyze(sample, {"ドル円": {"change_pct": 0.4}, "NASDAQ": {"change_pct": 0.5},
                           "S&P500": {"change_pct": 0.3}})
    print(json.dumps({k: ctx[k] for k in ("major_themes", "positive_theme_tags",
          "caution_theme_tags", "market_summary")}, ensure_ascii=False, indent=2))
    print("\n防衛/重工:", build_stock_news_comment(["防衛", "重工"], "機械", "防衛・宇宙・エネルギー", ctx, detailed=True))
    print("relevance:", calculate_macro_relevance_score(["防衛", "重工"], "機械", "防衛・宇宙", ctx))
