"""
samples/generate_extended_samples.py

機能拡張（成績蓄積・追跡・パーソナライズ）のダミーデータ出力サンプル（成果物6）。

  1. samples/sample_delivery_v2.md   … 通常配信文（追跡セクション込み）
  2. samples/sample_monthly_report.md … 月次成績レポート

実運用と同じ followup / recommendation_tracker / report_writer / personalize の
ロジックで生成する（ネットワーク不要・銘柄と数値はすべてダミー）。

実行: python samples/generate_extended_samples.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import followup
import personalize
import recommendation_tracker as rt
import report_writer as rw

# 既存サンプルのダミー市場・銘柄を再利用
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "gs", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "generate_sample.py"))
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)


def _series(start_date, prices):
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    out = []
    for p in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d.isoformat(), float(p)))
        d += timedelta(days=1)
    return out


def _build_dummy_tracking(tmpdir):
    """ダミーの推奨履歴・多期間トラック・イベントを一時ディレクトリに構築する。"""
    recs_path = os.path.join(tmpdir, "recommendations.csv")
    tracks_path = os.path.join(tmpdir, "price_tracks.csv")
    events_path = os.path.join(tmpdir, "followup_events.csv")

    # 過去8週×5銘柄の推奨履歴と5d/1d/3dトラック（勝ち負け混在の現実的な系列）
    weeks = [
        ("2026-05-25", [3.2, -1.1, 0.8, 2.4, -0.5], 1.1),
        ("2026-06-01", [-2.0, 1.5, -3.4, 0.2, -1.0], -0.8),
        ("2026-06-08", [5.1, 2.2, 1.0, -0.7, 3.3], 1.9),
        ("2026-06-15", [-4.2, -1.8, 0.5, -2.6, -3.0], -2.4),
        ("2026-06-22", [1.2, 0.4, 2.8, -0.3, 1.1], 0.6),
        ("2026-06-29", [2.6, 4.0, -1.2, 1.8, 0.9], 1.3),
        ("2026-07-06", [-1.5, -2.2, 0.3, -0.8, -1.1], -1.0),
        ("2026-07-13", [3.4, 1.1, 2.0, 4.2, 0.6], 1.7),
    ]
    recs, tracks = [], []
    for rd, rets, bench in weeks:
        exit_d = (datetime.strptime(rd, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        for i, r5 in enumerate(rets, start=1):
            code = f"{rd[5:7]}{rd[8:10]}{i}"
            recs.append({"run_date": rd, "code": code, "name": f"銘柄{i}",
                         "rank": i, "score": "7.5", "entry_price": "1000.00",
                         "basis_conditions": "出来高増加|25日線上|テーマ該当：DX",
                         "basis_count": 3, "basis_total": 9,
                         "ref_upper": "1050.00", "ref_lower": "940.00",
                         "ref_hold": 5, "status": "closed",
                         "close_date": exit_d, "close_reason": "expired"})
            for h, frac in (("1d", 0.3), ("3d", 0.7), ("5d", 1.0), ("20d", 1.4)):
                tracks.append({"run_date": rd, "code": code, "horizon": h,
                               "snap_date": exit_d,
                               "price": f"{1000 * (1 + r5 * frac / 100):.2f}",
                               "return_pct": f"{r5 * frac:.4f}",
                               "nikkei_return_pct": f"{bench * frac:.4f}"})
    # 直近（監視中）の推奨2件
    for rd, code, name in [("2026-07-16", "4432", "サンプルＡ"),
                           ("2026-07-17", "2471", "サンプルＢ")]:
        recs.append({"run_date": rd, "code": code, "name": name, "rank": 1,
                     "score": "8.2", "entry_price": "1000.00",
                     "basis_conditions": "出来高増加|25日線上|テーマ該当：DX",
                     "basis_count": 3, "basis_total": 9,
                     "ref_upper": "1050.00", "ref_lower": "940.00",
                     "ref_hold": 5, "status": "open",
                     "close_date": "", "close_reason": ""})
    rt._write_csv(recs_path, rt.REC_FIELDS, recs)
    rt._write_csv(tracks_path, rt.TRACK_FIELDS, tracks)
    return recs_path, tracks_path, events_path


def main():
    tmpdir = tempfile.mkdtemp(prefix="sample_ext_")
    recs_path, tracks_path, events_path = _build_dummy_tracking(tmpdir)
    base = os.path.dirname(os.path.abspath(__file__))

    # ── 1. 通常配信文（追跡セクション込み） ──
    open_recs = [r for r in rt.load_recommendations(recs_path)
                 if r["status"] == "open"]
    prices = {"4432": 1052.0, "2471": 935.0}  # A=上値メド到達 / B=下値ライン割れ
    events = followup.detect_events(open_recs, prices, today_str="2026-07-22",
                                    existing_events=[])
    followup.record_events(events, path=events_path)
    tracking_lines = followup.build_morning_section(
        open_recs=open_recs, current_prices=prices, path=events_path)

    msum = rt.monthly_summary(tracks_path=tracks_path, recs_path=recs_path)
    followup_text = rw.build_followup_text(
        gs.MARKET, gs.SCORED, gs.STATS,
        validations=[], macro_context=gs.MACRO,
        theme_ranking=[{"theme": "半導体", "count": 12, "avg_score": 7.9,
                        "stocks": [{"name": "サンプル半導体", "code": "9990",
                                    "score": 8.6}]}],
        basis_label="データ基準日：7月21日 大引け時点",
        performance=msum["performance"], tracking_lines=tracking_lines)

    # パーソナライズの見え方（価格帯フィルタのグループ化デモ）
    users = [{"user_id": "U_default", "price_cap": None, "active": True},
             {"user_id": "U_cap10", "price_cap": 100000.0, "active": True}]
    groups = personalize.group_users(users, gs.SCORED)

    md = ["# サンプル：通常配信文（フォローアップ込み・ダミーデータ）", "",
          "> 実運用と同じ followup / recommendation_tracker / report_writer で生成。"
          "銘柄・数値はすべてダミー。", "",
          "## 補足テキスト（3通目・追跡セクション込み）", "",
          "```", followup_text, "```", "",
          "## 即時通知の例（場中・登録読者のみに送信）", "", "```",
          followup.build_instant_text(events, now_str="10:30"), "```", "",
          "## パーソナライズ（価格帯フィルタ）のグループ化", ""]
    for g in groups:
        label = "全銘柄版（設定なし読者）" if g["is_default"] else "フィルタ版"
        md.append(f"- **{label}**: {len(g['user_ids'])}人 → "
                  f"表示 {len(g['stocks'])}銘柄（{'・'.join(s['name'] for s in g['stocks'])}）")
        note = personalize.filtered_note(g, len(gs.SCORED))
        if note:
            md.append(f"  - {note}")
    out1 = os.path.join(base, "sample_delivery_v2.md")
    with open(out1, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"通常配信サンプル: {out1}")

    # ── 2. 月次成績レポート ──
    monthly_text = rw.build_monthly_report_text(msum, "2026年7月度まで")
    out2 = os.path.join(base, "sample_monthly_report.md")
    with open(out2, "w", encoding="utf-8") as f:
        f.write("# サンプル：月次成績レポート（ダミーデータ）\n\n```\n"
                + monthly_text + "\n```\n")
    print(f"月次レポートサンプル: {out2}")

    # NG語チェック（全出力）
    from promo import ng_words
    for path in (out1, out2):
        hits = ng_words.check_ng(open(path, encoding="utf-8").read())
        print(f"NG語チェック {os.path.basename(path)}: {hits or 'クリーン'}")


if __name__ == "__main__":
    main()
