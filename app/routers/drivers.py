from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.credit_service import CreditService
from app.schemas.credit import (
    DriverBalanceResponse,
    DriverHistoryResponse,
    RedeemRequest,
    RedeemResponse,
)

router = APIRouter(prefix="/drivers", tags=["Drivers"])


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

