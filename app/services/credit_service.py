import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException, status

from app.models.credit import Driver, Ride, CreditLot, CreditLedger, TransactionType
from app.schemas.credit import (
    RideCompletedRequest,
    RideCompletedResponse,
    RideCancelledRequest,
    RideCancelledResponse,
    DriverBalanceResponse,
    RedeemRequest,
    RedeemResponse,
    LedgerEntryResponse,
    DriverHistoryResponse,
)

ELECTRIC_VEHICLE_TYPES = {"electric", "ev", "bev", "phev"}

# Assumptions for carbon calculation:
# Baseline internal combustion engine emits ~0.120 kg CO2 / km (120g/km).
# 1 Carbon Credit = 1 Metric Tonne (1,000 kg) CO2 avoided.
# Factor = 0.120 / 1000 = 0.00012 credits per km.
# For demo friendliness and high precision, we award 0.12 credits per km (or 120 credits per 1,000 km).
# Specifically: credits = distance_km * 0.12
CREDITS_PER_KM = Decimal("0.12")


def calculate_credits(vehicle_type: str, distance_km: Decimal) -> Decimal:
    """
    Calculates carbon credits for a ride.
    Formula: distance_km * 0.12 credits/km for electric vehicles, 0 for others.
    """
    if vehicle_type.strip().lower() not in ELECTRIC_VEHICLE_TYPES:
        return Decimal("0.0000")
    
    amount = distance_km * CREDITS_PER_KM
    return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def get_or_create_driver(db: Session, driver_id: str, for_update: bool = False) -> Driver:
    """
    Retrieves or initializes a Driver row.
    If for_update=True, acquires an exclusive row-level lock on the driver.
    """
    query = db.query(Driver).filter(Driver.id == driver_id)
    if for_update:
        query = query.with_for_update()
    
    driver = query.first()
    if not driver:
        driver = Driver(id=driver_id)
        db.add(driver)
        db.flush()
        if for_update:
            # Re-fetch with lock once created
            driver = db.query(Driver).filter(Driver.id == driver_id).with_for_update().first()
    return driver


