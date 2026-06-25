# 毎朝の日本株 AIレポートBot（東証スクリーニング版）

日本・世界の株価指数、為替、日本株の株価データをもとに、
**東証上場銘柄全体（約4,000社）から**注目銘柄10銘柄を選定し、
**点数付きの日本語レポート**をターミナルに出力 ＋ **LINEへ自動送信**するBotです。
**GitHub Actions で毎朝7:00（日本時間）に自動実行**できます。
銘柄一覧は **JPX公式の上場銘柄一覧(`data_j.xls`)を起動時に自動取得**するため、
手動でのCSV差し替えは不要です（取得に失敗しても既存CSVで継続します）。

現時点では以下は含まれていません（今後の拡張予定）。

- OpenAI API などの外部AI連携

**株価データの取得にAPIキーは不要**です（`yfinance` の公開データを使用）。
LINE送信を使う場合のみ、LINEの認証情報を環境変数で設定します
（未設定ならターミナル表示だけで動作します）。

---

## 機能概要

1. **JPX公式の上場銘柄一覧(`data_j.xls`)を自動取得**して
   `jpx_listed_companies.csv` を更新し、そこから**普通株ユニバース**を構築
   - 起動時に最新の一覧をダウンロード（直近取得から20時間以内なら省略）
   - ダウンロードに失敗しても既存の `jpx_listed_companies.csv` で続行
   - `市場・商品区分(market)` に「内国株式」を含む行のみ採用
   - ETF・ETN / REIT・インフラファンド / 外国株 / 出資証券 を除外
   - 優先株など証券コードが4桁でないものを除外
   - 流動性が低すぎる銘柄（直近20日平均出来高が下限未満）を除外
   - yfinance用ティッカーは「コード + `.T`」で生成（例: 7203 → 7203.T）
2. 市場指数・為替を取得
   - 日経平均 / TOPIX(連動ETF) / ドル円 / S&P500 / NASDAQ
3. **2段階スクリーニング**で重い処理を抑えつつ候補を絞り込み
   - **一次スクリーニング（軽量・高速）**: 全候補から上位50銘柄へ
     - 25日移動平均より上にある
     - 5日移動平均が25日移動平均を上回っている
     - 出来高が増えている
     - 急騰しすぎていない
     - データ取得できない銘柄はスキップ
   - **二次スクリーニング（詳細）**: 上位50銘柄を6観点で精密スコアリングし注目10銘柄を選定
4. 各銘柄を6つの観点でスコアリング（0.0〜10.0点、小数第1位まで）
   - 5日移動平均と25日移動平均の関係
   - 25日移動平均と75日移動平均の関係
   - 直近出来高が平均より増えているか
   - 日経平均に対して相対的に強いか
   - 直近で急騰しすぎていないか
   - ボラティリティが高すぎないか
5. レポートに以下を日本語で出力
   - 本日の市場概況
   - スクリーニング結果サマリー（分析対象数 / 取得成功数 / 一次通過数 / 最終数）
   - 最終注目10銘柄（銘柄名 / 証券コード / 現在株価 / 評価点 / 想定上昇率 / 注目理由 / リスク）
   - 末尾に注意書き（投資助言ではない旨）

---

## ファイル構成

| ファイル | 役割 |
|----------|------|
| `main.py` | エントリポイント。2段階スクリーニングの処理フローを制御 |
| `jpx_fetcher.py` | JPX公式の `data_j.xls` を自動取得し `jpx_listed_companies.csv` に変換（方法B） |
| `universe_loader.py` | JPX一覧から普通株ユニバースを構築（フィルタリング） |
| `data_fetcher.py` | yfinance による市場・個別株データの取得（進捗表示つき） |
| `stock_scorer.py` | 一次スクリーニング＋二次の詳細スコアリング |
| `report_writer.py` | 日本語レポートの整形 |
| `line_sender.py` | LINE Messaging API への Push 送信（分割送信・失敗時も継続） |
| `get_line_user_id.py` | （補助）`LINE_USER_ID` 取得用の簡易Webhookサーバー。本番では未使用 |
| `jpx_listed_companies.csv` | 上場銘柄一覧（code, name, market, sector）。起動時にJPXから自動更新 |
| `stocks.csv` | （旧MVP版の30銘柄リスト。現在は未使用・参考） |
| `.env.example` | LINE認証情報のテンプレート（`.env` にコピーして使う） |
| `.github/workflows/daily_report.yml` | GitHub Actions（毎朝7:00 JST 自動実行） |
| `requirements.txt` | 依存ライブラリ |
| `README.md` | 本ファイル |

