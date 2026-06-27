"""
profile_loader.py

各企業の「事業内容」「テーマタグ」を提供するモジュール。

2段構え:
  A. company_profiles.csv に登録済みの銘柄 → その内容を使う
  B. 未登録の銘柄 → JPXの業種名(sector)から簡易プロフィールを自動生成
     - 業種名からテーマタグを推定
     - 事業内容は「〇〇業種に属する企業」とする

テーマタグはスコアリング（業種テーマ性評価）にも渡される。
"""

import csv
import os


DEFAULT_PATH = "company_profiles.csv"

# テーマタグの語彙（レポート・スコアリングで扱う想定テーマ）
THEME_VOCAB = [
    "半導体", "防衛", "電力", "金融", "商社", "インバウンド", "AI", "データセンター",
    "円安メリット", "資源", "エネルギー", "造船", "重工", "建設", "インフラ",
    "内需ディフェンシブ", "物流", "医療", "DX", "サイバーセキュリティ",
]

# 注目度の高いテーマ（テーマ性評価でやや加点）
HIGH_ATTENTION_THEMES = {"半導体", "防衛", "AI", "データセンター", "サイバーセキュリティ"}

# JPX 33業種 → 推定テーマタグ（あくまで簡易推定）
SECTOR_THEMES = {
    "水産・農林業": ["内需ディフェンシブ"],
    "鉱業": ["資源", "エネルギー"],
    "建設業": ["建設", "インフラ"],
    "食料品": ["内需ディフェンシブ"],
    "繊維製品": ["内需ディフェンシブ"],
    "パルプ・紙": ["資源"],
    "化学": ["資源"],
    "医薬品": ["医療"],
    "石油・石炭製品": ["資源", "エネルギー"],
    "ゴム製品": ["円安メリット"],
    "ガラス・土石製品": ["建設"],
    "鉄鋼": ["資源"],
    "非鉄金属": ["資源"],
    "金属製品": ["建設"],
    "機械": ["重工"],
    "電気機器": ["AI", "DX"],
    "輸送用機器": ["円安メリット"],
    "精密機器": ["医療"],
    "その他製品": [],
    "電気・ガス業": ["電力", "エネルギー", "インフラ"],
    "陸運業": ["物流"],
    "海運業": ["物流"],
    "空運業": ["インバウンド"],
    "倉庫・運輸関連業": ["物流"],
    "情報・通信業": ["DX", "AI", "サイバーセキュリティ"],
    "卸売業": ["商社"],
    "小売業": ["インバウンド", "内需ディフェンシブ"],
    "銀行業": ["金融"],
    "証券、商品先物取引業": ["金融"],
    "保険業": ["金融"],
    "その他金融業": ["金融"],
    "不動産業": ["内需ディフェンシブ"],
    "サービス業": ["DX"],
}


def _split_tags(raw):
    """'|' / ';' / '、' 区切りのタグ文字列をリストにする。"""
    if not raw:
        return []
    for sep in ("|", ";", "、", ","):
        raw = raw.replace(sep, "|")
    return [t.strip() for t in raw.split("|") if t.strip()]


def load_profiles(csv_path=DEFAULT_PATH):
    """company_profiles.csv を読み込み {code: profile} を返す。失敗時は空dict。"""
    profiles = {}
    if not os.path.exists(csv_path):
        print(f"[情報] {csv_path} が無いため、全銘柄を業種から自動プロフィール化します。")
        return profiles
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = (row.get("code") or "").strip()
                if not code:
                    continue
                profiles[code] = {
                    "code": code,
                    "name": (row.get("name") or "").strip(),
                    "sector": (row.get("sector") or "").strip(),
                    "business_summary": (row.get("business_summary") or "").strip(),
                    "theme_tags": _split_tags(row.get("theme_tags")),
                    "size_category": (row.get("size_category") or "").strip(),
                    "notes": (row.get("notes") or "").strip(),
                    "source": "csv",
                }
    except Exception as e:
        print(f"[警告] company_profiles.csv の読み込みに失敗しました: {e}")
    return profiles


def tags_from_business(business_summary):
    """事業内容の文章から、語彙(THEME_VOCAB)に含まれるテーマタグを抽出する。"""
    if not business_summary:
        return []
    return [v for v in THEME_VOCAB if v in business_summary]


def _merge_tags(*tag_lists):
    """複数のタグリストを順序を保って重複なく結合する。"""
    out = []
    for tags in tag_lists:
        for t in tags or []:
            if t not in out:
                out.append(t)
    return out


def themes_from_sector(sector):
    """業種名からテーマタグを推定する。"""
    if not sector:
        return []
    if sector in SECTOR_THEMES:
        return list(SECTOR_THEMES[sector])
    # 表記ゆれに備えた部分一致
    for key, tags in SECTOR_THEMES.items():
        if key and (key in sector or sector in key):
            return list(tags)
    return []


def get_profile(code, name="", sector="", profiles=None):
    """
    銘柄の事業内容・テーマタグを返す。

    - CSVに登録があればそれを使う（不足項目は業種から補完）
    - 無ければ業種から簡易プロフィールを自動生成
    """
    profiles = profiles or {}
    prof = profiles.get(code)
    if prof:
        merged = dict(prof)
        if not merged.get("sector"):
            merged["sector"] = sector
        if not merged.get("name"):
            merged["name"] = name
        if not merged.get("business_summary"):
            merged["business_summary"] = f"{merged.get('sector') or sector}に属する企業"
        # CSVのタグ＋業種推定＋事業内容から抽出したタグを統合（順序維持・重複なし）
        merged["theme_tags"] = _merge_tags(
            merged.get("theme_tags"),
            themes_from_sector(merged.get("sector") or sector),
            tags_from_business(merged.get("business_summary")),
        )
        return merged

    # 未登録 → 業種から自動生成
    business = f"{sector}に属する企業" if sector else "事業内容の登録なし"
    return {
        "code": code,
        "name": name,
        "sector": sector,
        "business_summary": business,
        "theme_tags": _merge_tags(themes_from_sector(sector), tags_from_business(business)),
        "size_category": "",
        "notes": "",
        "source": "auto",
    }
