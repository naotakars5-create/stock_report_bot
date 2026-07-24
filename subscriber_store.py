"""
subscriber_store.py

読者ごとの設定（機能拡張3）へのアクセス層。

読者設定の実体は Cloudflare Workers + D1（webhook/ 配下）にあり、LINEの
ポストバック（リッチメニュー）で書き込まれる。バッチ側（GitHub Actions）は
本モジュール経由で **読むだけ**（書き込みはWebhook側の責務・関心の分離）。

データ源は2系統（上から順に試す）:
  1. Worker API（環境変数 SUBSCRIBER_API_URL / SUBSCRIBER_API_TOKEN）
     GET {url}/export → {"users":[{user_id,price_cap,active}...],
                          "watch_items":[{user_id,code,kind}...]}
  2. ローカルCSV（data/subscribers.csv / data/watch_items.csv）
     Worker 稼働前の手動管理・テスト用フォールバック。

どちらも無ければ空（＝読者設定なし・従来通りの一斉配信で体験は劣化しない）。

個人情報の最小化:
  保持するのは LINE userId と設定値（価格上限・銘柄コード・種別）のみ。
  保有株数・取得単価・氏名などは設計上持たない。
"""

import csv
import os

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


USERS_CSV = os.path.join("data", "subscribers.csv")
WATCH_CSV = os.path.join("data", "watch_items.csv")

KIND_INTEREST = "interest"   # 「気になる」登録
KIND_HOLDING = "holding"     # 保有銘柄の登録


def _read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] {path} の読み込みに失敗しました: {e}")
        return []


def _fetch_from_api(timeout=15):
    """Worker API から読者設定を取得する。未設定・失敗は None（フォールバックへ）。"""
    url = (os.environ.get("SUBSCRIBER_API_URL") or "").strip().rstrip("/")
    token = (os.environ.get("SUBSCRIBER_API_TOKEN") or "").strip()
    if not url or not token or requests is None:
        return None
    try:
        resp = requests.get(f"{url}/export", timeout=timeout,
                            headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            print(f"[読者設定] API取得失敗: HTTP {resp.status_code}（ローカルCSVへフォールバック）")
            return None
        data = resp.json()
        return {"users": data.get("users") or [],
                "watch_items": data.get("watch_items") or []}
    except Exception as e:
        print(f"[読者設定] API取得で例外（ローカルCSVへフォールバック）: {e}")
        return None


def load_settings():
    """
    読者設定を取得する（Worker API → ローカルCSV → 空 の順）。

    戻り値: {
      "users": [{"user_id", "price_cap"(float|None), "active"(bool)}, ...],
      "watchers": {code: {"interest": [user_id,...], "holding": [user_id,...]}},
    }
    """
    raw = _fetch_from_api()
    if raw is None:
        raw = {
            "users": _read_csv(USERS_CSV),
            "watch_items": _read_csv(WATCH_CSV),
        }

    users = []
    for u in raw["users"]:
        uid = (u.get("user_id") or "").strip()
        if not uid:
            continue
        active = str(u.get("active", "1")).strip().lower() not in ("0", "false", "")
        cap = u.get("price_cap")
        try:
            cap = float(cap) if cap not in (None, "") else None
        except (TypeError, ValueError):
            cap = None
        users.append({"user_id": uid, "price_cap": cap, "active": active})

    watchers = {}
    active_ids = {u["user_id"] for u in users if u["active"]}
    for w in raw["watch_items"]:
        uid = (w.get("user_id") or "").strip()
        code = (w.get("code") or "").strip()
        kind = (w.get("kind") or "").strip() or KIND_INTEREST
        if not uid or not code:
            continue
        if active_ids and uid not in active_ids:
            continue  # ブロック済み読者には送らない
        bucket = watchers.setdefault(code, {KIND_INTEREST: [], KIND_HOLDING: []})
        if uid not in bucket.setdefault(kind, []):
            bucket[kind].append(uid)
    return {"users": users, "watchers": watchers}


def watchers_for_codes(codes, settings=None):
    """
    指定銘柄のいずれかを「気になる/保有」登録している読者IDの集合を返す。

    即時通知の宛先（登録読者のみ）を決めるために使う。
    """
    settings = settings or load_settings()
    out = set()
    for code in codes:
        bucket = settings["watchers"].get(str(code).strip())
        if not bucket:
            continue
        for kind in (KIND_INTEREST, KIND_HOLDING):
            out.update(bucket.get(kind) or [])
    return sorted(out)
