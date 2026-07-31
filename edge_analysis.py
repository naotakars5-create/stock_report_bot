"""
edge_analysis.py

「そもそも売れる強み（エッジ）があるか」を実データで正直に検証するツール。

目的:
  月額課金の根拠は「指数に勝つ」か「指数に匹敵する成果をより低いリスクで出す」
  のいずれか。本ツールは実際の記録データから、戦略（掲載上位群）と日経平均を
  **同じ基準・複数のリスク調整軸**で比較し、売り文句になり得る優位が
  統計的に言えるかどうかを判定する。

正直さのための3原則（都合よく見せない）:
  1. 全軸を一貫して出す（有利な軸だけ抜き出さない）。
  2. サンプル数のゲートを設ける。観測が少ないうちは「点推定は出すが断言しない」。
     少数データで偶然良く見えた数字を売り文句にしないための安全弁。
  3. レジーム依存を警告する。下げ相場で「負けが小さかっただけ」を「守りのエッジ」と
     早合点しないよう、ベンチマークが下落した局面かどうかを明示する。

データ源（上から順に優先）:
  1. data/price_tracks.csv … 掲載銘柄の期間別リターン＋同窓の日経（最も筋が良い）
  2. data/daily_stats.csv  … 前日上位群の翌日リターン(avg_change_pct)と対日経(vs_nikkei_pt)
                             （移行前からの実績。1営業日先の系列）

実行: python edge_analysis.py [--horizon 5d] [--report path.md]
"""

import argparse
import csv
import math
import os

DAILY_STATS = os.path.join("data", "daily_stats.csv")
PRICE_TRACKS = os.path.join("data", "price_tracks.csv")
# シャドウアーム（配信しない検証アーム）のトラックCSV。
SHADOW_ARMS = {
    "defensive": os.path.join("data", "shadow_defensive_tracks.csv"),
    "catalyst": os.path.join("data", "shadow_catalyst_tracks.csv"),
}

# 統計的に「兆し」を語ってよい最低観測数と、ある程度信頼できる観測数。
MIN_HINT = 20      # これ未満は点推定のみ・断言しない
MIN_CREDIBLE = 40  # これ以上でようやく「継続すれば語れる」水準
# 期間ラベル→年間の観測回数（年率換算・非重複前提の目安）
PERIODS_PER_YEAR = {"1d": 252, "3d": 84, "5d": 50, "20d": 12, "daily": 252}


# ====== 読み込み ======
def _read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] {path} の読み込みに失敗: {e}")
        return []


def _flt(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_from_daily_stats(path=DAILY_STATS):
    """
    daily_stats から (戦略日次リターン%, 日経日次リターン%, 勝, 敗) の系列を作る。

    vs_nikkei_pt = avg_change_pct - nikkei なので nikkei = avg - vs で復元する。
    戻り値: {"label":"daily", "strat":[...], "bench":[...], "wins":int, "losses":int,
            "dates":[...]}
    """
    strat, bench, dates = [], [], []
    wins = losses = 0
    for r in _read_csv(path):
        a = _flt(r.get("avg_change_pct"))
        vs = _flt(r.get("vs_nikkei_pt"))
        if a is None:
            continue
        strat.append(a)
        bench.append(a - vs if vs is not None else None)
        dates.append((r.get("stat_date") or "").strip())
        w, l = _flt(r.get("win_count")), _flt(r.get("lose_count"))
        if w is not None:
            wins += int(w)
        if l is not None:
            losses += int(l)
    return {"label": "daily", "strat": strat, "bench": bench,
            "wins": wins, "losses": losses, "dates": dates}


def load_from_tracks(horizon="5d", path=PRICE_TRACKS):
    """
    price_tracks の指定期間から (戦略リターン%, 日経リターン%) の系列を作る。

    掲載日ごとにコホート平均（等加重）を1観測とする。銘柄単位の勝敗も数える。
    """
    by_date = {}
    wins = losses = 0
    for t in _read_csv(path):
        if (t.get("horizon") or "").strip() != horizon:
            continue
        rd = (t.get("run_date") or "").strip()
        ret = _flt(t.get("return_pct"))
        nk = _flt(t.get("nikkei_return_pct"))
        if rd and ret is not None:
            by_date.setdefault(rd, {"r": [], "nk": []})
            by_date[rd]["r"].append(ret)
            if nk is not None:
                by_date[rd]["nk"].append(nk)
            if ret > 0:
                wins += 1
            else:
                losses += 1
    strat, bench, dates = [], [], []
    for rd in sorted(by_date):
        rs = by_date[rd]["r"]
        nks = by_date[rd]["nk"]
        strat.append(sum(rs) / len(rs))
        bench.append((sum(nks) / len(nks)) if nks else None)
        dates.append(rd)
    return {"label": horizon, "strat": strat, "bench": bench,
            "wins": wins, "losses": losses, "dates": dates}


# ====== 統計（依存ライブラリなし） ======
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs, ddof=1):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n <= ddof:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _downside_std(xs, ddof=1):
    """下方偏差（0未満のリターンだけの二乗平均・Sortino用）。"""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n <= ddof:
        return None
    neg = [min(x, 0.0) for x in xs]
    return math.sqrt(sum(x ** 2 for x in neg) / (n - ddof))


