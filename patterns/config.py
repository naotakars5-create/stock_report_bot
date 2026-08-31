"""
patterns/config.py

上昇パターン検出の設定（すべて可変・環境変数で上書き可）。
"""

import os


def _f(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _i(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# ===== 母集団（軽い流動性フィルタ・株価のみ） =====
MIN_AVG_TURNOVER = _f("PAT_MIN_TURNOVER", 1.0e8)   # 20日平均売買代金 1億円以上
TURNOVER_WINDOW = _i("PAT_TURNOVER_WINDOW", 20)
MIN_ROWS = _i("PAT_MIN_ROWS", 130)                 # 判定に必要な最低日数（半年強）

# ===== 過熱除外（上げしすぎを弾く） =====
MAX_SURGE_5D = _f("PAT_MAX_SURGE_5D", 20.0)        # 直近5日騰落がこれ超は過熱で除外
MAX_ABOVE_25MA = _f("PAT_MAX_ABOVE_25MA", 25.0)    # 25日線からの上方乖離がこれ超は除外

# ===== 各パターンの窓・閾値 =====
GC_CROSS_LOOKBACK = _i("PAT_GC_LOOKBACK", 5)       # ゴールデンクロスを直近何日以内に見るか
BREAKOUT_HIGH_WINDOW = _i("PAT_BREAKOUT_WIN", 60)  # 新高値ブレイクの高値参照日数
VOLUME_MULT = _f("PAT_VOLUME_MULT", 1.5)           # 出来高が平均比これ以上でブレイク確度↑
TRIANGLE_WINDOW = _i("PAT_TRIANGLE_WIN", 30)       # 三角保ち合いの収束を見る窓
PULLBACK_WINDOW = _i("PAT_PULLBACK_WIN", 10)       # 移動平均接近→反発を見る窓
PULLBACK_NEAR_PCT = _f("PAT_PULLBACK_NEAR", 3.0)   # 25日線への接近とみなす乖離%
CUP_WINDOW = _i("PAT_CUP_WIN", 60)                 # カップの形成を見る窓

# ===== 抽出 =====
TOP_N = _i("PAT_TOP_N", 10)                        # 全体で上位N銘柄

# ===== 必須免責（末尾・省略不可） =====
REQUIRED_DISCLAIMER = (
    "本情報は公開データに基づき、株価チャートに特定の形状が現れた銘柄を機械的に"
    "検出したものです。特定銘柄の売買を推奨するものではありません。投資助言ではなく、"
    "検出は過去の値動きに基づく事実の提示で、将来の値動きを示すものではありません。"
    "投資判断は必ずご自身の責任で行ってください。"
)
