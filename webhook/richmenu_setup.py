"""
webhook/richmenu_setup.py

LINEリッチメニューの作成・登録スクリプト（機能拡張3・1回だけ実行）。

レイアウト（2500x1686・2行3列の6ボタン）:
  ┌──────────────┬──────────────┬──────────────┐
  │ 〜5万円      │ 〜10万円     │ 〜30万円     │  ← 価格帯フィルタ（単元購入価格）
  ├──────────────┼──────────────┼──────────────┤
  │ 全銘柄表示    │ 設定確認     │ 保有銘柄登録  │
  └──────────────┴──────────────┴──────────────┘
  ・価格帯/全銘柄/設定確認 → postback（Workerが処理・応答は無料のreply）
  ・保有銘柄登録 → 入力ガイドのメッセージを送るだけ（「保有 1234」と送る方式）

実行:
  LINE_CHANNEL_ACCESS_TOKEN=... python webhook/richmenu_setup.py [画像PNGのパス]
  画像を省略すると Pillow でシンプルな6分割画像を自動生成する（Pillow必要）。
"""

import json
import os
import sys

import requests

API = "https://api.line.me/v2/bot"
W, H = 2500, 1686
CW, CH = W // 3, H // 2

MENU = {
    "size": {"width": W, "height": H},
    "selected": True,
    "name": "stock-report-menu",
    "chatBarText": "設定メニュー",
    "areas": [
        {"bounds": {"x": 0, "y": 0, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=cap&value=50000",
                    "displayText": "価格帯: 5万円以下に設定"}},
        {"bounds": {"x": CW, "y": 0, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=cap&value=100000",
                    "displayText": "価格帯: 10万円以下に設定"}},
        {"bounds": {"x": CW * 2, "y": 0, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=cap&value=300000",
                    "displayText": "価格帯: 30万円以下に設定"}},
        {"bounds": {"x": 0, "y": CH, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=cap&value=0",
                    "displayText": "価格帯フィルタを解除（全銘柄）"}},
        {"bounds": {"x": CW, "y": CH, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=settings",
                    "displayText": "現在の設定を確認"}},
        {"bounds": {"x": CW * 2, "y": CH, "width": CW, "height": CH},
         "action": {"type": "message", "text": "保有 "}},
    ],
}

LABELS = ["〜5万円", "〜10万円", "〜30万円", "全銘柄表示", "設定確認", "保有銘柄登録"]


def _generate_image(path):
    """Pillow でシンプルな6分割メニュー画像を生成する。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), "#13335A")
    d = ImageDraw.Draw(img)
    for i, label in enumerate(LABELS):
        cx, cy = (i % 3) * CW, (i // 3) * CH
        d.rectangle([cx + 8, cy + 8, cx + CW - 8, cy + CH - 8],
                    fill="#1C4E8A" if i < 3 else "#2F5E52", outline="#FFFFFF", width=4)
        d.text((cx + CW // 2, cy + CH // 2), label, fill="#FFFFFF", anchor="mm")
    img.save(path, "PNG")
    return path


def main():
    token = (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN を設定してください。")
        return 1
    headers = {"Authorization": f"Bearer {token}"}

    image_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not image_path:
        image_path = "/tmp/richmenu.png"
        try:
            _generate_image(image_path)
            print(f"メニュー画像を自動生成しました: {image_path}")
        except ImportError:
            print("Pillow が無いため画像を生成できません。PNGパスを引数で渡してください。")
            return 1

    # 1. リッチメニュー作成
    resp = requests.post(f"{API}/richmenu", headers={**headers,
                         "Content-Type": "application/json"}, data=json.dumps(MENU))
    if resp.status_code != 200:
        print(f"作成失敗: {resp.status_code} {resp.text}")
        return 1
    menu_id = resp.json()["richMenuId"]
    print(f"リッチメニュー作成: {menu_id}")

    # 2. 画像アップロード
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"https://api-data.line.me/v2/bot/richmenu/{menu_id}/content",
            headers={**headers, "Content-Type": "image/png"}, data=f.read())
    if resp.status_code != 200:
        print(f"画像アップロード失敗: {resp.status_code} {resp.text}")
        return 1

    # 3. デフォルトメニューに設定（全読者に表示）
    resp = requests.post(f"{API}/user/all/richmenu/{menu_id}", headers=headers)
    if resp.status_code != 200:
        print(f"デフォルト設定失敗: {resp.status_code} {resp.text}")
        return 1
    print("リッチメニューを全読者のデフォルトに設定しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
