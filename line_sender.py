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


def _post_batches(messages, headers, user_id, timeout, label):
    """
    messages を最大5件ずつのバッチに分けて Push する。
    全バッチ成功で True、1つでも失敗すれば False（例外は握りつぶす）。
    """
    if not messages:
        return True
    total = (len(messages) + MAX_MESSAGES_PER_REQUEST - 1) // MAX_MESSAGES_PER_REQUEST
    print(f"[LINE] {label}: {len(messages)} メッセージ（{total} リクエスト）を送信します...")
    ok = True
    for bi, batch in enumerate(_batched(messages, MAX_MESSAGES_PER_REQUEST), start=1):
        payload = {"to": user_id, "messages": batch}
        try:
            resp = requests.post(
                LINE_PUSH_ENDPOINT, headers=headers, json=payload, timeout=timeout
            )
            if resp.status_code == 200:
                print(f"[LINE] {label} バッチ {bi}/{total} 送信成功"
                      f"（{len(batch)} メッセージ）")
            else:
                ok = False
                # 失敗してもプログラムは止めない。原因がわかるよう本文を表示。
                print(f"[LINE] {label} バッチ {bi}/{total} 送信失敗: "
                      f"HTTP {resp.status_code} {resp.text}")
        except Exception as e:
            ok = False
            print(f"[LINE] {label} バッチ {bi}/{total} 送信中に例外: {e}")
    return ok


def send_report(report, flex_messages=None, token=None, user_id=None, timeout=30):
    """
    レポートをLINEへPush送信する。

    送信順:
        1. Flexメッセージ（まとめカード → 銘柄カルーセル）
        2. テキストの詳細レポート

    Flexメッセージの送信に失敗した場合でも、テキストの詳細レポートは必ず送信する
    （＝通常テキスト送信へのフォールバック）。テキストにも評価バランス図を含むため、
    Flexが崩れても情報は失われない。

    引数:
        report: 送信する本文（注意書き・評価バランス図を含む完成済みレポート）
        flex_messages: [(alt_text, contents), ...] のリスト（まとめカード・カルーセル等）
        token / user_id: 明示指定が無ければ環境変数から取得
    戻り値:
        "sent"    : 必要なメッセージ（最低限テキスト）を送信できた
        "skipped" : 環境変数未設定などで送信せずスキップ
        "failed"  : テキストも含めて送信できなかった
    例外は送出しない（全体を止めない）。
    """
    if token is None or user_id is None:
        env_token, env_user = get_credentials()
        token = token or env_token
        user_id = user_id or env_user

    # 未設定ならスキップ（ターミナル表示のみで完結）
    if not token or not user_id:
        print("[LINE] LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID が未設定のため、"
              "LINE送信をスキップします（ターミナル表示のみ）。")
        return "skipped"

    if requests is None:
        print("[LINE] requests が未インストールのため送信できません。"
              "`pip install -r requirements.txt` を実行してください。")
        return "failed"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    flex_msgs = [
        {"type": "flex", "altText": alt_text, "contents": contents}
        for alt_text, contents in (flex_messages or [])
    ]
    text_msgs = [{"type": "text", "text": c} for c in split_message(report)]

    if not flex_msgs and not text_msgs:
        print("[LINE] 送信する内容が空のためスキップします。")
        return "skipped"

    # 1. Flexメッセージ（まとめカード＋カルーセル）。失敗してもテキストは送る。
    flex_ok = True
    if flex_msgs:
        flex_ok = _post_batches(flex_msgs, headers, user_id, timeout, "Flexカード")
        if not flex_ok:
            print("[LINE] Flexメッセージの送信に失敗したため、"
                  "テキストレポートへフォールバックします。")

    # 2. テキストの詳細レポート（フォールバック先でもある）
    text_ok = _post_batches(text_msgs, headers, user_id, timeout, "テキスト")

    if flex_ok and text_ok:
        print("[LINE] すべてのメッセージを送信しました。")
        return "sent"
    if text_ok:
        print("[LINE] Flexは一部失敗しましたが、テキストレポートは送信しました。")
        return "sent"
    print("[LINE] テキストレポートの送信にも失敗しました（処理は継続します）。")
    return "failed"
