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