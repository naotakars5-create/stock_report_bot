"""
sector_per/jquants_client.py

J-Quants API クライアント（財務データ取得専用）。

役割:
  会社予想EPS・自己資本・総資産・純利益・売上高・業績予想の修正など、yfinance で
  取れない財務項目を J-Quants /fins/statements から取得する。株価（当日終値・
  売買代金）は既存の yfinance をそのまま使い、本クライアントは財務のみを担う。

認証（無料枠でも同じ）:
  1) メールアドレス＋パスワード → refreshToken（1週間有効）
  2) refreshToken → idToken（24時間有効）
  3) idToken を Authorization: Bearer で各APIに付与
  環境変数: JQUANTS_MAILADDRESS / JQUANTS_PASSWORD（推奨・自動更新）
            もしくは JQUANTS_REFRESH_TOKEN（週次で手動更新が必要）

無料枠の制約（重要・コードで吸収）:
  - データは約12週間遅延・格納2年。取得できる statements の DisclosedDate を
    fundamentals_asof として必ず記録し、配信・CSV・ログに遅延を明示する。
  - 「当日終値ベース」の価格は yfinance 側で取得するため、財務の遅延は
    PER = 当日終値 ÷（約12週前基準の予想EPS）という形で参考値になる。

解析ロジック（parse_statements 等）はネットワーク非依存の純粋関数にし、
API応答の JSON を渡せばテストできるようにする（この環境では実API未接続）。
"""

import os

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


AUTH_USER = "https://api.jquants.com/v1/token/auth_user"
AUTH_REFRESH = "https://api.jquants.com/v1/token/auth_refresh"
STATEMENTS = "https://api.jquants.com/v1/fins/statements"


class JQuantsError(Exception):
    pass


