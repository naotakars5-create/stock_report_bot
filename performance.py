"""
performance.py

過去のスクリーニング上位群（各営業日の上位5銘柄コホート）と日経平均の
**累積成績**を、一貫した集計基準で記録・集計するモジュール（機能4）。

━━ 一貫した集計基準（README／コードで明記・恣意的操作を避ける） ━━
  1. 1コホート = ある営業日の上位5銘柄。等加重（各20%）。
  2. 1銘柄の成績 = 抽出日終値(entry)を起点に **5立会い日後の終値(exit)** で確定。
     ・「5立会い日後」はその銘柄自身の日足で entry の 5 本後の終値（休場を跨いでも
       立会い日ベースで数える）。銘柄が5本先まで無い（未成熟）コホートは集計に含めない。
  3. コホート成績 = 5銘柄の単純平均（等加重）。日経平均も同じ entry→exit 窓で算出。
  4. 累積カーブ = **週次非重複**（保有期間が重ならないよう5立会い日以上あけて）
     コホートを1つずつ採用し、等加重リターンを **複利連鎖**（Πを取る）。
     ・毎営業日コホートを重複計上すると同じ値動きを二重計上して成績が歪むため、
       累積は非重複チェーンで作る（日次コホートの平均は別途「参考」として併記）。
  5. 最大ドローダウン(DD) = 上記チェーンのエクイティカーブから算出。
  6. 勝率は「コホート勝率（週次）」と「銘柄ベース勝率」を併記する。
  7. 月次は run_date の月でチェーンを区切り、各月リターンと累積を出す。
     期間の取り方で良く見せる操作はしない（全期間・全チェーンを一貫集計）。

これは過去の抽出結果の追跡であり、売買の成果・推奨を示すものではありません。

永続化:
  - data/pick_ledger.csv     : 各コホートの銘柄別 entry/exit/リターン（インクリメンタル）
  - data/performance_monthly.csv : 月次の累積スナップショット（配信・監査用）
"""

import csv
import os

import market_calendar


LEDGER_PATH = os.path.join("data", "pick_ledger.csv")
MONTHLY_PATH = os.path.join("data", "performance_monthly.csv")

LEDGER_FIELDS = ["run_date", "code", "name", "rank", "entry_price",
                 "exit_date", "exit_price", "return_pct", "bench_return_pct", "status"]
MONTHLY_FIELDS = ["month", "cohorts", "cohort_win_rate", "avg_cohort_return",
                  "month_return", "cum_return", "cum_vs_nikkei", "max_drawdown"]

HOLDING_SESSIONS = 5      # 正式な保有基準（5立会い日）
_STATUS_PENDING = "pending"
_STATUS_CLOSED = "closed"


# ====== CSV I/O（失敗しても全体を止めない） ======
def _read_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] {path} の読み込みに失敗しました: {e}")
        return []


def _write_csv(path, fields, rows):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in fields})
        return True
    except Exception as e:
        print(f"[警告] {path} の書き込みに失敗しました: {e}")
        return False


def load_ledger(path=LEDGER_PATH):
    return _read_csv(path)


def _f(row, key):
    """行の数値セルを float で取り出す。空・不正は None。"""
    try:
        v = row.get(key)
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ====== 5立会い日後の終値・窓リターン（銘柄自身の日足ベース） ======
def _exit_after_sessions(close_series, entry_date_str, sessions=HOLDING_SESSIONS):
    """
    close_series（日付昇順の [(date, close), ...] または pandas Series）で、
    entry_date 起点の「N立会い日後の終値」を (exit_date_str, exit_price) で返す。

    起点バーの決め方（集計基準の一貫性・重要）:
      配信は朝（寄り付き前）に実行され、記録される entry_price は
      **run_date より前の最新バー（＝前営業日終値）** である。後日取得した日足には
      run_date 当日のバーも含まれるため、「entry_date 以下」で起点を取ると
      記録価格のバーより1本後ろにずれ、銘柄リターン（記録価格起点）と
      ベンチマーク窓（当日バー起点）の期間が食い違う。
      → 起点は **run_date より前の最新バー** とする。過去バーが無い系列
      （テスト・手動データ）のみ run_date 当日のバーにフォールバックする。

    N本先が無ければ None（未成熟）。
    """
    pairs = _as_pairs(close_series)
    if not pairs:
        return None
    idx = None
    fallback = None
    for i, (d, _v) in enumerate(pairs):
        if d < entry_date_str:
            idx = i
        elif d == entry_date_str:
            fallback = i
        else:
            break
    if idx is None:
        idx = fallback
    if idx is None:
        return None
    exit_idx = idx + sessions
    if exit_idx >= len(pairs):
        return None  # まだN立会い日先の終値が無い（未成熟）
    ed, ep = pairs[exit_idx]
    return ed, float(ep)


