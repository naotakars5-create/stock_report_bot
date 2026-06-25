"""
universe_loader.py

JPX上場銘柄一覧(jpx_listed_companies.csv)から、分析対象とする
「普通株ユニバース」を構築するモジュール。

方針:
  - 方法A（優先）: ローカルの jpx_listed_companies.csv を読み込む
  - 方法B（将来）: JPX公式の上場銘柄一覧ファイルを取得して読み込む
    （安定運用のため、現時点では方法Aのみを実装）

絞り込みルール:
  1. 普通株のみを対象とする
     → 「市場・商品区分(market)」に "内国株式" を含む行のみ採用
       （ETF・ETN / REIT・インフラファンド / 外国株 / 出資証券 などを除外）
  2. 優先株などコードが4桁でないものを除外
     → 証券コードがちょうど4文字の行のみ採用
       （例: 伊藤園第1種優先株 25935 のような5桁コードを除外）
  3. yfinance用ティッカーは「コード + .T」で生成（例: 7203 → 7203.T）

将来 JPX公式CSV(data_j.xls をCSV化したもの等)に差し替えられるよう、
列見出しは英語(code/name/market/sector)に加えてJPXの日本語見出しにも対応する。
"""

import csv


DEFAULT_CSV = "jpx_listed_companies.csv"

# 普通株とみなす market 列のキーワード（内国株式）
COMMON_STOCK_MARKET_KEYWORD = "内国株式"

# 列名のエイリアス。CSVの見出しが日本語(JPX公式)でも英語でも読めるようにする。
COLUMN_ALIASES = {
    "code": ["code", "コード"],
    "name": ["name", "銘柄名"],
    "market": ["market", "市場・商品区分", "市場区分"],
    "sector": ["sector", "33業種区分", "業種", "業種区分"],
}


def _build_header_map(fieldnames):
    """
    CSVの実際の見出しから、論理名(code/name/market/sector) → 実見出し
    のマッピングを作る。見つからない論理名はキーに含めない。
    """
    if not fieldnames:
        return {}
    # BOMや前後空白を除去した見出しで照合
    normalized = {fn: (fn or "").strip().lstrip("﻿") for fn in fieldnames}
    header_map = {}
    for logical, aliases in COLUMN_ALIASES.items():
        for raw, norm in normalized.items():
            if norm in aliases:
                header_map[logical] = raw
                break
    return header_map


def load_universe(csv_path=DEFAULT_CSV, max_stocks=300, verbose=True):
    """
    上場銘柄一覧を読み込み、普通株ユニバースを返す。

    引数:
        csv_path: 銘柄一覧CSVのパス
        max_stocks: 分析対象の最大銘柄数（安全のための上限）。None で無制限。
        verbose: 絞り込みの内訳を表示するか

    戻り値:
        [
          {"code": "7203", "ticker": "7203.T", "name": "トヨタ自動車",
           "market": "プライム（内国株式）", "sector": "輸送用機器"},
          ...
        ]
        （ティッカーで重複排除済み・max_stocks件まで）
    """
    rows = []
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header_map = _build_header_map(reader.fieldnames)

            if "code" not in header_map or "name" not in header_map:
                print(f"[エラー] {csv_path} に 'code'/'name' 列が見つかりません。"
                      f"見出し: {reader.fieldnames}")
                return []

            for row in reader:
                rows.append(row)
    except FileNotFoundError:
        print(f"[エラー] 銘柄一覧が見つかりません: {csv_path}")
        return []
    except Exception as e:
        print(f"[エラー] 銘柄一覧の読み込みに失敗しました: {e}")
        return []

    # 絞り込みカウンタ（原因がわかるように内訳を記録）
    total = len(rows)
    excluded_not_common = 0   # 普通株でない（ETF/REIT/外国株など）
    excluded_bad_code = 0     # コードが4桁でない（優先株など）
    excluded_dup = 0          # 重複
    universe = []
    seen = set()

    for row in rows:
        code = (row.get(header_map["code"]) or "").strip()
        name = (row.get(header_map["name"]) or "").strip()
        market = ""
        sector = ""
        if "market" in header_map:
            market = (row.get(header_map["market"]) or "").strip()
        if "sector" in header_map:
            sector = (row.get(header_map["sector"]) or "").strip()

        if not code or not name:
            continue

        # ルール1: 普通株(内国株式)のみ。market列が無い場合は通す（後段ルールで判定）。
        if market and COMMON_STOCK_MARKET_KEYWORD not in market:
            excluded_not_common += 1
            continue

        # ルール2: コードはちょうど4文字（優先株の5桁コード等を除外）
        if len(code) != 4:
            excluded_bad_code += 1
            continue

        ticker = f"{code}.T"
        if ticker in seen:
            excluded_dup += 1
            continue
        seen.add(ticker)

        universe.append({
            "code": code,
            "ticker": ticker,
            "name": name,
            "market": market,
            "sector": sector,
        })

    capped = False
    if max_stocks is not None and len(universe) > max_stocks:
        universe = universe[:max_stocks]
        capped = True

    if verbose:
        print("─" * 60)
        print("ユニバース構築（普通株の絞り込み）")
        print(f"  読み込み総数            : {total} 件")
        print(f"  除外（普通株以外）      : {excluded_not_common} 件 "
              f"(ETF/REIT/インフラ/外国株 等)")
        print(f"  除外（コード4桁でない） : {excluded_bad_code} 件 (優先株 等)")
        print(f"  除外（重複）            : {excluded_dup} 件")
        if capped:
            print(f"  上限(max_stocks={max_stocks})により絞り込み")
        print(f"  → 分析対象ユニバース    : {len(universe)} 件")
        print("─" * 60)

    return universe
