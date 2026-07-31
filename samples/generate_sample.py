"""
samples/generate_sample.py

改善後の毎朝配信を、**実際のダミーデータ**で1本組み立てて出力するスクリプト（成果物5）。

ネットワーク（yfinance）に触れず、ダミーの scored_stocks / market / macro_context /
成績台帳から、実運用と同じ report_writer / stock_insights / performance のロジックで
配信内容を生成する。機能1〜4がどう配信に反映されるかを確認できる。

実行: python samples/generate_sample.py   → samples/sample_delivery.md に書き出す
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import performance
import report_writer as rw
import stock_insights as si
import macro_state


# ====== ダミー: 市場・マクロ ======
MARKET = {
    "日経平均": {"price": 39820.0, "change_pct": 0.62},
    "TOPIX": {"price": 2765.0, "change_pct": 0.48},
    "ドル円": {"price": 156.8, "change_pct": 0.35},
    "S&P500": {"price": 5620.0, "change_pct": 0.71},
    "NASDAQ": {"price": 18500.0, "change_pct": 0.95},
}

MACRO = {
    "available": True,
    "major_themes": ["半導体", "AI", "データセンター"],
    "positive_theme_tags": ["半導体", "AI", "データセンター", "DX"],
    "caution_theme_tags": ["内需ディフェンシブ"],
    "market_summary": "米ハイテク株が堅調。半導体・AI・データセンターが意識されやすい環境。",
    "us_market_comment": "米国株は堅調。半導体・グロース関連が連想されやすい。",
    "fx_comment": "ドル円は円安方向。輸出・海外売上比率の高い業種が意識されやすい。",
    "rates_comment": "",
    "commodity_comment": "",
    "geopolitical_comment": "",
    "theme_intensity": {"半導体": 1.0, "AI": 0.9, "データセンター": 0.7},
}


def _stock(name, code, sector, score, price, tags, metrics, business, macro_reason,
           macro_caution):
    m = {"gap_5_25": None, "gap_25_75": None, "vol_ratio": None, "turnover": None,
         "rel_strength": None, "surge_5": None, "volatility": None, "per": None,
         "pbr": None, "div_yield": None, "market_cap": None, "news_ratio": 0.6,
         "sma5": None, "sma25": None, "sma75": None, "recent_high_20": None,
         "recent_low_20": None, "recent_high_60": None, "recent_low_60": None,
         "atr14": None}
    m.update(metrics)
    return {
        "code": code, "name": name, "sector": sector, "score": score, "price": price,
        "theme_tags": tags, "business_summary": business, "metrics": m,
        "macro_reason": macro_reason, "macro_caution": macro_caution,
        "reasons": ["移動平均が上向きに揃っている", "テーマ性: " + "・".join(tags[:2])],
        "risks": ["目立ったリスクシグナルは検出されず"],
        "size_category": "中型",
        "display_ratios": {"トレンド": 0.8, "出来高": 0.75, "相対強度": 0.7,
                           "テーマ性": 0.85, "ニュース": 0.65, "割安感": 0.55,
                           "安定性": 0.6},
        "calendar": {}, "news_line": None, "news_detail": None,
    }


SCORED = [
    _stock("サンプル半導体", "9990", "電気機器", 8.6, 3120.0,
           ["半導体", "AI", "DX"],
           {"vol_ratio": 1.92, "gap_5_25": 3.2, "gap_25_75": 2.1, "rel_strength": 4.8,
            "surge_5": 6.1, "volatility": 2.4, "per": 18.5, "pbr": 1.9, "atr14": 78.0,
            "sma5": 3040.0, "sma25": 2960.0, "sma75": 2820.0,
            "recent_low_20": 2905.0, "recent_high_20": 3180.0,
            "recent_low_60": 2680.0, "recent_high_60": 3180.0},
           "半導体製造装置の部材・検査装置を手がける企業。",
           "半導体・AIタグが、半導体・AI関連のニュースと関連", "テーマ先行で短期過熱になりやすい点には注意"),
    _stock("サンプル電力", "9991", "電気・ガス業", 7.8, 1840.0,
           ["電力", "データセンター", "インフラ"],
           {"vol_ratio": 1.35, "gap_5_25": 1.1, "gap_25_75": 0.8, "rel_strength": 1.9,
            "surge_5": 2.4, "volatility": 1.7, "per": 13.2, "pbr": 0.85, "atr14": 41.0,
            "sma5": 1822.0, "sma25": 1790.0, "sma75": 1755.0,
            "recent_low_20": 1760.0, "recent_high_20": 1875.0,
            "recent_low_60": 1680.0, "recent_high_60": 1875.0},
           "電力・送配電とデータセンター向け電力供給を展開する企業。",
           "電力・データセンタータグが、データセンター関連のニュースと関連", None),
    _stock("サンプル防衛", "9992", "機械", 7.2, 6450.0,
           ["防衛", "重工"],
           {"vol_ratio": 2.6, "gap_5_25": 4.1, "gap_25_75": 3.0, "rel_strength": 6.2,
            "surge_5": 13.5, "volatility": 3.8, "per": 26.0, "pbr": 2.4, "atr14": 210.0,
            "sma5": 6210.0, "sma25": 5980.0, "sma75": 5600.0,
            "recent_low_20": 5850.0, "recent_high_20": 6480.0,
            "recent_low_60": 5200.0, "recent_high_60": 6480.0},
           "防衛・宇宙・エネルギー関連の重工業を手がける企業。",
           "防衛・重工タグが、防衛関連のニュースと関連", "テーマ先行で短期過熱になりやすい点には注意"),
]

STATS = {"universe": 3900, "primary_fetched": 3900, "primary_passed": 176,
         "detail_fetched": 50, "final": 3}


# ====== ダミー: 成績台帳（過去8週間の非重複コホートを想定） ======
def _dummy_ledger():
    """8コホート（週次・非重複）を closed で用意。勝ち月・負け月が混在する現実的な系列。"""
    # (run_date, exit_date, [銘柄別リターン%], 日経窓リターン%)
    weeks = [
        ("2026-05-25", "2026-06-01", [3.2, -1.1, 0.8, 2.4, -0.5], 1.1),
        ("2026-06-01", "2026-06-08", [-2.0, 1.5, -3.4, 0.2, -1.0], -0.8),
        ("2026-06-08", "2026-06-15", [5.1, 2.2, 1.0, -0.7, 3.3], 1.9),
        ("2026-06-15", "2026-06-22", [-4.2, -1.8, 0.5, -2.6, -3.0], -2.4),
        ("2026-06-22", "2026-06-29", [1.2, 0.4, 2.8, -0.3, 1.1], 0.6),
        ("2026-06-29", "2026-07-06", [2.6, 4.0, -1.2, 1.8, 0.9], 1.3),
        ("2026-07-06", "2026-07-13", [-1.5, -2.2, 0.3, -0.8, -1.1], -1.0),
        ("2026-07-13", "2026-07-17", [3.4, 1.1, 2.0, 4.2, 0.6], 1.7),
    ]
    ledger = []
    for rd, ed, rets, bench in weeks:
        for i, r in enumerate(rets, start=1):
            entry = 1000.0
            ledger.append({
                "run_date": rd, "code": f"{rd[-2:]}{i}", "name": f"銘柄{i}",
                "rank": str(i), "entry_price": f"{entry:.2f}",
                "exit_date": ed, "exit_price": f"{entry * (1 + r / 100):.2f}",
                "return_pct": f"{r:.4f}", "bench_return_pct": f"{bench:.4f}",
                "status": "closed",
            })
    return ledger


def main():
    # 機能3: 前日マクロと同一文が来た場合の鮮度判定をデモ（fx は前日から変化なし想定）。
    prev_state = {
        "comments": {"fx_comment": MACRO["fx_comment"]},  # 前日と同一 → stale
        "market": {"ドル円": 0.33},                        # 前日比とほぼ同じ → 変化なし
    }
    MACRO["_freshness"] = macro_state.evaluate_freshness(
        MACRO, market=MARKET, prev_state=prev_state)

    # 機能4: ダミー台帳から累積成績を集計（永続化なし）。
    perf = performance.summarize(_dummy_ledger())

    basis_label = "データ基準日：7月17日 大引け時点"
    validations = [{
        "label": "前回", "run_date": "2026-07-17", "evaluated": 5, "total": 5,
        "avg_return": 1.86, "wins": 4, "losses": 1,
        "best": {"name": "銘柄4", "code": "134", "return": 4.2},
        "worst": {"name": "銘柄2", "code": "132", "return": -2.2},
        "nikkei_return": 1.7, "topix_return": 1.5, "vs_nikkei": 0.16, "vs_topix": 0.36,
    }]

    # 実運用と同じ関数で配信内容を生成する。
    followup = rw.build_followup_text(
        MARKET, SCORED, STATS, validations=validations, macro_context=MACRO,
        theme_ranking=[{"theme": "半導体", "count": 12,
                        "avg_score": 7.9, "stocks": [{"name": "サンプル半導体",
                        "code": "9990", "score": 8.6}]},
                       {"theme": "データセンター", "count": 8, "avg_score": 7.4,
                        "stocks": [{"name": "サンプル電力", "code": "9991", "score": 7.8}]}],
        basis_label=basis_label, performance=perf)

    # サマリーカード（Flex）から要点をテキスト化して、機能4/免責の見え方も併記する。
    md = []
    md.append("# サンプル配信（ダミーデータ・改善4機能反映）\n")
    md.append("> 本ファイルは実運用と同じ `report_writer` / `stock_insights` / "
              "`performance` のロジックで生成したサンプルです。銘柄・数値はすべてダミー。\n")

    md.append("---\n\n## ① LINEサマリーカード（トップ）\n")
    md.append(f"**⚠️ はじめにお読みください**\n\n{rw.STRONG_DISCLAIMER_HEAD}\n")
    temp = si.daily_temperature(SCORED, MARKET, STATS)
    judg = si.market_judgment(MARKET, STATS)
    md.append(f"\n- **本日の温度感**：{temp['level']}（{temp['reason']}）")
    md.append(f"- **相場判定**：{judg['label']}")
    md.append(f"- **{basis_label}**\n")
    md.append("**スクリーニング上位3銘柄**\n")
    for i, s in enumerate(SCORED, 1):
        md.append(f"{i}. {s['name']}（{s['code']}）… {s['score']:.1f}/10")
    md.append("\n**📊 累積成績（スクリーニング上位群 vs 日経平均）**\n")
    for ln in rw._performance_card_lines(perf):
        md.append(f"- {ln}")
    md.append(f"\n**⚠️ {rw.STRONG_DISCLAIMER_TAIL}**\n")

    md.append("---\n\n## ② 銘柄カード（横スライド・各銘柄）\n")
    for i, s in enumerate(SCORED, 1):
        basis = si.selection_basis(s)
        tech = si.technical_levels(s)
        inst = si.institutional_view(s)
        md.append(f"### No.{i} {s['name']}（{s['code']}・{s['sector']}） 総合 {s['score']:.1f}/10\n")
        md.append(f"- 事業：{s['business_summary']}")
        md.append(f"- テーマ：{'・'.join(s['theme_tags'])}")
        md.append(f"- ニュース：{s['macro_reason']}")
        md.append(f"\n**✅ 選定根拠（{basis['summary']}）**")
        for it in basis["items"]:
            md.append(f"  - {it}")
        md.append(f"\n**📈 テクニカル節目・目安（参考値）**")
        md.append(f"  - 下値メド：{tech['support']}")
        if tech["resistance"]:
            md.append(f"  - 上値メド：{tech['resistance']}")
        if tech["downside"]:
            md.append(f"  - 参考下値ライン：{tech['downside']}")
            md.append(f"    - {tech['downside_note']}")
        md.append(f"  - 目安保有期間：{tech['holding']}")
        md.append(f"    - {tech['holding_note']}")
        md.append(f"  - ※上記の価格・期間はスクリーニング条件から機械的に算出した参考値です"
                  "（売買推奨ではありません）。")
        md.append(f"\n**⚠️ リスク**")
        for r in si.risk_flags(s)[:3]:
            md.append(f"  - {r}")
        md.append("")

    md.append("---\n\n## ③ 補足テキスト（3通目・機能3/4を含む深掘り）\n")
    md.append("```\n" + followup + "\n```\n")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "sample_delivery.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"サンプル配信を書き出しました: {out_path}")
    print(f"  累積成績: available={perf.get('available')} / "
          f"cum_return={perf.get('cum_return')} / chain={perf.get('chain_cohorts')}")
    print(f"  マクロ鮮度: stale_keys={MACRO['_freshness']['stale_keys']}")


if __name__ == "__main__":
    main()