def _window_return(close_series, entry_date_str, exit_date_str):
    """
    close_series の entry_date 起点〜exit_date 終点の騰落率(%)。算出不可は None。

    起点は _exit_after_sessions と同じ「entry_date より前の最新バー」
    （無ければ当日バーにフォールバック）。銘柄リターンとベンチマーク窓の
    期間を一致させるための基準統一。
    """
    pairs = _as_pairs(close_series)
    if not pairs:
        return None
    base = fallback = None
    for d, v in pairs:
        if d < entry_date_str:
            base = float(v)
        elif d == entry_date_str:
            fallback = float(v)
        else:
            break
    if base is None:
        base = fallback
    end = None
    for d, v in pairs:
        if d <= exit_date_str:
            end = float(v)
        else:
            break
    if base is None or end is None or base <= 0:
        return None
    return (end - base) / base * 100


def _as_pairs(close_series):
    """pandas Series / DataFrame / [(date,val)] を [(YYYY-MM-DD, float), ...] 昇順に正規化。"""
    if close_series is None:
        return []
    # pandas 系（Close 列 or Series）
    try:
        import pandas as pd  # noqa
        if hasattr(close_series, "columns"):  # DataFrame
            if "Close" not in close_series:
                return []
            close_series = close_series["Close"]
        if hasattr(close_series, "index") and hasattr(close_series, "values"):
            out = []
            for ts, v in zip(close_series.index, close_series.values):
                try:
                    ds = ts.date().isoformat()
                except Exception:
                    ds = str(ts)[:10]
                if v is None:
                    continue
                try:
                    out.append((ds, float(v)))
                except (TypeError, ValueError):
                    continue
            out.sort(key=lambda x: x[0])
            return out
    except Exception:
        pass
    # 素の [(date, val)]
    out = []
    for d, v in close_series:
        ds = d if isinstance(d, str) else getattr(d, "isoformat", lambda: str(d))()
        try:
            out.append((ds[:10], float(v)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


# ====== 台帳の同期（今日のコホート追記＋満期コホートの確定） ======
def sync_ledger(today_str, history_rows, price_history_provider,
                benchmark_series=None, path=LEDGER_PATH, holding=HOLDING_SESSIONS):
    """
    report_history の全コホート(history_rows)を台帳に反映し、満期コホートを確定する。

    引数:
        today_str: 実行日(JST) "YYYY-MM-DD"（当日コホートは未成熟なので確定しない）
        history_rows: report_history._read_rows 相当（run_date/code/name/rank/price を含む）
        price_history_provider: code(str) -> 終値系列（pandas Series / [(date,close)]）。
            満期判定・exit終値の取得に使う。取得不可は None を返す想定。
        benchmark_series: 日経平均の終値系列（entry→exit 窓で市場比を出すため）。
    戻り値: 更新後の台帳行（list[dict]）。

    設計:
      - 台帳に無い (run_date, code) を pending で取り込む。
      - pending 行のうち、その銘柄の日足に entry+holding 本先の終値が存在すれば
        exit を確定して closed にする（＝5立会い日後の終値が出そろったコホート）。
      - 銘柄の履歴取得は satus=pending の銘柄だけに限定（満期済みだけ引く）。
    """
    ledger = _read_csv(path)
    existing = {(r.get("run_date"), r.get("code")) for r in ledger}

    # 1) 未取り込みのコホート銘柄を pending で追加
    for r in history_rows or []:
        rd = (r.get("run_date") or "").strip()
        code = (r.get("code") or "").strip()
        if not rd or not code or rd >= today_str:
            continue  # 当日・未来は対象外（未成熟）
        if (rd, code) in existing:
            continue
        try:
            entry = float(r.get("price"))
        except (TypeError, ValueError):
            continue
        ledger.append({
            "run_date": rd, "code": code, "name": (r.get("name") or "").strip(),
            "rank": (r.get("rank") or "").strip(), "entry_price": f"{entry:.2f}",
            "exit_date": "", "exit_price": "", "return_pct": "",
            "bench_return_pct": "", "status": _STATUS_PENDING,
        })
        existing.add((rd, code))

    # 2) pending 行の満期確定（銘柄ごとに履歴を1回だけ引く）
    pending_codes = sorted({r["code"] for r in ledger
                            if r.get("status") == _STATUS_PENDING})
    series_cache = {}
    for code in pending_codes:
        try:
            series_cache[code] = price_history_provider(code)
        except Exception as e:
            print(f"[警告] 成績台帳: {code} の履歴取得に失敗（保留）: {e}")
            series_cache[code] = None

    bench_pairs = _as_pairs(benchmark_series)
    for r in ledger:
        if r.get("status") != _STATUS_PENDING:
            continue
        code, rd = r["code"], r["run_date"]
        try:
            entry = float(r.get("entry_price"))
        except (TypeError, ValueError):
            continue
        ex = _exit_after_sessions(series_cache.get(code), rd, holding)
        if ex is None or entry <= 0:
            continue  # まだ未成熟 → pending のまま
        exit_date, exit_price = ex
        r["exit_date"] = exit_date
        r["exit_price"] = f"{exit_price:.2f}"
        r["return_pct"] = f"{(exit_price - entry) / entry * 100:.4f}"
        if bench_pairs:
            br = _window_return(bench_pairs, rd, exit_date)
            r["bench_return_pct"] = f"{br:.4f}" if br is not None else ""
        r["status"] = _STATUS_CLOSED

    ledger.sort(key=lambda r: (r.get("run_date") or "", int(r.get("rank") or 99)
                               if str(r.get("rank") or "").isdigit() else 99))
    _write_csv(path, LEDGER_FIELDS, ledger)
    return ledger


# ====== コホート集計（純粋関数・テスト可能） ======
def cohorts_from_ledger(ledger):
    """
    確定済み(closed)行を run_date ごとにまとめ、コホート成績（等加重）を作る。

    戻り値: [{"run_date", "exit_date", "n", "wins", "losses", "cohort_return",
             "bench_return", "picks":[{code,name,return}, ...]}, ...]（run_date 昇順）
    """
    by_date = {}
    for r in ledger or []:
        if r.get("status") != _STATUS_CLOSED:
            continue
        ret = _f(r, "return_pct")
        if ret is None:
            continue
        by_date.setdefault(r["run_date"], []).append(r)

    cohorts = []
    for rd in sorted(by_date):
        rows = by_date[rd]
        rets = [_f(r, "return_pct") for r in rows]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        benches = [_f(r, "bench_return_pct") for r in rows]
        benches = [x for x in benches if x is not None]
        cohorts.append({
            "run_date": rd,
            "exit_date": max((r.get("exit_date") or "") for r in rows),
            "n": len(rets),
            "wins": sum(1 for x in rets if x > 0),
            "losses": sum(1 for x in rets if x <= 0),
            "cohort_return": sum(rets) / len(rets),
            "bench_return": (sum(benches) / len(benches)) if benches else None,
            "picks": [{"code": r.get("code"), "name": r.get("name"),
                       "return": _f(r, "return_pct")} for r in rows],
        })
    return cohorts


def _business_days_between(a, b):
    """a〜b の立会い日数（概算・非重複判定用）。a,b は "YYYY-MM-DD"。"""
    from datetime import datetime, timedelta
    try:
        da = datetime.strptime(a, "%Y-%m-%d").date()
        db = datetime.strptime(b, "%Y-%m-%d").date()
    except Exception:
        return None
    if da > db:
        da, db = db, da
    days, cur = 0, da
    while cur < db:
        cur += timedelta(days=1)
        if market_calendar.is_trading_day(cur):
            days += 1
    return days


def nonoverlap_chain(cohorts, holding=HOLDING_SESSIONS):
    """
    保有期間が重ならないよう、run_date 昇順に holding 立会い日以上あけて
    コホートを1つずつ採用したチェーンを返す（週次非重複）。

    戻り値: 採用したコホートのリスト（run_date 昇順）。
    """
    chain = []
    last_run = None
    for c in cohorts:  # cohorts は run_date 昇順の前提
        if last_run is None:
            chain.append(c)
            last_run = c["run_date"]
            continue
        gap = _business_days_between(last_run, c["run_date"])
        if gap is not None and gap >= holding:
            chain.append(c)
            last_run = c["run_date"]
    return chain


def _max_drawdown(equity_curve):
    """エクイティカーブ（1.0起点の資産倍率リスト）から最大DD(%)を返す（0以下）。"""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (v - peak) / peak * 100 if peak > 0 else 0.0
        mdd = min(mdd, dd)
    return mdd


def summarize(ledger, holding=HOLDING_SESSIONS):
    """
    台帳から累積成績サマリーを作る（配信・監査用の中核）。

    戻り値 dict:
      available(bool), chain_cohorts(int), pick_count(int),
      cohort_win_rate(%|None), pick_win_rate(%|None),
      cum_return(%|None), cum_nikkei(%|None), cum_vs_nikkei(pt|None),
      avg_cohort_return(%|None), max_drawdown(%|None),
      first_date, last_date, daily_avg_cohort_return(参考・全コホート平均),
      monthly: [{month, cohorts, month_return, cum_return, cum_vs_nikkei,
                 cohort_win_rate, avg_cohort_return, max_drawdown}, ...]
    """
    cohorts = cohorts_from_ledger(ledger)
    if not cohorts:
        return {"available": False, "chain_cohorts": 0, "pick_count": 0,
                "monthly": []}

    # 参考: 全コホート（重複あり）の単純平均リターン
    daily_avg = sum(c["cohort_return"] for c in cohorts) / len(cohorts)

    chain = nonoverlap_chain(cohorts, holding=holding)
    if not chain:
        return {"available": False, "chain_cohorts": 0, "pick_count": 0,
                "monthly": [], "daily_avg_cohort_return": daily_avg}

    # 複利連鎖のエクイティカーブ（戦略・日経）
    equity = [1.0]
    nk_equity = [1.0]
    have_bench = any(c["bench_return"] is not None for c in chain)
    for c in chain:
        equity.append(equity[-1] * (1 + c["cohort_return"] / 100))
        if c["bench_return"] is not None:
            nk_equity.append(nk_equity[-1] * (1 + c["bench_return"] / 100))
        else:
            nk_equity.append(nk_equity[-1])

    cum_return = (equity[-1] - 1) * 100
    cum_nikkei = (nk_equity[-1] - 1) * 100 if have_bench else None
    cohort_wins = sum(1 for c in chain if c["cohort_return"] > 0)
    pick_rets = [p["return"] for c in chain for p in c["picks"]
                 if p.get("return") is not None]
    pick_wins = sum(1 for x in pick_rets if x > 0)

    monthly = _monthly_breakdown(chain)

    return {
        "available": True,
        "chain_cohorts": len(chain),
        "pick_count": len(pick_rets),
        "cohort_win_rate": cohort_wins / len(chain) * 100,
        "pick_win_rate": (pick_wins / len(pick_rets) * 100) if pick_rets else None,
        "cum_return": cum_return,
        "cum_nikkei": cum_nikkei,
        "cum_vs_nikkei": (cum_return - cum_nikkei) if cum_nikkei is not None else None,
        "avg_cohort_return": sum(c["cohort_return"] for c in chain) / len(chain),
        "max_drawdown": _max_drawdown(equity),
        "first_date": chain[0]["run_date"],
        "last_date": chain[-1]["run_date"],
        "daily_avg_cohort_return": daily_avg,
        "monthly": monthly,
    }


def _monthly_breakdown(chain):
    """非重複チェーンを run_date の月で区切り、月次リターンと累積を作る。"""
    months = []
    buckets = {}
    order = []
    for c in chain:
        m = c["run_date"][:7]  # YYYY-MM
        if m not in buckets:
            buckets[m] = []
            order.append(m)
        buckets[m].append(c)

    cum_mult = 1.0
    nk_cum_mult = 1.0
    for m in order:
        cs = buckets[m]
        mult = 1.0
        nk_mult = 1.0
        equity = [1.0]
        wins = 0
        have_bench = False
        for c in cs:
            mult *= (1 + c["cohort_return"] / 100)
            equity.append(equity[-1] * (1 + c["cohort_return"] / 100))
            if c["cohort_return"] > 0:
                wins += 1
            if c["bench_return"] is not None:
                nk_mult *= (1 + c["bench_return"] / 100)
                have_bench = True
        cum_mult *= mult
        if have_bench:
            nk_cum_mult *= nk_mult
        months.append({
            "month": m,
            "cohorts": len(cs),
            "month_return": (mult - 1) * 100,
            "cum_return": (cum_mult - 1) * 100,
            "cum_vs_nikkei": ((cum_mult - nk_cum_mult) * 100) if have_bench else None,
            "cohort_win_rate": wins / len(cs) * 100 if cs else None,
            "avg_cohort_return": sum(c["cohort_return"] for c in cs) / len(cs),
            "max_drawdown": _max_drawdown(equity),
        })
    return months


def save_monthly(summary, path=MONTHLY_PATH):
    """月次スナップショットを CSV に保存（配信・監査用）。失敗しても止めない。"""
    if not summary or not summary.get("monthly"):
        return False
    rows = []
    for m in summary["monthly"]:
        rows.append({
            "month": m["month"],
            "cohorts": m["cohorts"],
            "cohort_win_rate": f"{m['cohort_win_rate']:.1f}" if m.get("cohort_win_rate") is not None else "",
            "avg_cohort_return": f"{m['avg_cohort_return']:.2f}",
            "month_return": f"{m['month_return']:.2f}",
            "cum_return": f"{m['cum_return']:.2f}",
            "cum_vs_nikkei": f"{m['cum_vs_nikkei']:.2f}" if m.get("cum_vs_nikkei") is not None else "",
            "max_drawdown": f"{m['max_drawdown']:.2f}",
        })
    return _write_csv(path, MONTHLY_FIELDS, rows)


def sync_and_summarize(today_str, history_rows, price_history_provider,
                       benchmark_series=None, ledger_path=LEDGER_PATH,
                       monthly_path=MONTHLY_PATH, persist=True,
                       holding=HOLDING_SESSIONS):
    """
    台帳の同期→累積サマリー算出→月次スナップショット保存 を一括で行う（main から利用）。

    persist=False なら台帳・月次を書き込まず、サマリーだけ返す（dry-run／休場日用）。
    """
    if persist:
        ledger = sync_ledger(today_str, history_rows, price_history_provider,
                             benchmark_series=benchmark_series, path=ledger_path,
                             holding=holding)
    else:
        # 非永続でも「今回の見え方」を出せるよう、メモリ上だけで満期確定して集計する。
        ledger = _sync_ledger_in_memory(today_str, history_rows,
                                        price_history_provider, benchmark_series,
                                        ledger_path, holding)
    summary = summarize(ledger, holding=holding)
    if persist and summary.get("available"):
        save_monthly(summary, path=monthly_path)
    return summary


def _sync_ledger_in_memory(today_str, history_rows, price_history_provider,
                           benchmark_series, path, holding):
    """sync_ledger と同じ確定処理をディスクに書かずに行う（dry-run 用）。"""
    ledger = _read_csv(path)
    existing = {(r.get("run_date"), r.get("code")) for r in ledger}
    for r in history_rows or []:
        rd = (r.get("run_date") or "").strip()
        code = (r.get("code") or "").strip()
        if not rd or not code or rd >= today_str or (rd, code) in existing:
            continue
        try:
            entry = float(r.get("price"))
        except (TypeError, ValueError):
            continue
        ledger.append({"run_date": rd, "code": code,
                       "name": (r.get("name") or "").strip(),
                       "rank": (r.get("rank") or "").strip(),
                       "entry_price": f"{entry:.2f}", "exit_date": "",
                       "exit_price": "", "return_pct": "", "bench_return_pct": "",
                       "status": _STATUS_PENDING})
        existing.add((rd, code))

    pending_codes = sorted({r["code"] for r in ledger
                            if r.get("status") == _STATUS_PENDING})
    cache = {}
    for code in pending_codes:
        try:
            cache[code] = price_history_provider(code)
        except Exception:
            cache[code] = None
    bench_pairs = _as_pairs(benchmark_series)
    for r in ledger:
        if r.get("status") != _STATUS_PENDING:
            continue
        try:
            entry = float(r.get("entry_price"))
        except (TypeError, ValueError):
            continue
        ex = _exit_after_sessions(cache.get(r["code"]), r["run_date"], holding)
        if ex is None or entry <= 0:
            continue
        ed, ep = ex
        r["exit_date"], r["exit_price"] = ed, f"{ep:.2f}"
        r["return_pct"] = f"{(ep - entry) / entry * 100:.4f}"
        if bench_pairs:
            br = _window_return(bench_pairs, r["run_date"], ed)
            r["bench_return_pct"] = f"{br:.4f}" if br is not None else ""
        r["status"] = _STATUS_CLOSED
    return ledger