def _max_drawdown(returns_pct):
    """日次(期間)リターン%系列を複利でつないだエクイティの最大DD(%・0以下)。"""
    xs = [x for x in returns_pct if x is not None]
    if not xs:
        return None
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in xs:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        mdd = min(mdd, (equity - peak) / peak * 100)
    return mdd


def _cumulative(returns_pct):
    xs = [x for x in returns_pct if x is not None]
    eq = 1.0
    for r in xs:
        eq *= (1 + r / 100)
    return (eq - 1) * 100


def _metrics(returns_pct, ppy):
    """1系列のリスク調整指標をまとめる。"""
    xs = [x for x in returns_pct if x is not None]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    m = _mean(xs)
    sd = _std(xs)
    dsd = _downside_std(xs)
    sharpe = (m / sd * math.sqrt(ppy)) if (sd and sd > 0) else None
    sortino = (m / dsd * math.sqrt(ppy)) if (dsd and dsd > 0) else None
    return {
        "n": n,
        "mean": m,
        "vol": sd,
        "cumulative": _cumulative(xs),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": _max_drawdown(xs),
        "win_rate_periods": sum(1 for x in xs if x > 0) / n * 100,
    }


def _excess_stats(strat, bench, ppy):
    """対ベンチマークの超過リターンの平均・情報比・t値・勝率。"""
    pairs = [(s, b) for s, b in zip(strat, bench) if s is not None and b is not None]
    n = len(pairs)
    if n < 2:
        return {"n": n}
    exc = [s - b for s, b in pairs]
    m = _mean(exc)
    sd = _std(exc)
    ir = (m / sd * math.sqrt(ppy)) if (sd and sd > 0) else None
    t = (m / (sd / math.sqrt(n))) if (sd and sd > 0) else None
    return {
        "n": n,
        "mean_excess": m,
        "info_ratio": ir,
        "t_stat": t,
        "beat_rate": sum(1 for e in exc if e > 0) / n * 100,
        "bench_mean": _mean([b for _s, b in pairs]),
    }


