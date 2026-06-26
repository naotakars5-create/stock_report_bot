"""
news_fetcher.py

世界情勢・経済ニュースの見出しを取得するモジュール。

設計方針:
  - **APIキー不要**で動くこと（無料で安定しやすい RSS を優先）
  - 取得に失敗してもプログラム全体は止めない（失敗時は空リストを返す）
  - 見出しの文字列リストを返すだけのシンプルな責務にする
  - ネットワーク不通・RSS仕様変更があっても握りつぶして継続する

主なソース（いずれも APIキー不要の RSS）:
  - Google ニュース（経済・ビジネス／マーケット検索）
  これらは取得できる範囲のみ利用し、取れなければスキップする。
"""

import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# 取得元 RSS（APIキー不要）。Google ニュースの「ビジネス」トピックと、
# 為替・米国株・金利・原油など主要マクロを横断する検索クエリ。
DEFAULT_FEEDS = [
    # ビジネス/経済トピック
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
    # マーケット横断の検索（日経平均・為替・米国株・金利・原油・半導体・防衛）
    "https://news.google.com/rss/search?q="
    "%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87+OR+%E7%82%BA%E6%9B%BF+OR+%E7%B1%B3%E5%9B%BD%E6%A0%AA"
    "+OR+%E9%87%91%E5%88%A9+OR+%E5%8E%9F%E6%B2%B9+OR+%E5%8D%8A%E5%B0%8E%E4%BD%93+OR+%E9%98%B2%E8%A1%9B"
    "&hl=ja&gl=JP&ceid=JP:ja",
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; stock-report-bot/1.0; +https://example.invalid)"
}


def _parse_rss_titles(xml_text):
    """RSS/Atom の XML テキストから <item><title> の見出しを取り出す。失敗時は []。"""
    titles = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return titles
    # RSS 2.0: channel/item/title、Atom: entry/title の両方に対応
    for tag in (".//item/title", ".//{http://www.w3.org/2005/Atom}entry/"
                "{http://www.w3.org/2005/Atom}title"):
        for el in root.findall(tag):
            text = (el.text or "").strip()
            if text:
                titles.append(text)
    return titles


def fetch_headlines(max_items=40, timeout=10, feeds=None):
    """
    経済・世界情勢ニュースの見出しを取得して文字列リストで返す。

    - APIキー不要。RSS から見出しのみ取得する。
    - 1ソースが失敗しても他を試し、すべて失敗しても **空リスト** を返す。
    - 重複見出しは除外し、最大 max_items 件に丸める。

    戻り値: ["見出し1", "見出し2", ...]（取得できなければ []）
    """
    if requests is None:
        print("[ニュース] requests 未導入のため、ニュース取得をスキップします。")
        return []

    feeds = feeds or DEFAULT_FEEDS
    seen = set()
    headlines = []
    ok_sources = 0
    for url in feeds:
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            if resp.status_code != 200:
                print(f"[ニュース] ソース取得失敗(HTTP {resp.status_code}): {url[:60]}...")
                continue
            titles = _parse_rss_titles(resp.text)
            if titles:
                ok_sources += 1
            for t in titles:
                # Google ニュースは "見出し - 媒体名" の形式が多いので媒体名を落とす
                head = t.rsplit(" - ", 1)[0].strip() if " - " in t else t
                key = head
                if key and key not in seen:
                    seen.add(key)
                    headlines.append(head)
        except Exception as e:
            print(f"[ニュース] ソース取得中に例外（継続します）: {e}")
            continue

    if not headlines:
        print("[ニュース] 見出しを取得できませんでした（ニュース評価は中立になります）。")
    else:
        print(f"[ニュース] {ok_sources} ソースから {len(headlines)} 件の見出しを取得しました。")
    return headlines[:max_items]


if __name__ == "__main__":
    for i, h in enumerate(fetch_headlines(), start=1):
        print(f"{i:2d}. {h}")
