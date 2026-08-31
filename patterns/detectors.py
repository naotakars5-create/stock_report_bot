"""
patterns/detectors.py

チャートパターン検出（純粋関数・OHLCVの数値列のみに依存＝pandas不要でテスト可能）。

入力 ohlcv: {"close":[...], "high":[...], "low":[...], "volume":[...]}（昇順・日足）
各検出関数の戻り値: {"key","label","strength"(0〜1),"note"} または None（非該当/データ不足）。

表現方針: ラベル・note は事実の提示に留め、禁止語（買い/押し目/注目/上がる等）を使わない。
"""


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _sma_at(vals, w, idx):
    """vals[idx] を末尾とする w 本の単純移動平均。データ不足は None。"""
    if idx + 1 < w or idx >= len(vals):
        return None
    seg = vals[idx + 1 - w:idx + 1]
    seg = [v for v in seg if v is not None]
    return sum(seg) / len(seg) if len(seg) == w else None


def _slope(vals):
    """最小二乗の傾き（x=0,1,2,...）。データ不足は 0。"""
    n = len(vals)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((xs[i] - mx) * (vals[i] - my) for i in range(n)) / den


def _vol_ratio(volume, window):
    """当日出来高 ÷ 直近 window 日平均出来高。算出不可は None。"""
    if len(volume) < window + 1:
        return None
    avg = sum(volume[-window:]) / window
    return (volume[-1] / avg) if avg > 0 else None


def _ok(ohlcv, need):
    c = ohlcv.get("close") or []
    return len(c) >= need


# ===== 各パターン =====
def golden_cross(o, cross_lookback=5):
    """25日線が75日線を下から上抜け（直近 cross_lookback 日以内）＋価格が25日線上。"""
    close = o["close"]
    if not _ok(o, 75 + cross_lookback + 1):
        return None
    n = len(close)
    price = close[-1]
    crossed_i = None
    for i in range(n - cross_lookback, n):
        s25p, s75p = _sma_at(close, 25, i - 1), _sma_at(close, 75, i - 1)
        s25, s75 = _sma_at(close, 25, i), _sma_at(close, 75, i)
        if None in (s25p, s75p, s25, s75):
            continue
        if s25p <= s75p and s25 > s75:
            crossed_i = i
    s25_now, s75_now = _sma_at(close, 25, n - 1), _sma_at(close, 75, n - 1)
    if crossed_i is None or s25_now is None or s75_now is None:
        return None
    # ノイズによる微小クロスを弾く: 現時点で 0.3% 以上の明確な上抜けであること
    if not (s25_now > s75_now * 1.003 and price > s25_now):
        return None
    recency = 1.0 - (n - 1 - crossed_i) / max(1, cross_lookback)
    gap = _clamp((s25_now - s75_now) / s75_now / 0.05)  # 5%乖離で最大
    strength = _clamp(0.5 * recency + 0.5 * gap)
    return {"key": "golden_cross", "label": "ゴールデンクロス",
            "strength": round(strength, 3),
            "note": "25日線が75日線を上抜け、価格も25日線の上で推移"}


def new_high_breakout(o, window=60, vol_mult=1.5):
    """当日終値が直近 window 日高値を更新＋出来高増加。"""
    close, high, vol = o["close"], o["high"], o.get("volume") or []
    if not _ok(o, window + 1):
        return None
    prior_high = max(high[-window - 1:-1]) if len(high) >= window + 1 else None
    if prior_high is None or close[-1] < prior_high:
        return None
    vr = _vol_ratio(vol, window) if vol else None
    margin = _clamp((close[-1] - prior_high) / prior_high / 0.03)
    vpart = _clamp((vr - 1.0) / (vol_mult - 1.0)) if vr is not None else 0.4
    strength = _clamp(0.5 * margin + 0.5 * vpart)
    vtxt = f"・出来高は平均比{vr:.1f}倍" if vr is not None else ""
    return {"key": "new_high_breakout", "label": "新高値ブレイク",
            "strength": round(strength, 3),
            "note": f"当日終値が直近{window}日の高値を更新{vtxt}"}


