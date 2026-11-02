from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.credit_service import CreditService
from app.schemas.credit import (
    RideCompletedRequest,
    RideCompletedResponse,
    RideCancelledRequest,
    RideCancelledResponse,
)

router = APIRouter(prefix="/rides", tags=["Rides"])


@router.post(
    "/completed",
    response_model=RideCompletedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a completed ride and award carbon credits",
)
def ride_completed(
    payload: RideCompletedRequest,
    db: Session = Depends(get_db),
):
    """
    Receives ride completion event.
    - Idempotent: Duplicate ride_id events will not award duplicate credits.
    - Formula: EV distance (km) * 0.12 = Carbon Credits.
    - Credits expire 12 months from completed_at.
    """
    return CreditService.process_completed_ride(db, payload)


@router.post(
    "/cancelled",
    response_model=RideCancelledResponse,
    summary="Reverse credits for a refunded or cancelled ride",
)
def ride_cancelled(
    payload: RideCancelledRequest,
    db: Session = Depends(get_db),
):
    """
    Reverses the credits for a ride that was refunded or cancelled after completion.
    The original earn entry remains in history; a reversal debit is added to the ledger.
    """
    return CreditService.cancel_ride(db, payload)

