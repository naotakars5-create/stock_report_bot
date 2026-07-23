-- webhook/schema.sql
-- 読者設定（機能拡張3）の D1 スキーマ。
-- 個人情報の最小化: LINE userId と設定値のみ。保有株数・取得単価・氏名は持たない。
--
-- 適用: wrangler d1 execute stock-report-subscribers --file=schema.sql

CREATE TABLE IF NOT EXISTS users (
  user_id    TEXT PRIMARY KEY,          -- LINE userId
  price_cap  REAL,                      -- 単元購入価格の上限（円）。NULL=フィルタなし
  active     INTEGER NOT NULL DEFAULT 1,-- 0=ブロック済み（unfollow）
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_items (
  user_id    TEXT NOT NULL,
  code       TEXT NOT NULL,             -- 証券コード
  kind       TEXT NOT NULL,             -- 'interest'（気になる） / 'holding'（保有）
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, code, kind)
);

CREATE INDEX IF NOT EXISTS idx_watch_code ON watch_items (code);

-- 銘柄Q&A（改善B）: 読者からの問い合わせキュー。
-- Worker が pending で積み、バッチ(query_worker.py)が処理して status を done に更新。
CREATE TABLE IF NOT EXISTS query_requests (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL,
  code       TEXT NOT NULL,             -- 問い合わせ証券コード
  status     TEXT NOT NULL DEFAULT 'pending', -- pending / done / error
  created_at TEXT NOT NULL,
  answered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_query_status ON query_requests (status);
