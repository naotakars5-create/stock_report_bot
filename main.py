"""
main.py

毎朝の日本株 AIレポートBot（東証スクリーニング版）のエントリポイント。

処理の流れ（2段階スクリーニング）:
  0. JPX公式の上場銘柄一覧(data_j.xls)を自動取得し、普通株ユニバースを構築
  1. 市場指数・為替を取得
  2. 日経平均の履歴（相対強さ計算用）を取得
  3. 【一次スクリーニング】軽量データで全候補をふるい分け → 上位50銘柄
  4. 【二次スクリーニング】上位50銘柄を詳細スコアリング → 注目10銘柄
  5. 日本語レポートをターミナルに出力

この段階では LINE送信・自動実行・OpenAI連携は含みません（APIキー不要）。
"""

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
import stock_scorer
import report_writer
import line_sender


# ====== 設定（全銘柄版・各段階の件数）======
JPX_CSV = "jpx_listed_companies.csv"
AUTO_FETCH_JPX = True     # 起動時にJPX公式の上場銘柄一覧(data_j.xls)を自動取得する
JPX_CSV_MAX_AGE_HOURS = 20  # 直近取得からこの時間内なら再ダウンロードを省略
MAX_STOCKS = None         # 分析対象の最大銘柄数。None=全銘柄。数値を入れると上限になる
PRIMARY_TOP_N = 50        # 一次スクリーニングで残す銘柄数
FINAL_TOP_N = 10          # 最終的に選ぶ注目銘柄数
MIN_AVG_VOLUME = 50000    # 流動性フィルタ: 直近20日平均出来高の下限（株）

# 各段階のyfinance取得設定
PRIMARY_PERIOD = "3mo"    # 一次: 5日/25日移動平均・出来高に十分
PRIMARY_MIN_ROWS = 25
DETAIL_PERIOD = "6mo"     # 二次: 75日移動平均の計算に必要
DETAIL_MIN_ROWS = 75


def main():
    print("=" * 60)
    print("日本株 AIレポートBot（東証スクリーニング版）を開始します")
    print("=" * 60)

    # 0. JPX公式の上場銘柄一覧を最新化（自動取得）→ 普通株ユニバースを構築
    jpx_fetcher.ensure_jpx_csv(
        JPX_CSV,
        auto_fetch=AUTO_FETCH_JPX,
        max_age_hours=JPX_CSV_MAX_AGE_HOURS,
    )
    universe = universe_loader.load_universe(JPX_CSV, max_stocks=MAX_STOCKS)
    if not universe:
        print("[中断] 分析対象の銘柄が構築できませんでした。"
              "jpx_listed_companies.csv を確認してください。")
        return 1

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

    # 2. 市場平均(TOPIX連動ETF)の履歴（相対強さ計算の基準）
    #    日経平均(^N225)は異常値を返すことがあるため、安定して取れるTOPIX ETFを基準にする。
    benchmark_df = data_fetcher.get_benchmark_history()
    if benchmark_df is None:
        print("[警告] 市場平均(TOPIX連動ETF)の履歴が取得できませんでした。"
              "相対強さは中立扱いになります。")

    # 3. 一次スクリーニング ----------------------------------------------------
    print("\n■ 一次スクリーニング（全候補を軽量データでふるい分け）")
    primary_histories = data_fetcher.fetch_histories(
        universe,
        period=PRIMARY_PERIOD,
        min_rows=PRIMARY_MIN_ROWS,
        stage_label="一次スクリーニング",
    )
    stats["primary_fetched"] = len(primary_histories)

    passed = stock_scorer.screen_primary(
        primary_histories,
        min_avg_volume=MIN_AVG_VOLUME,
        top_n=PRIMARY_TOP_N,
    )
    stats["primary_passed"] = len(passed)
    print(f"一次スクリーニング通過: {len(passed)} 銘柄（上位{PRIMARY_TOP_N}に絞り込み）")

    if not passed:
        print("[警告] 一次スクリーニングを通過した銘柄がありませんでした。")
        report = report_writer.build_report(market, [], stats)
        print()
        print(report)
        _deliver(report)
        return 0

    # 4. 二次スクリーニング ----------------------------------------------------
    print("\n■ 二次スクリーニング（通過銘柄を詳細スコアリング）")
    detail_histories = data_fetcher.fetch_histories(
        passed,
        period=DETAIL_PERIOD,
        min_rows=DETAIL_MIN_ROWS,
        stage_label="二次スクリーニング",
    )
    stats["detail_fetched"] = len(detail_histories)

    scored_stocks = stock_scorer.score_all(
        detail_histories, benchmark_df=benchmark_df, top_n=FINAL_TOP_N
    )
    stats["final"] = len(scored_stocks)

    # 5. レポート出力
    #    - ターミナルには詳細テキスト
    #    - LINEには「短縮テキスト＋まとめカード＋銘柄別の詳細カード（カルーセル）」
    report_full = report_writer.build_report(market, scored_stocks, stats, detailed=True)
    report_line = report_writer.build_report(market, scored_stocks, stats, detailed=False)
    flex_messages = [report_writer.build_flex_message(market, scored_stocks, stats)]
    flex_messages.extend(report_writer.build_detail_carousels(scored_stocks))
    print()
    print(report_full)
    _deliver(report_line, flex_messages)

    return 0


def _deliver(report, flex_messages=None):
    """
    レポートをLINEへ送信する（まとめカード＋銘柄別の詳細カード＋テキスト本文）。
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
    except Exception as e:
        # 想定外のエラーでも原因がわかるように出力
        import traceback
        print("\n[致命的エラー] 予期せぬ例外が発生しました:")
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
