# options-radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天扫描 S&P 500 + 热门 ETF 期权成交量，取前 20 名标的，存入 VPS PostgreSQL，通过 FastAPI + Web 界面提供数据。

**Architecture:** GitHub Actions 每天 UTC 22:30 运行扫描脚本，通过 SSH 隧道连接 VPS PostgreSQL 写入数据；VPS 部署 FastAPI 提供 REST API 和 Jinja2 Web 页面。

**Tech Stack:** Python 3.12, yfinance, pandas, psycopg2, FastAPI, Jinja2, PostgreSQL 15, GitHub Actions, systemd, Nginx

---

## File Map

```
options-radar/
├── .github/workflows/daily_scan.yml   # Actions 定时+手动触发
├── scanner/
│   ├── __init__.py
│   ├── main.py          # 入口：调度全流程
│   ├── fetch.py         # yfinance 数据拉取
│   ├── calculate.py     # IV/HV/百分位/YTD 计算
│   └── db.py            # PostgreSQL 连接和写入
├── api/
│   ├── __init__.py
│   ├── main.py          # FastAPI 应用
│   └── models.py        # Pydantic 响应模型
├── web/templates/
│   └── index.html       # 排名页面（Jinja2）
├── tests/
│   ├── test_calculate.py
│   └── test_api.py
├── deploy/
│   ├── options-radar.service  # systemd 服务文件
│   └── nginx.conf             # Nginx 配置片段
├── requirements.txt
├── .env.example
└── README.md
```

---

### Task 1: 项目初始化

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `scanner/__init__.py`
- Create: `api/__init__.py`

- [ ] **Step 1: 初始化 git 仓库**

```bash
cd /home/jinzaizhichi/options-radar
git init
git checkout -b main
```

- [ ] **Step 2: 写 requirements.txt**

```
yfinance==0.2.54
pandas==2.2.3
psycopg2-binary==2.9.9
fastapi==0.115.0
uvicorn==0.30.6
jinja2==3.1.4
python-dotenv==1.0.1
sshtunnel==0.4.0
httpx==0.27.2
pytest==8.3.3
```

- [ ] **Step 3: 写 .env.example**

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=options_radar
DB_USER=options_user
DB_PASSWORD=your_password

# SSH 隧道（Actions 用，本地直连时留空）
SSH_HOST=91.230.73.42
SSH_USER=deploy
SSH_KEY_PATH=~/.ssh/id_rsa
```

- [ ] **Step 4: 创建空 __init__.py**

```bash
touch scanner/__init__.py api/__init__.py tests/__init__.py
```

- [ ] **Step 5: 创建 .gitignore**

```
.env
__pycache__/
*.pyc
.pytest_cache/
venv/
.venv/
*.egg-info/
```

- [ ] **Step 6: 初始提交**

```bash
git add .
git commit -m "chore: init project structure"
```

---

### Task 2: 数据库 Schema

**Files:**
- Create: `db/schema.sql`
- Create: `scanner/db.py`

- [ ] **Step 1: 写 schema.sql**

```sql
-- db/schema.sql

CREATE TABLE IF NOT EXISTS options_rankings (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    rank            INT NOT NULL,
    ticker          VARCHAR(10) NOT NULL,
    market_cap      BIGINT,
    total_vol       BIGINT,
    call_vol        BIGINT,
    put_vol         BIGINT,
    opt_oi          BIGINT,
    iv              NUMERIC(8,4),
    iv_change       NUMERIC(8,4),
    hv              NUMERIC(8,4),
    iv_hv_ratio     NUMERIC(8,4),
    iv_pct_52w      NUMERIC(6,2),
    close_price     NUMERIC(10,2),
    price_change    NUMERIC(8,4),
    volume          BIGINT,
    ytd_change      NUMERIC(8,4),
    next_earnings   DATE,
    days_to_earnings INT,
    UNIQUE (date, rank)
);

