# Carbon Credits Service

A FastAPI & PostgreSQL service for tracking, recording, and redeeming carbon credits earned from completed electric vehicle (EV) rides.

---

## 1. Credit Calculation Formula & Assumptions

### Assumptions:
1. **Vehicle Eligibility**: Only electric vehicles (`electric`, `ev`, `bev`, `phev`) earn carbon credits. Internal combustion engine (ICE) or hybrid vehicles emit baseline carbon and earn `0` credits.
2. **Emissions Baseline**: Average passenger ICE vehicles emit roughly **120g CO₂ / km** (~0.120 kg CO₂ / km).
3. **Credit Unit**: 1 credit unit represents 1 kg of avoided CO₂ emissions (allowing meaningful granularity for everyday rides).
4. **Formula**:
   $$\text{Credits} = \text{distance\_km} \times 0.12$$
   *Example: A 50 km electric ride earns $50 \times 0.12 = 6.0000$ carbon credits.*

---

## 2. API Endpoints

### 1. `POST /rides/completed`
Receives completed ride events and records credits.
- **Idempotency**: Duplicate calls with the same `ride_id` return the original calculation with `"is_duplicate": true` and will never double-credit.
- **Payload**:
  ```json
  {
    "ride_id": "ride-101",
    "driver_id": "driver-A",
    "vehicle_type": "electric",
    "distance_km": 25.5,
    "completed_at": "2026-11-01T14:30:00Z"
  }
  ```

### 2. `GET /drivers/{id}/balance`
Returns the driver's current **unexpired** spendable balance along with aggregate metrics (total earned, redeemed, reversed, and expired).

### 3. `GET /drivers/{id}/history`
Returns the chronological audit trail of all transactions (`EARN`, `REDEEM`, `REVERSAL`), timestamp, balance after, and detailed reasons.

### 4. `POST /drivers/{id}/redeem`
Spends credits using **FIFO** (oldest unexpired credits first).
- **Concurrency Protection**: Uses row-level database locking (`SELECT ... FOR UPDATE`) on the driver record, guaranteeing that simultaneous requests will never overdraw or drop the balance below zero.
- **Payload**:
  ```json
  {
    "amount": 2.50,
    "reason": "Gift card voucher"
  }
  ```

### 5. `POST /rides/cancelled`
Reverses credits for rides refunded or cancelled after completion.
- Marks the credit lot invalid and writes a `REVERSAL` transaction to the audit ledger.
- The original `EARN` entry remains intact in history.
- **Payload**:
  ```json
  {
    "ride_id": "ride-101",
    "reason": "Passenger disputed ride"
  }
  ```

### 6. Expiration & FIFO Rule
- Credits expire **12 months** after the ride's `completed_at` timestamp.
- Expired credits are automatically excluded from balance calculation and cannot be spent.
- When redeeming, the oldest active credit lots are consumed first.

---

## 3. Local Development

### 1. Start Database
```powershell
docker compose up -d
```

### 2. Run Migrations
```powershell
alembic upgrade head
```

### 3. Start API
```powershell
fastapi dev app/main.py
```
Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 4. Run Test Suite
```powershell
pytest -v
```
