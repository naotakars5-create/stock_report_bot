"""
sector_per/config.py

業種別PERランキングの設定（すべて可変）。依頼の各閾値・重みはここで一元管理する。
環境変数で上書きできる項目は _env_float/_env_int を用意（運用中の微調整用）。
"""

import os


def _env_float(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# ===== 2-1. 母集団の絞り込み =====
MIN_FORECAST_EPS = _env_float("SPER_MIN_FORECAST_EPS", 0.0)      # 予想EPS > これ（赤字・取得不可は除外）
MIN_MARKET_CAP = _env_float("SPER_MIN_MARKET_CAP", 3.0e10)       # 時価総額 300億円以上
MIN_AVG_TURNOVER = _env_float("SPER_MIN_AVG_TURNOVER", 1.0e8)    # 20営業日平均売買代金 1億円以上
TURNOVER_WINDOW = _env_int("SPER_TURNOVER_WINDOW", 20)           # 売買代金の平均日数
MIN_LISTING_YEARS = _env_float("SPER_MIN_LISTING_YEARS", 3.0)    # 上場3年未満は除外
# 対象市場（プライム・スタンダード。グロース/REIT等は除外）
TARGET_MARKETS = ("プライム", "スタンダード")

# ===== 2-2. 業種内相対評価 =====
# 業種中央値を出すのに最低限必要な同業種の銘柄数（これ未満の業種は評価不能）
MIN_PEERS_FOR_MEDIAN = _env_int("SPER_MIN_PEERS", 3)
# 自己過去比の参照年数（無料枠は格納2年のため、実際に使えた年数を別途記録する）
PER_HISTORY_YEARS = _env_float("SPER_HISTORY_YEARS", 3.0)
# 過去比パーセンタイルを算出するのに必要な最低サンプル日数
MIN_HISTORY_SAMPLES = _env_int("SPER_MIN_HISTORY_SAMPLES", 60)

# ===== 2-3. バリュートラップ除外 =====
MIN_ROE = _env_float("SPER_MIN_ROE", 8.0)                # ROE 8%以上
MIN_EQUITY_RATIO = _env_float("SPER_MIN_EQUITY_RATIO", 30.0)  # 自己資本比率 30%以上
# 「増収 or 増益予想」「直近1年に下方修正なし」は真偽データで判定（閾値なし）

# ===== 2-4. 最終スコアと抽出 =====
WEIGHT_SECTOR_RELATIVE = _env_float("SPER_W_SECTOR", 0.6)   # 業種内割安度の重み
WEIGHT_SELF_HISTORY = _env_float("SPER_W_SELF", 0.4)        # 自己過去比の重み
TOP_N_PER_SECTOR = _env_int("SPER_TOP_N", 3)               # 業種ごとの抽出数

# 候補に含める総合スコアの下限（None=依頼2-4通り「上位3を無条件抽出」）。
# 0.0 を設定すると「業種内で実際に割安（スコアが正）な銘柄だけ」に絞れる
# （＝中央値より高い銘柄を『割安ランキング』に載せない運用）。環境変数で切替可。
_min_score_raw = (os.environ.get("SPER_MIN_TOTAL_SCORE") or "").strip()
MIN_TOTAL_SCORE_FOR_CANDIDATE = float(_min_score_raw) if _min_score_raw else None

# ===== 表現・免責（必須） =====
# 配信の末尾に必ず入れる定型文（コード上で省略不可＝builderが構造的に強制する）。
REQUIRED_DISCLAIMER = (
    "本情報は公開データに基づき、東証33業種ごとに予想PERが業種内で相対的に低い銘柄を"
    "機械的に抽出したものです。特定銘柄の売買を推奨するものではありません。"
    "投資助言ではなく、表示の指標は算出時点の参考値で、将来を保証しません。"
    "投資判断は必ずご自身の責任で行ってください。"
)