CREATE TABLE IF NOT EXISTS iv_history (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    ticker      VARCHAR(10) NOT NULL,
    iv          NUMERIC(8,4),
    is_proxy    BOOLEAN DEFAULT FALSE,
    UNIQUE (date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_rankings_date   ON options_rankings(date DESC);
CREATE INDEX IF NOT EXISTS idx_rankings_ticker ON options_rankings(ticker);
CREATE INDEX IF NOT EXISTS idx_iv_ticker_date  ON iv_history(ticker, date DESC);
```

- [ ] **Step 2: 在 VPS 上建库建用户**

SSH 进 VPS 执行：
```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE options_radar;
CREATE USER options_user WITH PASSWORD 'your_strong_password';
GRANT ALL PRIVILEGES ON DATABASE options_radar TO options_user;
\c options_radar
GRANT ALL ON SCHEMA public TO options_user;
SQL

psql -U options_user -d options_radar -f db/schema.sql
```

- [ ] **Step 3: 写 scanner/db.py**

```python
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
```

- [ ] **Step 4: 提交**

```bash
git add db/schema.sql scanner/db.py
git commit -m "feat: add database schema and db module"
```

---

### Task 3: 计算模块（TDD）

**Files:**
- Create: `scanner/calculate.py`
- Create: `tests/test_calculate.py`

- [ ] **Step 1: 先写测试**

```python
# tests/test_calculate.py
import pytest
import pandas as pd
from scanner.calculate import (
    calc_hv,
    calc_iv_percentile,
    calc_ytd_change,
    calc_iv_hv_ratio,
    calc_days_to_earnings,
)


def test_calc_hv_normal():
    """30日历史波动率应该是正数。"""
    prices = [100.0 + i * 0.5 + (i % 3) * 2 for i in range(60)]
    hv = calc_hv(prices)
    assert hv is not None
    assert 0 < hv < 5.0  # 年化，正常范围


def test_calc_hv_insufficient_data():
    """数据不足 31 条时返回 None。"""
    assert calc_hv([100.0] * 10) is None


def test_calc_hv_constant_prices():
    """价格不变时 HV 为 0。"""
    prices = [100.0] * 60
    hv = calc_hv(prices)
    assert hv == 0.0


def test_calc_iv_percentile_middle():
    """当前 IV 处于历史中间时，百分位约为 50。"""
    history = list(range(1, 101))  # 1~100
    pct = calc_iv_percentile(50.0, history)
    assert 45.0 <= pct <= 55.0


def test_calc_iv_percentile_max():
    """当前 IV 高于历史所有值时，百分位为 100。"""
    history = [10.0, 20.0, 30.0]
    assert calc_iv_percentile(99.0, history) == 100.0


def test_calc_iv_percentile_empty_history():
    """历史数据为空时返回 None。"""
    assert calc_iv_percentile(0.5, []) is None


def test_calc_ytd_change_positive():
    """年初到昨日上涨 10%。"""
    assert abs(calc_ytd_change(110.0, 100.0) - 10.0) < 0.01


def test_calc_ytd_change_zero_base():
    """年初价格为 0 时返回 None。"""
    assert calc_ytd_change(100.0, 0.0) is None


def test_calc_iv_hv_ratio():
    """IV/HV 比率计算正确。"""
    assert abs(calc_iv_hv_ratio(0.4, 0.2) - 2.0) < 0.001


def test_calc_iv_hv_ratio_zero_hv():
    """HV 为 0 时返回 None（避免除零）。"""
    assert calc_iv_hv_ratio(0.4, 0.0) is None


def test_calc_days_to_earnings_future():
    """财报在未来时返回正整数。"""
    from datetime import date, timedelta
    future = date.today() + timedelta(days=30)
    assert calc_days_to_earnings(future) == 30


def test_calc_days_to_earnings_none():
    """财报日期为 None 时返回 None。"""
    assert calc_days_to_earnings(None) is None
```

- [ ] **Step 2: 运行测试，确认全部 FAIL**

```bash
pytest tests/test_calculate.py -v
```

期望：`ImportError` 或 `ModuleNotFoundError`（模块还未实现）

- [ ] **Step 3: 实现 scanner/calculate.py**

```python
# scanner/calculate.py
import math
from datetime import date


def calc_hv(prices: list[float], window: int = 30) -> float | None:
    """
    计算年化历史波动率（30日）。
    公式：对数收益率的标准差 * sqrt(252)
    """
    if len(prices) < window + 1:
        return None
    recent = prices[-(window + 1):]
    log_returns = [
        math.log(recent[i] / recent[i - 1])
        for i in range(1, len(recent))
        if recent[i - 1] > 0 and recent[i] > 0
    ]
    if not log_returns:
        return None
    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / n
    return round(math.sqrt(variance * 252), 4)


def calc_iv_percentile(current_iv: float, history: list[float]) -> float | None:
    """
    计算 52周 IV 百分位。
    百分位 = 历史中低于当前 IV 的数量 / 总数量 * 100
    """
    if not history:
        return None
    below = sum(1 for h in history if h < current_iv)
    return round(below / len(history) * 100, 2)


def calc_ytd_change(current_price: float, ytd_open: float) -> float | None:
    """年初至今涨跌幅（%）。"""
    if not ytd_open:
        return None
    return round((current_price - ytd_open) / ytd_open * 100, 4)


def calc_iv_hv_ratio(iv: float | None, hv: float | None) -> float | None:
    """IV / HV 比率，HV 为 0 时返回 None。"""
    if iv is None or hv is None or hv == 0:
        return None
    return round(iv / hv, 4)


def calc_days_to_earnings(earnings_date: date | None) -> int | None:
    """距离下一个财报日的天数。"""
    if earnings_date is None:
        return None
    return (earnings_date - date.today()).days
```

- [ ] **Step 4: 运行测试，确认全部 PASS**

```bash
pytest tests/test_calculate.py -v
```

期望：全部绿色

- [ ] **Step 5: 提交**

```bash
git add scanner/calculate.py tests/test_calculate.py
git commit -m "feat: add calculation module with tests"
```

---

### Task 4: 数据拉取模块

**Files:**
- Create: `scanner/fetch.py`

- [ ] **Step 1: 写 scanner/fetch.py**

```python
# scanner/fetch.py
"""
yfinance 数据拉取。
每个函数独立拉取，失败时返回 None 而非抛出异常，
主流程决定是否跳过该 ticker。
"""
import time
import datetime
import pandas as pd
import yfinance as yf


# S&P 500 + 热门 ETF
EXTRA_ETFS = ["SPY", "QQQ", "IWM", "GLD", "TLT", "XLF", "XLE", "XLK", "ARKK"]


def get_sp500_tickers() -> list[str]:
    """从 Wikipedia 拉取 S&P 500 成分股列表。"""
    table = pd.read_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    )[0]
    tickers = table["Symbol"].str.replace(".", "-", regex=False).tolist()
    return sorted(set(tickers + EXTRA_ETFS))


def get_options_data(ticker: str) -> dict | None:
    """
    拉取单个 ticker 的期权链，返回汇总数据。
    取最近 3 个到期日的合约汇总，避免遗漏近期活跃合约。
    """
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return None

        # 取最近 3 个到期日
        recent_expiries = expirations[:3]
        total_call_vol = 0
        total_put_vol = 0
        total_call_oi = 0
        total_put_oi = 0
        atm_iv = None

        try:
            info = t.fast_info
            current_price = info.last_price
        except Exception:
            current_price = None

        for expiry in recent_expiries:
            try:
                chain = t.option_chain(expiry)
                calls = chain.calls
                puts = chain.puts

                total_call_vol += int(calls["volume"].fillna(0).sum())
                total_put_vol += int(puts["volume"].fillna(0).sum())
                total_call_oi += int(calls["openInterest"].fillna(0).sum())
                total_put_oi += int(puts["openInterest"].fillna(0).sum())

                # 用第一个到期日的 ATM 合约估算 IV
                if atm_iv is None and current_price:
                    atm_iv = _get_atm_iv(calls, puts, current_price)
            except Exception:
                continue

        return {
            "call_vol": total_call_vol,
            "put_vol": total_put_vol,
            "total_vol": total_call_vol + total_put_vol,
            "opt_oi": total_call_oi + total_put_oi,
            "iv": atm_iv,
        }
    except Exception:
        return None


def _get_atm_iv(calls: pd.DataFrame, puts: pd.DataFrame, price: float) -> float | None:
    """找最近 ATM 合约的 IV（认购和认沽平均）。"""
    try:
        call_atm = calls.iloc[(calls["strike"] - price).abs().argsort()[:1]]
        put_atm = puts.iloc[(puts["strike"] - price).abs().argsort()[:1]]
        call_iv = float(call_atm["impliedVolatility"].values[0])
        put_iv = float(put_atm["impliedVolatility"].values[0])
        if call_iv > 0 and put_iv > 0:
            return round((call_iv + put_iv) / 2, 4)
    except Exception:
        pass
    return None


def get_stock_info(ticker: str) -> dict | None:
    """拉取股票基本信息：价格变化、成交量、市值、财报日。"""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info

        # 今年年初日期
        today = datetime.date.today()
        ytd_start = datetime.date(today.year, 1, 1)

        # 历史价格：过去 1 年（用于 HV + YTD）
        hist = t.history(period="1y")
        if hist.empty:
            return None

        prices = hist["Close"].tolist()
        ytd_open = None
        try:
            ytd_hist = t.history(start=ytd_start, end=ytd_start + datetime.timedelta(days=7))
            if not ytd_hist.empty:
                ytd_open = float(ytd_hist["Close"].iloc[0])
        except Exception:
            pass

        # 财报日
        next_earnings = None
        try:
            cal = t.calendar
            if cal is not None and "Earnings Date" in cal:
                ed = cal["Earnings Date"]
                if hasattr(ed, "__iter__"):
                    # 取未来最近的财报日
                    future = [
                        d for d in ed
                        if hasattr(d, "date") and d.date() >= today
                    ]
                    if future:
                        next_earnings = future[0].date()
                elif hasattr(ed, "date"):
                    next_earnings = ed.date()
        except Exception:
            pass

        return {
            "close_price": round(float(info.last_price), 2) if info.last_price else None,
            "price_change": round(float(info.regular_market_previous_close), 2) if hasattr(info, "regular_market_previous_close") else None,
            "volume": int(info.three_month_average_volume) if hasattr(info, "three_month_average_volume") else None,
            "market_cap": int(info.market_cap) if hasattr(info, "market_cap") and info.market_cap else None,
            "prices": prices,        # 用于计算 HV
            "ytd_open": ytd_open,    # 用于计算 YTD
            "next_earnings": next_earnings,
        }
    except Exception:
        return None


def backfill_iv_history(ticker: str) -> list[dict]:
    """
    回填过去 52 周的 HV 作为 IV 历史代理种子。
    每周取一个数据点（减少 API 调用次数）。
    """
    try:
        hist = yf.Ticker(ticker).history(period="2y")
        if hist.empty or len(hist) < 31:
            return []

        prices = hist["Close"].tolist()
        dates = hist.index.tolist()
        rows = []

        # 每 5 个交易日取一个点（约每周一次）
        for i in range(30, len(prices), 5):
            from scanner.calculate import calc_hv
            hv = calc_hv(prices[max(0, i-60):i+1])
            if hv is not None:
                d = dates[i]
                rows.append({
                    "date": d.date() if hasattr(d, "date") else d,
                    "ticker": ticker,
                    "iv": hv,
                    "is_proxy": True,
                })
        return rows
    except Exception:
        return []
```

- [ ] **Step 2: 提交**

```bash
git add scanner/fetch.py
git commit -m "feat: add yfinance fetch module"
```

---

### Task 5: 扫描主流程

**Files:**
- Create: `scanner/main.py`

- [ ] **Step 1: 写 scanner/main.py**

```python
# scanner/main.py
"""
主扫描流程：
1. 拉取股票池
2. 逐一获取期权数据，取前 20 名
3. 补充股票信息和计算指标
4. 写入数据库
"""
import datetime
import time
import sys

from scanner.fetch import get_sp500_tickers, get_options_data, get_stock_info, backfill_iv_history
from scanner.calculate import calc_hv, calc_iv_percentile, calc_ytd_change, calc_iv_hv_ratio, calc_days_to_earnings
from scanner.db import upsert_rankings, upsert_iv_history, get_iv_history, get_previous_iv


def run_scan(backfill: bool = False) -> None:
    today = datetime.date.today()
    # 美股昨天收盘日（跳过周末）
    scan_date = _last_trading_day(today)
    print(f"[options-radar] 扫描日期: {scan_date}")

    # 1. 获取股票池
    print("[1/5] 拉取股票池...")
    tickers = get_sp500_tickers()
    print(f"  股票池: {len(tickers)} 只")

    # 2. 逐一拉取期权成交量
    print("[2/5] 拉取期权数据（可能需要 30-40 分钟）...")
    vol_data: list[tuple[str, dict]] = []
    for i, ticker in enumerate(tickers):
        data = get_options_data(ticker)
        if data and data["total_vol"] > 0:
            vol_data.append((ticker, data))
        if i % 50 == 0:
            print(f"  进度: {i}/{len(tickers)}")
        time.sleep(0.3)  # 避免被 yfinance 限速

    # 3. 按总成交量排序，取前 20
    vol_data.sort(key=lambda x: x[1]["total_vol"], reverse=True)
    top20 = vol_data[:20]
    top20_tickers = [t for t, _ in top20]
    print(f"[3/5] 前 20 名: {top20_tickers}")

    # 4. 回填 IV 历史（仅首次运行）
    if backfill:
        print("[4/5] 回填 52 周 IV 历史（首次运行，耗时较长）...")
        for ticker in top20_tickers:
            rows = backfill_iv_history(ticker)
            if rows:
                upsert_iv_history(rows)
                print(f"  {ticker}: 回填 {len(rows)} 条")
            time.sleep(0.5)
    else:
        print("[4/5] 跳过回填（非首次运行）")

    # 5. 补充计算指标，写入数据库
    print("[5/5] 计算指标，写入数据库...")
    iv_histories = get_iv_history(top20_tickers)
    prev_ivs = get_previous_iv(top20_tickers)

    ranking_rows = []
    iv_history_rows = []

    for rank, (ticker, opt_data) in enumerate(top20, start=1):
        stock = get_stock_info(ticker)
        if stock is None:
            stock = {}
        time.sleep(0.3)

        prices = stock.get("prices", [])
        current_iv = opt_data.get("iv")
        hv = calc_hv(prices)
        iv_history = iv_histories.get(ticker, [])
        prev_iv = prev_ivs.get(ticker)

        iv_change = None
        if current_iv and prev_iv:
            iv_change = round(current_iv - prev_iv, 4)

        iv_pct = calc_iv_percentile(current_iv, iv_history) if current_iv else None

        ytd_open = stock.get("ytd_open")
        close = stock.get("close_price")
        ytd_change = calc_ytd_change(close, ytd_open) if close and ytd_open else None

        next_earnings = stock.get("next_earnings")

        row = {
            "date": scan_date,
            "rank": rank,
            "ticker": ticker,
            "market_cap": stock.get("market_cap"),
            "total_vol": opt_data["total_vol"],
            "call_vol": opt_data["call_vol"],
            "put_vol": opt_data["put_vol"],
            "opt_oi": opt_data["opt_oi"],
            "iv": current_iv,
            "iv_change": iv_change,
            "hv": hv,
            "iv_hv_ratio": calc_iv_hv_ratio(current_iv, hv),
            "iv_pct_52w": iv_pct,
            "close_price": close,
            "price_change": stock.get("price_change"),
            "volume": stock.get("volume"),
            "ytd_change": ytd_change,
            "next_earnings": next_earnings,
            "days_to_earnings": calc_days_to_earnings(next_earnings),
        }
        ranking_rows.append(row)

        # 当日 IV 存入历史
        if current_iv:
            iv_history_rows.append({
                "date": scan_date,
                "ticker": ticker,
                "iv": current_iv,
                "is_proxy": False,
            })

    upsert_rankings(ranking_rows, str(scan_date))
    if iv_history_rows:
        upsert_iv_history(iv_history_rows)

    print(f"[完成] {len(ranking_rows)} 条排名写入数据库")


def _last_trading_day(today: datetime.date) -> datetime.date:
    """返回最近一个交易日（排除周末）。"""
    d = today - datetime.timedelta(days=1)
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= datetime.timedelta(days=1)
    return d


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    run_scan(backfill=backfill)
```

- [ ] **Step 2: 本地测试（只跑 3 只股票验证流程）**

临时修改 `main.py` 中 `tickers = get_sp500_tickers()` 改为 `tickers = ["AAPL", "NVDA", "SPY"]`，
运行：
```bash
python -m scanner.main
```
确认输出正常后恢复原代码。

- [ ] **Step 3: 提交**

```bash
git add scanner/main.py
git commit -m "feat: add main scan orchestrator"
```

---

### Task 6: FastAPI + 模型

**Files:**
- Create: `api/models.py`
- Create: `api/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: 写 api/models.py**

```python
# api/models.py
from datetime import date
from pydantic import BaseModel


class RankingRow(BaseModel):
    rank: int
    ticker: str
    market_cap: int | None
    total_vol: int | None
    call_vol: int | None
    put_vol: int | None
    opt_oi: int | None
    iv: float | None
    iv_change: float | None
    hv: float | None
    iv_hv_ratio: float | None
    iv_pct_52w: float | None
    close_price: float | None
    price_change: float | None
    volume: int | None
    ytd_change: float | None
    next_earnings: date | None
    days_to_earnings: int | None

    class Config:
        from_attributes = True


class RankingsResponse(BaseModel):
    date: date
    rankings: list[RankingRow]
```

- [ ] **Step 2: 写 api/main.py**

```python
# api/main.py
from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

from api.models import RankingsResponse, RankingRow
from scanner.db import get_rankings_by_date, get_latest_ranking_date

app = FastAPI(title="options-radar", version="1.0.0")
templates = Jinja2Templates(directory="web/templates")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/rankings/latest", response_model=RankingsResponse)
def latest_rankings():
    latest = get_latest_ranking_date()
    if not latest:
        raise HTTPException(status_code=404, detail="暂无数据")
    rows = get_rankings_by_date(latest)
    return _build_response(latest, rows)


@app.get("/rankings/{target_date}", response_model=RankingsResponse)
def rankings_by_date(target_date: date):
    rows = get_rankings_by_date(str(target_date))
    if not rows:
        raise HTTPException(status_code=404, detail=f"{target_date} 无数据")
    return _build_response(str(target_date), rows)


@app.get("/", response_class=HTMLResponse)
def web_index(request: Request, date: str | None = None):
    if date:
        rows = get_rankings_by_date(date)
        display_date = date
    else:
        display_date = get_latest_ranking_date()
        rows = get_rankings_by_date(display_date) if display_date else []
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "date": display_date, "rankings": rows},
    )


def _build_response(d: str, rows: list[dict]) -> RankingsResponse:
    return RankingsResponse(
        date=date.fromisoformat(d),
        rankings=[RankingRow(**row) for row in rows],
    )
```

- [ ] **Step 3: 写测试**

```python
# tests/test_api.py
from unittest.mock import patch
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

MOCK_DATE = "2026-04-16"
MOCK_ROW = {
    "rank": 1, "ticker": "NVDA", "market_cap": 2800000000000,
    "total_vol": 12000000, "call_vol": 8000000, "put_vol": 4000000,
    "opt_oi": 45000000, "iv": 0.62, "iv_change": 0.03,
    "hv": 0.51, "iv_hv_ratio": 1.21, "iv_pct_52w": 78.5,
    "close_price": 875.20, "price_change": 2.34,
    "volume": 45000000, "ytd_change": 15.6,
    "next_earnings": "2026-05-28", "days_to_earnings": 42,
    "date": "2026-04-16",
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_latest_rankings():
    with patch("api.main.get_latest_ranking_date", return_value=MOCK_DATE), \
         patch("api.main.get_rankings_by_date", return_value=[MOCK_ROW]):
        resp = client.get("/rankings/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == MOCK_DATE
    assert len(data["rankings"]) == 1
    assert data["rankings"][0]["ticker"] == "NVDA"


def test_latest_rankings_no_data():
    with patch("api.main.get_latest_ranking_date", return_value=None):
        resp = client.get("/rankings/latest")
    assert resp.status_code == 404


def test_rankings_by_date():
    with patch("api.main.get_rankings_by_date", return_value=[MOCK_ROW]):
        resp = client.get(f"/rankings/{MOCK_DATE}")
    assert resp.status_code == 200


def test_rankings_by_date_not_found():
    with patch("api.main.get_rankings_by_date", return_value=[]):
        resp = client.get("/rankings/2000-01-01")
    assert resp.status_code == 404
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_api.py -v
```

期望：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add api/models.py api/main.py tests/test_api.py
git commit -m "feat: add FastAPI endpoints and models with tests"
```

---

### Task 7: Web 模板

**Files:**
- Create: `web/templates/index.html`

- [ ] **Step 1: 写 index.html**

```html
<!-- web/templates/index.html -->
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Options Radar — 期权成交量排行</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }
    h1 { color: #58a6ff; margin-bottom: 4px; font-size: 1.4rem; }
    .meta { color: #8b949e; font-size: 0.85rem; margin-bottom: 16px; }
    .date-nav { margin-bottom: 16px; }
    .date-nav input { background: #161b22; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 8px; border-radius: 4px; }
    .date-nav button { background: #238636; border: none; color: white; padding: 4px 12px; border-radius: 4px; cursor: pointer; margin-left: 6px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
    th { background: #161b22; color: #8b949e; padding: 8px 6px; text-align: right; border-bottom: 1px solid #30363d; white-space: nowrap; }
    th:first-child, th:nth-child(2) { text-align: left; }
    td { padding: 6px 6px; border-bottom: 1px solid #21262d; text-align: right; }
    td:first-child, td:nth-child(2) { text-align: left; }
    tr:hover { background: #161b22; }
    .ticker { color: #58a6ff; font-weight: bold; }
    .pos { color: #3fb950; }
    .neg { color: #f85149; }
    .earnings-soon { color: #f0883e; }
    .na { color: #484f58; }
  </style>
</head>
<body>
  <h1>⚡ Options Radar</h1>
  <div class="meta">美股期权成交量前 20 名 | 数据日期：{{ date or '暂无数据' }}</div>

  <div class="date-nav">
    <form method="get">
      <input type="date" name="date" value="{{ date }}">
      <button type="submit">查询</button>
    </form>
  </div>

  {% if rankings %}
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Ticker</th>
        <th>市值</th>
        <th>Opt Vol</th>
        <th>Call</th>
        <th>Put</th>
        <th>OI</th>
        <th>IV</th>
        <th>IV Chg</th>
        <th>HV</th>
        <th>IV/HV</th>
        <th>52IV%</th>
        <th>Price</th>
        <th>Chg%</th>
        <th>Vol</th>
        <th>YTD%</th>
        <th>Next Earnings</th>
        <th>Days</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rankings %}
      <tr>
        <td>{{ r.rank }}</td>
        <td class="ticker">{{ r.ticker }}</td>
        <td>{{ (r.market_cap / 1e9) | round(1) }}B</td>
        <td>{{ '{:,.0f}'.format(r.total_vol or 0) }}</td>
        <td>{{ '{:,.0f}'.format(r.call_vol or 0) }}</td>
        <td>{{ '{:,.0f}'.format(r.put_vol or 0) }}</td>
        <td>{{ '{:,.0f}'.format(r.opt_oi or 0) }}</td>
        <td>{{ (r.iv * 100) | round(1) if r.iv else '-' }}%</td>
        <td class="{{ 'pos' if r.iv_change and r.iv_change > 0 else 'neg' if r.iv_change and r.iv_change < 0 else '' }}">
          {{ ('+' if r.iv_change and r.iv_change > 0 else '') + ((r.iv_change * 100) | round(2) | string) + '%' if r.iv_change else '-' }}
        </td>
        <td>{{ (r.hv * 100) | round(1) if r.hv else '-' }}%</td>
        <td>{{ r.iv_hv_ratio | round(2) if r.iv_hv_ratio else '-' }}</td>
        <td>{{ r.iv_pct_52w | round(1) if r.iv_pct_52w else '-' }}%</td>
        <td>${{ r.close_price }}</td>
        <td class="{{ 'pos' if r.price_change and r.price_change > 0 else 'neg' }}">
          {{ ('+' if r.price_change and r.price_change > 0 else '') + (r.price_change | round(2) | string) + '%' if r.price_change else '-' }}
        </td>
        <td>{{ '{:,.0f}'.format(r.volume or 0) }}</td>
        <td class="{{ 'pos' if r.ytd_change and r.ytd_change > 0 else 'neg' }}">
          {{ ('+' if r.ytd_change and r.ytd_change > 0 else '') + (r.ytd_change | round(2) | string) + '%' if r.ytd_change else '-' }}
        </td>
        <td class="{{ 'earnings-soon' if r.days_to_earnings and r.days_to_earnings <= 14 else '' }}">
          {{ r.next_earnings or '-' }}
        </td>
        <td>{{ r.days_to_earnings or '-' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#8b949e; margin-top:40px;">暂无数据</p>
  {% endif %}
</body>
</html>
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/index.html
git commit -m "feat: add web ranking page"
```

---

### Task 8: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/daily_scan.yml`

- [ ] **Step 1: 写 daily_scan.yml**

```yaml
# .github/workflows/daily_scan.yml
name: Daily Options Scan

on:
  schedule:
    - cron: '30 22 * * 1-5'   # 美东时间收盘后，周一到周五
  workflow_dispatch:
    inputs:
      backfill:
        description: '首次运行回填52周IV历史'
        type: boolean
        default: false

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 90

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Setup SSH key
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.VPS_SSH_KEY }}" > ~/.ssh/id_rsa
          chmod 600 ~/.ssh/id_rsa
          ssh-keyscan -H ${{ secrets.VPS_HOST }} >> ~/.ssh/known_hosts

      - name: Open SSH tunnel to PostgreSQL
        run: |
          # 后台建立 SSH 隧道：本地 15432 -> VPS 5432
          ssh -f -N -L 15432:localhost:5432 \
            ${{ secrets.VPS_USER }}@${{ secrets.VPS_HOST }} \
            -o StrictHostKeyChecking=no
          sleep 3

      - name: Run scan
        env:
          DB_HOST: localhost
          DB_PORT: 15432
          DB_NAME: ${{ secrets.DB_NAME }}
          DB_USER: ${{ secrets.DB_USER }}
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
        run: |
          if [ "${{ inputs.backfill }}" = "true" ]; then
            python -m scanner.main --backfill
          else
            python -m scanner.main
          fi
```

- [ ] **Step 2: 在 GitHub 仓库设置 Secrets**

进入仓库 → Settings → Secrets and variables → Actions，添加：
- `VPS_SSH_KEY`：VPS 的 SSH 私钥内容
- `VPS_HOST`：`91.230.73.42`
- `VPS_USER`：VPS 上的部署用户名
- `DB_NAME`：`options_radar`
- `DB_USER`：`options_user`
- `DB_PASSWORD`：数据库密码

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/daily_scan.yml
git commit -m "feat: add GitHub Actions daily scan workflow"
```

---

### Task 9: VPS 部署

**Files:**
- Create: `deploy/options-radar.service`
- Create: `deploy/nginx.conf`

- [ ] **Step 1: 写 systemd 服务文件**

```ini
# deploy/options-radar.service
[Unit]
Description=Options Radar API
After=network.target postgresql.service

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/options-radar
ExecStart=/opt/options-radar/venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5
EnvironmentFile=/opt/options-radar/.env

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 写 Nginx 配置**

```nginx
# deploy/nginx.conf
# 加入到 /etc/nginx/sites-available/options-radar
server {
    listen 80;
    server_name options.zaizhichi.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

- [ ] **Step 3: 在 VPS 上部署**

SSH 进 VPS 执行：
```bash
# 克隆代码
git clone https://github.com/jinzaizhichi/options-radar.git /opt/options-radar
cd /opt/options-radar

# 建虚拟环境
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 复制并填写环境变量
cp .env.example .env
nano .env   # 填入 DB_HOST=localhost, DB_PORT=5432 等

# 安装 systemd 服务
sudo cp deploy/options-radar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable options-radar
sudo systemctl start options-radar

# 配置 Nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/options-radar
sudo ln -s /etc/nginx/sites-available/options-radar /etc/nginx/sites-enabled/
sudo nginx -t && sudo nginx -s reload

# 申请 SSL（可选）
sudo certbot --nginx -d options.zaizhichi.com
```

- [ ] **Step 4: 验证 API 正常**

```bash
curl https://options.zaizhichi.com/health
# 期望: {"status": "ok"}
```

- [ ] **Step 5: 提交**

```bash
git add deploy/
git commit -m "chore: add systemd and nginx deploy config"
```

---

### Task 10: 创建 GitHub 仓库并推送

- [ ] **Step 1: 在 GitHub 创建仓库**

```bash
gh repo create jinzaizhichi/options-radar --public --description "美股期权成交量前20名日扫描"
```

- [ ] **Step 2: 推送**

```bash
git remote add origin https://github.com/jinzaizhichi/options-radar.git
git push -u origin main
```

- [ ] **Step 3: 首次运行（回填 IV 历史）**

在 GitHub Actions 页面手动触发 workflow，勾选 `backfill: true`

- [ ] **Step 4: 验证数据**

```bash
curl https://options.zaizhichi.com/rankings/latest | python3 -m json.tool | head -50
```

---

## 附：本地开发快速启动

```bash
# 1. 安装依赖
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 配置本地 .env（需要本地 PostgreSQL 或 SSH 隧道）
cp .env.example .env

# 3. 初始化数据库
psql -U options_user -d options_radar -f db/schema.sql

# 4. 运行扫描（测试用，仅 3 只股票）
python -m scanner.main  # 修改 main.py 中 tickers 为小列表

# 5. 启动 API
uvicorn api.main:app --reload --port 8001

# 6. 访问
open http://localhost:8001/
```
