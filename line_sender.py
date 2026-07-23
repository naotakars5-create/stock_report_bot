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
LINE_MULTICAST_ENDPOINT = "https://api.line.me/v2/bot/message/multicast"

# LINEの上限は1テキスト5000文字。余裕をもって分割する。
MAX_CHARS_PER_MESSAGE = 4800
# 1回のpushリクエストに含められるメッセージ数の上限
MAX_MESSAGES_PER_REQUEST = 5
# multicast の1リクエストあたり宛先上限
MAX_RECIPIENTS_PER_MULTICAST = 500


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

    TEST_USER_ID が設定されている場合は、配信先を **TEST_USER_ID に限定** する
    （本番ユーザーには送らない・テスト配信モード）。

    戻り値:
        (channel_access_token, user_id)  未設定の項目は空文字
    """
    _load_dotenv()
    token = (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
    test_user = (os.environ.get("TEST_USER_ID") or "").strip()
    user_id = test_user or (os.environ.get("LINE_USER_ID") or "").strip()
    if test_user:
        print(f"[LINE] TEST_USER_ID が設定されているため、テストユーザーにのみ配信します。")
    return token, user_id


def get_admin_user_id():
    """管理者通知先（ADMIN_USER_ID）。未設定なら空文字。"""
    _load_dotenv()
    return (os.environ.get("ADMIN_USER_ID") or "").strip()


def send_admin_alert(message, token=None, admin_id=None, timeout=20, dry_run=False):
    """
    管理者（ADMIN_USER_ID）へ運用アラートを LINE で送る（データ異常・配信中止など）。

    ADMIN_USER_ID 未設定ならログ出力のみ（送信はスキップ）。dry_run 時も送信しない。
    例外は送出しない（監視通知の失敗で本体を止めない）。
    """
    if admin_id is None:
        admin_id = get_admin_user_id()
    if token is None:
        token, _ = get_credentials()
    print(f"[ADMIN ALERT] {message}")
    if dry_run:
        print("[ADMIN ALERT] dry-run のため送信しません。")
        return "skipped"
    if not token or not admin_id:
        print("[ADMIN ALERT] ADMIN_USER_ID / トークン未設定のため送信をスキップします。")
        return "skipped"
    if requests is None:
        return "failed"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"to": admin_id, "messages": [{"type": "text", "text": message[:4900]}]}
    try:
        resp = requests.post(LINE_PUSH_ENDPOINT, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            print("[ADMIN ALERT] 管理者へ通知しました。")
            return "sent"
        print(f"[ADMIN ALERT] 送信失敗: HTTP {resp.status_code} {resp.text}")
        return "failed"
    except Exception as e:
        print(f"[ADMIN ALERT] 送信中に例外: {e}")
        return "failed"


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


def send_multicast(message_objects, user_ids, token=None, timeout=30,
                   dry_run=False, label="multicast"):
    """
    同一内容のメッセージ束（最大5オブジェクト）を複数読者へ multicast 送信する。

    メッセージ課金の最小化（重要）:
      LINE の課金は「送信リクエスト1回×受信者数」でカウントされ、1リクエスト内の
      メッセージオブジェクト（最大5個）はまとめて1通扱い。したがって毎朝の
      サマリーカード＋銘柄カルーセル＋補足テキストは **必ず1リクエストに束ねて**
      送る（read: ライトプラン5,000通/月で読者200人×22営業日=4,400通に収める）。

    宛先は500人ずつに分割する。戻り値: "sent" / "skipped" / "failed"。
    例外は送出しない。
    """
    user_ids = [u for u in (user_ids or []) if u]
    if not user_ids or not message_objects:
        return "skipped"
    if len(message_objects) > MAX_MESSAGES_PER_REQUEST:
        # 1通に収まらない場合は先頭5件に丸める（課金増を静かに起こさない）
        print(f"[LINE] {label}: メッセージが{len(message_objects)}件のため"
              f"先頭{MAX_MESSAGES_PER_REQUEST}件に丸めます（1通に収めるため）。")
        message_objects = message_objects[:MAX_MESSAGES_PER_REQUEST]
    if token is None:
        token, _ = get_credentials()
    if not token:
        print(f"[LINE] {label}: トークン未設定のためスキップします。")
        return "skipped"
    if dry_run:
        print(f"[DRY-RUN] {label}: {len(user_ids)}人へ {len(message_objects)}"
              f"オブジェクト（=1通/人）を送信予定（送信はしません）。")
        return "dry-run"
    if requests is None:
        return "failed"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    ok = True
    for batch in _batched(user_ids, MAX_RECIPIENTS_PER_MULTICAST):
        payload = {"to": batch, "messages": message_objects}
        try:
            resp = requests.post(LINE_MULTICAST_ENDPOINT, headers=headers,
                                 json=payload, timeout=timeout)
            if resp.status_code == 200:
                print(f"[LINE] {label}: {len(batch)}人へ送信成功（1通/人）。")
            else:
                ok = False
                print(f"[LINE] {label}: 送信失敗 HTTP {resp.status_code} {resp.text}")
        except Exception as e:
            ok = False
            print(f"[LINE] {label}: 送信中に例外: {e}")
    return "sent" if ok else "failed"


def send_report(followup_text, flex_messages=None, fallback_text=None,
                token=None, user_id=None, timeout=30, dry_run=False):
    """
    レポートをLINEへ「カード中心」で Push 送信する。

    通常の送信順:
        1. サマリーFlexカード
        2. 銘柄別Carousel Flexカード
        3. 短い補足テキスト（ニュース／テーマ／検証のみ・カードと重複しない）

    Flexメッセージの送信に成功した場合は、銘柄詳細はカードに集約されているため
    テキストは「短い補足テキスト」だけを送る（長文の詳細テキストは送らない）。

    Flexメッセージの送信に失敗した場合のみ、カードが届かない分を補う短縮テキスト
    （fallback_text: 上位銘柄の概要＋補足）にフォールバックする。フォールバック時も
    長文にはせず、補足テキスト側で最大1500字に収めている。

    引数:
        followup_text: カードの後に送る短い補足テキスト（最大1500字）
        flex_messages: [(alt_text, contents), ...]（サマリーカード・銘柄カルーセル）
        fallback_text: Flex送信失敗時のみ使う短縮テキスト（無ければ followup_text）
        token / user_id: 明示指定が無ければ環境変数から取得
    戻り値:
        "sent"    : 必要なメッセージを送信できた
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

    if not flex_msgs and not (followup_text or "").strip():
        print("[LINE] 送信する内容が空のためスキップします。")
        return "skipped"

    # dry-run: 実際には送らず、送信予定の内容だけを表示して終了。
    if dry_run:
        print(f"[DRY-RUN] 送信は行いません。宛先={user_id[:6]}… / "
              f"Flexカード {len(flex_msgs)} 件 / 補足テキスト {'あり' if followup_text else 'なし'}")
        print("[DRY-RUN] --- 補足テキスト本文 ---")
        print(followup_text or "(なし)")
        return "dry-run"

    # 1. Flexメッセージ（サマリーカード＋銘柄カルーセル）
    flex_ok = True
    if flex_msgs:
        flex_ok = _post_batches(flex_msgs, headers, user_id, timeout, "Flexカード")

    # 2. テキスト送信。Flex成功時は短い補足テキスト、失敗時のみ短縮フォールバック。
    if flex_ok:
        text_to_send = followup_text
    else:
        print("[LINE] Flexメッセージの送信に失敗したため、"
              "短縮テキストへフォールバックします。")
        text_to_send = fallback_text or followup_text

    text_label = "補足テキスト" if flex_ok else "短縮テキスト"
    text_msgs = [{"type": "text", "text": c}
                 for c in split_message(text_to_send or "")]
    text_ok = _post_batches(text_msgs, headers, user_id, timeout, text_label)

    if flex_ok and text_ok:
        print("[LINE] すべてのメッセージを送信しました。")
        return "sent"
    if text_ok:
        print("[LINE] Flexは一部失敗しましたが、短縮テキストは送信しました。")
        return "sent"
    print("[LINE] テキストの送信にも失敗しました（処理は継続します）。")
    return "failed"