---

## 主な設定（`main.py` 冒頭）

| 設定 | 既定値 | 意味 |
|------|--------|------|
| `AUTO_FETCH_JPX` | `True` | 起動時にJPX公式の上場銘柄一覧を自動取得する |
| `JPX_CSV_MAX_AGE_HOURS` | `20` | 直近取得からこの時間内なら再ダウンロードを省略 |
| `MAX_STOCKS` | `None` | 分析対象の最大銘柄数。`None`=全銘柄。数値で上限を設定 |
| `PRIMARY_TOP_N` | `50` | 一次スクリーニングで残す銘柄数 |
| `FINAL_TOP_N` | `10` | 最終的に選ぶ注目銘柄数 |
| `MIN_AVG_VOLUME` | `50000` | 流動性フィルタ（直近20日平均出来高の下限・株） |

> 既定では `MAX_STOCKS = None`（全銘柄）です。全銘柄を対象にすると一次スクリーニングの
> データ取得に**十数分〜数十分**かかります。処理時間を抑えたい場合は `MAX_STOCKS` に
> 数値（例: `500`）を設定すると、コード昇順で先頭から指定件数に絞り込みます。

---

## 実行方法（Windows / PowerShell）

```powershell
# 1. 仮想環境を作成
python -m venv .venv

# 2. 仮想環境を有効化
.\.venv\Scripts\Activate.ps1

# 3. 依存ライブラリをインストール
pip install -r requirements.txt

# 4. 実行
python main.py
```

> PowerShell でスクリプト実行が許可されていない場合、`Activate.ps1` でエラーが出ることがあります。
> その場合は以下を一度だけ実行してください（現在のユーザーのみに適用）。
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### macOS / Linux の場合

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## 出力イメージ

```
============================================================
       毎朝の日本株 AIレポート（東証スクリーニング版）
       生成日時: 2026年06月24日 08:00
============================================================

【市場概況】
  日経平均     :    39,500.00  （前日比 +0.85%）
  TOPIX(連動ETF) :     2,750.00  （前日比 +0.60%）
  ...

【スクリーニング結果サマリー】
  分析対象銘柄数          : 159 件
  一次データ取得成功数    : 157 件
  一次スクリーニング通過数: 20 件
  二次データ取得成功数    : 20 件
  最終注目銘柄数          : 10 件

【本日の注目銘柄 TOP10】

─── 第1位 ────────────────────────────────
  銘柄名     : レーザーテック
  証券コード : 6920
  現在株価   : 50,610.0 円
  評価点     : 8.8 / 10.0
  想定上昇率 : +7.40%
  注目理由   :
      ・5日線が25日線を22.3%上回り短期上昇基調
      ・過去20日で日経平均を15.4ポイント上回る相対的な強さ
  リスク     :
      ・日次ボラティリティ5.9%と高く値動きが荒い
...
```

---

## 全銘柄版の仕組み（JPX公式の上場銘柄一覧を自動取得：方法B）

本Botは **JPX公式の上場銘柄一覧(`data_j.xls`)を起動時に自動取得**し、
`jpx_listed_companies.csv`（`code,name,market,sector`）に変換してから分析します。
**手動でのExcelダウンロード・CSV差し替えは不要**です。
既定の `MAX_STOCKS = None` で**東証の全普通株（約3,700銘柄）**が対象になります。

担当モジュールは `jpx_fetcher.py` で、`main.py` 冒頭の以下の設定で制御します。

| 設定 | 既定値 | 意味 |
|------|--------|------|
| `AUTO_FETCH_JPX` | `True` | 起動時に自動取得する（`False` で既存CSVのみ使用） |
| `JPX_CSV_MAX_AGE_HOURS` | `20` | 直近取得からこの時間内なら再ダウンロードを省略 |

### 動作の流れ

1. 起動時に `jpx_fetcher.ensure_jpx_csv()` が呼ばれる
2. 直近の取得が新しければ（`JPX_CSV_MAX_AGE_HOURS` 未満）ダウンロードを省略
   - 取得時刻は `jpx_listed_companies.csv.fetched_at`（gitignore済み）に記録
3. それ以外は <https://www.jpx.co.jp/.../data_j.xls> をダウンロードして
   `jpx_listed_companies.csv` を上書き更新
4. **ダウンロードや解析に失敗しても処理は止まらず**、
   既存の `jpx_listed_companies.csv`（前回取得分／同梱分）で続行する

