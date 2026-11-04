## What happens if a cancellation arrives before the completion event?

The API is built to implement tombstoning to handle out-of-order event delivery gracefully:
When cancellation arrives first, if `ride_id` is not yet in the database, the API creates a tombstone record for that ride with `is_cancelled = 1` and notes the cancellation reason. It returns a 200 confirming the ride has been tombstoned.
When completion request arrives later, it checks the database and sees that the `ride_id` is already marked as cancelled. It saves the ride metadata for auditing but awards 0 credits and skips creating any credit lots or ledger entries.
The driver's balance remains unaffected, and no spurious credits or debits are ever recorded.

## How do you store credit amounts so rounding errors never appear?

A two-layer approach is used here. In the database layer, we use Decimal type to store the credit amounts. PostgreSQL stores the Numeric type as exact base-10 digits, guaranteeing zero precision loss on arithmetic operations.
In the application layer, we use Decimal type to store the credit amounts. Python's in-built decimal.Decimal module is utilized to ensure that all floating point arithmetic operations are performed with exact decimal representations. This prevents any rounding errors from occurring during credit calculations.

## What happens if your service crashes halfway through a request?

The API achieves atomicity in a way by ensuring that the database operation either completes successfully, or the changes are rolled back using db.commit(). Python's db_commit() function holds the intended changes in a staging until the operation completes successfully. If the operation fails, the changes are rolled back and the database is restored to its previous state This ensures that the database is always in a consistent state, even if the service crashes.
Also, if there is a crash before commit, postgresql detects the dropped connection and automatically issues a rollback. None of the partial writes are persisted. 
If the crash happened after commit, maybe due to a network timeout, the API will handle any possible retries gracefully since it is idempotent; checking ride_id for uniqueness on each request. The retried request will fetch the existing committed record without re-awarding credits.

## The Three Biggest Trade-offs Made

1. **Credit Lots (FIFO Tracking) vs. Single Balance Field**: 
   I chose to track credits via individual lots (credit_lots) with remaining balances rather than a single `balance` number on the driver. This introduces slightly more write operations during redemption, but is the only clean way to enforce 12-month expiration per ride and FIFO redemption.
2. **Pessimistic Row-Level Locking vs. Optimistic Locking**: 
   I chose pessimistic locks (`SELECT ... FOR UPDATE`) on the driver record during redemptions. While this briefly serializes concurrent redemptions for the same driver, it guarantees with 100% mathematical certainty that concurrent requests never drop the balance below zero. Given the constraints, the balance never dropping below zero is a higher priority than the potential performance increase from optimistic locking.
3. **Synchronous ACID Processing vs. Asynchronous Queuing**I c 
   Chose synchronous HTTP transaction processing in PostgreSQL. This incurs slightly higher request latency compared to async message queuing, but provides immediate consistency and instant idempotency feedback to callers.

## Data Model & Constraints

- **`drivers`**: Concurrency lock root and driver identity (`id` PK).
Fields are: id only
- **`rides`**: Stores ride telemetry with a `UNIQUE(ride_id)` constraint for database-level idempotency and nullable fields to support pre-cancellation tombstoning
Fields are `id (primary key)`,`ride_id`,`driver_id`,`vehicle_type`,`distance_km`,`completed_at`,`is_cancelled`,`timestamps`
- **`credit_lots`**: Tracks per-ride spendable amounts and expiration dates with a composite index on `(driver_id, expires_at)` for fast balance calculation and FIFO ordering.
Fields are `id(primary key)`,`driver_id(foreign key)`,`ride_id(foreign key)`, `initial_amount`,`earned_at`, `expires_at`
- **`credit_ledger`**: Immutable audit log of all transactions (`EARN`, `REDEEM`, `REVERSAL`) with timestamps and running balances. Fields are `id(primary key)`, `driver_id(foreign key)`, `transaction_type`, `amount`, `balance_after`, `reasons`, `timestamps` 

**Alternative Design Rejected**: 
A pure event-sourced ledger with no lot tracking. Calculating 12-month expiration and FIFO usage from a raw stream of unindexed ledger entries requires re-aggregating every past ride on every balance check.

## What I Would Do With Two More Days

1. **Background Expiration Job**: I will add a scheduled background worker to formally transition fully expired lots and write explicit `EXPIRED` audit entries to the ledger.
2. **Asynchronous Message Ingestion**: Buffer ride completion events through an event queue (e.g. RabbitMQ / Redis Streams) to smoothly absorb peak rush-hour traffic.
3. **Authentication & Rate Limiting**: I will secure endpoints with API key/JWT middleware and add Redis-backed rate limiting to defend against aggressive client retries.
4. **Model Buff up**: I will add more columns to the existing tables. A simple example is the driver table. It needs driver details and a separate table for vehicle with a one-to-one relationship between them. 

## One Thing That Did Not Work (and how it was solved)

When consolidating migration files, running `alembic upgrade head` failed with `psycopg.errors.DuplicateObject: type "transactiontype" already exists`. Because PostgreSQL manages `ENUM` types as standalone objects separate from tables, deleting table migrations left the enum type behind. We solved this by executing a clean schema reset (`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`) before reapplying the migration, ensuring clean reproducibility.