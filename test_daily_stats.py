"""
test_daily_stats.py

P1-3 パーセンタイル文脈づけの改修（raw_pass_rate 導入＋同値・低分散の安全弁）の
受け入れテスト。外部通信は行わず、ダミーデータのみで検証する。

実行方法:
    python3 -m unittest test_daily_stats -v

確認内容:
  1. daily_stats.csv に raw_pass_rate 列が保存・読み込みできること
  2. 旧形式（raw_pass_rate 列なし）のCSVが壊れず自動移行されること
  3. percentile_context の順位付けが正しく動くこと
  4. 同値タイ・低分散（固定値）のとき表示が抑制されること
  5. screen_primary がキャップ前の条件合致数を返し、日々の市場の広がりに
     応じて raw_pass_rate が変動する値として記録されること（ダミー相場で再現）
"""

import os
import tempfile
import unittest

import report_history

try:
    import pandas as pd
    import stock_scorer
except ImportError:  # pandas 未導入環境でも 1〜4 は実行できるようにする
    pd = None
    stock_scorer = None


class TestSaveDailyStat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "daily_stats.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_raw_pass_rate_roundtrip(self):
        report_history.save_daily_stat(
            "2026-07-15", 1.35, None, raw_pass_rate=8.42, path=self.path)
        rows = report_history.load_daily_stats(path=self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pass_rate"], "1.35")
        self.assertEqual(rows[0]["raw_pass_rate"], "8.42")

    def test_raw_pass_rate_omitted_is_blank(self):
        report_history.save_daily_stat("2026-07-15", 1.35, None, path=self.path)
        rows = report_history.load_daily_stats(path=self.path)
        self.assertEqual(rows[0]["raw_pass_rate"], "")

    def test_old_format_csv_migrates(self):
        # 旧形式（raw_pass_rate 列なし）のCSVに新形式で追記しても壊れないこと。
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("stat_date,pass_rate,avg_change_pct,vs_nikkei_pt,"
                    "win_count,lose_count\n")
            f.write("2026-07-14,1.35,2.20,1.45,4,1\n")
        report_history.save_daily_stat(
            "2026-07-15", 1.35, {"avg_return": -0.3, "vs_nikkei": 0.1,
                                 "wins": 2, "losses": 3},
            raw_pass_rate=8.42, path=self.path)
        rows = report_history.load_daily_stats(path=self.path)
        self.assertEqual(len(rows), 2)
        # 旧行は raw_pass_rate 空欄のまま保持され、他の列は失われない
        self.assertEqual(rows[0]["stat_date"], "2026-07-14")
        self.assertEqual(rows[0]["raw_pass_rate"], "")
        self.assertEqual(rows[0]["avg_change_pct"], "2.20")
        self.assertEqual(rows[1]["raw_pass_rate"], "8.42")


class TestPercentileContext(unittest.TestCase):
    def test_lowest_value_shows_context(self):
        hist = [5.0 + i * 0.1 for i in range(30)]  # 5.0〜7.9
        text = report_history.percentile_context(4.5, hist, kind="low")
        self.assertEqual(text, "過去30営業日で最も絞られた水準")

    def test_second_lowest(self):
        hist = [5.0 + i * 0.1 for i in range(30)]
        text = report_history.percentile_context(5.05, hist, kind="low")
        self.assertEqual(text, "過去30営業日で低い方から2番目")

    def test_middle_value_returns_none(self):
        hist = [5.0 + i * 0.1 for i in range(30)]
        self.assertIsNone(report_history.percentile_context(6.55, hist, kind="low"))

    def test_insufficient_history_returns_none(self):
        hist = [5.0 + i * 0.1 for i in range(29)]  # min_n=30 に届かない
        self.assertIsNone(report_history.percentile_context(4.5, hist, kind="low"))

    def test_tie_with_today_suppressed(self):
        # 今日の値と同値が履歴にあると順位が同率になるため表示しない（安全弁）
        hist = [5.0 + i * 0.1 for i in range(29)] + [4.5]
        self.assertIsNone(report_history.percentile_context(4.5, hist, kind="low"))

    def test_constant_history_suppressed(self):
        # 固定値の系列（旧 pass_rate の実態）では毎日「最も絞られた水準」に
        # なってしまうバグの再発防止。低分散の安全弁で必ず None になること。
        hist = [1.35] * 30
        self.assertIsNone(report_history.percentile_context(1.35, hist, kind="low"))
        self.assertIsNone(report_history.percentile_context(1.30, hist, kind="low"))

    def test_kind_high_still_works(self):
        hist = [0.1 * i for i in range(30)]  # 0.0〜2.9
        text = report_history.percentile_context(3.5, hist, kind="high")
        self.assertEqual(text, "過去30営業日で最も高い水準")


def _dummy_history(rising=True, days=40):
    """一次スクリーニングを通過する/しないダミー株価履歴を作る。"""
    if rising:
        closes = [100.0 + 0.3 * i for i in range(days)]      # 緩やかな上昇
        volumes = [100000.0] * (days - 5) + [150000.0] * 5   # 直近出来高増
    else:
        closes = [100.0 - 0.3 * i for i in range(days)]      # 下落（25日線割れ）
        volumes = [100000.0] * days
    return pd.DataFrame({"Close": closes, "Volume": volumes})


@unittest.skipIf(pd is None, "pandas が未導入のためスキップ")
class TestScreenPrimaryRawCount(unittest.TestCase):
    def test_raw_count_is_before_cap(self):
        items = [{"code": str(1000 + i), "history": _dummy_history(rising=True)}
                 for i in range(5)]
        top, raw = stock_scorer.screen_primary(items, top_n=3)
        self.assertEqual(raw, 5)      # 条件合致はキャップ前の5銘柄
        self.assertEqual(len(top), 3)  # 返すのは上位3銘柄

    def test_failing_stocks_not_counted(self):
        items = ([{"code": str(1000 + i), "history": _dummy_history(rising=True)}
                  for i in range(2)] +
                 [{"code": str(2000 + i), "history": _dummy_history(rising=False)}
                  for i in range(3)])
        top, raw = stock_scorer.screen_primary(items, top_n=50)
        self.assertEqual(raw, 2)
        self.assertEqual(len(top), 2)

    def test_raw_pass_rate_varies_day_by_day(self):
        # ダミー相場5日分: 条件合致銘柄数を日ごとに変えて実行し、
        # daily_stats.csv に記録される raw_pass_rate が変動することを確認する。
        universe_size = 100
        qualifying_per_day = [60, 55, 70, 52, 58]  # いずれも top_n=50 を超える
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "daily_stats.csv")
            for day, n_qualify in enumerate(qualifying_per_day):
                items = (
                    [{"code": str(1000 + i), "history": _dummy_history(True)}
                     for i in range(n_qualify)] +
                    [{"code": str(5000 + i), "history": _dummy_history(False)}
                     for i in range(universe_size - n_qualify)]
                )
                top, raw = stock_scorer.screen_primary(items, top_n=50)
                # 旧実装の問題の再現: キャップ後の通過率は毎日同じ値になる
                pass_rate = len(top) / universe_size * 100
                raw_pass_rate = raw / universe_size * 100
                report_history.save_daily_stat(
                    f"2026-07-{15 + day:02d}", pass_rate, None,
                    raw_pass_rate=raw_pass_rate, path=path)

            rows = report_history.load_daily_stats(path=path)
            capped = {r["pass_rate"] for r in rows}
            raws = [float(r["raw_pass_rate"]) for r in rows]
            self.assertEqual(capped, {"50.00"})           # 旧指標は固定値のまま
            self.assertEqual(raws, [60.0, 55.0, 70.0, 52.0, 58.0])  # 新指標は日々変動
            self.assertGreater(len(set(raws)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