def compute_driver_unexpired_balance(db: Session, driver_id: str, as_of: datetime = None) -> Decimal:
    """
    Computes total unexpired, unredeemed credits currently available for a driver.
    Only lots with expires_at > as_of and remaining_amount > 0 are included.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)

    result = (
        db.query(func.coalesce(func.sum(CreditLot.remaining_amount), Decimal("0.0000")))
        .filter(
            CreditLot.driver_id == driver_id,
            CreditLot.expires_at > as_of,
            CreditLot.remaining_amount > 0,
        )
        .scalar()
    )
    return Decimal(str(result)).quantize(Decimal("0.0001"))


class CreditService:
    @staticmethod
    def process_completed_ride(db: Session, data: RideCompletedRequest) -> RideCompletedResponse:
        # Check idempotency or pre-cancellation tombstone
        existing_ride = db.query(Ride).filter(Ride.ride_id == data.ride_id).first()
        if existing_ride:
            # If the ride was tombstoned (cancelled before completion arrived):
            if existing_ride.is_cancelled:
                # Update ride details for audit record, but award ZERO credits
                existing_ride.driver_id = data.driver_id
                existing_ride.vehicle_type = data.vehicle_type
                existing_ride.distance_km = data.distance_km
                existing_ride.completed_at = data.completed_at
                db.commit()
                return RideCompletedResponse(
                    ride_id=data.ride_id,
                    driver_id=data.driver_id,
                    credits_earned=Decimal("0.0000"),
                    is_duplicate=True,
                    message="Ride was previously cancelled (tombstoned). Zero credits awarded.",
                )

            # Normal duplicate ride: return existing calculation
            lot = db.query(CreditLot).filter(CreditLot.ride_id == data.ride_id).first()
            earned = lot.initial_amount if lot else Decimal("0.0000")
            return RideCompletedResponse(
                ride_id=data.ride_id,
                driver_id=data.driver_id,
                credits_earned=earned,
                is_duplicate=True,
                message="Ride event has already been processed.",
            )

        completed_at = data.completed_at
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        get_or_create_driver(db, data.driver_id, for_update=True)

        credits_earned = calculate_credits(data.vehicle_type, data.distance_km)

        ride = Ride(
            ride_id=data.ride_id,
            driver_id=data.driver_id,
            vehicle_type=data.vehicle_type,
            distance_km=data.distance_km,
            completed_at=completed_at,
        )
        db.add(ride)

        if credits_earned > Decimal("0.0000"):
            expires_at = completed_at + relativedelta(months=12)

            lot = CreditLot(
                driver_id=data.driver_id,
                ride_id=data.ride_id,
                initial_amount=credits_earned,
                remaining_amount=credits_earned,
                earned_at=completed_at,
                expires_at=expires_at,
            )
            db.add(lot)
            db.flush()

            current_balance = compute_driver_unexpired_balance(db, data.driver_id)

            ledger_entry = CreditLedger(
                driver_id=data.driver_id,
                amount=credits_earned,
                balance_after=current_balance,
                transaction_type=TransactionType.EARN,
                reason=f"Earned from ride {data.ride_id} ({data.vehicle_type}, {data.distance_km} km)",
                reference_id=data.ride_id,
            )
            db.add(ledger_entry)

        db.commit()

        return RideCompletedResponse(
            ride_id=data.ride_id,
            driver_id=data.driver_id,
            credits_earned=credits_earned,
            is_duplicate=False,
            message="Credits successfully recorded." if credits_earned > 0 else "Non-EV ride; no credits awarded.",
        )

    @staticmethod
    def get_driver_balance(db: Session, driver_id: str) -> DriverBalanceResponse:
        now = datetime.now(timezone.utc)
        unexpired_balance = compute_driver_unexpired_balance(db, driver_id, as_of=now)
        earned_sum = (
            db.query(func.coalesce(func.sum(CreditLedger.amount), Decimal("0.0000")))
            .filter(CreditLedger.driver_id == driver_id, CreditLedger.transaction_type == TransactionType.EARN)
            .scalar()
        )
        redeemed_sum = (
            db.query(func.coalesce(func.sum(CreditLedger.amount), Decimal("0.0000")))
            .filter(CreditLedger.driver_id == driver_id, CreditLedger.transaction_type == TransactionType.REDEEM)
            .scalar()
        )
        reversed_sum = (
            db.query(func.coalesce(func.sum(CreditLedger.amount), Decimal("0.0000")))
            .filter(CreditLedger.driver_id == driver_id, CreditLedger.transaction_type == TransactionType.REVERSAL)
            .scalar()
        )
        expired_sum = (
            db.query(func.coalesce(func.sum(CreditLot.remaining_amount), Decimal("0.0000")))
            .filter(CreditLot.driver_id == driver_id, CreditLot.expires_at <= now)
            .scalar()
        )

        return DriverBalanceResponse(
            driver_id=driver_id,
            unexpired_balance=unexpired_balance,
            total_earned=Decimal(str(earned_sum)),
            total_redeemed=abs(Decimal(str(redeemed_sum))),
            total_reversed=abs(Decimal(str(reversed_sum))),
            expired_credits=Decimal(str(expired_sum)),
        )

    @staticmethod
    def get_driver_history(db: Session, driver_id: str) -> DriverHistoryResponse:
        entries = (
            db.query(CreditLedger)
            .filter(CreditLedger.driver_id == driver_id)
            .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
            .all()
        )
        return DriverHistoryResponse(
            driver_id=driver_id,
            history=[LedgerEntryResponse.model_validate(entry) for entry in entries],
        )

    @staticmethod
    def redeem_credits(db: Session, driver_id: str, data: RedeemRequest) -> RedeemResponse:
        now = datetime.now(timezone.utc)
        redeem_amount = data.amount.quantize(Decimal("0.0001"))

        if redeem_amount <= Decimal("0.0000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Redeem amount must be greater than zero.",
            )

        get_or_create_driver(db, driver_id, for_update=True)

        current_balance = compute_driver_unexpired_balance(db, driver_id, as_of=now)
        if current_balance < redeem_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient balance. Current unexpired balance is {current_balance}, attempted to redeem {redeem_amount}.",
            )

        available_lots: List[CreditLot] = (
            db.query(CreditLot)
            .filter(
                CreditLot.driver_id == driver_id,
                CreditLot.expires_at > now,
                CreditLot.remaining_amount > 0,
            )
            .order_by(CreditLot.earned_at.asc(), CreditLot.id.asc())
            .with_for_update()
            .all()
        )

        amount_to_deduct = redeem_amount
        for lot in available_lots:
            if amount_to_deduct <= Decimal("0.0000"):
                break

            deduct = min(lot.remaining_amount, amount_to_deduct)
            lot.remaining_amount -= deduct
            amount_to_deduct -= deduct

        if amount_to_deduct > Decimal("0.0000"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to allocate sufficient unexpired credits.",
            )

        new_balance = current_balance - redeem_amount
        redemption_id = f"red_{uuid.uuid4().hex[:12]}"

        ledger_entry = CreditLedger(
            driver_id=driver_id,
            amount=-redeem_amount,
            balance_after=new_balance,
            transaction_type=TransactionType.REDEEM,
            reason=data.reason or "Credits redeemed",
            reference_id=redemption_id,
        )
        db.add(ledger_entry)
        db.commit()

        return RedeemResponse(
            driver_id=driver_id,
            redeemed_amount=redeem_amount,
            remaining_balance=new_balance,
            message="Redemption successful.",
        )

    @staticmethod
    def cancel_ride(db: Session, data: RideCancelledRequest) -> RideCancelledResponse:
        """
        Requirement 5: Reverses the credits for a ride that was refunded or cancelled after completion.
        The history must still show the original entry and include a reversal.
        """
        ride = db.query(Ride).filter(Ride.ride_id == data.ride_id).first()
        if not ride:
            # Tombstoning: cancellation arrived before completion event.
            # Record a tombstone so when completion arrives later, zero credits will be awarded.
            tombstone_ride = Ride(
                ride_id=data.ride_id,
                is_cancelled=1,
                cancellation_reason=data.reason,
            )
            db.add(tombstone_ride)
            db.commit()
            return RideCancelledResponse(
                ride_id=data.ride_id,
                driver_id=None,
                reversed_credits=Decimal("0.0000"),
                new_balance=Decimal("0.0000"),
                message="Cancellation received before completion. Ride has been tombstoned to prevent future credit awards.",
            )

        if ride.is_cancelled:
            # Already cancelled, return idempotent status
            current_balance = compute_driver_unexpired_balance(db, ride.driver_id) if ride.driver_id else Decimal("0.0000")
            return RideCancelledResponse(
                ride_id=ride.ride_id,
                driver_id=ride.driver_id,
                reversed_credits=Decimal("0.0000"),
                new_balance=current_balance,
                message="Ride credits have already been cancelled.",
            )

        get_or_create_driver(db, ride.driver_id, for_update=True)

        lot = db.query(CreditLot).filter(CreditLot.ride_id == data.ride_id).first()
        if not lot or lot.initial_amount <= Decimal("0.0000"):
            ride.is_cancelled = 1
            db.commit()
            current_balance = compute_driver_unexpired_balance(db, ride.driver_id)
            return RideCancelledResponse(
                ride_id=ride.ride_id,
                driver_id=ride.driver_id,
                reversed_credits=Decimal("0.0000"),
                new_balance=current_balance,
                message="Ride had no earned credits to reverse.",
            )

        reversed_amount = lot.initial_amount
        lot.remaining_amount = Decimal("0.0000")
        ride.is_cancelled = 1
        db.flush()

        new_balance = compute_driver_unexpired_balance(db, ride.driver_id)

        ledger_entry = CreditLedger(
            driver_id=ride.driver_id,
            amount=-reversed_amount,
            balance_after=new_balance,
            transaction_type=TransactionType.REVERSAL,
            reason=f"Reversal for ride {data.ride_id}: {data.reason}",
            reference_id=data.ride_id,
        )
        db.add(ledger_entry)
        db.commit()

        return RideCancelledResponse(
            ride_id=ride.ride_id,
            driver_id=ride.driver_id,
            reversed_credits=reversed_amount,
            new_balance=new_balance,
            message="Ride credits successfully reversed.",
        )
