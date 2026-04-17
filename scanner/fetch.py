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
