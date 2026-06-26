"""
macro_analyzer.py

ニュース見出し（news_fetcher 由来）と市場データ（為替・米国株）を分析し、
「世界情勢・ニュース環境」を機械的に評価するモジュール。

責務:
  - 見出しから市場テーマ（半導体・防衛・円安・金利 等）を抽出する
  - 各テーマの強度を数値化する（0〜1）
  - テーマを銘柄の theme_tags と照合し、銘柄ごとの「ニュース環境」シグナルを作る
  - サマリー用の主要テーマ・マクロコメントを用意する

重要な方針:
  - これは投資助言ではありません。「買い」「上昇予想」等の表現は使いません。
    「意識されやすい」「関連がある」「注意」といった機械的な関連性のみを示します。
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
    "物流": ["物流", "海運", "運賃", "コンテナ"],
    "建設": ["建設", "ゼネコン"],
    "インフラ": ["インフラ"],
    "医療": ["医療", "製薬", "創薬"],
    "サイバーセキュリティ": ["サイバー", "セキュリティ", "ランサムウェア"],
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
    "物流": {"物流": 1.0},
    "建設": {"建設": 1.0, "インフラ": 0.5},
    "インフラ": {"インフラ": 1.0, "建設": 0.4, "電力": 0.3},
    "医療": {"医療": 1.0},
    "サイバーセキュリティ": {"サイバーセキュリティ": 1.0, "DX": 0.4, "AI": 0.3},
}

# テーマ → 銘柄カード/詳細用の短い説明（プラス方向）。売買推奨表現は使わない。
NEWS_THEME_PHRASE = {
    "半導体": "半導体テーマに関心が向かいやすい地合い",
    "AI": "AI関連テーマが市場で意識されやすい環境",
    "データセンター": "データセンター関連が話題になりやすい環境",
    "防衛": "防衛関連テーマが市場で意識されやすい環境",
    "地政学リスク": "地政学リスクから防衛・資源関連が意識されやすい環境",
    "円安": "円安環境では輸出関連として注目されやすい",
    "金利上昇": "金利上昇局面で金融関連が意識されやすい環境",
    "金利低下": "金利低下局面でグロース関連が意識されやすい環境",
    "銀行": "金融関連テーマが市場で意識されやすい環境",
    "商社": "商社・資源関連が話題になりやすい環境",
    "資源": "資源・エネルギー関連が意識されやすい環境",
    "原油": "原油高で資源・商社関連が意識されやすい環境",
    "LNG": "エネルギー関連テーマが意識されやすい環境",
    "電力": "電力・インフラ関連が話題になりやすい環境",
    "原発": "原子力関連テーマが意識されやすい環境",
    "インバウンド": "インバウンド関連として注目されやすい環境",
    "米国株": "米国株堅調で関連テーマに関心が向かいやすい地合い",
    "NASDAQ": "NASDAQ動向で半導体・グロースが意識されやすい環境",
    "SOX指数": "半導体指数の動向が意識されやすい環境",
    "自動車": "自動車・輸出関連が話題になりやすい環境",
    "物流": "物流関連テーマが意識されやすい環境",
    "建設": "建設・インフラ関連が話題になりやすい環境",
    "インフラ": "インフラ関連テーマが意識されやすい環境",
    "医療": "医療・製薬関連が話題になりやすい環境",
    "サイバーセキュリティ": "サイバーセキュリティ関連が意識されやすい環境",
}

# 注意（マイナス方向）テーマの短い説明
NEWS_THEME_CAUTION_PHRASE = {
    "円高": "円高方向では輸出関連の値動きに注意",
    "中国景気": "中国景気懸念で素材・機械関連は値動きに注意",
}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def analyze(headlines, market=None):
    """
    ニュース見出しと市場データから macro_context（ニュース環境の評価材料）を作る。

    戻り値 dict:
        available       : ニュースを取得できたか
        headlines       : 主要見出し（表示用・先頭数件）
        theme_hits      : {テーマ: 出現件数}
        theme_intensity : {テーマ: 強度0〜1}
        tag_signal      : {銘柄タグ: シグナル(+/-)}（ニュース環境評価に使用）
        summary_themes  : 主要テーマ（表示用・強度順）
        macro_comment   : マクロ環境の短いコメント
        note            : 取得制限時の注意文（無ければ空）
    どんな入力でも例外を投げずに有効な dict を返す。
    """
    try:
        return _analyze(headlines, market)
    except Exception as e:  # 何があってもニュートラルな context を返す
        print(f"[マクロ分析] 分析中に例外（ニュース評価は中立扱い）: {e}")
        return _neutral_context("ニュース分析に失敗したため中立扱いにしました。")


def _neutral_context(note=""):
    return {
        "available": False, "headlines": [], "theme_hits": {}, "theme_intensity": {},
        "tag_signal": {}, "summary_themes": [], "macro_comment": "本日の主要テーマは限定的です。",
        "note": note,
    }


def _analyze(headlines, market):
    headlines = headlines or []
    market = market or {}
    available = bool(headlines)

    # 1. 見出しからテーマ出現件数を数える
    theme_hits = {}
    for theme, kws in NEWS_THEME_KEYWORDS.items():
        c = sum(1 for h in headlines if any(k in h for k in kws))
        if c:
            theme_hits[theme] = c

    # 2. テーマ強度（0〜1）。3件以上で最大とみなす。
    theme_intensity = {t: _clamp(c / 3.0) for t, c in theme_hits.items()}

    tag_signal = defaultdict(float)
    macro_bits = []

    # 3. 市場データ由来の方向性（為替・米国株）
    fx = (market.get("ドル円") or {}).get("change_pct")
    if fx is not None:
        if fx >= 0.15:
            theme_intensity["円安"] = max(theme_intensity.get("円安", 0), _clamp(abs(fx)))
            macro_bits.append("ドル円は円安方向")
        elif fx <= -0.15:
            theme_intensity["円高"] = max(theme_intensity.get("円高", 0), _clamp(abs(fx)))
            macro_bits.append("ドル円は円高方向")

    us_changes = [
        (market.get(k) or {}).get("change_pct")
        for k in ("S&P500", "NASDAQ")
    ]
    us_changes = [v for v in us_changes if v is not None]
    if us_changes:
        avg = sum(us_changes) / len(us_changes)
        if avg >= 0.2:
            macro_bits.append("米国株は堅調")
            theme_intensity["米国株"] = max(theme_intensity.get("米国株", 0), 0.6)
            for tag, w in {"半導体": 0.5, "AI": 0.4, "データセンター": 0.4, "DX": 0.3}.items():
                tag_signal[tag] += w * 0.5
        elif avg <= -0.2:
            macro_bits.append("米国株は軟調")
            for tag, w in {"半導体": 0.5, "AI": 0.4, "データセンター": 0.4, "DX": 0.3}.items():
                tag_signal[tag] -= w * 0.5
        else:
            macro_bits.append("米国株は横ばい圏")

    # 4. テーマ強度 → 銘柄タグのシグナルへ反映
    for theme, inten in theme_intensity.items():
        for tag, w in NEWS_THEME_TO_TAGS.get(theme, {}).items():
            tag_signal[tag] += w * inten * 0.5

    # 5. 表示用の主要テーマ（強度順。強いものを優先）
    ordered = sorted(theme_intensity.items(), key=lambda kv: kv[1], reverse=True)
    summary_themes = [t for t, v in ordered if v >= 0.34][:5]
    if not summary_themes:
        summary_themes = [t for t, _ in ordered[:4]]

    macro_comment = _build_macro_comment(macro_bits, summary_themes, available)
    note = "" if available else "ニュース取得が一部制限されています（ニュース評価は中立扱い）。"

    return {
        "available": available,
        "headlines": headlines[:8],
        "theme_hits": theme_hits,
        "theme_intensity": dict(theme_intensity),
        "tag_signal": dict(tag_signal),
        "summary_themes": summary_themes,
        "macro_comment": macro_comment,
        "note": note,
    }


def _build_macro_comment(macro_bits, summary_themes, available):
    parts = []
    if macro_bits:
        parts.append("、".join(macro_bits))
    if summary_themes:
        parts.append("・".join(summary_themes[:4]) + "が意識されやすい環境")
    if not parts:
        return "本日の主要テーマは限定的です。"
    return "。".join(parts) + "。"


# ====== 銘柄ごとのニュース環境（スコア・表示） ======
def stock_news_ratio(macro_context, theme_tags):
    """
    銘柄の theme_tags と macro の tag_signal を照合し、ニュース環境評価(0〜1)を返す。
    関連シグナルが無ければ中立(0.5)。
    """
    if not macro_context:
        return 0.5
    sig = macro_context.get("tag_signal") or {}
    tags = theme_tags or []
    if not sig or not tags:
        return 0.5
    rel = [sig.get(t, 0.0) for t in tags]
    rel = [v for v in rel if v != 0]
    if not rel:
        return 0.5
    rel.sort(key=abs, reverse=True)
    eff = sum(rel[:2]) / min(2, len(rel))   # 効いている上位2タグの平均
    return _clamp(0.5 + eff)


def _matched_themes(macro_context, theme_tags):
    """銘柄タグに関連する（プラス/マイナス）テーマを効き目順に返す。"""
    if not macro_context:
        return []
    tags = set(theme_tags or [])
    intensity = macro_context.get("theme_intensity") or {}
    scored = []
    for theme, inten in intensity.items():
        weights = NEWS_THEME_TO_TAGS.get(theme, {})
        eff = sum(weights.get(t, 0.0) for t in tags) * inten
        if abs(eff) >= 0.05:
            scored.append((theme, eff))
    scored.sort(key=lambda x: abs(x[1]), reverse=True)
    return scored


def stock_news_line(macro_context, theme_tags):
    """銘柄カード/詳細用の「ニュース環境」1〜2行を返す。"""
    matched = _matched_themes(macro_context, theme_tags)
    if not matched:
        return "本日は明確なニューステーマは限定的"
    parts = []
    for theme, eff in matched[:2]:
        if eff >= 0:
            phrase = NEWS_THEME_PHRASE.get(theme)
        else:
            phrase = NEWS_THEME_CAUTION_PHRASE.get(theme) or f"{theme}の影響で値動きに注意"
        if phrase and phrase not in parts:
            parts.append(phrase)
    if not parts:
        return "本日は明確なニューステーマは限定的"
    return "。".join(parts[:2])


def stock_news_reason(macro_context, theme_tags):
    """評価理由の「ニュース環境との関連」1行を返す（無ければ None）。"""
    matched = _matched_themes(macro_context, theme_tags)
    if not matched:
        return None
    theme, eff = matched[0]
    if eff >= 0:
        return NEWS_THEME_PHRASE.get(theme) or f"{theme}テーマが足元のニュース環境と関連"
    return NEWS_THEME_CAUTION_PHRASE.get(theme) or f"{theme}の影響で値動きに注意"


if __name__ == "__main__":
    sample = [
        "半導体関連が上昇、エヌビディア決算に注目",
        "防衛費増額で関連銘柄に関心",
        "ドル円、円安進行で輸出関連に追い風観測",
        "長期金利上昇、銀行株の動向に関心",
    ]
    ctx = analyze(sample, {"ドル円": {"change_pct": 0.4}, "NASDAQ": {"change_pct": 0.5},
                           "S&P500": {"change_pct": 0.3}})
    import json
    print(json.dumps(ctx, ensure_ascii=False, indent=2))
    print("防衛|重工 →", stock_news_line(ctx, ["防衛", "重工"]))
    print("半導体 →", stock_news_ratio(ctx, ["半導体", "AI"]))
