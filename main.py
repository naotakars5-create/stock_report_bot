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
  6. レポートをターミナル出力 ＋ LINE配信（カード中心：サマリーカード＋
     銘柄カルーセル＋短い補足テキスト。長文レポートはLINEには送らない）
  7. 今回の抽出結果を履歴に保存（次回の検証用）
"""

import argparse
import os
import sys
from datetime import datetime

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
import news_fetcher
import macro_analyzer
import stock_scorer
import report_writer
import report_history
import performance
import macro_state
import line_sender
import market_calendar
import recommendation_tracker
import followup
import subscriber_store
import personalize


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

# 分析対象の最大銘柄数。原則として「全銘柄」が既定（None=制限なし）。
# デバッグ用に環境変数 MAX_STOCKS を設定したときだけ、その件数に絞り込む
# （none/all/0/未設定 → 全銘柄）。全銘柄版は取得に時間がかかる点は README を参照。
MAX_STOCKS = _int_env("MAX_STOCKS", None)

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


def _data_basis_date(*dfs):
    """
    取得した市場データ（TOPIX連動ETF・日経平均など）の最新バーの日付を返す。
    ＝「このレポートが基づく立会い日（データ基準日）」。取得不可なら None。
    """
    for df in dfs:
        try:
            if df is not None and len(df.index):
                return df.index[-1].date()
        except Exception:
            continue
    return None


def _basis_label(basis_date):
    """ヘッダー表示用の『データ基準日：M月D日 大引け時点』ラベル。"""
    if basis_date is None:
        return "データ基準日：取得できませんでした"
    return f"データ基準日：{basis_date.month}月{basis_date.day}日 大引け時点"


def _attach_calendar(scored_stocks):
    """
    最終抽出（上位5）銘柄に、決算予定日・配当権利日を best-effort で付与する。

    カレンダー取得は上位数銘柄だけなので追加のAPI負荷は小さい。取得できない項目は
    None のまま（カード側で「データ未対応」と明示）。決算の市場予想比などの評価は
    現状データが無いため取得しない（＝カードでも未対応と表示する）。
    """
    for s in scored_stocks:
        code = (s.get("code") or "").strip()
        ticker = code if "." in code else f"{code}.T"
        try:
            s["calendar"] = data_fetcher.get_calendar_events(ticker)
        except Exception as e:
            print(f"[警告] カレンダー取得に失敗（中立扱い）: {s.get('name')} ({code}): {e}")
            s["calendar"] = {}
    return scored_stocks


def _build_performance(today_str, nikkei_df, persist=True):
    """
    【機能4】成績台帳を同期し、累積成績サマリー（月次含む）を作る。

    過去コホート（report_history の全上位5銘柄）を成績台帳に取り込み、5立会い日
    保有・週次非重複・等加重の複利連鎖で累積成績を集計する。1銘柄の exit 終値は
    yfinance の日足から「抽出日終値の5立会い日後」を取得して確定する。
    取得・集計に失敗しても配信本体は止めない（成績は「集計中」表示にフォールバック）。
    """
    def price_history_provider(code):
        ticker = code if "." in str(code) else f"{code}.T"
        # 5立会い日後の終値まで見られれば十分。3か月あれば直近コホートを確定できる。
        return data_fetcher._download_history(ticker, period="3mo")

    try:
        history_rows = report_history.all_history_rows()
        nikkei_close = nikkei_df if nikkei_df is not None else None
        summary = performance.sync_and_summarize(
            today_str, history_rows, price_history_provider,
            benchmark_series=nikkei_close, persist=persist)
        if summary.get("available"):
            print(f"[成績] 累積リターン {summary['cum_return']:+.2f}% / "
                  f"コホート勝率 {summary.get('cohort_win_rate', 0):.0f}% / "
                  f"最大DD {summary.get('max_drawdown', 0):.1f}%（"
                  f"{summary.get('chain_cohorts', 0)}コホート・非重複）")
        else:
            print("[成績] 累積成績は集計中（確定コホートが未成熟／不足）。")
        return summary
    except Exception as e:
        print(f"[警告] 累積成績の集計で予期せぬエラー（集計中表示にフォールバック）: {e}")
        return {"available": False, "monthly": []}


def _build_tracking(today_str, today, price_map, nikkei_df, persist=True):
    """
    【機能拡張1・2】推奨追跡の一括処理。

      1. 既存 pick_ledger からの初回移行（冪等・persist時のみ）
      2. 多期間（1d/3d/5d/20d）リターンの確定（price_tracks.csv 追記）
      3. 目安保有期間（5立会い日）満了のクローズ＋満了イベント記録
      4. 朝イベント判定（上値メド到達／参考下値ライン割れ・前日終値ベース）
      5. 朝配信に織り込む「追跡」セクションの行を生成
      6. 毎月最初の営業日なら月次成績レポートのテキストを生成

    失敗しても配信本体は止めない。戻り値: (tracking_lines, monthly_text|None)
    """
    def provider(code):
        ticker = code if "." in str(code) else f"{code}.T"
        return data_fetcher._download_history(ticker, period="3mo")

    tracking_lines, monthly_text = [], None
    try:
        if persist:
            recommendation_tracker.migrate_from_pick_ledger()
            recommendation_tracker.update_tracks(today_str, provider,
                                                 nikkei_series=nikkei_df)
            closed = recommendation_tracker.close_expired(today_str)
            followup.record_expirations(closed, today_str=today_str)

        # 朝のイベント判定（前日終値ベース）。場中モニタと同じ二重通知ガードを共有。
        open_recs = recommendation_tracker.open_recommendations()
        events = followup.detect_events(open_recs, price_map, today_str=today_str)
        if events and persist:
            followup.record_events(events)
        tracking_lines = followup.build_morning_section(
            events_today=events, open_recs=open_recs, current_prices=price_map,
            persist=persist)

        # 月次レポート: 毎月最初の営業日に1通（前月までの全期間を一貫基準で開示）。
        prev_bd = market_calendar.previous_trading_day(today)
        if prev_bd.month != today.month:
            msum = recommendation_tracker.monthly_summary()
            label = f"{prev_bd.year}年{prev_bd.month}月度まで"
            monthly_text = report_writer.build_monthly_report_text(msum, label)
            print(f"[月次] {label} の成績レポートを配信に追加します。")
    except Exception as e:
        print(f"[警告] 推奨追跡の処理で予期せぬエラー（配信は継続）: {e}")
    return tracking_lines, monthly_text


def _deliver_to_subscribers(flex_messages, followup_text, monthly_text=None,
                            scored_stocks=None, dry_run=False):
    """
    【機能拡張3】読者への multicast 配信（読者設定があるときのみ使用）。

    - 価格帯フィルタの結果が同じ読者をグループ化し、グループごとに
      「サマリーカード＋（フィルタ済み）銘柄カルーセル＋補足テキスト（＋月次）」を
      **1リクエストに束ねて** multicast（＝1通/人。メッセージ予算の最小化）。
    - 設定なしの読者には全銘柄版が届く（従来体験の維持）。
    戻り値: 配信した読者数（0 なら呼び出し側で従来配信にフォールバック）。
    """
    try:
        settings = subscriber_store.load_settings()
    except Exception as e:
        print(f"[警告] 読者設定の取得に失敗（従来配信へ）: {e}")
        return 0
    users = [u for u in settings["users"] if u["active"]]
    if not users:
        return 0

    groups = personalize.group_users(users, scored_stocks or [])
    total = len(scored_stocks or [])
    sent = 0
    for g in groups:
        msgs = []
        # サマリーカードは全グループ共通（上位5の顔ぶれは透明性のため隠さない）
        if flex_messages:
            alt, contents = flex_messages[0]
            msgs.append({"type": "flex", "altText": alt, "contents": contents})
        # カルーセルはグループごとにフィルタ済み銘柄で再構築
        cards = report_writer.build_stock_cards(g["stocks"]) if g["stocks"] else None
        if cards:
            msgs.append({"type": "flex", "altText": cards[0], "contents": cards[1]})
        text = followup_text or ""
        note = personalize.filtered_note(g, total)
        if note:
            text = note + "\n\n" + text
        if text.strip():
            msgs.append({"type": "text", "text": text[:4900]})
        if monthly_text:
            msgs.append({"type": "text", "text": monthly_text[:4900]})
        status = line_sender.send_multicast(
            msgs, g["user_ids"], dry_run=dry_run,
            label=f"朝配信({'全銘柄版' if g['is_default'] else 'フィルタ版'}"
                  f"・{len(g['user_ids'])}人)")
        if status in ("sent", "dry-run"):
            sent += len(g["user_ids"])
    return sent


def _build_validations(current_prices, nikkei_df, benchmark_df, today_str):
    """過去の上位5銘柄を、前回・3営業日前・1週間前の各回でデータがある範囲で検証する。"""
    runs = report_history.load_runs(before_date=today_str)
    validations = []
    for label, run, _age in report_history.select_horizon_runs(runs, today=today_str):
        nk = report_history.benchmark_return(nikkei_df, run["run_date"])
        tx = report_history.benchmark_return(benchmark_df, run["run_date"])
        v = report_history.build_validation(
            run, current_prices, nikkei_pct=nk, topix_pct=tx, label=label)
        if v:
            validations.append(v)
    return validations


def main(dry_run=False):
    print("=" * 60)
    print("日本株 朝のスクリーニング速報（東証スクリーニング版）を開始します")
    print(f"  MAX_STOCKS = {MAX_STOCKS if MAX_STOCKS is not None else '全銘柄'}")
    if dry_run:
        print("  実行モード: DRY-RUN（LINE送信は行いません）")
    print("=" * 60)

    # 【P0-1】休場日ガード: 土日・祝日・年末年始は配信せず正常終了(exit 0)。
    #   スケジュール実行・手動実行(workflow_dispatch)の両方で先頭に判定する。
    #   ただし手動検証のため FORCE_RUN=true のときは休場日でも実行する（上書き）。
    force_run = (os.environ.get("FORCE_RUN") or "").strip().lower() in ("1", "true", "yes", "on")
    today = market_calendar.today_jst()
    is_open, reason = market_calendar.is_market_open(today)
    if not is_open and not force_run:
        print(f"Market closed: {today} ({reason})")
        print("休場日のため配信をスキップして正常終了します。")
        return 0
    if not is_open and force_run:
        print(f"[強制実行] {today} は休場日（{reason}）ですが、FORCE_RUN のため実行します"
              "（手動検証用）。休場日データの汚染を避けるため履歴・集計は保存しません。")
    # 休場日の強制実行では、週末の据え置きデータで履歴を汚さないよう永続化を抑止する。
    skip_persist = force_run and not is_open

    # 【二重配信ガード】同じ日に既に配信済みなら、何もせず正常終了する。
    #   配信時刻の正確性のため外部cron(repository_dispatch)で起動しつつ、保険として
    #   GitHubのschedule(遅延起動あり)も残しているため、両方走ると1日2回配信になり得る。
    #   daily_stats は配信のたびに必ず1行保存されるので、それをマーカーに使う。
    #   （dry-run と強制実行はテスト用途なので対象外）
    today_str = today.strftime("%Y-%m-%d")
    if not dry_run and not force_run and report_history.has_daily_stat(today_str):
        print(f"[スキップ] 本日({today_str})は既に配信済みのため、重複配信を防いで終了します。")
        return 0

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

    # 1.5 世界情勢・経済ニュースの取得とマクロ分析
    #     失敗してもスクリーニングは止めず、ニュース評価は中立扱いにする。
    print("\n■ 世界情勢・経済ニュースの取得")
    try:
        headlines = news_fetcher.fetch_headlines()
    except Exception as e:
        print(f"[警告] ニュース取得で予期せぬエラー（ニュース評価は中立）: {e}")
        headlines = []
    macro_context = macro_analyzer.analyze(headlines, market)
    if macro_context.get("major_themes"):
        print(f"  主要テーマ: {' / '.join(macro_context['major_themes'])}")
    if macro_context.get("market_summary"):
        print(f"  マクロ概況: {macro_context['market_summary']}")
    if not macro_context.get("available"):
        print("[情報] ニュース取得が一部制限されています（ニュース評価は中立扱い）。")

    # 2. 市場平均(TOPIX連動ETF)・日経平均の履歴（相対強度・検証の市場比に使用）
    benchmark_df = data_fetcher.get_benchmark_history()
    if benchmark_df is None:
        print("[警告] 市場平均(TOPIX連動ETF)の履歴が取得できませんでした。"
              "相対強度は中立扱いになります。")
    nikkei_df = data_fetcher.get_nikkei_history()

    # 【P0-2】データ鮮度ガード: 取得データの基準日が「今時点で確定している最新の
    #   立会い日」より古い（データ提供側の遅延・キャッシュ等）なら、古いデータの
    #   再配信を避けるため配信を中止し、管理者へLINE通知する。
    basis_date = _data_basis_date(benchmark_df, nikkei_df)
    expected_date = market_calendar.expected_data_date()
    print(f"[鮮度] データ基準日={basis_date} / 期待基準日={expected_date}")
    if basis_date is None:
        print("[警告] データ基準日を判定できませんでした（市場データ取得失敗）。"
              "配信は継続しますが、管理者に通知します。")
        line_sender.send_admin_alert(
            f"[Daily Stock Report] データ基準日を判定できませんでした。"
            f"本日={today} / 期待基準日={expected_date}。市場データ取得に失敗した可能性。",
            dry_run=dry_run)
    elif basis_date < expected_date:
        msg = (f"[Daily Stock Report] 配信を中止しました（データが古い）。\n"
               f"ジョブ名: Daily Stock Report\n"
               f"データ基準日: {basis_date}\n"
               f"本日(JST): {today}\n"
               f"期待される最新立会い日: {expected_date}\n"
               f"→ 前日以前のデータの再配信を防ぐため中止しました。")
        print("[中止] " + msg.replace("\n", " / "))
        line_sender.send_admin_alert(msg, dry_run=dry_run)
        return 0

    # 過去レポート（検証用）を先に読み込む（当日を含めない）。日付はJST基準の today_str。
    previous = report_history.load_previous(before_date=today_str)
    previous_codes = set()
    if previous:
        previous_codes = {(e.get("code") or "").strip() for e in previous["entries"]}
        # 検証が参照する前回picksが「直近の営業日」かを確認（古ければ警告のみ）。
        prev_expected = market_calendar.previous_trading_day(today)
        prev_actual = (previous.get("run_date") or "").strip()
        if prev_actual and prev_actual != prev_expected.strftime("%Y-%m-%d"):
            print(f"[注意] 検証の参照picks({prev_actual})が直近営業日"
                  f"({prev_expected})と一致しません（実行の欠落や休場の可能性）。")

    theme_ranking = []  # テーマ別ランキング（score_all が候補全体から集計して埋める）

    # 3. 一次スクリーニング
    print("\n■ 一次スクリーニング（全候補を軽量データでふるい分け）")
    primary_histories = data_fetcher.fetch_histories(
        universe, period=PRIMARY_PERIOD, min_rows=PRIMARY_MIN_ROWS,
        stage_label="一次スクリーニング",
    )
    stats["primary_fetched"] = len(primary_histories)
    price_map = _build_price_map(primary_histories)

    # 通過率（物色の裾野）には「絞り込み前の実際の通過数」を使う。
    # top_n で切った後の件数だと常に一定（=PRIMARY_TOP_N）になり、通過率・相場判定・
    # 温度感・パーセンタイルがすべて意味を失うため。
    passed, raw_passed = stock_scorer.screen_primary(
        primary_histories, min_avg_volume=MIN_AVG_VOLUME, top_n=PRIMARY_TOP_N,
    )
    stats["primary_passed"] = raw_passed
    print(f"一次スクリーニング通過: {raw_passed} 銘柄"
          f"（二次スクリーニングは上位{PRIMARY_TOP_N}に絞り込み: {len(passed)} 銘柄）")

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
            macro_context=macro_context, theme_ranking_out=theme_ranking,
        )
    else:
        print("[情報] 一次スクリーニングを通過した銘柄はありませんでした。")
    stats["final"] = len(scored_stocks)

    # 4.5 上位銘柄に決算・配当カレンダー（取得可能な日程のみ）を付与
    if scored_stocks:
        print("\n■ 上位銘柄の決算・配当カレンダーを取得（取得可能な範囲のみ）")
        _attach_calendar(scored_stocks)

    # 5. 過去レポートの検証（前回・3営業日前・1週間前を、データがある範囲で）
    validations = _build_validations(price_map, nikkei_df, benchmark_df, today_str)

    # 5.5 【機能3】マクロ解説の鮮度担保: 前日配信のマクロ文と当日文の類似度を測り、
    #     酷似（使い回し）や前日から数値変化のない項目を落とす。判定結果を
    #     macro_context["_freshness"] に載せ、配信文生成側で「その日ならでは」に絞る。
    prev_macro_state = macro_state.load_state()
    macro_context["_freshness"] = macro_state.evaluate_freshness(
        macro_context, market=market, prev_state=prev_macro_state)

    # 5.6 【機能4】累積成績: 過去コホート（上位5銘柄）を成績台帳に反映し、5立会い日
    #     保有・週次非重複・等加重の複利連鎖で累積リターン/勝率/最大DD/月次を算出する。
    #     dry-run／休場日の強制実行では台帳・月次を永続化しない（データ汚染防止）。
    no_persist = dry_run or skip_persist
    performance_summary = _build_performance(today_str, nikkei_df, persist=not no_persist)

    # 5.7 【機能拡張1・2】推奨追跡: 多期間リターンの確定・満了クローズ・
    #     朝配信に織り込む「追跡」セクション、月次成績レポートの生成。
    tracking_lines, monthly_text = _build_tracking(
        today_str, today, price_map, nikkei_df, persist=not no_persist)

    # 6. レポート出力（LINEは「カード中心」）
    #    LINE送信順: (1)サマリーカード → (2)上位5銘柄の横スライドカード
    #              → (3)短い補足テキスト（ニュース／テーマ／検証のみ）
    #    銘柄ごとの詳細はカードに集約し、補足テキストでは繰り返さない。
    #    長文レポート(build_report)はターミナル確認・将来のWeb/PDF用で、LINEには送らない。
    basis_label = _basis_label(basis_date)

    # P1-2: 前回上位銘柄の「スコア・加点/リスク」と実際の結果を接続した自己言及文。
    pick_results = report_history.build_pick_results(previous, price_map)
    self_ref_lines = report_writer.self_ref_sentences(pick_results)
    # 今回の上位銘柄に、次回の自己言及文用の主因・リスクを付与（履歴に保存する）。
    for s in scored_stocks:
        pr = report_writer.positive_reasons(s, macro_context)
        nr = report_writer.negative_reasons(s)
        s["top_reason"] = pr[0] if pr else ""
        s["top_risk"] = nr[0] if nr else ""

    # P1-3: 通過率のパーセンタイル文脈（過去30営業日が貯まってから表示）。
    #   通過率は絞り込み前の条件合致数ベース（primary_passed）。ただし過去の
    #   daily_stats の pass_rate 列には「絞り込み後の固定値(約1.35)」時代の行が
    #   混ざっているため、パーセンタイルは今回導入した raw_pass_rate 列だけで
    #   評価し、意味の異なる指標が同じ分布に混在しないようにする。
    pass_rate = (stats["primary_passed"] / stats["universe"] * 100
                 if stats.get("universe") else None)
    raw_pass_rate = pass_rate
    hist_rows = report_history.load_daily_stats(before_date=today_str)
    raw_hist = []
    for r in hist_rows:
        try:
            raw_hist.append(float(r.get("raw_pass_rate")))
        except (TypeError, ValueError):
            pass
    pctl = report_history.percentile_context(raw_pass_rate, raw_hist, kind="low")
    pass_rate_ctx = f"通過率は{pctl}" if pctl else None

    report = report_writer.build_report(
        market, scored_stocks, stats, validations=validations,
        macro_context=macro_context, theme_ranking=theme_ranking, basis_label=basis_label)
    followup_text = report_writer.build_followup_text(
        market, scored_stocks, stats, validations=validations,
        macro_context=macro_context, theme_ranking=theme_ranking, basis_label=basis_label,
        performance=performance_summary, tracking_lines=tracking_lines)
    fallback_text = report_writer.build_fallback_text(
        market, scored_stocks, stats, validations=validations,
        macro_context=macro_context, theme_ranking=theme_ranking, basis_label=basis_label,
        performance=performance_summary)
    flex_messages = [
        report_writer.build_flex_message(
            market, scored_stocks, stats, validations=validations,
            macro_context=macro_context, theme_ranking=theme_ranking,
            basis_label=basis_label, pass_rate_ctx=pass_rate_ctx,
            self_ref_lines=self_ref_lines, performance=performance_summary)
    ]
    cards = report_writer.build_stock_cards(scored_stocks, macro_context=macro_context)
    if cards:
        flex_messages.append(cards)
    print()
    print(report)  # ターミナル表示のみ（LINEには送らない長文レポート）
    print("\n--- LINE補足テキスト（実際に送信する短縮版）---")
    print(followup_text)
    if monthly_text:
        print("\n--- 月次成績レポート（本日追加配信）---")
        print(monthly_text)

    # 【機能拡張3】読者設定があれば multicast（価格帯フィルタ・1通/人に束ねる）。
    # 読者がいない・API未設定なら従来のLINE_USER_ID宛て配信（体験は劣化しない）。
    delivered = _deliver_to_subscribers(
        flex_messages, followup_text, monthly_text=monthly_text,
        scored_stocks=scored_stocks, dry_run=dry_run)
    if delivered:
        print(f"[LINE] 読者 {delivered} 人へ multicast 配信しました。")
    else:
        _deliver(followup_text, flex_messages, fallback_text, dry_run=dry_run)
        if monthly_text:
            line_sender.send_report(monthly_text, dry_run=dry_run)

    # 6.5 【機能1-a】X へ朝ダイジェストを投稿（LINE配信の直後）。
    #     休場日の強制実行では、休場日の内容を公開投稿しないようスキップする。
    if skip_persist:
        print("[X] 休場日の強制実行のため、X投稿はスキップします。")
    else:
        _post_morning_digest(today, market, scored_stocks, stats, theme_ranking,
                             dry_run=dry_run)

    # 7. 今回の抽出結果を履歴に保存（次回の検証用）。
    #    dry-run／休場日の強制実行では状態を変更しない（データ汚染防止）。
    #    no_persist は 5.6 で算出済み（dry_run or skip_persist）。
    if scored_stocks and not no_persist:
        report_history.save_report(scored_stocks)
        # 【機能拡張1】推奨の生記録（該当条件・目安レベル込み）。フォローアップと
        # 多期間追跡の土台になる。
        recommendation_tracker.record_today(scored_stocks, run_date=today_str)
    elif no_persist:
        print("[情報] 履歴・日次集計の保存はスキップしました（dry-run または休場日の強制実行）。")

    # 7.2 【機能3】当日のマクロ状態を保存（次回の鮮度判定＝前日比較の基準に使う）。
    if not no_persist:
        macro_state.save_state(macro_context, market=market, run_date=today_str)

    # 7.5 日次集計（通過率・前回検証成績）を保存（P1-3 パーセンタイル用の蓄積）。
    if not no_persist:
        report_history.save_daily_stat(
            today_str, pass_rate, validations[0] if validations else None,
            raw_pass_rate=raw_pass_rate)

    return 0


def _post_morning_digest(today, market, scored_stocks, stats, theme_ranking, dry_run=False):
    """
    【機能1-a】X(旧Twitter)へ朝ダイジェストを投稿する（LINE配信の直後）。

    銘柄名は出さず、市況・温度感・強いテーマ＋LINE導線のみ。
    X投稿の失敗で **LINE配信本体を落とさない** よう、全体を try/except で分離する。
    """
    try:
        import stock_insights as si
        from promo import promo_posts, text_builder, x_client
        post_date = today.strftime("%Y-%m-%d")
        if promo_posts.already_posted("morning_digest", post_date):
            print("[X] 本日の朝ダイジェストは投稿済みのためスキップします。")
            return
        temp = si.daily_temperature(scored_stocks, market, stats)
        text = text_builder.build_morning_digest(today, market, temp, theme_ranking)
        x_client.post_tweet(text, "morning_digest", dry_run=dry_run, post_date=post_date)
    except Exception as e:
        print(f"[警告] X投稿(朝ダイジェスト)で予期せぬエラー（処理は継続）: {e}")


def _deliver(followup_text, flex_messages=None, fallback_text=None, dry_run=False):
    """
    レポートをLINEへ送信する（サマリーカード＋銘柄カルーセル＋短い補足テキスト）。
    Flex送信に失敗した場合のみ、短縮テキスト(fallback_text)へフォールバックする。
    dry_run=True なら送信せず内容表示のみ。環境変数未設定ならスキップ、失敗しても止めない。
    """
    try:
        line_sender.send_report(
            followup_text, flex_messages=flex_messages, fallback_text=fallback_text,
            dry_run=dry_run)
    except Exception as e:
        print(f"[警告] LINE送信処理で予期せぬエラー（処理は継続）: {e}")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="日本株 朝のスクリーニング速報（東証スクリーニング版）")
    parser.add_argument("--dry-run", action="store_true",
                        help="LINEに送信せず、生成内容の表示のみ行う")
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    try:
        exit_code = main(dry_run=_args.dry_run)
    except KeyboardInterrupt:
        print("\n[中断] ユーザーによって処理が中断されました。")
        exit_code = 130
    except Exception:
        import traceback
        print("\n[致命的エラー] 予期せぬ例外が発生しました:")
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
