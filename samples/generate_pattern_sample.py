"""
samples/generate_pattern_sample.py

上昇チャートパターン配信のサンプル出力（合成価格・ネットワーク不要）。

  1. samples/pattern_sample.md            … 配信内容（Flexカードの要点＋補足）
  2. samples/pattern_scores_sample.csv    … 全銘柄検出一覧（検証用CSV）

実運用と同じ patterns.screen / delivery で生成する（実データは workflow で）。
実行: python samples/generate_pattern_sample.py
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patterns import screen as PS
from patterns import delivery as PD
from promo import ng_words


def _ohlcv(close, high=None, low=None, volume=None):
    n = len(close)
    return {"close": close, "high": high or close, "low": low or close,
            "volume": volume or [2_000_000] * n}


def _breakout():
    c = [100 + (i % 5) for i in range(130)] + [106, 108, 110, 111, 112]
    return _ohlcv(c, high=c, low=c, volume=[2_000_000] * 134 + [6_000_000])


def _golden():
    c = [100 - i * 0.3 for i in range(80)] + [76 + i * 1.6 for i in range(55)]
    return _ohlcv(c)


def _triangle():
    highs, lows, closes = [], [], []
    for i in range(135):
        highs.append(120 + 15 - min(i, 100) * 0.12)
        lows.append(120 - 15 + min(i, 100) * 0.12)
        closes.append(120 + (i % 2))
    closes[-1] = max(highs[:-1]) + 3
    vol = [2_000_000] * 134 + [5_000_000]
    return _ohlcv(closes, high=highs, low=lows, volume=vol)


def _flat():
    c = [100 + (i % 2) for i in range(135)]
    return _ohlcv(c)


STOCKS = [
    {"code": "1111", "name": "サンプル新高値", "sector": "機械", "ohlcv": _breakout()},
    {"code": "2222", "name": "サンプルGC", "sector": "電気機器", "ohlcv": _golden()},
    {"code": "3333", "name": "サンプル三角", "sector": "化学", "ohlcv": _triangle()},
    {"code": "4444", "name": "サンプル横ばい", "sector": "銀行業", "ohlcv": _flat()},
]


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    ranked, all_rows = PS.score_all(STOCKS)
    flex_messages, summary = PD.build_delivery(
        ranked, basis_label="データ基準日：8月17日 大引け時点")

    md = ["# サンプル：上昇チャートパターン配信（合成データ）", "",
          "> 実運用と同じ patterns.screen / delivery で生成。株価は合成。J-Quants不要。", "",
          "## Flexカルーセル（全体・パターン強度順の上位N）", ""]
    for _alt, carousel in flex_messages:
        for b in carousel["contents"]:
            name = b["header"]["contents"][1]["text"]
            md.append(f"### {name}")
            for c in b["body"]["contents"]:
                if c.get("type") == "text":
                    md.append(f"- {c['text']}")
                elif c.get("type") == "box" and c.get("layout") == "baseline":
                    kv = c["contents"]
                    md.append(f"  - {kv[0]['text']}: {kv[1]['text']}")
            md.append("")
    md.append("## 補足テキスト（一覧＋必須免責）")
    md.append("")
    md.append("```")
    md.append(summary)
    md.append("```")
    out1 = os.path.join(base, "pattern_sample.md")
    with open(out1, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    out2 = os.path.join(base, "pattern_scores_sample.csv")
    fields = ["code", "name", "sector", "price", "surge_5", "above_25ma",
              "vol_ratio", "pattern_labels", "score", "in_universe", "reasons"]
    with open(out2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"配信サンプル: {out1}")
    print(f"検証用CSV  : {out2}")
    print(f"NG語チェック(補足): {ng_words.check_ng(summary) or 'クリーン'}")
    print(f"検出上位: {[(r['name'], r['pattern_labels']) for r in ranked]}")


if __name__ == "__main__":
    main()
