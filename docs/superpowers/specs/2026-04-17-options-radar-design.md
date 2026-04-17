# options-radar 设计文档

**日期：** 2026-04-17  
**项目：** options-radar  
**目标：** 每天扫描美股期权成交量前 20 名标的，存入 PostgreSQL，通过 FastAPI + Web 界面提供数据

---

## 1. 背景

chart.zaizhichi.com（tv-chart 项目）目前只展示价格数据。options-radar 作为独立服务，提供期权市场数据，未来可供 chart.zaizhichi.com 调用。

---

## 2. 架构

```
GitHub Actions（UTC 14:30 每日自动 + workflow_dispatch 手动触发）
    │
    ├─ 拉取 S&P 500 成分股列表（Wikipedia）
    ├─ 加入热门 ETF（SPY QQQ IWM GLD TLT 等）
    ├─ yfinance 批量获取昨日期权链 + 股票信息
    ├─ 计算各标的期权总成交量，取前 20 名
    ├─ 计算 IV、HV、IV/HV、IV 变化、YTD 涨跌幅等指标
    └─ SSH 隧道连接 VPS PostgreSQL，写入数据
         │
         VPS（91.230.73.42）
         ├─ PostgreSQL：存储历史排名数据
         ├─ FastAPI：REST API（供 chart.zaizhichi.com 调用）
         └─ Web 页面：直接浏览排名（Jinja2 模板）
```

**数据流：**
- Actions 负责数据采集和计算，不占 VPS 资源
- VPS 只负责存储和提供 API，负载极低
- SSH 隧道保证 PostgreSQL 不对外暴露

---

## 3. 数据库设计

### 3.1 主表：options_rankings

```sql
CREATE TABLE options_rankings (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    rank            INT NOT NULL,           -- 1~20

    -- 标的信息
    ticker          VARCHAR(10) NOT NULL,
    market_cap      BIGINT,                 -- 市值（美元）

    -- 期权量
    total_vol       BIGINT,                 -- 期权总成交量（认购+认沽）
    call_vol        BIGINT,                 -- 认购成交量
    put_vol         BIGINT,                 -- 认沽成交量
    opt_oi          BIGINT,                 -- 期权未平仓量

    -- 波动率
    iv              NUMERIC(8,4),           -- 隐含波动率（ATM 近月合约近似）
    iv_change       NUMERIC(8,4),           -- IV 日变化（与前一日对比）
    hv              NUMERIC(8,4),           -- 历史波动率（30日）
    iv_hv_ratio     NUMERIC(8,4),           -- IV/HV 比率
    iv_pct_52w      NUMERIC(6,2),           -- 52周IV百分位（%）

    -- 股票行情
    close_price     NUMERIC(10,2),          -- 昨日收盘价
    price_change    NUMERIC(8,4),           -- 日涨跌幅（%）
    volume          BIGINT,                 -- 股票成交量
    ytd_change      NUMERIC(8,4),           -- YTD 涨跌幅（%）

    -- 财报
    next_earnings   DATE,                   -- 下一个财报日
    days_to_earnings INT,                   -- 距财报天数

    UNIQUE (date, rank)
);

CREATE INDEX idx_rankings_date ON options_rankings(date DESC);
CREATE INDEX idx_rankings_ticker ON options_rankings(ticker);
```

### 3.2 IV 历史表（用于计算 52周IV百分位）

```sql
CREATE TABLE iv_history (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    ticker      VARCHAR(10) NOT NULL,
    iv          NUMERIC(8,4),       -- 当日 IV（或 HV 代理值）
    is_proxy    BOOLEAN DEFAULT FALSE, -- TRUE = HV 代理，FALSE = 真实 IV
    UNIQUE (date, ticker)
);

CREATE INDEX idx_iv_history_ticker_date ON iv_history(ticker, date DESC);
```

### 3.3 52周IV百分位说明

- 首次运行时，回填过去 52 周的历史波动率（HV）作为 IV 代理种子，`is_proxy=TRUE`
- 每日运行后，存入当日真实 IV，`is_proxy=FALSE`
- 随着真实 IV 数据积累，百分位计算越来越准确（约 3 个月后 HV 代理数据占比低于 25%）

---

## 4. 数据采集

### 4.1 股票池

