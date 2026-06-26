"""
main.py

「日本株 朝のスクリーニング速報（東証スクリーニング版）」のエントリポイント。

本サービスは「おすすめ株」を出すものではなく、東証銘柄を機械的条件で
スクリーニングし、朝の情報整理に使うためのレポートです（売買推奨ではありません）。

処理の流れ:
  0. JPX公式の上場銘柄一覧(data_j.xls)を自動取得し、普通株ユニバースを構築
  1. 市場指数・為替を取得
  2. 市場平均(TOPIX連動ETF)の履歴を取得（相対強度の基準）
  3. 【一次スクリーニング】軽量データで全候補をふるい分け
  4. 【二次スクリーニング】通過銘柄を 7軸でスコアリング → 上位5銘柄を抽出
     （事業内容・テーマ性・流動性・バリュエーション・前回継続性を加味）
  5. 前回レポートの検証（前回上位銘柄のその後の推移を機械的に集計）
  6. レポートをターミナル出力 ＋ LINE配信（まとめカード＋詳細テキスト）
  7. 今回の抽出結果を履歴に保存（次回の検証用）
"""

import os
import sys

# Windows のコンソールが Shift-JIS の場合に日本語が文字化けするのを防ぐため、
# 標準出力・標準エラーを UTF-8 に再設定する（Python 3.7+）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import universe_loader
import jpx_fetcher
import data_fetcher
import profile_loader
import stock_scorer
import report_writer
import report_history
import line_sender


