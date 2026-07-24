# Phase 3 ゴーライブ手順（パーソナライズ＋Q&A＋リッチメニュー）

読者パーソナライズ（価格帯フィルタ・気になる/保有登録）、銘柄Q&A、リッチメニューを
本番稼働させる手順。**この順番で進めること**（リッチメニューは Worker 稼働後に登録する）。

前提: 毎朝配信（`daily_report.yml`）はこの Phase 3 とは独立に動く。Worker 未デプロイでも
配信自体は従来通り動く（読者設定が無ければ一斉配信にフォールバック）。

---

## 0. 事前に用意するもの
- Cloudflare アカウント（無料枠）
- LINE Developers の Messaging API チャネル（`LINE_CHANNEL_SECRET` /
  `LINE_CHANNEL_ACCESS_TOKEN`）
- 任意の長いランダム文字列（`EXPORT_TOKEN`。バッチ↔Worker 認証用）

## 1. Cloudflare Worker + D1 をデプロイ
```
cd webhook
npm install -g wrangler && wrangler login
wrangler d1 create stock-report-subscribers
#  → 出力された database_id を wrangler.toml の [[d1_databases]] に貼る
wrangler d1 execute stock-report-subscribers --file=schema.sql --remote
wrangler secret put LINE_CHANNEL_SECRET
wrangler secret put LINE_CHANNEL_ACCESS_TOKEN
wrangler secret put EXPORT_TOKEN          # 上で用意したランダム文字列
wrangler deploy
#  → https://stock-report-webhook.<アカウント>.workers.dev が発行される
```

## 2. LINE Webhook を接続
- LINE Developers コンソール → Messaging API 設定
- Webhook URL: `https://stock-report-webhook.<アカウント>.workers.dev/webhook`
- 「Webhookの利用」を **ON**、「応答メッセージ」を **OFF**（自動応答が邪魔をしないため）
- ここまでで友だち追加のウェルカム応答・価格帯設定・保有登録・Q&A受付が動く

## 3. GitHub Secrets を登録（バッチが Worker を読む）
リポジトリ Settings → Secrets and variables → Actions:
- `SUBSCRIBER_API_URL` = `https://stock-report-webhook.<アカウント>.workers.dev`
  （末尾に /export などは付けない）
- `SUBSCRIBER_API_TOKEN` = `EXPORT_TOKEN` と同じ値

これで毎朝配信が読者設定を取得し、価格帯フィルタ付き multicast に切り替わる。

## 4. リッチメニューを登録 ← ここで初めて登録する
Worker が稼働しボタンが反応する状態になってから登録する。

- 本ブランチ（またはマージ後の main）で GitHub Actions → **Setup Rich Menu** を
  手動実行（`setup_richmenu.yml`）。同梱の `webhook/richmenu.png` を全読者の
  デフォルトメニューに設定する。
  - ※`setup_richmenu.yml` を API/ボタンから起動するには、このワークフローが
    **デフォルトブランチ(main)に存在する**必要がある（GitHubの仕様）。PR #2 を
    マージするか、main にこのファイルを取り込んでから実行する。
- 画像を差し替えたい場合は `python webhook/richmenu_setup.py 自作画像.png`。

## 5. 場中モニタ・Q&A の外部cronを登録（即時性のため）
GitHub の schedule は遅延が大きいので、外部cron（cron-job.org 等）から起動する:
- 場中モニタ（フォローアップ即時通知）:
  `POST /repos/naotakars5-create/stock_report_bot/dispatches`
  body `{"event_type":"intraday-monitor"}` を平日 9:00–15:30 JST に30分毎
- 銘柄Q&A の回答処理:
  body `{"event_type":"query-worker"}` を平日日中に数分〜十数分毎
  （※`intraday_monitor.yml` / `query_worker.yml` も main に存在する必要がある）

---

## 稼働確認チェックリスト
- [ ] 自分のアカウントで友だち追加 → ウェルカムメッセージが届く
- [ ] リッチメニューの「〜10万円」→ 設定完了の返信が届く
- [ ] 「評価 7203」を送る → 数分後に機械的評価が届く（query-worker 起動後）
- [ ] 配信カードの「⭐気になる」→ 登録完了の返信が届く
- [ ] 翌朝の配信が multicast（ログに「読者 N 人へ multicast 配信」）になっている

## メッセージ通数の注意
ライトプラン 5,000通/月。毎朝配信 ≒ 読者数 × 22営業日。`message_budget` が 80%/95% 超で
管理者に警告する。読者が 200人 を超え始めたらプラン見直しを検討。
