from enum import Enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    ForeignKey,
    Index,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class TransactionType(str, Enum):
    EARN = "EARN"
    REDEEM = "REDEEM"
    REVERSAL = "REVERSAL"


class Ride(Base):
    __tablename__ = "rides"

    id = Column(Integer, primary_key=True, index=True)
    ride_id = Column(String(100), unique=True, nullable=False, index=True)
    driver_id = Column(String(100), nullable=True, index=True)  # nullable for pre-cancellation tombstone
    vehicle_type = Column(String(50), nullable=True)            # nullable until completion details arrive
    distance_km = Column(Numeric(10, 4), nullable=True)         # nullable until completion details arrive
    completed_at = Column(DateTime(timezone=True), nullable=True) # nullable until completion details arrive
    is_cancelled = Column(Integer, default=0, nullable=False)  # 0: active, 1: cancelled
    cancellation_reason = Column(String(255), nullable=True)   # reason if cancelled
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    credit_lots = relationship("CreditLot", back_populates="ride")


class Driver(Base):
    """
    Driver record used to synchronize and serialize concurrent balance mutations
    (such as simultaneous redemptions or cancellations) using SELECT ... FOR UPDATE.
    """
    __tablename__ = "drivers"

    id = Column(String(100), primary_key=True)  # driver_id
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    credit_lots = relationship("CreditLot", back_populates="driver")
    ledger_entries = relationship("CreditLedger", back_populates="driver")


class CreditLot(Base):
    """
    Tracks earned credits on a per-ride basis with expiration and remaining spendable balance.
    Enforces FIFO redemption order.
    """
    __tablename__ = "credit_lots"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(String(100), ForeignKey("drivers.id"), nullable=False, index=True)
    ride_id = Column(String(100), ForeignKey("rides.ride_id"), nullable=False, unique=True, index=True)
    initial_amount = Column(Numeric(12, 4), nullable=False)
    remaining_amount = Column(Numeric(12, 4), nullable=False)
    earned_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    driver = relationship("Driver", back_populates="credit_lots")
    ride = relationship("Ride", back_populates="credit_lots")

    __table_args__ = (
        Index("ix_credit_lots_driver_expires", "driver_id", "expires_at"),
    )


class CreditLedger(Base):
    """
    Audit log of all credit additions, redemptions, and reversals.
    """
    __tablename__ = "credit_ledger"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(String(100), ForeignKey("drivers.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 4), nullable=False)  # positive for earn, negative for debit/reversal
    balance_after = Column(Numeric(12, 4), nullable=False)
    transaction_type = Column(SQLEnum(TransactionType), nullable=False)
    reason = Column(String(255), nullable=False)
    reference_id = Column(String(100), nullable=True, index=True)  # ride_id or redemption id
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    driver = relationship("Driver", back_populates="ledger_entries")
