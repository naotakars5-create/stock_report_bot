"""
webhook/richmenu_setup.py

LINEリッチメニューの作成・登録スクリプト（機能拡張3・1回だけ実行）。

レイアウト（2500x1686・2行3列の6ボタン）:
  ┌──────────────┬──────────────┬──────────────┐
  │ 〜5万円      │ 〜10万円     │ 〜30万円     │  ← 価格帯フィルタ（単元購入価格）
  ├──────────────┼──────────────┼──────────────┤
  │ 全銘柄表示    │ 銘柄を評価    │ 設定・保有    │
  └──────────────┴──────────────┴──────────────┘
  ・価格帯/全銘柄/設定確認 → postback（Workerが処理・応答は無料のreply）
  ・銘柄を評価（改善B・Q&A）→ 入力欄に「評価 」を差し込む。続けてコードを打って送ると
    機械的な評価が届く（Workerがキュー→query_worker がpush）
  ・設定・保有 → 現在の設定確認＋「保有 1234」での保有登録を案内

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

# 2行×3列の6ボタン。上段＝価格帯フィルタ（青系）、下段＝機能（緑系）。
# 銘柄Q&A（改善B）を下段中央に配置。保有登録は「設定・保有」から案内する。
MENU = {
    "size": {"width": W, "height": H},
    "selected": True,
    "name": "stock-report-menu",
    "chatBarText": "メニュー",
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
        # 銘柄Q&A: 「評価 」を入力欄に差し込む → 続けてコードを打って送信すると評価が届く
        {"bounds": {"x": CW, "y": CH, "width": CW, "height": CH},
         "action": {"type": "message", "text": "評価 "}},
        {"bounds": {"x": CW * 2, "y": CH, "width": CW, "height": CH},
         "action": {"type": "postback", "data": "action=settings",
                    "displayText": "現在の設定を確認"}},
    ],
}

# (見出し, 補足) の6ボタン。順番は areas と対応。
# 絵文字はgothicフォントに glyph が無く豆腐(□)化するため使わず、テキストで構成する。
BUTTONS = [
    ("〜5万円", "価格帯で絞る"),
    ("〜10万円", "価格帯で絞る"),
    ("〜30万円", "価格帯で絞る"),
    ("全銘柄表示", "フィルタ解除"),
    ("銘柄を評価", "コードを送ると診断"),
    ("設定・保有", "設定確認／保有登録"),
]

# 日本語フォント（環境にある gothic を探索。無ければ Pillow 既定＝英数字のみ）。
_JP_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]


def _font(size):
    from PIL import ImageFont
    for path in _JP_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _generate_image(path):
    """Pillow で6分割のリッチメニュー画像を日本語フォントで描く。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), "#0B2340")
    d = ImageDraw.Draw(img)
    title_f, sub_f, tag_f = _font(104), _font(56), _font(44)
    for i, (title, sub) in enumerate(BUTTONS):
        cx, cy = (i % 3) * CW, (i // 3) * CH
        pad = 14
        top_row = i < 3
        d.rounded_rectangle([cx + pad, cy + pad, cx + CW - pad, cy + CH - pad],
                            radius=28, fill="#1C4E8A" if top_row else "#20614F",
                            outline="#FFFFFF", width=3)
        mx = cx + CW // 2
        # 上部に小さな区分タグ（価格帯 / 機能）、中央に見出し、その下に補足。
        d.text((mx, cy + 70), "価格帯フィルタ" if top_row else "機能",
               font=tag_f, anchor="mm", fill="#8FB2DA" if top_row else "#8FD3BC")
        d.text((mx, cy + CH // 2 + 6), title, font=title_f, anchor="mm",
               fill="#FFFFFF")
        d.text((mx, cy + CH // 2 + 120), sub, font=sub_f, anchor="mm",
               fill="#BFD3EC" if top_row else "#BFE6D6")
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