def _int_env(name, default):
    """環境変数を整数で取得。'none'/'all'/'0'/空 は None（全銘柄）扱い。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    v = raw.strip().lower()
    if v in ("none", "all", "0"):
        return None
    try:
        return int(v)
    except ValueError:
        return default


# ====== 設定 ======
JPX_CSV = "jpx_listed_companies.csv"
PROFILES_CSV = "company_profiles.csv"
AUTO_FETCH_JPX = True       # 起動時にJPX公式の上場銘柄一覧(data_j.xls)を自動取得する
JPX_CSV_MAX_AGE_HOURS = 20  # 直近取得からこの時間内なら再ダウンロードを省略

# 分析対象の最大銘柄数。環境変数 MAX_STOCKS で上書き可能（none/all で全銘柄）。
# デフォルトは処理負荷を抑えた 500。全銘柄に広げる方法は README を参照。
MAX_STOCKS = _int_env("MAX_STOCKS", 500)

PRIMARY_TOP_N = 50          # 一次スクリーニングで残す銘柄数
FINAL_TOP_N = 5             # 最終的に抽出する銘柄数（上位5銘柄）
MIN_AVG_VOLUME = 50000      # 流動性フィルタ: 直近20日平均出来高の下限（株）

# yfinance 取得設定
PRIMARY_PERIOD = "3mo"
PRIMARY_MIN_ROWS = 25
DETAIL_PERIOD = "6mo"
DETAIL_MIN_ROWS = 75


def _build_price_map(histories):
    """取得済み銘柄の {証券コード: 最新終値}（前回検証の今回株価に使用）。"""
    price_map = {}
    for item in histories:
        try:
            close = item["history"]["Close"].dropna()
            if len(close):
                price_map[item["code"]] = float(close.iloc[-1])
        except Exception:
            pass
    return price_map


def _fetch_valuations(detail_histories):
    """二次通過銘柄のバリュエーション(PER/PBR/配当/時価総額)を best-effort 取得。"""
    valuations = {}
    total = len(detail_histories)
    print(f"[バリュエーション] {total} 銘柄の指標を取得します（取得不可は中立扱い）...")
    for i, item in enumerate(detail_histories, start=1):
        ticker = item.get("ticker") or f"{item['code']}.T"
        valuations[item["code"]] = data_fetcher.get_valuation(ticker)
        if i % 10 == 0 or i == total:
            print(f"  進捗 {i}/{total}")
    return valuations


def main():
    print("=" * 60)
    print("日本株 朝のスクリーニング速報（東証スクリーニング版）を開始します")
    print(f"  MAX_STOCKS = {MAX_STOCKS if MAX_STOCKS is not None else '全銘柄'}")
    print("=" * 60)

    # 0. JPX公式の上場銘柄一覧を最新化 → 普通株ユニバースを構築
    jpx_fetcher.ensure_jpx_csv(
        JPX_CSV, auto_fetch=AUTO_FETCH_JPX, max_age_hours=JPX_CSV_MAX_AGE_HOURS,
    )
    universe = universe_loader.load_universe(JPX_CSV, max_stocks=MAX_STOCKS)
    if not universe:
        print("[中断] 分析対象の銘柄が構築できませんでした。"
              "jpx_listed_companies.csv を確認してください。")
        return 1

    # 企業プロフィール（事業内容・テーマタグ）を読み込む
    profiles_csv = profile_loader.load_profiles(PROFILES_CSV)

    stats = {
        "universe": len(universe),
        "primary_fetched": 0,
        "primary_passed": 0,
        "detail_fetched": 0,
        "final": 0,
    }

    # 1. 市場指数・為替
    try:
        market = data_fetcher.fetch_market_data()
    except Exception as e:
        print(f"[警告] 市場データの取得で予期せぬエラー: {e}")
        market = {}

    # 2. 市場平均(TOPIX連動ETF)の履歴（相対強度の基準）
    benchmark_df = data_fetcher.get_benchmark_history()
    if benchmark_df is None:
        print("[警告] 市場平均(TOPIX連動ETF)の履歴が取得できませんでした。"
              "相対強度は中立扱いになります。")

    # 前回レポート（検証用）を先に読み込む
    previous = report_history.load_previous()
    previous_codes = set()
    if previous:
        previous_codes = {(e.get("code") or "").strip() for e in previous["entries"]}

    # 3. 一次スクリーニング
    print("\n■ 一次スクリーニング（全候補を軽量データでふるい分け）")
    primary_histories = data_fetcher.fetch_histories(
        universe, period=PRIMARY_PERIOD, min_rows=PRIMARY_MIN_ROWS,
        stage_label="一次スクリーニング",
    )
    stats["primary_fetched"] = len(primary_histories)
    price_map = _build_price_map(primary_histories)

    passed = stock_scorer.screen_primary(
        primary_histories, min_avg_volume=MIN_AVG_VOLUME, top_n=PRIMARY_TOP_N,
    )
    stats["primary_passed"] = len(passed)
    print(f"一次スクリーニング通過: {len(passed)} 銘柄（上位{PRIMARY_TOP_N}に絞り込み）")

    # 4. 二次スクリーニング（7軸スコアリング）
    scored_stocks = []
    if passed:
        print("\n■ 二次スクリーニング（通過銘柄を詳細スコアリング）")
        detail_histories = data_fetcher.fetch_histories(
            passed, period=DETAIL_PERIOD, min_rows=DETAIL_MIN_ROWS,
            stage_label="二次スクリーニング",
        )
        stats["detail_fetched"] = len(detail_histories)
        price_map.update(_build_price_map(detail_histories))

        # 事業内容・テーマタグ（CSV優先、無ければ業種から自動生成）
        profiles_map = {
            it["code"]: profile_loader.get_profile(
                it["code"], it.get("name", ""), it.get("sector", ""), profiles_csv)
            for it in detail_histories
        }
        # バリュエーション（best-effort）
        valuations = _fetch_valuations(detail_histories)

        scored_stocks = stock_scorer.score_all(
            detail_histories, benchmark_df=benchmark_df, top_n=FINAL_TOP_N,
            profiles=profiles_map, valuations=valuations, previous_codes=previous_codes,
        )
    else:
        print("[情報] 一次スクリーニングを通過した銘柄はありませんでした。")
    stats["final"] = len(scored_stocks)

    # 5. 前回レポートの検証
    validation = None
    if previous:
        bench_pct = report_history.benchmark_return(benchmark_df, previous["run_date"])
        validation = report_history.build_validation(previous, price_map, bench_pct)

    # 6. レポート出力（ターミナル＝詳細テキスト、LINE＝まとめカード＋詳細テキスト）
    report = report_writer.build_report(market, scored_stocks, stats, validation=validation)
    flex_messages = [
        report_writer.build_flex_message(market, scored_stocks, stats, validation=validation)
    ]
    print()
    print(report)
    _deliver(report, flex_messages)

    # 7. 今回の抽出結果を履歴に保存（次回の検証用）
    if scored_stocks:
        report_history.save_report(scored_stocks)

    return 0


def _deliver(report, flex_messages=None):
    """
    レポートをLINEへ送信する（まとめカード＋詳細テキスト）。
    環境変数が未設定ならスキップ、失敗しても全体は止めない。
    """
    try:
        line_sender.send_report(report, flex_messages=flex_messages)
    except Exception as e:
        print(f"[警告] LINE送信処理で予期せぬエラー（処理は継続）: {e}")


if __name__ == "__main__":
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\n[中断] ユーザーによって処理が中断されました。")
        exit_code = 130
    except Exception:
        import traceback
        print("\n[致命的エラー] 予期せぬ例外が発生しました:")
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
