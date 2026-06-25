"""
jpx_fetcher.py

JPX公式の「東証上場銘柄一覧」(data_j.xls)を自動取得し、
jpx_listed_companies.csv（code,name,market,sector）へ変換するモジュール。
（README でいう「方法B」の実装）

これにより、手動での Excel ダウンロード・CSV 変換なしで
**東証の全銘柄（約4,000社）を対象にした全銘柄版**を運用できる。

設計方針:
  - ネットワークやファイル形式の事情で失敗しても全体を止めない。
    失敗時は既存のローカルCSV（前回取得分／同梱サンプル）にフォールバックする。
  - 毎回ダウンロードし直さないよう、直近の取得時刻をマーカーファイルに記録し、
    十分新しい場合はダウンロードを省略する（max_age_hours）。
    ※ CSV本体の更新時刻ではなくマーカーで判定するのは、GitHub Actions の
      チェックアウトでファイル更新時刻がリセットされても正しく再取得するため。
  - .xls の読み込みには xlrd が必要（requirements.txt に同梱）。
"""

import io
import os
import time


# 公式の上場銘柄一覧（Excel: data_j.xls）。
# 参照ページ: https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
# 注意: JPX 側で URL やファイル形式が変わると取得に失敗する（その場合は既存CSVで継続）。
JPX_DATA_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

# data_j.xls の日本語見出し → 出力CSVの論理列
COLUMN_MAP = {
    "コード": "code",
    "銘柄名": "name",
    "市場・商品区分": "market",
    "33業種区分": "sector",
}

REQUEST_TIMEOUT = 30
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (stock_report_bot)"}


def _marker_path(csv_path):
    """直近の取得時刻を記録するマーカーファイルのパス。"""
    return csv_path + ".fetched_at"


def _last_fetch_age_hours(csv_path):
    """前回の取得成功からの経過時間（時間）。マーカーが無ければ None。"""
    try:
        with open(_marker_path(csv_path), encoding="utf-8") as f:
            ts = float(f.read().strip())
    except (OSError, ValueError):
        return None
    return (time.time() - ts) / 3600.0


def _write_marker(csv_path):
    """取得成功時刻を記録する（失敗しても致命的ではないので握りつぶす）。"""
    try:
        with open(_marker_path(csv_path), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def fetch_jpx_csv(csv_path, url=JPX_DATA_URL, verbose=True):
    """
    JPXから data_j.xls をダウンロードし、code,name,market,sector の
    CSV(utf-8-sig) として csv_path に書き出す。

    成功すれば True、失敗（ネットワーク/解析エラー）なら False を返す。
    失敗時に csv_path は変更しない（既存ファイルを壊さない）。
    """
    # 重い依存はこの関数内で読み込む（自動取得を使わない構成では不要にするため）
    try:
        import requests
        import pandas as pd
    except ImportError as e:
        if verbose:
            print(f"[警告] JPX自動取得に必要なライブラリがありません: {e}")
        return False

    if verbose:
        print(f"JPX上場銘柄一覧をダウンロード中: {url}")
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=REQUEST_HEADERS)
        resp.raise_for_status()
    except Exception as e:
        if verbose:
            print(f"[警告] JPXファイルのダウンロードに失敗しました: {e}")
        return False

    try:
        df = pd.read_excel(io.BytesIO(resp.content), dtype=str, engine="xlrd")
    except Exception as e:
        if verbose:
            print(f"[警告] JPXファイル(data_j.xls)の読み込みに失敗しました: {e}")
            print("       xlrd が未インストールの可能性があります"
                  "（pip install -r requirements.txt）。")
        return False

    # 必要列の存在チェック（JPX側の様式変更を早期に検知する）
    missing = [jp for jp in COLUMN_MAP if jp not in df.columns]
    if missing:
        if verbose:
            print(f"[警告] JPXファイルに想定列が見つかりません: {missing}")
            print(f"       実際の列: {list(df.columns)}")
        return False

    out = df[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    # 余白除去・欠損を空文字へ
    for col in out.columns:
        out[col] = out[col].fillna("").astype(str).str.strip()

    # 一時ファイルへ書いてから置き換える（書き込み中断で既存CSVを壊さない）
    tmp_path = csv_path + ".tmp"
    try:
        out.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, csv_path)
    except Exception as e:
        if verbose:
            print(f"[警告] CSVの書き出しに失敗しました: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False

    _write_marker(csv_path)
    if verbose:
        print(f"  → 取得成功: {len(out)} 行を {csv_path} に保存しました。")
    return True


def ensure_jpx_csv(csv_path, auto_fetch=True, force=False,
                   max_age_hours=20, verbose=True):
    """
    分析に使う jpx_listed_companies.csv を「利用可能な状態」にする。

    - auto_fetch=False: 何もしない（既存CSVをそのまま使う）。
    - 直近の取得が十分新しい（max_age_hours未満）かつ force=False: ダウンロードを省略。
    - それ以外: JPXから取得を試みる。失敗しても既存CSVがあればそれで続行する。

    戻り値:
        True  … csv_path が利用可能（取得成功 or 既存CSVあり）
        False … 利用可能なCSVが無い（取得失敗かつ既存CSVも無い）
    """
    exists = os.path.exists(csv_path)

    if not auto_fetch:
        if not exists and verbose:
            print(f"[警告] 自動取得は無効で、CSVも見つかりません: {csv_path}")
        return exists

    if exists and not force:
        age = _last_fetch_age_hours(csv_path)
        if age is not None and age < max_age_hours:
            if verbose:
                print(f"JPX銘柄一覧は最新です（前回取得から {age:.1f} 時間）。"
                      "ダウンロードを省略します。")
            return True

    if fetch_jpx_csv(csv_path, verbose=verbose):
        return True

    # 取得失敗 → 既存CSV（前回分／同梱サンプル）にフォールバック
    if exists:
        if verbose:
            print("[情報] JPX自動取得に失敗したため、既存のCSVで続行します。")
        return True

    if verbose:
        print("[エラー] JPX自動取得に失敗し、利用可能なCSVもありません。")
    return False


if __name__ == "__main__":
    # 単体で実行すると、強制的に最新のJPX一覧を取得して上書きする。
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "jpx_listed_companies.csv"
    ok = fetch_jpx_csv(target)
    sys.exit(0 if ok else 1)
