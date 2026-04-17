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
