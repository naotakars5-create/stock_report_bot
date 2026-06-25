"""
line_sender.py

LINE Messaging API でレポートを自分のLINEへ Push 送信するモジュール。

設計方針:
  - 環境変数(LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID)が未設定なら
    送信せずスキップする（ターミナル表示だけで完結できる）
  - 送信に失敗してもプログラム全体は落とさない（例外を握りつぶして警告表示）
  - LINEの1メッセージ文字数上限(5000文字)に配慮して分割送信する
  - 1リクエストにつき最大5メッセージまでなのでバッチに分けて送る

必要な環境変数:
  - LINE_CHANNEL_ACCESS_TOKEN : Messaging API のチャネルアクセストークン
  - LINE_USER_ID              : 送信先（自分）のユーザーID
"""

import os


# requests は yfinance の依存にも含まれるが、念のため未導入でも落ちないようにする
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"

# LINEの上限は1テキスト5000文字。余裕をもって分割する。
MAX_CHARS_PER_MESSAGE = 4800
# 1回のpushリクエストに含められるメッセージ数の上限
MAX_MESSAGES_PER_REQUEST = 5


def _load_dotenv():
    """
    ローカル実行用に .env があれば読み込む（無くても問題ない）。
    python-dotenv が未導入でも落ちないようにする。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def get_credentials():
    """
    環境変数から認証情報を取得して返す。

    戻り値:
        (channel_access_token, user_id)  未設定の項目は空文字
    """
    _load_dotenv()
    token = (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
    user_id = (os.environ.get("LINE_USER_ID") or "").strip()
    return token, user_id


def split_message(text, max_chars=MAX_CHARS_PER_MESSAGE):
    """
    長文を行単位で max_chars 以内のチャンクに分割する。
    （単語/行の途中で切れにくいよう、できるだけ改行位置で分ける）

    1行が max_chars を超える場合のみ、その行を強制的に分割する。
    """
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(line) > max_chars:
            # 異常に長い1行はハードに分割
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), max_chars):
                chunks.append(line[i:i + max_chars])
            continue

        if not current:
            current = line
        elif len(current) + 1 + len(line) > max_chars:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line

    if current:
        chunks.append(current)
    return chunks


def _batched(items, size):
    """リストを size 件ずつのバッチに分ける。"""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def send_report(report, flex_messages=None, token=None, user_id=None, timeout=30):
    """
    レポートをLINEへPush送信する。

    引数:
        report: 送信する本文（注意書きを含む完成済みレポート）
        flex_messages: [(alt_text, contents), ...] のリスト。指定すると先頭に
                       Flexメッセージ（まとめカード・詳細カルーセル等）を順に送る。
        token / user_id: 明示指定が無ければ環境変数から取得
    戻り値:
        "sent"    : 送信成功
        "skipped" : 環境変数未設定などで送信せずスキップ
        "failed"  : 送信を試みたが失敗
    例外は送出しない（全体を止めない）。
    """
    if token is None or user_id is None:
        env_token, env_user = get_credentials()
        token = token or env_token
        user_id = user_id or env_user

    # 未設定ならスキップ（要件6）
    if not token or not user_id:
        print("[LINE] LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID が未設定のため、"
              "LINE送信をスキップします（ターミナル表示のみ）。")
        return "skipped"

    if requests is None:
        print("[LINE] requests が未インストールのため送信できません。"
              "`pip install -r requirements.txt` を実行してください。")
        return "failed"

    # 送信メッセージを組み立てる（Flexカード → テキスト本文の順）
    messages = []
    for fm in (flex_messages or []):
        alt_text, contents = fm
        messages.append({"type": "flex", "altText": alt_text, "contents": contents})
    messages.extend({"type": "text", "text": c} for c in split_message(report))

    if not messages:
        print("[LINE] 送信する内容が空のためスキップします。")
        return "skipped"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    total_batches = (len(messages) + MAX_MESSAGES_PER_REQUEST - 1) // MAX_MESSAGES_PER_REQUEST
    print(f"[LINE] {len(messages)} メッセージ"
          f"（{total_batches} リクエスト）に分けて送信します...")

    all_ok = True
    for bi, batch in enumerate(_batched(messages, MAX_MESSAGES_PER_REQUEST), start=1):
        payload = {"to": user_id, "messages": batch}
        try:
            resp = requests.post(
                LINE_PUSH_ENDPOINT, headers=headers, json=payload, timeout=timeout
            )
            if resp.status_code == 200:
                print(f"[LINE] バッチ {bi}/{total_batches} 送信成功"
                      f"（{len(batch)} メッセージ）")
            else:
                all_ok = False
                # 失敗してもプログラムは止めない。原因がわかるよう本文を表示。
                print(f"[LINE] バッチ {bi}/{total_batches} 送信失敗: "
                      f"HTTP {resp.status_code} {resp.text}")
        except Exception as e:
            all_ok = False
            print(f"[LINE] バッチ {bi}/{total_batches} 送信中に例外: {e}")

    if all_ok:
        print("[LINE] すべてのメッセージを送信しました。")
        return "sent"
    print("[LINE] 一部またはすべての送信に失敗しました（処理は継続します）。")
    return "failed"
