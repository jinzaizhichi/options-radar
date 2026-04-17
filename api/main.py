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