def triangle_breakout(o, window=30, vol_mult=1.5):
    """三角保ち合い（高値切り下げ＋安値切り上げ＝収束）からの上放れ。"""
    close, high, low, vol = o["close"], o["high"], o["low"], o.get("volume") or []
    if not _ok(o, window + 1):
        return None
    highs, lows = high[-window:], low[-window:]
    sh, sl = _slope(highs), _slope(lows)
    if not (sh < 0 and sl > 0):     # 収束していない
        return None
    range_high = max(high[-window:-1])
    if close[-1] <= range_high:      # 上放れしていない
        return None
    vr = _vol_ratio(vol, window) if vol else None
    conv = _clamp((sl - sh) / (highs[0] * 0.01 + 1e-9) / 5.0) if highs[0] else 0.3
    vpart = _clamp((vr - 1.0) / (vol_mult - 1.0)) if vr is not None else 0.4
    strength = _clamp(0.4 + 0.3 * conv + 0.3 * vpart)
    return {"key": "triangle_breakout", "label": "三角保ち合い上放れ",
            "strength": round(strength, 3),
            "note": f"高値切り下げ・安値切り上げの収束から直近{window}日レンジを上抜け"}


def ma_pullback_bounce(o, window=10, near_pct=3.0):
    """上昇基調で25日線付近まで調整後に反発（『押し目』の語は使わない）。"""
    close, low = o["close"], o["low"]
    if not _ok(o, 75 + window):
        return None
    n = len(close)
    price = close[-1]
    s25, s75 = _sma_at(close, 25, n - 1), _sma_at(close, 75, n - 1)
    if None in (s25, s75):
        return None
    if not (s25 > s75 and price > s75):     # 上昇基調でない
        return None
    recent_low = min(low[-window:])
    near = abs(recent_low - s25) / s25 * 100 <= near_pct   # 25日線へ接近
    bounce = price > close[-2] and price > s25             # 反発して25日線上
    if not (near and bounce):
        return None
    prox = _clamp(1.0 - abs(recent_low - s25) / s25 / (near_pct / 100))
    strength = _clamp(0.5 + 0.5 * prox)
    return {"key": "ma_pullback_bounce", "label": "移動平均接近後の反発",
            "strength": round(strength, 3),
            "note": "上昇基調のなか25日線付近まで調整し、反発して25日線の上に回復"}


def cup_with_handle(o, window=60):
    """カップウィズハンドルの近似（左リム→谷→右リム回復→小さな調整→上抜け）。"""
    close = o["close"]
    if not _ok(o, window + 5):
        return None
    seg = close[-window:]
    third = window // 3
    left_rim = max(seg[:third])
    trough = min(seg[third:2 * third])
    right_rim = max(seg[2 * third:])
    price = close[-1]
    # カップ: 谷が両リムより十分低く、右リムが左リム付近まで回復
    depth = (left_rim - trough) / left_rim if left_rim else 0
    recovered = right_rim >= left_rim * 0.97
    # ハンドル: 直近数日で軽い調整のあと当日リム上抜け
    handle_low = min(close[-5:])
    handle = handle_low < right_rim and price >= left_rim * 0.99
    if not (0.1 <= depth <= 0.5 and recovered and handle):
        return None
    strength = _clamp(0.4 + 0.6 * (1 - abs(depth - 0.25) / 0.25))
    return {"key": "cup_with_handle", "label": "カップウィズハンドル（近似）",
            "strength": round(strength, 3),
            "note": "調整で作った受け皿型から高値圏を回復し、直近の小調整を経て上抜け"}


DETECTORS = [golden_cross, new_high_breakout, triangle_breakout,
             ma_pullback_bounce, cup_with_handle]


def detect_all(ohlcv):
    """全パターンを適用し、検出できたものを strength 降順で返す。"""
    fired = []
    for fn in DETECTORS:
        try:
            r = fn(ohlcv)
        except Exception:
            r = None
        if r:
            fired.append(r)
    fired.sort(key=lambda p: p["strength"], reverse=True)
    return fired