def _f(v):
    """J-Quantsの数値文字列を float に。空文字・'-'・None は None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "-", "－", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ===== 認証 =====
def get_id_token(mailaddress=None, password=None, refresh_token=None, timeout=30):
    """
    idToken を取得する。mail+password があれば refreshToken から自動取得、
    無ければ JQUANTS_REFRESH_TOKEN を使う。未設定・失敗は JQuantsError。
    """
    if requests is None:
        raise JQuantsError("requests が未インストールです。")
    mailaddress = mailaddress or os.environ.get("JQUANTS_MAILADDRESS")
    password = password or os.environ.get("JQUANTS_PASSWORD")
    refresh_token = refresh_token or os.environ.get("JQUANTS_REFRESH_TOKEN")

    if not refresh_token:
        if not mailaddress or not password:
            raise JQuantsError(
                "JQUANTS_MAILADDRESS/PASSWORD もしくは JQUANTS_REFRESH_TOKEN を設定してください。")
        r = requests.post(AUTH_USER, json={"mailaddress": mailaddress,
                                           "password": password}, timeout=timeout)
        if r.status_code != 200:
            raise JQuantsError(f"auth_user 失敗: HTTP {r.status_code} {r.text[:200]}")
        refresh_token = r.json().get("refreshToken")
        if not refresh_token:
            raise JQuantsError("refreshToken を取得できませんでした。")

    r = requests.post(f"{AUTH_REFRESH}?refreshtoken={refresh_token}", timeout=timeout)
    if r.status_code != 200:
        raise JQuantsError(f"auth_refresh 失敗: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json().get("idToken")
    if not tok:
        raise JQuantsError("idToken を取得できませんでした。")
    return tok


# ===== 取得 =====
def fetch_statements_by_date(id_token, date_yyyymmdd, timeout=30):
    """
    ある開示日(YYYY-MM-DD)に開示された全銘柄の財務情報を取得する（日次差分向け）。

    ページネーション(pagination_key)に対応。戻り値: 生statementレコードのリスト。
    """
    if requests is None:
        raise JQuantsError("requests が未インストールです。")
    headers = {"Authorization": f"Bearer {id_token}"}
    out, pkey = [], None
    while True:
        params = {"date": date_yyyymmdd}
        if pkey:
            params["pagination_key"] = pkey
        r = requests.get(STATEMENTS, headers=headers, params=params, timeout=timeout)
        if r.status_code != 200:
            raise JQuantsError(f"statements 取得失敗: HTTP {r.status_code} {r.text[:200]}")
        j = r.json()
        out.extend(j.get("statements") or [])
        pkey = j.get("pagination_key")
        if not pkey:
            break
    return out


def fetch_statements_by_code(id_token, code, timeout=30):
    """ある銘柄の財務情報の履歴（過去比・下方修正判定用）を取得する。"""
    if requests is None:
        raise JQuantsError("requests が未インストールです。")
    headers = {"Authorization": f"Bearer {id_token}"}
    r = requests.get(STATEMENTS, headers=headers, params={"code": code}, timeout=timeout)
    if r.status_code != 200:
        raise JQuantsError(f"statements(code) 取得失敗: HTTP {r.status_code} {r.text[:200]}")
    return r.json().get("statements") or []


# ===== 解析（純粋関数・ネットワーク非依存＝テスト可能） =====
def parse_statement(rec):
    """
    1件の statement レコードから、ランキングに必要な財務項目を抽出する。

    戻り値 dict:
      code, disclosed_date, forecast_eps, equity, total_assets, equity_ratio,
      net_sales, profit, forecast_net_sales, forecast_profit,
      prev_forecast_eps（同レコードの前回予想があれば）
    取得できない項目は None。
    """
    def g(*keys):
        for k in keys:
            if k in rec and str(rec.get(k)).strip() not in ("", "-", "－"):
                return rec.get(k)
        return None

    code = (g("LocalCode", "Code") or "")
    code = str(code)[:4] if code else code
    equity = _f(g("Equity"))
    total_assets = _f(g("TotalAssets"))
    er = _f(g("EquityToAssetRatio"))
    if er is None and equity is not None and total_assets not in (None, 0):
        er = equity / total_assets * 100
    elif er is not None and er <= 1.0:
        er = er * 100  # 比率(0-1)で来た場合は%へ
    return {
        "code": code,
        "disclosed_date": g("DisclosedDate"),
        "type_of_document": g("TypeOfDocument"),
        "forecast_eps": _f(g("ForecastEarningsPerShare")),
        "prev_forecast_eps": _f(g("NextYearForecastEarningsPerShare")),
        "equity": equity,
        "total_assets": total_assets,
        "equity_ratio": er,
        "net_sales": _f(g("NetSales")),
        "profit": _f(g("Profit")),
        "forecast_net_sales": _f(g("ForecastNetSales")),
        "forecast_profit": _f(g("ForecastProfit")),
    }


def roe_from(statement):
    """当期純利益 ÷ 自己資本 × 100（%）。算出不可は None。"""
    profit = statement.get("profit")
    equity = statement.get("equity")
    if profit is None or equity in (None, 0):
        return None
    return profit / equity * 100


def latest_and_history(statements):
    """
    同一銘柄の statements 群から、最新レコードと予想EPS履歴(降順→昇順)を返す。

    戻り値: (latest_parsed, [ (disclosed_date, forecast_eps), ... 昇順 ])
    下方修正判定・過去比の材料に使う。
    """
    parsed = [parse_statement(s) for s in (statements or [])]
    parsed = [p for p in parsed if p.get("disclosed_date")]
    parsed.sort(key=lambda p: p["disclosed_date"])
    if not parsed:
        return None, []
    eps_hist = [(p["disclosed_date"], p["forecast_eps"]) for p in parsed
                if p["forecast_eps"] is not None]
    return parsed[-1], eps_hist


def has_downward_revision(eps_hist, within_from=None):
    """
    予想EPS履歴に「下方修正」があるか（後の予想が前より低い箇所があるか）。

    within_from（"YYYY-MM-DD"）以降に限定して判定できる（直近1年など）。
    """
    # 比較のため履歴は全て残し、「下げ転換した回の日付」が窓内かで判定する
    # （窓で予め絞ると、窓境界のすぐ手前にある比較対象を失い、修正を見逃すため）。
    hist = sorted([(d, e) for d, e in (eps_hist or []) if e is not None],
                  key=lambda x: x[0])
    for i in range(1, len(hist)):
        if hist[i][1] < hist[i - 1][1] - 1e-9:
            if within_from is None or hist[i][0] >= within_from:
                return True
    return False
