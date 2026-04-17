import os
from contextlib import contextmanager

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _get_conn():
    """直连 PostgreSQL（本地开发或 VPS 本地使用）。"""
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


@contextmanager
def get_connection():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_rankings(rows: list[dict], date: str) -> None:
    """写入当日前 20 名排名，已存在则覆盖。"""
    sql = """
        INSERT INTO options_rankings (
            date, rank, ticker, market_cap,
            total_vol, call_vol, put_vol, opt_oi,
            iv, iv_change, hv, iv_hv_ratio, iv_pct_52w,
            close_price, price_change, volume, ytd_change,
            next_earnings, days_to_earnings
        ) VALUES (
            %(date)s, %(rank)s, %(ticker)s, %(market_cap)s,
            %(total_vol)s, %(call_vol)s, %(put_vol)s, %(opt_oi)s,
            %(iv)s, %(iv_change)s, %(hv)s, %(iv_hv_ratio)s, %(iv_pct_52w)s,
            %(close_price)s, %(price_change)s, %(volume)s, %(ytd_change)s,
            %(next_earnings)s, %(days_to_earnings)s
        )
        ON CONFLICT (date, rank) DO UPDATE SET
            ticker          = EXCLUDED.ticker,
            market_cap      = EXCLUDED.market_cap,
            total_vol       = EXCLUDED.total_vol,
            call_vol        = EXCLUDED.call_vol,
            put_vol         = EXCLUDED.put_vol,
            opt_oi          = EXCLUDED.opt_oi,
            iv              = EXCLUDED.iv,
            iv_change       = EXCLUDED.iv_change,
            hv              = EXCLUDED.hv,
            iv_hv_ratio     = EXCLUDED.iv_hv_ratio,
            iv_pct_52w      = EXCLUDED.iv_pct_52w,
            close_price     = EXCLUDED.close_price,
            price_change    = EXCLUDED.price_change,
            volume          = EXCLUDED.volume,
            ytd_change      = EXCLUDED.ytd_change,
            next_earnings   = EXCLUDED.next_earnings,
            days_to_earnings = EXCLUDED.days_to_earnings
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)


def upsert_iv_history(rows: list[dict]) -> None:
    """写入 IV 历史（用于计算 52周百分位）。"""
    sql = """
        INSERT INTO iv_history (date, ticker, iv, is_proxy)
        VALUES (%(date)s, %(ticker)s, %(iv)s, %(is_proxy)s)
        ON CONFLICT (date, ticker) DO UPDATE SET
            iv       = EXCLUDED.iv,
            is_proxy = EXCLUDED.is_proxy
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)


def get_iv_history(tickers: list[str]) -> dict[str, list[float]]:
    """拉取所有 ticker 过去 52 周的 IV 历史，返回 {ticker: [iv, ...]}。"""
    sql = """
        SELECT ticker, iv FROM iv_history
        WHERE ticker = ANY(%s)
          AND date >= CURRENT_DATE - INTERVAL '365 days'
          AND iv IS NOT NULL
        ORDER BY ticker, date
    """
    result: dict[str, list[float]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tickers,))
            for ticker, iv in cur.fetchall():
                result.setdefault(ticker, []).append(float(iv))
    return result


def get_previous_iv(tickers: list[str]) -> dict[str, float]:
    """拉取每个 ticker 最近一条 IV 记录（用于计算 IV 日变化）。"""
    sql = """
        SELECT DISTINCT ON (ticker) ticker, iv
        FROM iv_history
        WHERE ticker = ANY(%s)
          AND date < CURRENT_DATE
          AND iv IS NOT NULL
        ORDER BY ticker, date DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tickers,))
            return {row[0]: float(row[1]) for row in cur.fetchall()}


def get_latest_ranking_date() -> str | None:
    """查询数据库中最新的排名日期。"""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM options_rankings")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None


def get_rankings_by_date(date: str) -> list[dict]:
    """按日期查询排名，返回有序列表。"""
    sql = """
        SELECT date, rank, ticker, market_cap,
               total_vol, call_vol, put_vol, opt_oi,
               iv, iv_change, hv, iv_hv_ratio, iv_pct_52w,
               close_price, price_change, volume, ytd_change,
               next_earnings, days_to_earnings
        FROM options_rankings
        WHERE date = %s
        ORDER BY rank
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (date,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
