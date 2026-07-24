"""
scoring_profiles.py

スコアリングの「プロファイル」定義（改善A: 守り仮説の検証用）。

背景:
  実データのエッジ検証（edge_analysis）で、現状ロジックの唯一の兆しは
  「下げ相場で負けを小さくする（守り）」だった。これを本当に強められるか
  試すため、配点と過熱・ボラのペナルティを変えた defensive プロファイルを用意する。

方針（安全第一）:
  - balanced は現行と完全に同一（既定）。配信は当面 balanced のまま変えない。
  - defensive は「シャドウ運用」で毎日その上位5銘柄を別途記録・追跡し、
    balanced と成績を突き合わせる（forward A/B）。少数データで見た目が良い方に
    飛びつかないよう、判定は edge_analysis のサンプル数ゲートに委ねる。

各プロファイル:
  weights          : 8軸の配点（合計10.0に正規化）
  surge_div        : 過熱ペナルティの強さ（小さいほど急騰を強く減点）
  vol_hi           : 安定性で「高ボラ」とみなす基準（小さいほど高ボラを強く減点）
  defensive_sectors: ディフェンシブ業種（安定性に加点）
  defensive_bonus  : 上記業種への安定性加点（0〜1、加点前スケール）
"""

# 現行と完全一致（合計10.0）。既定プロファイル。
BALANCED = {
    "name": "balanced",
    "weights": {
        "トレンド": 1.7, "出来高": 1.3, "相対強度": 1.3, "テーマ性": 1.3,
        "ニュース": 1.0, "割安感": 0.9, "安定性": 1.3, "継続性": 1.2,
    },
    "surge_div": 15.0,
    "vol_hi": 4.0,
    "defensive_sectors": set(),
    "defensive_bonus": 0.0,
}

# 守り重視（合計10.0）。安定性・割安感・継続性を厚く、テーマ性・ニュースを薄く。
# 急騰・高ボラをより強く減点し、ディフェンシブ業種を加点する。
DEFENSIVE = {
    "name": "defensive",
    "weights": {
        "トレンド": 1.5, "出来高": 1.0, "相対強度": 1.3, "テーマ性": 0.8,
        "ニュース": 0.7, "割安感": 1.2, "安定性": 2.0, "継続性": 1.5,
    },
    "surge_div": 10.0,   # 急騰をより強く減点
    "vol_hi": 3.5,       # 高ボラをより強く減点
    "defensive_sectors": {
        "電気・ガス業", "食料品", "医薬品", "陸運業", "情報・通信業",
        "小売業", "水産・農林業", "保険業", "銀行業",
    },
    "defensive_bonus": 0.35,
}

PROFILES = {"balanced": BALANCED, "defensive": DEFENSIVE}


def get_profile(name):
    """プロファイル名（balanced/defensive）から定義を返す。未知名は balanced。"""
    if isinstance(name, dict):
        return name
    return PROFILES.get((name or "balanced").strip().lower(), BALANCED)


def _validate():
    """配点合計が10.0であることを確認（スケールと6.0ゲートの一貫性のため）。"""
    for p in PROFILES.values():
        total = sum(p["weights"].values())
        assert abs(total - 10.0) < 1e-9, f"{p['name']} の配点合計が10.0でない: {total}"


_validate()