# ====== 判定 ======
def analyze(series, horizon_label=None):
    """1データ源のエッジ分析結果（指標＋判定）をまとめる。"""
    label = horizon_label or series["label"]
    ppy = PERIODS_PER_YEAR.get(label, 252)
    strat, bench = series["strat"], series["bench"]
    s_m = _metrics(strat, ppy)
    b_m = _metrics(bench, ppy)
    exc = _excess_stats(strat, bench, ppy)
    n = s_m.get("n", 0)
    wins, losses = series.get("wins", 0), series.get("losses", 0)
    stock_hit = (wins / (wins + losses) * 100) if (wins + losses) else None

    # どの軸で戦略がベンチを上回るか（正直に全軸を判定）
    axes = []
    if exc.get("mean_excess") is not None:
        axes.append(("超過リターン", exc["mean_excess"] > 0, f"{exc['mean_excess']:+.3f}pt/期"))
    if s_m.get("vol") is not None and b_m.get("vol") is not None:
        axes.append(("低ボラティリティ", s_m["vol"] < b_m["vol"],
                     f"{s_m['vol']:.2f} vs 日経{b_m['vol']:.2f}"))
    if s_m.get("max_dd") is not None and b_m.get("max_dd") is not None:
        axes.append(("浅い最大DD", s_m["max_dd"] > b_m["max_dd"],
                     f"{s_m['max_dd']:.1f}% vs 日経{b_m['max_dd']:.1f}%"))
    if s_m.get("sortino") is not None and b_m.get("sortino") is not None:
        axes.append(("下方リスク調整(Sortino)", s_m["sortino"] > b_m["sortino"],
                     f"{s_m['sortino']:.2f} vs 日経{b_m['sortino']:.2f}"))
    winning_axes = [a for a in axes if a[1]]

    # レジーム依存の警告（ベンチが下落局面だと「守れただけ」の可能性）
    regime_warn = (exc.get("bench_mean") is not None and exc["bench_mean"] < 0
                   and exc.get("mean_excess", 0) > 0)

    # 統計的信頼度
    t = exc.get("t_stat")
    significant = t is not None and abs(t) >= 2.0

    # 総合判定
    if n < MIN_HINT:
        verdict = "SAMPLE_TOO_SMALL"
        headline = (f"エッジは判定不能（観測 {n} 期・最低 {MIN_HINT} 期必要）。"
                    "点推定は出すが、売り文句にしてはいけない水準。")
    elif significant and exc["mean_excess"] > 0:
        verdict = "EDGE_LIKELY"
        headline = f"超過リターンに統計的な兆し（t={t:.2f}）。継続検証で売り文句化の可能性。"
    elif winning_axes:
        best = winning_axes[0][0]
        verdict = "WEAK_RISK_EDGE"
        headline = (f"リターンでの明確な優位は無いが、{best}など"
                    f"{len(winning_axes)}軸でベンチを上回る（統計的有意ではない・要継続）。")
    else:
        verdict = "NO_EDGE"
        headline = "指数と同等かそれ以下。現状の売り文句になる優位は見当たらない。"

    return {
        "label": label, "n": n, "strat": s_m, "bench": b_m, "excess": exc,
        "stock_hit_rate": stock_hit, "axes": axes, "winning_axes": winning_axes,
        "regime_warn": regime_warn, "significant": significant,
        "verdict": verdict, "headline": headline, "dates": series.get("dates", []),
    }


# ====== レポート ======
def _fmt(v, suffix="", nd=2):
    return "—" if v is None else f"{v:.{nd}f}{suffix}"


