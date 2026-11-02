import pytest
from datetime import datetime, timezone
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

# In-memory SQLite for comprehensive unit & integration testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_ride_completed_earns_credits():
    """Requirement 1: Completed EV ride calculates and records credits."""
    payload = {
        "ride_id": "ride-101",
        "driver_id": "driver-A",
        "vehicle_type": "electric",
        "distance_km": 50.0,
        "completed_at": "2026-05-10T12:00:00Z",
    }
    response = client.post("/rides/completed", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["ride_id"] == "ride-101"
    assert float(data["credits_earned"]) == 6.0
    assert data["is_duplicate"] is False

    bal_res = client.get("/drivers/driver-A/balance")
    assert bal_res.status_code == 200
    assert float(bal_res.json()["unexpired_balance"]) == 6.0


def test_ride_completed_idempotency():
    """Requirement 2: Duplicate ride event must never add credits twice."""
    payload = {
        "ride_id": "ride-dup-1",
        "driver_id": "driver-B",
        "vehicle_type": "EV",
        "distance_km": 100.0,
        "completed_at": "2026-06-01T10:00:00Z",
    }
    res1 = client.post("/rides/completed", json=payload)
    assert res1.status_code == 201
    assert float(res1.json()["credits_earned"]) == 12.0
    assert res1.json()["is_duplicate"] is False

    res2 = client.post("/rides/completed", json=payload)
    assert res2.status_code == 201
    assert float(res2.json()["credits_earned"]) == 12.0
    assert res2.json()["is_duplicate"] is True

    bal_res = client.get("/drivers/driver-B/balance")
    assert float(bal_res.json()["unexpired_balance"]) == 12.0

    hist_res = client.get("/drivers/driver-B/history")
    assert len(hist_res.json()["history"]) == 1


def test_driver_history_audit():
    """Requirement 3: GET /drivers/{id}/history returns every credit and debit with reason."""
    client.post("/rides/completed", json={
        "ride_id": "ride-h1",
        "driver_id": "driver-C",
        "vehicle_type": "electric",
        "distance_km": 10.0,
        "completed_at": "2026-06-01T10:00:00Z",
    })
    client.post("/drivers/driver-C/redeem", json={
        "amount": 0.5,
        "reason": "Coffee voucher",
    })

    hist_res = client.get("/drivers/driver-C/history")
    history = hist_res.json()["history"]
    assert len(history) == 2
    assert history[0]["transaction_type"] == "EARN"
    assert "ride-h1" in history[0]["reason"]
    assert history[1]["transaction_type"] == "REDEEM"
    assert history[1]["reason"] == "Coffee voucher"
    assert float(history[1]["amount"]) == -0.5


def test_redeem_cannot_exceed_balance():
    """Requirement 4: Redeem requests must never take balance below zero."""
    client.post("/rides/completed", json={
        "ride_id": "ride-r1",
        "driver_id": "driver-D",
        "vehicle_type": "EV",
        "distance_km": 20.0,
        "completed_at": "2026-06-01T10:00:00Z",
    }) 

    res = client.post("/drivers/driver-D/redeem", json={"amount": 5.0})
    assert res.status_code == 400
    assert "Insufficient balance" in res.json()["detail"]

    bal = client.get("/drivers/driver-D/balance").json()
    assert float(bal["unexpired_balance"]) == 2.4


def test_cancelled_ride_reversal():
    """Requirement 5: Reverse credits for cancelled ride; history shows both original and reversal."""
    client.post("/rides/completed", json={
        "ride_id": "ride-cancel-1",
        "driver_id": "driver-E",
        "vehicle_type": "electric",
        "distance_km": 50.0,
        "completed_at": "2026-07-01T10:00:00Z",
    }) 

    cancel_res = client.post("/rides/cancelled", json={
        "ride_id": "ride-cancel-1",
        "reason": "Customer passenger no-show refund",
    })
    assert cancel_res.status_code == 200
    assert float(cancel_res.json()["reversed_credits"]) == 6.0
    assert float(cancel_res.json()["new_balance"]) == 0.0

    hist = client.get("/drivers/driver-E/history").json()["history"]
    assert len(hist) == 2
    assert hist[0]["transaction_type"] == "EARN"
    assert hist[1]["transaction_type"] == "REVERSAL"
    assert "refund" in hist[1]["reason"].lower()


def test_expired_credits_not_in_balance_and_fifo_redemption():
    """Requirement 6: Credits expire 12 months after completed_at. Oldest credits used first."""
    client.post("/rides/completed", json={
        "ride_id": "ride-exp",
        "driver_id": "driver-F",
        "vehicle_type": "electric",
        "distance_km": 100.0,
        "completed_at": "2025-01-01T00:00:00Z",
    }) 

    client.post("/rides/completed", json={
        "ride_id": "ride-valid-old",
        "driver_id": "driver-F",
        "vehicle_type": "electric",
        "distance_km": 50.0,
        "completed_at": "2026-05-01T00:00:00Z",
    }) 

    client.post("/rides/completed", json={
        "ride_id": "ride-valid-new",
        "driver_id": "driver-F",
        "vehicle_type": "electric",
        "distance_km": 30.0,
        "completed_at": "2026-11-01T00:00:00Z",
    })  

    bal = client.get("/drivers/driver-F/balance").json()
    assert float(bal["unexpired_balance"]) == 9.6
    assert float(bal["expired_credits"]) == 12.0

    redeem_res = client.post("/drivers/driver-F/redeem", json={"amount": 7.0})
    assert redeem_res.status_code == 200
    assert float(redeem_res.json()["remaining_balance"]) == 2.6