> 💡 `data_j.xls` の読み込みには `xlrd` を使用します（`requirements.txt` に同梱）。
> JPXファイル単体での取得テストは `python jpx_fetcher.py` でも実行できます。

> ⚠️ **処理時間に注意**
> 全銘柄（約3,700銘柄）を対象にすると、一次スクリーニングのデータ取得だけで
> **十数分〜数十分**かかることがあります。処理時間を抑えたい場合は
> `main.py` の `MAX_STOCKS` に数値（例: `500`）を設定してください
> （コード昇順で先頭から指定件数に絞り込みます）。
> 一次スクリーニングは進捗（`進捗 NN/総数`）を表示します。

### フィルタリングの仕組み（自動で普通株のみに絞り込み）

`universe_loader.py` が以下を自動で行います。

- `市場・商品区分` に「内国株式」を含む行のみ採用
  → **ETF・ETN / REIT・インフラファンド / 外国株 / 出資証券 を自動除外**
- 証券コードがちょうど4桁の行のみ採用 → **優先株（5桁コード）などを除外**
- 一次スクリーニング時に **流動性が低すぎる銘柄**（直近20日平均出来高 < `MIN_AVG_VOLUME`）を除外

### 自動取得を使わない場合（方法A：ローカルCSV固定）

ネットワークを使わず手元のCSVだけで運用したい場合は、
`main.py` の `AUTO_FETCH_JPX = False` にしてください。
その状態では `jpx_listed_companies.csv` を編集・差し替えすれば、
それがそのまま分析対象になります（列見出しは日本語/英語どちらも自動認識）。

---

## カスタマイズ

### 候補銘柄ユニバースを変える

`jpx_listed_companies.csv` を編集してください。列は以下の通りです。

```csv
code,name,market,sector
7203,トヨタ自動車,プライム（内国株式）,輸送用機器
6758,ソニーグループ,プライム（内国株式）,電気機器
```

- `code` … 証券コード（**4桁**。`.T` は付けない。yfinance用ティッカーは自動生成）
- `name` … レポートに表示する銘柄名（日本語可）
- `market` … 市場・商品区分。「内国株式」を含む行だけが普通株として採用される
- `sector` … 業種（任意・現状は表示に未使用だが将来の拡張用）

### 各種しきい値・件数を変える

`main.py` 冒頭の `MAX_STOCKS` / `PRIMARY_TOP_N` / `FINAL_TOP_N` / `MIN_AVG_VOLUME` を編集してください。

### スコアリングの重み付けを変える

`stock_scorer.py` の `WEIGHTS` を編集すると、二次スクリーニングの各観点の配点を調整できます。
一次スクリーニングの急騰しきい値・流動性下限は `PRIMARY_MAX_SURGE_5D` / `PRIMARY_MIN_AVG_VOLUME` です。

---

## 注意事項

- 本ツールの評価点・想定上昇率は、過去の株価データに基づく**機械的な計算結果**です。
- **投資助言ではなく、情報整理・スクリーニング支援**を目的としています。
- 投資判断はご自身の責任で行ってください。
- yfinance は非公式APIのため、取得に失敗する銘柄が出ることがあります。
  その場合でも処理は止まらず、取得できた銘柄のみでレポートを生成します。

---

## LINE送信のセットアップ

レポートを自分のLINEへ自動送信するには、LINE Messaging API のチャネルを作成し、
**チャネルアクセストークン**と**自分のユーザーID**を取得します。

### 1. LINE Developers で Messaging API チャネルを作る

