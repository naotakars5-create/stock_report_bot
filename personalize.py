"""
personalize.py

読者ごとのパーソナライズ（機能拡張3）のバッチ側ロジック。

責務:
  - 価格帯フィルタ: 読者が設定した「単元購入価格の上限」（price_cap 円）で
    配信する銘柄カードを絞り込む（単元 = 100株。単元購入価格 = 株価 × 100）。
  - 配信グループ化: フィルタ結果が同じ読者をまとめ、multicast のリクエスト数を
    最小化する（LINEの課金は 1リクエスト×受信者数=通数。内容が同じなら
    1回の multicast で済む）。
  - 設定していない読者には全銘柄版（従来通り）が届く＝体験は劣化しない。

すべて純粋関数（副作用なし）。読者設定の取得は subscriber_store が担う。
"""

UNIT_SHARES = 100  # 単元株数


def unit_price(stock):
    """単元購入価格（株価×100株）。価格不明は None。"""
    p = stock.get("price")
    try:
        return float(p) * UNIT_SHARES if p is not None else None
    except (TypeError, ValueError):
        return None


def filter_by_cap(scored_stocks, price_cap):
    """
    価格上限で銘柄を絞る。cap=None は全銘柄。単元価格が不明な銘柄は
    「フィルタで誤って隠さない」方針で残す（判定できないものを勝手に落とさない）。
    """
    if price_cap is None:
        return list(scored_stocks or [])
    out = []
    for s in (scored_stocks or []):
        up = unit_price(s)
        if up is None or up <= price_cap:
            out.append(s)
    return out


def group_users(users, scored_stocks):
    """
    フィルタ結果（銘柄コード集合）が同じ読者をグループ化する。

    引数:
        users: subscriber_store.load_settings()["users"]（active のみ渡すこと推奨）
    戻り値: [{"user_ids": [...], "codes": (code,...), "stocks": [filtered...],
              "is_default": bool}, ...]
      is_default=True のグループが「全銘柄版」（設定なし読者向け・従来体験）。
    """
    groups = {}  # codes_tuple -> {"user_ids": [], "stocks": [...]}
    all_codes = tuple(s.get("code") for s in (scored_stocks or []))
    for u in (users or []):
        if not u.get("active", True):
            continue
        cap = u.get("price_cap")
        filtered = filter_by_cap(scored_stocks, cap)
        codes = tuple(s.get("code") for s in filtered)
        g = groups.setdefault(codes, {"user_ids": [], "stocks": filtered})
        g["user_ids"].append(u["user_id"])
    out = []
    for codes, g in groups.items():
        out.append({"user_ids": g["user_ids"], "codes": codes,
                    "stocks": g["stocks"], "is_default": codes == all_codes})
    # 全銘柄版を先頭に（デフォルト体験を最優先で送る）
    out.sort(key=lambda x: (not x["is_default"], -len(x["user_ids"])))
    return out


def filtered_note(group, total_count):
    """
    フィルタ適用グループ向けの説明1行（配信文に添える）。全銘柄版は None。

    「あなたの設定で絞り込んでいる」ことを明示し、全体では何銘柄あったかも
    伝える（情報を隠したと誤解されないため）。
    """
    if group.get("is_default"):
        return None
    n = len(group.get("stocks") or [])
    return (f"※あなたの価格帯設定により、本日の上位{total_count}銘柄のうち"
            f"{n}銘柄を表示しています（設定はメニューから変更できます）。")