def build_report(result):
    """人間が読む正直なレポート（Markdown）。"""
    r = result
    s, b, e = r["strat"], r["bench"], r["excess"]
    L = []
    L.append(f"# エッジ検証レポート（実データ・期間={r['label']}）\n")
    L.append(f"**判定: {r['headline']}**\n")
    if r["dates"]:
        L.append(f"対象: {r['dates'][0]}〜{r['dates'][-1]}（{r['n']}観測）\n")

    if r["n"] < MIN_HINT:
        L.append(f"> ⚠️ 観測数が {r['n']} 期と少なく（統計的に語るには最低 {MIN_HINT}、"
                 f"信頼するには {MIN_CREDIBLE} 期が目安）、以下の数値はすべて"
                 "「参考の点推定」です。この段階で優位を主張してはいけません。\n")

    L.append("## リスク調整指標（戦略 vs 日経平均）\n")
    L.append("| 指標 | 戦略 | 日経平均 |")
    L.append("|---|---|---|")
    L.append(f"| 平均リターン/期 | {_fmt(s.get('mean'),'%',3)} | {_fmt(b.get('mean'),'%',3)} |")
    L.append(f"| 累積リターン | {_fmt(s.get('cumulative'),'%')} | {_fmt(b.get('cumulative'),'%')} |")
    L.append(f"| ボラティリティ | {_fmt(s.get('vol'),'%')} | {_fmt(b.get('vol'),'%')} |")
    L.append(f"| Sharpe(年率) | {_fmt(s.get('sharpe'))} | {_fmt(b.get('sharpe'))} |")
    L.append(f"| Sortino(年率) | {_fmt(s.get('sortino'))} | {_fmt(b.get('sortino'))} |")
    L.append(f"| 最大ドローダウン | {_fmt(s.get('max_dd'),'%')} | {_fmt(b.get('max_dd'),'%')} |")
    L.append(f"| 勝率(期間) | {_fmt(s.get('win_rate_periods'),'%',1)} | {_fmt(b.get('win_rate_periods'),'%',1)} |")
    L.append("")
    L.append("## 対ベンチマーク\n")
    L.append(f"- 平均超過リターン: {_fmt(e.get('mean_excess'),'pt/期',3)}")
    L.append(f"- 情報比(年率): {_fmt(e.get('info_ratio'))}")
    L.append(f"- t値: {_fmt(e.get('t_stat'))}（|t|≥2で統計的有意の目安 → "
             f"{'有意' if r['significant'] else '有意ではない'}）")
    L.append(f"- ベンチ超過の勝率: {_fmt(e.get('beat_rate'),'%',1)}")
    if r["stock_hit_rate"] is not None:
        L.append(f"- 銘柄単位の勝率: {_fmt(r['stock_hit_rate'],'%',1)}")
    L.append("")
    L.append("## 軸ごとの優劣（正直に全軸）\n")
    for name, win, detail in r["axes"]:
        L.append(f"- {'✅' if win else '❌'} {name}: {detail}")
    L.append("")
    if r["regime_warn"]:
        L.append("> ⚠️ **レジーム依存の疑い**: この期間はベンチ（日経）が平均で下落して"
                 "おり、超過はプラス。「下げ相場で負けを小さくできた」だけの可能性があり、"
                 "上げ相場でも同じ優位が出るとは限りません。上昇局面のデータで再検証が必要。\n")
    L.append("## 売り文句にできるか（結論）\n")
    if r["verdict"] == "SAMPLE_TOO_SMALL":
        L.append("現時点では**エッジの有無を判定できません**。まず毎営業日の記録を"
                 f"最低 {MIN_HINT}〜{MIN_CREDIBLE} 期ためること。ここで良く見えた数字を"
                 "配信の売り文句にするのは、少数データの偶然を売ることになり危険です。")
        if r["winning_axes"]:
            best = r["winning_axes"][0]
            L.append(f"\n参考までに、現状で相対的にマシな軸は「{best[0]}（{best[2]}）」。"
                     "ただし有意性はなく、継続検証の観察対象にとどめること。")
    elif r["verdict"] == "EDGE_LIKELY":
        L.append("超過リターンに統計的な兆しがあります。これは正直な売り文句の候補です。"
                 "ただしサンプルを増やして頑健性（レジーム横断・銘柄分散）を確認してから"
                 "訴求すること。")
    elif r["verdict"] == "WEAK_RISK_EDGE":
        best = r["winning_axes"][0]
        L.append(f"「指数に勝つ」では戦えませんが、**{best[0]}**（{best[2]}）は"
                 "『同等のリターンをより低いリスクで』という正直な訴求の候補になり得ます。"
                 "統計的有意ではないので、継続データで裏を取ってから前面に出すこと。")
    else:
        L.append("現状、月額課金の根拠になる**成績上の優位は見当たりません**。"
                 "価値の源泉を「当てること」から、①ロジックのエッジ追加（カタリスト連動等）、"
                 "②当たり外れを全部見せる透明性、③追跡通知・銘柄Q&Aなどの対話性、へ"
                 "移すことを強く推奨します。")
    return "\n".join(L)


