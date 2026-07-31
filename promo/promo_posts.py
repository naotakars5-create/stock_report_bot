"""
promo/promo_posts.py

投稿済みツイートの記録（重複投稿防止・後の効果分析用）。

この構成にはデータベースが無いため、既存の履歴（data/report_history.csv 等）と
同じく **CSV に保存し、GitHub Actions がコミットして永続化** する。

CSV: data/promo_posts.csv
  posted_at : 投稿日時（JST・ISO風）
  post_date : 投稿日（YYYY-MM-DD・同種別の重複判定に使用）
  kind      : 投稿種別（morning_digest / close_result / weekly / monthly / disclosure）
  tweet_id  : X の tweet id（dry-run 時は "dry-run"）
  text      : 投稿本文
"""

import csv
import os

import market_calendar

DEFAULT_PATH = os.path.join("data", "promo_posts.csv")
FIELDS = ["posted_at", "post_date", "kind", "tweet_id", "text"]


def _read_rows(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[警告] 投稿履歴の読み込みに失敗しました: {e}")
        return []


def already_posted(kind, post_date, path=DEFAULT_PATH):
    """同じ種別・同じ日付の投稿が既にあるか（重複投稿防止）。"""
    for r in _read_rows(path):
        if ((r.get("kind") or "").strip() == kind
                and (r.get("post_date") or "").strip() == post_date):
            return True
    return False


def count_on_date(kind, post_date, path=DEFAULT_PATH):
    """その日の同種別の投稿数（適時開示の1日上限チェック用）。"""
    n = 0
    for r in _read_rows(path):
        if ((r.get("kind") or "").strip() == kind
                and (r.get("post_date") or "").strip() == post_date):
            n += 1
    return n


def record(kind, tweet_id, text, post_date=None, posted_at=None, path=DEFAULT_PATH):
    """投稿を記録する。失敗しても全体は止めない。"""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        rows = _read_rows(path)
        # 投稿日時・日付は JST 基準（Actions は UTC 稼働のため素の now は前日にずれる）。
        now = posted_at or market_calendar.now_jst().strftime("%Y-%m-%d %H:%M:%S")
        rows.append({
            "posted_at": now,
            "post_date": post_date or now[:10],
            "kind": kind,
            "tweet_id": str(tweet_id),
            "text": (text or "").replace("\n", "\\n"),
        })
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in FIELDS})
        return True
    except Exception as e:
        print(f"[警告] 投稿履歴の保存に失敗しました: {e}")
        return False
