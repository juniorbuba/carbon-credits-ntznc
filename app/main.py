from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.config import settings
from app.core.database import get_db
from app.routers import rides, drivers

app = FastAPI(
    title=settings.APP_NAME,
    description="Carbon Credits Service for EV ride-hailing tracking, FIFO redemption, and ledger management.",
    version="1.0.0",
)

app.include_router(rides.router)
app.include_router(drivers.router)


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
