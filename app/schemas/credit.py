from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field
from app.models.credit import TransactionType


# --- Ride Schemas ---
class RideCompletedRequest(BaseModel):
    ride_id: str = Field(..., description="Unique ride ID")
    driver_id: str = Field(..., description="Unique driver ID")
    vehicle_type: str = Field(..., description="Vehicle type, e.g. EV, electric, gas")
    distance_km: Decimal = Field(..., gt=0, description="Distance traveled in km")
    completed_at: datetime = Field(..., description="Ride completion timestamp")


class RideCompletedResponse(BaseModel):
    ride_id: str
    driver_id: str
    credits_earned: Decimal
    is_duplicate: bool
    message: str


class RideCancelledRequest(BaseModel):
    ride_id: str = Field(..., description="Ride ID to cancel/reverse")
    reason: Optional[str] = Field("Ride cancelled/refunded", description="Reason for cancellation")


class RideCancelledResponse(BaseModel):
    ride_id: str
    driver_id: str
    reversed_credits: Decimal
    new_balance: Decimal
    message: str


# --- Driver Schemas ---
class DriverBalanceResponse(BaseModel):
    driver_id: str
    unexpired_balance: Decimal
    total_earned: Decimal
    total_redeemed: Decimal
    total_reversed: Decimal
    expired_credits: Decimal


class RedeemRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Amount of credits to redeem")
    reason: Optional[str] = Field("Credits redeemed by driver", description="Redemption reason or vendor/reward")


class RedeemResponse(BaseModel):
    driver_id: str
    redeemed_amount: Decimal
    remaining_balance: Decimal
    message: str


class LedgerEntryResponse(BaseModel):
    id: int
    driver_id: str
    amount: Decimal
    balance_after: Decimal
    transaction_type: TransactionType
    reason: str
    reference_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DriverHistoryResponse(BaseModel):
    driver_id: str
    history: List[LedgerEntryResponse]
