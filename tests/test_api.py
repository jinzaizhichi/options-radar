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
