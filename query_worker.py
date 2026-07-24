"""
query_worker.py

銘柄Q&A のバッチ処理（改善B）。

Cloudflare Worker が D1 にキューした問い合わせ（query_requests・pending）を取得し、
stock_query で機械的評価を生成して読者へ LINE push し、処理済みに更新する。

常時稼働の Python サーバーを持たない構成のため、外部cron（cron-job.org 等）から
repository_dispatch(query-worker) で数分〜十数分おきに起動する想定
（回答の遅延は「次の処理タイミングで送る」と受付時に伝えている）。

必要な環境変数:
  SUBSCRIBER_API_URL   … Worker のベースURL（/queries, /queries/done を叩く）
  SUBSCRIBER_API_TOKEN … Worker の EXPORT_TOKEN
  LINE_CHANNEL_ACCESS_TOKEN … 回答の push 用

実行: python query_worker.py [--dry-run]
"""

import argparse
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

import data_fetcher
import line_sender
import stock_query


def _api_base():
    return (os.environ.get("SUBSCRIBER_API_URL") or "").strip().rstrip("/")


def _api_token():
    return (os.environ.get("SUBSCRIBER_API_TOKEN") or "").strip()


def fetch_pending(timeout=15):
    """Worker から未処理の問い合わせを取得する。未設定・失敗は空リスト。"""
    base, token = _api_base(), _api_token()
    if not base or not token or requests is None:
        print("[Q&A] SUBSCRIBER_API_URL/TOKEN 未設定のため処理をスキップします。")
        return []
    try:
        r = requests.get(f"{base}/queries", timeout=timeout,
                         headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            print(f"[Q&A] 問い合わせ取得失敗: HTTP {r.status_code}")
            return []
        return r.json().get("queries") or []
    except Exception as e:
        print(f"[Q&A] 問い合わせ取得で例外: {e}")
        return []


def mark_done(ids, status="done", timeout=15):
    """処理済み（またはerror）に更新する。"""
    base, token = _api_base(), _api_token()
    if not ids or not base or not token or requests is None:
        return
    try:
        requests.post(f"{base}/queries/done", timeout=timeout,
                      headers={"Authorization": f"Bearer {token}"},
                      json={"ids": ids, "status": status})
    except Exception as e:
        print(f"[Q&A] 完了更新で例外: {e}")


def main(dry_run=False):
    pending = fetch_pending()
    if not pending:
        print("[Q&A] 未処理の問い合わせはありません。")
        return 0
    print(f"[Q&A] {len(pending)} 件の問い合わせを処理します。")

    # ベンチマークは1回だけ取得して使い回す（複数問い合わせで無駄打ちしない）。
    benchmark_df = data_fetcher.get_benchmark_history()
    token, _ = line_sender.get_credentials()
    done_ids = []
    for q in pending:
        qid, uid, code = q.get("id"), q.get("user_id"), q.get("code")
        if not uid or not code:
            done_ids.append(qid)
            continue
        text = stock_query.answer_text(code, benchmark_df=benchmark_df)
        print(f"  #{qid} {code} → {uid[:6]}…（{len(text)}字）")
        if dry_run:
            print("    [dry-run] 送信しません。")
            print("    " + text.replace("\n", "\n    "))
        else:
            line_sender.send_multicast([{"type": "text", "text": text[:4900]}],
                                       [uid], token=token, label=f"Q&A({code})")
        done_ids.append(qid)

    if not dry_run:
        mark_done(done_ids)
    print(f"[Q&A] {len(done_ids)} 件を処理済みにしました。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="銘柄Q&Aのバッチ処理")
    parser.add_argument("--dry-run", action="store_true",
                        help="push・完了更新を行わず内容表示のみ")
    args = parser.parse_args()
    try:
        sys.exit(main(dry_run=args.dry_run))
    except Exception:
        import traceback
        print("[Q&A] 予期せぬ例外:")
        traceback.print_exc()
        sys.exit(1)
