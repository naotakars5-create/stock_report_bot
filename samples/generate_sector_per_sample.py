"""
samples/generate_sector_per_sample.py

業種別PERランキングのサンプル出力（ダミー財務データ・ネットワーク不要）。

  1. samples/sector_per_sample.md   … 配信内容（Flexカルーセルの要点＋補足テキスト）
  2. samples/sector_per_scores_sample.csv … 全銘柄スコア一覧（検証用CSV）

実運用と同じ sector_per.ranking / delivery のロジックで生成する。
※実データ実行は J-Quants 認証情報を GitHub Secrets に入れて workflow で行う
  （この環境からは J-Quants API に接続できないため、ここではダミー財務を使用）。

実行: python samples/generate_sector_per_sample.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sector_per import ranking as R
from sector_per import delivery as D
from promo import ng_words


def _stock(code, name, sector, close, eps, roe, er, market_cap, turnover,
           growth=True, no_rev=True, per_hist=None, listing_years=10.0):
    return {
        "code": code, "name": name, "sector33": sector, "market": "プライム",
        "close": close, "forecast_eps": eps, "market_cap": market_cap,
        "avg_turnover": turnover, "listing_years": listing_years,
        "roe": roe, "equity_ratio": er,
        "revenue_or_profit_growth": growth, "no_downward_revision": no_rev,
        "per_history": per_hist or [close / eps * (0.9 + 0.01 * (i % 40)) for i in range(80)],
        "fundamentals_asof": "2026-05-12",
    }


# ダミーの母集団（3業種・各複数銘柄＋バリュートラップで落ちる銘柄・該当なし業種）
STOCKS = [
    # 銀行業（低PERが普通の業種。中央値も低い）
    _stock("8001", "サンプル銀行A", "銀行業", 900, 120, 9.0, 55, 8e11, 3e9),   # PER7.5
    _stock("8002", "サンプル銀行B", "銀行業", 1100, 110, 10.0, 52, 6e11, 2e9),  # PER10
    _stock("8003", "サンプル銀行C", "銀行業", 1300, 100, 8.5, 48, 5e11, 1.5e9),# PER13
    _stock("8004", "サンプル銀行D", "銀行業", 1500, 90, 5.0, 40, 4e11, 1.2e9), # ROE不足で除外
    # 電気機器（PER高め業種）
    _stock("6001", "サンプル電機A", "電気機器", 2400, 120, 14.0, 60, 1.2e12, 5e9),# PER20
    _stock("6002", "サンプル電機B", "電気機器", 3000, 100, 12.0, 58, 9e11, 4e9),  # PER30
    _stock("6003", "サンプル電機C", "電気機器", 1800, 120, 15.0, 62, 7e11, 3e9),  # PER15(業種内で割安)
    # 化学（全員バリュートラップで落ちる→該当なしを明示）
    _stock("4001", "サンプル化学A", "化学", 1000, 100, 4.0, 25, 5e11, 2e9, growth=False),
    _stock("4002", "サンプル化学B", "化学", 1200, 100, 3.0, 20, 4e11, 1.5e9, no_rev=False),
    _stock("4003", "サンプル化学C", "化学", 1400, 100, 2.0, 22, 3e11, 1.1e9, growth=False),
    # 水産・農林業（1社のみ＝業種中央値は出ないが自己過去比で候補化）
    _stock("1301", "サンプル水産", "水産・農林業", 600, 100, 10.0, 45, 4e11, 2e9,
           per_hist=[float(x) for x in range(5, 25)] * 4),  # PER6・歴史的に安い
]


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    rankings, all_rows = R.score_all(STOCKS)
    flex_messages, summary = D.build_delivery(
        rankings, basis_label="データ基準日：8月17日 大引け時点",
        asof_label="2026-05-12")

    # 1. 配信サンプル(Markdown)
    md = ["# サンプル：業種別PERランキング配信（ダミー財務データ）", "",
          "> 実運用と同じ sector_per.ranking / delivery で生成。財務はダミー。"
          "実データ実行はJ-Quants認証をSecretsに入れてworkflowで行う。", "",
          "## Flexカルーセル（業種＝バブル・該当ありの業種のみ）", ""]
    for _alt, carousel in flex_messages:
        for bubble in carousel["contents"]:
            sector = bubble["header"]["contents"][1]["text"]
            md.append(f"### 【{sector}】")
            for c in bubble["body"]["contents"]:
                if c.get("type") == "text":
                    md.append(f"- {c['text']}")
                elif c.get("type") == "box" and c.get("layout") == "baseline":
                    kv = c["contents"]
                    md.append(f"  - {kv[0]['text']}: {kv[1]['text']}")
            md.append("")
    md.append("## 補足テキスト（該当なし業種の明示＋必須免責）")
    md.append("")
    md.append("```")
    md.append(summary)
    md.append("```")
    out1 = os.path.join(base, "sector_per_sample.md")
    with open(out1, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # 2. 検証用CSV（全銘柄スコア一覧）
    out2 = os.path.join(base, "sector_per_scores_sample.csv")
    fields = ["code", "name", "sector33", "close", "forecast_per",
              "sector_median_per", "sector_relative", "self_cheapness",
              "roe", "equity_ratio", "total_score", "in_universe",
              "passes_valuetrap", "reasons"]
    with open(out2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"配信サンプル: {out1}")
    print(f"検証用CSV  : {out2}")
    print(f"NG語チェック(補足): {ng_words.check_ng(summary) or 'クリーン'}")
    print(f"該当あり業種: {[s for s in sorted(rankings) if rankings[s]['has_candidates']]}")
    print(f"該当なし業種: {[s for s in sorted(rankings) if not rankings[s]['has_candidates']]}")


if __name__ == "__main__":
    main()
