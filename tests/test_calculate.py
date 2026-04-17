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