1. [LINE Developers コンソール](https://developers.line.biz/console/) にLINEアカウントでログイン
2. **プロバイダー**を作成（例: 自分の名前など。すでにあれば流用可）
3. プロバイダー内で **「新規チャネル作成」→「Messaging API」** を選択
4. チャネル名・説明・カテゴリなど必須項目を入力して作成
5. 作成後、スマホのLINEで、そのチャネル（公式アカウント）を**友だち追加**しておく
   （友だちになっていないとPush送信が届きません）

### 2. チャネルアクセストークンを取得する

1. 作成したチャネルの **「Messaging API設定」** タブを開く
2. 一番下の **「チャネルアクセストークン（長期）」** で **「発行」** を押す
3. 表示された文字列が `LINE_CHANNEL_ACCESS_TOKEN` です（再表示できないので控える）

### 3. LINE_USER_ID（自分のユーザーID）を取得する

ユーザーIDは `U` から始まる文字列です。取得方法の例:

- **方法A（簡単）**: 「Messaging API設定」タブの中の **「あなたのユーザーID」** 欄に
  自分のIDが表示されます（チャネル管理者の場合）。
- **方法B（Webhookで確認）**: チャネルを友だち追加した状態でメッセージを送り、
  Webhookで受信した `events[].source.userId` を確認します。
- いずれの場合も、取得した `U...` の文字列が `LINE_USER_ID` です。

> 補足: ここで使う `LINE_USER_ID` は「友だちのユーザーID」であり、
> プロフィールに表示される「LINE ID（@から始まる検索用ID）」とは**別物**です。

#### 方法B-2: スクリプトで取得する（`get_line_user_id.py`）

「あなたのユーザーID」欄が見つからない場合は、付属の補助スクリプト
`get_line_user_id.py`（Flask製の簡易Webhookサーバー）を使うと確実に取得できます。
Botにメッセージを送ると、受信したWebhookから `userId` をターミナルに表示します。

> ⚠️ このスクリプトは **LINE_USER_ID を一度取得するためだけ**のものです。
> 本番の毎朝レポート（`main.py` / GitHub Actions）では一切使いません。
> 取得が終わったらサーバーは停止してください。

**① 依存をインストール（Flaskを含む）**

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**② サーバーを起動する**

```powershell
python get_line_user_id.py
```

`Running on http://0.0.0.0:5000` と表示されれば起動成功です（ポート5000で待ち受け）。
このターミナルは**起動したまま**にしておきます。

**③ ngrok でローカルサーバーを外部公開する**

LINEのWebhookはインターネットからアクセスできるURLが必要なため、
[ngrok](https://ngrok.com/download) でローカルの5000番ポートを公開します。
**別のターミナル**を開いて実行します（初回はngrokの無料アカウント登録と
`ngrok config add-authtoken <あなたのトークン>` が必要です）。

```powershell
ngrok http 5000
```

表示される `Forwarding` の **`https://xxxxxxxx.ngrok-free.app`** のようなURLを控えます。

**④ LINE Developers の Webhook URL に設定する**

1. LINE Developersコンソールで対象チャネルの **「Messaging API設定」** を開く
2. **Webhook URL** に、③で控えたURLの末尾に **`/callback`** を付けて設定
   - 例: `https://xxxxxxxx.ngrok-free.app/callback`
3. **「検証(Verify)」** を押す → `成功` と出ればOK
   （このときサーバー側ターミナルには「イベント数: 0」と表示されます）
4. **「Webhookの利用」をオン**にする

> 補足: 同じ画面の「応答メッセージ」「あいさつメッセージ」はオフでも構いません。

**⑤ Botにメッセージを送って userId を取得する**

スマホのLINEで、友だち追加した公式アカウント（Bot）に**何かメッセージを送信**します。
すると `get_line_user_id.py` を起動したターミナルに次のように表示されます。

```
========================================================
Webhook を受信しました
イベント数: 1

[event 0] type=message source.type=user
  ┌────────────────────────────────────────
  │  LINE_USER_ID = U1234567890abcdef1234567890abcdef
  └────────────────────────────────────────
  → この値を .env または GitHub Secrets の LINE_USER_ID に設定してください。
========================================================
```

この `U...` が `LINE_USER_ID` です。控えたら、サーバーは **Ctrl+C** で停止し、
ngrok も停止して構いません（以降の毎朝レポートには不要です）。

### 4. ローカルで使う（.env）

```powershell
# テンプレートをコピー
Copy-Item .env.example .env
# .env をエディタで開き、トークンとユーザーIDを記入
```

`.env` の中身:

```
LINE_CHANNEL_ACCESS_TOKEN=（手順2のトークン）
LINE_USER_ID=（手順3のU...から始まるID）
```

この状態で `python main.py` を実行すると、ターミナル表示に加えてLINEにも届きます。
`.env` は `.gitignore` 済みなのでGitには含まれません。

> 環境変数が未設定の場合は、**LINE送信を自動でスキップ**してターミナル表示だけ行います。
> 送信に失敗しても、プログラム全体は止まりません（警告を表示して継続）。
> 長いレポートはLINEの文字数制限（1メッセージ5000文字）に配慮して**自動分割送信**します。

---

## 毎朝の自動実行（GitHub Actions）

`.github/workflows/daily_report.yml` により、**毎朝7:00（日本時間）に自動実行**されます。

### 仕組み

- GitHub Actions の `schedule` (cron) でジョブを起動します。
- cron は **UTC** で指定するため、`0 22 * * *`（UTC 22:00）＝ **翌日 JST 07:00** としています。
- ジョブ内で Python をセットアップ →依存インストール → `python main.py` を実行します。
- LINE認証情報は **GitHub Secrets** から環境変数として渡します。
- 手動でも実行できます（Actionsタブ →「Daily Stock Report」→「Run workflow」）。

> ⚠️ GitHubのcronは混雑状況により**数分〜数十分ずれて起動**することがあります（仕様）。
> 厳密な時刻が必要な場合は、起動時刻を少し早める等で調整してください。

### GitHub Secrets の設定方法

1. GitHubのリポジトリページ → **Settings** → **Secrets and variables** → **Actions**
2. **「New repository secret」** を押し、以下の2つを登録する

   | Name | Secret（値） |
   |------|--------------|
   | `LINE_CHANNEL_ACCESS_TOKEN` | LINEのチャネルアクセストークン |
   | `LINE_USER_ID` | あなたの `U...` から始まるユーザーID |

3. ワークフローはこのSecretsを自動で読み込みます（コードに値を書く必要はありません）。

### 実行タイミングを変えたい

`daily_report.yml` の cron を編集してください（UTC指定）。例:

```yaml
on:
  schedule:
    - cron: "0 22 * * *"   # JST 07:00（既定）
    # - cron: "30 21 * * *" # JST 06:30 にしたい場合
```

---

## 実行方法まとめ

### ローカル実行

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

- `.env` にLINE情報があればLINE送信、なければターミナル表示のみ。

### GitHub Actions での実行

- **自動**: 毎朝7:00 JSTに起動（Secrets設定済みであればLINEへ届く）。
- **手動**: リポジトリの **Actions** タブ →「Daily Stock Report」→「Run workflow」。

---

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `ModuleNotFoundError: yfinance` | `pip install -r requirements.txt` を実行 |
| JPXファイルの読み込みに失敗（`xlrd`） | `pip install -r requirements.txt` を実行（`xlrd>=2.0.1` が必要） |
| 「JPX自動取得に失敗」と表示される | ネットワーク／JPX側URL変更の可能性。既存CSVで自動継続します。`MAX_STOCKS` を絞るか時間をおいて再実行。手動更新は `python jpx_fetcher.py` |
| 銘柄一覧を毎回ダウンロードしてほしくない | `JPX_CSV_MAX_AGE_HOURS` を大きくする、または `AUTO_FETCH_JPX = False` で固定 |
| 全銘柄が「取得失敗」になる | ネットワーク接続を確認。時間をおいて再実行 |
| `Activate.ps1` が実行できない | 上記の `Set-ExecutionPolicy` を参照 |
| ターミナルで日本語が文字化けする | `main.py` 内で標準出力をUTF-8に再設定済み。それでも崩れる場合は `chcp 65001` を実行してから起動 |
| TOPIXが「TOPIX(連動ETF)」と表示される | TOPIX指数本体はyfinanceで安定取得できないため、連動ETF(1306.T)を代理指標として使用しています |
| 特定銘柄だけデータ不足でスキップ | 新規上場などで履歴が短い銘柄。一次/二次で必要日数に満たない場合は自動スキップされます |
| 処理が遅い・終わらない | `MAX_STOCKS` を小さくする（例: 100〜300）。全銘柄版は十数分以上かかることがあります |
| 一次スクリーニング通過が0件 | 相場全体が下落局面だと条件を満たす銘柄が減ります。`MIN_AVG_VOLUME` を下げる等で調整可能 |
| LINEに届かない（送信スキップ表示） | `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` が未設定。`.env` またはSecretsを確認 |
| LINE送信が HTTP 400 で失敗 | `LINE_USER_ID` が誤り（`@`から始まる検索IDではなく`U...`のユーザーID）。チャネルを友だち追加済みかも確認 |
| LINE送信が HTTP 401/403 で失敗 | チャネルアクセストークンが誤り・失効。LINE Developersで再発行して設定し直す |
| GitHub Actionsが動かない | 既定ブランチに `daily_report.yml` がマージされているか、Actionsが有効か、Secretsが登録済みかを確認 |
| 一次/二次の通過数が想定より少ない | 一次は「25日線超え＋5日線>25日線＋出来高増＋非急騰＋流動性」を**すべて**満たす銘柄のみ通過します |