- S&P 500 成分股（从 Wikipedia 动态拉取，约 503 只）
- 热门 ETF：SPY、QQQ、IWM、GLD、TLT、XLF、XLE、XLK、ARKK（9 只）
- 合计约 512 只

### 4.2 yfinance 数据来源

| 字段 | yfinance 方法 |
|------|--------------|
| 期权成交量 / OI | `Ticker.option_chain(expiry)` |
| IV（ATM 近月） | `option_chain` 中最近到期 ATM 合约的 `impliedVolatility` |
| HV（30日） | `Ticker.history(period='3mo')` 计算年化标准差 |
| 市值、股票成交量、价格变化 | `Ticker.fast_info` |
| YTD 涨跌幅 | `Ticker.history(start=年初)` 计算 |
| 下一个财报日 | `Ticker.calendar` |

### 4.3 运行时间

- 美股收盘后约 1 小时数据稳定（UTC 22:00 / 北京时间次日 06:00）
- GitHub Actions 定时：**UTC 22:30（北京时间 06:30）**
- 约 520 只股票，yfinance 限速下估计运行 30-40 分钟

---

## 5. API 设计（FastAPI）

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/rankings/latest` | — | 最新一天前 20 名 |
| GET | `/rankings/{date}` | date: YYYY-MM-DD | 指定日期排名 |
| GET | `/health` | — | 服务健康检查 |

**响应格式：**
```json
{
  "date": "2026-04-16",
  "rankings": [
    {
      "rank": 1,
      "ticker": "NVDA",
      "market_cap": 2800000000000,
      "total_vol": 12500000,
      "call_vol": 8000000,
      "put_vol": 4500000,
      "opt_oi": 45000000,
      "iv": 0.6234,
      "iv_change": 0.0312,
      "hv": 0.5120,
      "iv_hv_ratio": 1.217,
      "iv_pct_52w": 78.5,
      "close_price": 875.20,
      "price_change": 2.34,
      "volume": 45000000,
      "ytd_change": 15.6,
      "next_earnings": "2026-05-28",
      "days_to_earnings": 42
    }
  ]
}
```

---

## 6. Web 界面

- Jinja2 模板，单页展示当日排名表格
- 路径：`/`（默认显示最新日期）
- 路径：`/?date=YYYY-MM-DD`（历史日期）
- 表格列顺序：Rank / Ticker / Market Cap / Opt Vol / Call / Put / OI / IV / IV Chg / HV / IV/HV / 52IV% / Price / Chg% / Vol / YTD% / Next Earnings / Days

---

## 7. 部署

### VPS（91.230.73.42）

```
/opt/options-radar/
├── api/          # FastAPI 应用
├── web/          # Jinja2 模板
├── .env          # DB 连接配置（不入库）
└── requirements.txt
```

- 进程管理：systemd
- 反向代理：Nginx（复用现有配置）
- 端口：内部 8001，Nginx 代理到子域名（如 options.zaizhichi.com）

### GitHub Actions

```
.github/workflows/
└── daily_scan.yml   # 定时 + 手动触发，SSH 隧道连接 VPS DB
```

**Secrets 配置：**
- `VPS_SSH_KEY`：SSH 私钥
- `VPS_HOST`：91.230.73.42
- `VPS_USER`：部署用户
- `DB_NAME` / `DB_USER` / `DB_PASSWORD`

---

## 8. 目录结构

```
options-radar/
├── .github/
│   └── workflows/
│       └── daily_scan.yml
├── scanner/
│   ├── main.py           # 入口，调度整体流程
│   ├── fetch.py          # yfinance 数据拉取
│   ├── calculate.py      # IV/HV/百分位计算
│   └── db.py             # PostgreSQL 写入
├── api/
│   ├── main.py           # FastAPI 应用
│   └── models.py         # Pydantic 响应模型
├── web/
│   └── templates/
│       └── index.html    # 排名页面
├── docs/
│   └── superpowers/specs/
│       └── 2026-04-17-options-radar-design.md
├── requirements.txt
├── .env.example
└── README.md
```

---

## 9. 技术栈

| 组件 | 技术 |
|------|------|
| 数据采集 | Python 3.12 + yfinance + pandas |
| 数据库 | PostgreSQL 15（VPS） |
| DB 连接 | psycopg2 |
| API | FastAPI + uvicorn |
| Web 模板 | Jinja2 |
| CI/CD | GitHub Actions |
| 进程管理 | systemd |
| 反向代理 | Nginx |