def compare_arms(horizon="5d"):
    """
    balanced(本番) と各シャドウアーム(defensive/catalyst)を同基準で突き合わせる。

    どのアームが下方耐性・対日経で優れるかを、サンプル数ゲート付きで正直に比較する。
    戻り値: 比較テキスト（データが全く無ければ None）。
    """
    arms = [("balanced(本番)", load_from_tracks(horizon, path=PRICE_TRACKS))]
    for name, path in SHADOW_ARMS.items():
        arms.append((f"{name}(シャドウ)", load_from_tracks(horizon, path=path)))
    arms = [(nm, sr) for nm, sr in arms if sr["strat"]]
    if not arms:
        return None

    lines = [f"# アームA/B比較（期間={horizon}）\n"]
    lines.append("観測数: " + " / ".join(f"{nm.split('(')[0]}={len(sr['strat'])}"
                                         for nm, sr in arms))
    n_min = min(len(sr["strat"]) for _nm, sr in arms)
    if n_min < MIN_HINT:
        lines.append(f"\n> ⚠️ 最小観測が {n_min} 期（{MIN_HINT}期未満）のため優劣は"
                     "判定できません。点推定のみ（シャドウ運用を継続して蓄積）:")

    analyzed = []
    for nm, sr in arms:
        a = analyze(sr)
        analyzed.append((nm, a))
        s, e = a["strat"], a["excess"]
        lines.append(
            f"- {nm}: 累積 {_fmt(s.get('cumulative'),'%')} / "
            f"Sortino {_fmt(s.get('sortino'))} / 最大DD {_fmt(s.get('max_dd'),'%')} / "
            f"超過 {_fmt(e.get('mean_excess'),'pt/期',3)}（t={_fmt(e.get('t_stat'))}）")

    if n_min >= MIN_HINT and len(analyzed) >= 2:
        # 下方リスク調整(Sortino)が最も高いアームを勝者候補に
        best = max(analyzed, key=lambda x: (x[1]["strat"].get("sortino") or -99))
        base = next((a for nm, a in analyzed if nm.startswith("balanced")), None)
        if base and best[0].startswith("balanced"):
            lines.append("\n**結論: 本番(balanced)を上回るアームは無し。現行維持。**")
        elif base:
            b_sortino = best[1]["strat"].get("sortino") or -99
            base_sortino = base["strat"].get("sortino") or -99
            if b_sortino > base_sortino:
                lines.append(f"\n**結論: {best[0]} が下方リスク調整で本番を上回る → "
                             "本番採用を検討（頑健性・レジーム横断を継続確認）。**")
            else:
                lines.append("\n**結論: シャドウの明確な優位は確認できず。現行維持。**")
    return "\n".join(lines)


# 後方互換のエイリアス（旧名）。
def compare_live_vs_shadow(horizon="5d"):
    return compare_arms(horizon)


def run(horizon="5d"):
    """price_tracks（あれば）と daily_stats の両方を分析して結果リストを返す。"""
    results = []
    tracks = load_from_tracks(horizon)
    if tracks["strat"]:
        results.append(analyze(tracks))
    ds = load_from_daily_stats()
    if ds["strat"]:
        results.append(analyze(ds))
    return results


def main():
    parser = argparse.ArgumentParser(description="実データのエッジ検証")
    parser.add_argument("--horizon", default="5d",
                        help="price_tracks の分析期間（1d/3d/5d/20d）")
    parser.add_argument("--report", default=None, help="Markdownレポートの出力先")
    args = parser.parse_args()

    results = run(args.horizon)
    if not results:
        print("分析できる実データがありません（daily_stats.csv / price_tracks.csv が空）。")
        return 0
    out = "\n\n---\n\n".join(build_report(r) for r in results)
    print(out)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print(f"\n[出力] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
