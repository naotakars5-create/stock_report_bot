"""
data_fetcher.py

yfinance を使って市場データ（指数・為替・個別株）を取得するモジュール。

- 取得に失敗した銘柄があっても全体が止まらないように、各取得を try/except で囲む
- エラー時は原因がわかるように警告メッセージを表示し、None を返す
"""

import csv
import time

import pandas as pd

try:
    import yfinance as yf
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "yfinance がインストールされていません。\n"
        "  pip install -r requirements.txt\n"
        "を実行してください。"
    ) from e


# 取得する市場指数・為替（表示名: yfinanceティッカー）
# 注: TOPIX 指数そのものは Yahoo Finance で安定して取得できないため、
#     TOPIX連動ETF(1306.T)を代理指標として使用している。
MARKET_TICKERS = {
    "日経平均": "^N225",
    "TOPIX(連動ETF)": "1306.T",
    "ドル円": "JPY=X",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
}


def load_stock_list(csv_path="stocks.csv"):
    """
    stocks.csv を読み込み、[{"code": ..., "name": ...}, ...] のリストを返す。

    CSV フォーマット:
        code,name
        7203.T,トヨタ自動車
    """
    stocks = []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if code and name:
                    stocks.append({"code": code, "name": name})
    except FileNotFoundError:
        print(f"[エラー] 銘柄リストが見つかりません: {csv_path}")
        return []
    except Exception as e:
        print(f"[エラー] 銘柄リストの読み込みに失敗しました: {e}")
        return []

    if not stocks:
        print(f"[警告] {csv_path} に有効な銘柄がありませんでした。")
    return stocks


def _download_history(ticker, period="6mo", interval="1d"):
    """
    1ティッカー分の株価履歴を取得する内部関数。
    失敗時は None を返す（例外は投げない）。
    """
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is None or df.empty:
            print(f"[警告] データが空でした: {ticker}")
            return None
        return df
    except Exception as e:
        print(f"[警告] データ取得に失敗しました ({ticker}): {e}")
        return None


def fetch_market_data():
    """
    市場指数・為替の最新値と前日比を取得する。

    戻り値:
        {
            "日経平均": {"price": float, "change_pct": float},
            ...
        }
    取得できなかった指標は値が None になる。
    """
    print("市場指数・為替データを取得中...")
    market = {}
    for name, ticker in MARKET_TICKERS.items():
        df = _download_history(ticker, period="1mo")
        if df is None or len(df) < 2:
            market[name] = {"price": None, "change_pct": None}
            continue

        close = df["Close"].dropna()
        if len(close) < 2:
            market[name] = {"price": None, "change_pct": None}
            continue

        latest = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        change_pct = (latest - prev) / prev * 100 if prev else None
        market[name] = {"price": latest, "change_pct": change_pct}
        time.sleep(0.2)  # API への配慮

    return market


def _ticker_of(stock):
    """銘柄dictからyfinance用ティッカーを取り出す。無ければ code+.T を生成。"""
    ticker = stock.get("ticker")
    if ticker:
        return ticker
    code = stock.get("code", "")
    # すでに .T 等が付いていればそのまま、数字コードなら .T を付与
    return code if "." in code else f"{code}.T"


def fetch_histories(stocks, period, min_rows, stage_label="データ取得",
                    progress_every=10, sleep_sec=0.1):
    """
    複数銘柄の株価履歴をまとめて取得する汎用関数（2段階スクリーニング共用）。

    引数:
        stocks: [{"code","name","ticker",...}, ...]
        period: yfinanceのperiod（一次は"3mo"、二次は"6mo"など）
        min_rows: 必要な最低行数（これ未満はデータ不足としてスキップ）
        stage_label: 進捗表示用のラベル
        progress_every: 何件ごとに進捗を表示するか
        sleep_sec: API配慮のための1件あたり待機秒

    戻り値:
        [{...元のstock, "history": DataFrame}, ...]
        （取得失敗・データ不足はスキップ。全体は止まらない）
    """
    total = len(stocks)
    print(f"[{stage_label}] {total} 銘柄のデータ取得を開始します (period={period})...")
    results = []
    skipped = 0
    for i, stock in enumerate(stocks, start=1):
        ticker = _ticker_of(stock)
        df = _download_history(ticker, period=period)
        if df is None or len(df) < min_rows:
            skipped += 1
        else:
            results.append({**stock, "history": df})
            if sleep_sec:
                time.sleep(sleep_sec)

        if i % progress_every == 0 or i == total:
            print(f"  進捗 {i}/{total}  取得成功 {len(results)} / スキップ {skipped}")

    print(f"[{stage_label}] 完了: 取得成功 {len(results)} / {total} 件 "
          f"（スキップ {skipped} 件）")
    return results


def get_nikkei_history():
    """
    日経平均の履歴（相対強さの計算用）を取得する。失敗時は None。
    """
    return _download_history("^N225", period="6mo")
