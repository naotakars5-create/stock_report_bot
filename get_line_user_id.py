"""
get_line_user_id.py

LINE_USER_ID（"U" から始まる自分のユーザーID）を取得するための
**一時的な補助スクリプト**。

仕組み:
  1. Flask で Webhook 受信用の簡易サーバーを立てる
  2. ngrok などでローカルサーバーを外部公開する
  3. LINE Developers の「Webhook URL」にその公開URL(/callback)を設定する
  4. 自分のスマホから Bot（公式アカウント）にメッセージを送る
  5. LINE から届いた Webhook の events[].source.userId をターミナルに表示する

⚠️ このスクリプトは LINE_USER_ID を一度取得するためだけのものです。
   本番の毎朝レポート実行（main.py / GitHub Actions）では一切使いません。
   取得が済んだら起動しっぱなしにせず停止してください。

使い方の詳細は README.md「LINE_USER_ID をスクリプトで取得する」を参照。
"""

import sys

# Windows コンソールでも "U..." や日本語が文字化けしないように UTF-8 に再設定
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from flask import Flask, request
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "Flask がインストールされていません。\n"
        "  pip install -r requirements.txt\n"
        "（または pip install flask）を実行してください。"
    ) from e


# 受信を待ち受けるポート。ngrok 側もこのポートに合わせる（例: ngrok http 5000）
PORT = 5000
# LINE Developers の Webhook URL に設定するパス
CALLBACK_PATH = "/callback"

app = Flask(__name__)


@app.get("/")
def index():
    """動作確認用。ブラウザでアクセスすると簡単な案内を返す。"""
    return (
        "get_line_user_id サーバー稼働中です。\n"
        f"LINE の Webhook URL には末尾に {CALLBACK_PATH} を付けて設定してください。\n"
    )


@app.post(CALLBACK_PATH)
def callback():
    """
    LINE からの Webhook(POST) を受け取り、各イベントの source.userId を表示する。

    - LINE は必ず HTTP 200 を期待するため、中身に関わらず 200 を返す。
    - Webhook URL の「検証(Verify)」時は events が空で届く（その場合も 200 を返す）。
    """
    body = request.get_json(silent=True) or {}
    events = body.get("events", [])

    print("\n" + "=" * 56)
    print("Webhook を受信しました")
    print(f"イベント数: {len(events)}")

    if not events:
        print("（events が空です。Webhook URL の検証メッセージか、テスト送信の可能性）")
        print("=" * 56)
        return "OK", 200

    for i, event in enumerate(events):
        source = event.get("source", {}) or {}
        src_type = source.get("type", "不明")
        user_id = source.get("userId")
        print(f"\n[event {i}] type={event.get('type')} source.type={src_type}")
        if user_id:
            print("  ┌────────────────────────────────────────")
            print(f"  │  LINE_USER_ID = {user_id}")
            print("  └────────────────────────────────────────")
            print("  → この値を .env または GitHub Secrets の LINE_USER_ID に設定してください。")
        else:
            print("  userId が含まれていません（グループ/ルームからの送信などの可能性）。")
            print("  個人トークから Bot に直接メッセージを送ると userId が取得できます。")

    print("=" * 56)
    return "OK", 200


if __name__ == "__main__":
    print("=" * 56)
    print("LINE_USER_ID 取得用サーバーを起動します（一時利用・本番では未使用）")
    print(f"  待ち受けポート : {PORT}")
    print(f"  受信パス       : {CALLBACK_PATH}")
    print("  別ターミナルで `ngrok http %d` を実行し、" % PORT)
    print(f"  発行されたURL + {CALLBACK_PATH} を LINE の Webhook URL に設定してください。")
    print("  取得が終わったら Ctrl+C で停止してください。")
    print("=" * 56)
    # debug=False / reloader 無効: ターミナル表示を見やすく保つ
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
