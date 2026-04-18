import os
import secrets
from datetime import date

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from api.models import RankingsResponse, RankingRow
from scanner.db import get_rankings_by_date, get_latest_ranking_date

load_dotenv()

app = FastAPI(title="options-radar", version="1.0.0")
templates = Jinja2Templates(directory="web/templates")
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """验证 HTTP Basic Auth，使用常量时间比较防止时序攻击。"""
    expected_user = os.environ.get("RADAR_USER", "")
    expected_pass = os.environ.get("RADAR_PASSWORD", "")
    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_pass.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="认证失败",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/rankings/latest", response_model=RankingsResponse)
def latest_rankings(_: None = Depends(verify_credentials)):
    latest = get_latest_ranking_date()
    if not latest:
        raise HTTPException(status_code=404, detail="暂无数据")
    rows = get_rankings_by_date(latest)
    return _build_response(latest, rows)


@app.get("/rankings/{target_date}", response_model=RankingsResponse)
def rankings_by_date(target_date: date, _: None = Depends(verify_credentials)):
    rows = get_rankings_by_date(str(target_date))
    if not rows:
        raise HTTPException(status_code=404, detail=f"{target_date} 无数据")
    return _build_response(str(target_date), rows)


@app.get("/", response_class=HTMLResponse)
def web_index(
    request: Request,
    date: str | None = None,
    _: None = Depends(verify_credentials),
):
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
