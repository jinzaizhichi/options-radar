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
