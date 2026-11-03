from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.credit import Driver
from app.services.credit_service import CreditService, get_or_create_driver
from app.schemas.credit import (
    DriverCreateRequest,
    DriverResponse,
    DriverBalanceResponse,
    DriverHistoryResponse,
    RedeemRequest,
    RedeemResponse,
)

router = APIRouter(prefix="/drivers", tags=["Drivers"])


@router.post(
    "/",
    response_model=DriverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or register a driver",
)
def create_driver(
    payload: DriverCreateRequest,
    db: Session = Depends(get_db),
):
    """
    Registers a new driver. Idempotent: returns existing driver if already registered.
    """
    existing = db.query(Driver).filter(Driver.id == payload.driver_id).first()
    if existing:
        return DriverResponse(
            id=existing.id,
            created_at=existing.created_at,
            message="Driver already registered.",
        )
    driver = get_or_create_driver(db, payload.driver_id)
    db.commit()
    db.refresh(driver)
    return DriverResponse(
        id=driver.id,
        created_at=driver.created_at,
        message="Driver created successfully.",
    )


@router.get(
    "/{driver_id}/balance",
    response_model=DriverBalanceResponse,
    summary="Get unexpired balance and balance metrics for a driver",
)
def get_driver_balance(
    driver_id: str,
    db: Session = Depends(get_db),
):
    """
    Returns unexpired, active credits only. Expired credits (>12 months) are excluded from spendable balance.
    """
    return CreditService.get_driver_balance(db, driver_id)


@router.get(
    "/{driver_id}/history",
    response_model=DriverHistoryResponse,
    summary="Get credit and debit history with reasons for a driver",
)
def get_driver_history(
    driver_id: str,
    db: Session = Depends(get_db),
):
    """
    Returns chronological ledger of every credit earn, debit (redemption), and reversal.
    """
    return CreditService.get_driver_history(db, driver_id)


@router.post(
    "/{driver_id}/redeem",
    response_model=RedeemResponse,
    status_code=status.HTTP_200_OK,
    summary="Redeem driver credits (FIFO, concurrency safe)",
)
def redeem_credits(
    driver_id: str,
    payload: RedeemRequest,
    db: Session = Depends(get_db),
):
    """
    Redeems credits for a driver using FIFO order on oldest unexpired lots.
    Row locks protect against simultaneous requests bringing balance below 0.
    """
    return CreditService.redeem_credits(db, driver_id, payload)

