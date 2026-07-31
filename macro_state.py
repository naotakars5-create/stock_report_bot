"""
macro_state.py

マクロ解説の「使い回し」を防ぎ、鮮度を担保するモジュール（機能3）。

課題:
  マクロ解説がテンプレ的で、日によって同一文が繰り返されると「使い回し」に見える。

対策:
  1. 前日配信のマクロ各文（為替・米国市場・金利・商品・地政学・サマリー）を保存する。
  2. 当日文と前日文の **類似度**（文字3-gram の Jaccard）を測り、同一・酷似なら
     「使い回し」とみなして落とす（＝その日ならではの情報だけ残す）。
  3. 為替・米国市場は、前日から **数値の方向が変わっていない** 項目も落とす
     （前日から変化があった項目のみ言及する）。

保存先: data/macro_state.json（前日の文と主要指標のスナップショット）
失敗しても全体を止めない（読み書きの例外は握りつぶし、鮮度判定は素通しにする）。
"""

import json
import os


STATE_PATH = os.path.join("data", "macro_state.json")

# 鮮度チェック対象のコメント（macro_context のキー）
_COMMENT_KEYS = [
    "market_summary", "fx_comment", "us_market_comment",
    "rates_comment", "commodity_comment", "geopolitical_comment",
]
# 前日からの数値変化で鮮度を見る指標（コメントキー: 市場データ名）
_MARKET_KEYS = {"fx_comment": "ドル円", "us_market_comment": "S&P500"}

SIMILARITY_THRESHOLD = 0.82   # これ以上の類似は「酷似（使い回し）」とみなす


def _char_ngrams(text, n=3):
    t = "".join((text or "").split())
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def ngram_jaccard(a, b, n=3):
    """2文の文字n-gram Jaccard類似度（0〜1）。空同士は0。"""
    ga, gb = _char_ngrams(a, n), _char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def load_state(path=STATE_PATH):
    """前日のマクロ状態を読み込む。無ければ None。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[警告] マクロ状態の読み込みに失敗しました（鮮度判定は素通し）: {e}")
        return None


def save_state(macro_context, market=None, run_date=None, path=STATE_PATH):
    """今日のマクロ状態（各コメント＋主要指標）を保存する。失敗しても止めない。"""
    mc = macro_context or {}
    market = market or {}
    comments = {k: (mc.get(k) or "") for k in _COMMENT_KEYS}
    snap = {}
    for name in set(_MARKET_KEYS.values()):
        v = (market.get(name) or {}).get("change_pct")
        if v is not None:
            snap[name] = v
    state = {"run_date": run_date, "comments": comments, "market": snap,
             "major_themes": mc.get("major_themes") or []}
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[警告] マクロ状態の保存に失敗しました: {e}")
        return False


def _same_direction(cur, prev, eps=0.1):
    """2つの前日比(change_pct)が同方向かつ大きく変わっていないか。"""
    if cur is None or prev is None:
        return False
    if abs(cur - prev) > eps:
        return False
    # 符号（方向）が同じ（0近傍の横ばい同士も同方向とみなす）
    return (cur >= 0) == (prev >= 0)


def evaluate_freshness(macro_context, market=None, prev_state=None,
                       threshold=SIMILARITY_THRESHOLD):
    """
    当日マクロと前日状態を比較し、鮮度情報を返す（機能3の中核・純粋関数）。

    戻り値: {
      "fresh_keys": [key, ...],       # その日ならではで載せてよいコメントキー
      "stale_keys": [key, ...],       # 前日と酷似 or 数値変化なしで落とすキー
      "reasons": {key: "similar"/"unchanged"},
      "summary_is_stale": bool,       # market_summary が前日と酷似か
      "note": str,                    # 「前日から変化した項目のみ記載」等
      "has_prev": bool,
    }
    """
    mc = macro_context or {}
    prev = prev_state or {}
    prev_comments = prev.get("comments") or {}
    prev_market = prev.get("market") or {}
    has_prev = bool(prev_comments)

    fresh_keys, stale_keys, reasons = [], [], {}
    for k in _COMMENT_KEYS:
        cur = (mc.get(k) or "").strip()
        if not cur:
            continue
        # 1) 前日文と酷似なら落とす（使い回し防止）
        pv = (prev_comments.get(k) or "").strip()
        if pv and ngram_jaccard(cur, pv) >= threshold:
            stale_keys.append(k)
            reasons[k] = "similar"
            continue
        # 2) 為替・米国は前日から数値変化が無ければ落とす（変化した項目のみ言及）
        mkey = _MARKET_KEYS.get(k)
        if mkey and market is not None:
            cur_v = (market.get(mkey) or {}).get("change_pct")
            prev_v = prev_market.get(mkey)
            if _same_direction(cur_v, prev_v):
                stale_keys.append(k)
                reasons[k] = "unchanged"
                continue
        fresh_keys.append(k)

    summary_is_stale = "market_summary" in stale_keys
    if not has_prev:
        note = ""
    elif stale_keys:
        note = "（前日から変化があった項目のみ記載しています）"
    else:
        note = ""
    return {"fresh_keys": fresh_keys, "stale_keys": stale_keys, "reasons": reasons,
            "summary_is_stale": summary_is_stale, "note": note, "has_prev": has_prev}


def is_fresh(freshness, key):
    """あるコメントキーが鮮度チェックを通過している（載せてよい）か。"""
    if not freshness:
        return True  # 判定情報が無ければ従来通り素通し
    return key not in set(freshness.get("stale_keys") or [])
