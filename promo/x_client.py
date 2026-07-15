"""
promo/x_client.py

X(旧Twitter)への投稿クライアント（tweepy ラッパー）。

設計方針:
  - 認証情報（X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET）が
    未設定なら **送信せずスキップ**（ローカル実行やSecrets未設定でも落ちない）。
  - `dry_run=True` なら投稿せず、投稿予定文をログ出力するだけ。
  - 失敗時は **1回だけリトライ** し、それでも失敗したら管理者へLINE通知。
  - **例外は外に出さない**（X投稿の失敗でLINE配信本体を落とさない）。
  - 投稿直前に禁止語チェックを行い、検出時は投稿せず管理者通知（人間の確認へ）。
  - 投稿できたら promo_posts.csv に記録（重複防止・効果分析用）。
"""

import os
import time

from . import ng_words, promo_posts

try:
    import tweepy
except ImportError:  # pragma: no cover
    tweepy = None

# X APIの上限に配慮した投稿本文の最大長（全角も1文字として概算）
MAX_TWEET_CHARS = 280


def _env(name):
    return (os.environ.get(name) or "").strip()


def get_credentials():
    """X APIの認証情報を環境変数から取得（未設定は空文字）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return (_env("X_API_KEY"), _env("X_API_SECRET"),
            _env("X_ACCESS_TOKEN"), _env("X_ACCESS_TOKEN_SECRET"))


def _notify_admin(message, dry_run=False):
    """管理者へLINE通知（line_sender を利用。失敗しても無視）。"""
    try:
        import line_sender
        line_sender.send_admin_alert(message, dry_run=dry_run)
    except Exception as e:
        print(f"[X] 管理者通知に失敗しました: {e}")


def _build_clients():
    """(v2 Client, v1.1 API) を返す。API v1.1 は画像アップロード用。"""
    key, secret, token, token_secret = get_credentials()
    if not all([key, secret, token, token_secret]):
        return None, None
    client = tweepy.Client(consumer_key=key, consumer_secret=secret,
                           access_token=token, access_token_secret=token_secret)
    api = None
    try:
        auth = tweepy.OAuth1UserHandler(key, secret, token, token_secret)
        api = tweepy.API(auth)
    except Exception:
        api = None
    return client, api


def post_tweet(text, kind, image_path=None, dry_run=False, post_date=None):
    """
    Xへ投稿する。

    引数:
        text: 投稿本文
        kind: 投稿種別（promo_posts の記録に使用）
        image_path: 添付画像のパス（任意）
        dry_run: True なら投稿せず内容表示のみ
    戻り値:
        tweet_id（str） / dry-run なら "dry-run" / スキップ・失敗なら None
    例外は送出しない。
    """
    text = (text or "").strip()
    if not text:
        print(f"[X] 本文が空のため投稿をスキップします（{kind}）。")
        return None

    # 禁止語ガード（投稿直前）。検出時は投稿せず人間の確認に回す。
    found = ng_words.check_ng(text)
    if found:
        msg = (f"[X投稿中止] 禁止語を検出したため投稿しませんでした（{kind}）: "
               f"{', '.join(found)}\n本文: {text[:200]}")
        print(msg)
        _notify_admin(msg, dry_run=dry_run)
        return None

    if len(text) > MAX_TWEET_CHARS:
        print(f"[X] 本文が{len(text)}字と長いため投稿をスキップします（{kind}・上限{MAX_TWEET_CHARS}）。")
        _notify_admin(f"[X投稿中止] 本文が長すぎます（{kind}・{len(text)}字）", dry_run=dry_run)
        return None

    if dry_run:
        print(f"[DRY-RUN][X:{kind}] 投稿しません。以下が投稿予定文（{len(text)}字）:")
        print("-" * 50)
        print(text)
        if image_path:
            print(f"[DRY-RUN][X:{kind}] 添付画像: {image_path}")
        print("-" * 50)
        return "dry-run"

    if tweepy is None:
        print("[X] tweepy が未インストールのため投稿できません（pip install -r requirements.txt）。")
        return None

    client, api = _build_clients()
    if client is None:
        print(f"[X] X_API_* が未設定のため投稿をスキップします（{kind}）。")
        return None

    media_ids = None
    if image_path and api is not None and os.path.exists(image_path):
        try:
            media = api.media_upload(filename=image_path)
            media_ids = [media.media_id]
        except Exception as e:
            print(f"[X] 画像アップロードに失敗（本文のみ投稿します）: {e}")

    # 投稿（失敗したら1回だけリトライ）
    last_err = None
    for attempt in (1, 2):
        try:
            resp = client.create_tweet(text=text, media_ids=media_ids)
            tweet_id = str((resp.data or {}).get("id", ""))
            print(f"[X] 投稿成功（{kind}）: id={tweet_id}")
            promo_posts.record(kind, tweet_id, text, post_date=post_date)
            return tweet_id
        except Exception as e:
            last_err = e
            print(f"[X] 投稿失敗（{kind}・{attempt}回目）: {e}")
            if attempt == 1:
                time.sleep(5)  # レート制限・一時障害に備えて少し待って1回だけ再試行

    _notify_admin(f"[X投稿失敗] {kind} の投稿に失敗しました（リトライ後）: {last_err}",
                  dry_run=dry_run)
    return None
