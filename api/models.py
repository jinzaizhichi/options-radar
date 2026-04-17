from datetime import date
from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class RankingsResponse(BaseModel):
    date: date
    rankings: list[RankingRow]
