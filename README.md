# Perishable Stock Rescue

A small Distributed Systems mini project written in Python. A supermarket has
40 yogurts nearing expiry. Nearby stores compete for them; the system ranks
requests and reserves stock without over-allocation. All stock, demand, and
travel data are simulated.

## Run locally

Requires Python 3.10 or newer. Uses the standard library; no pip install needed.
From this project folder:

```bash
python3 run.py
```

Open **http://localhost:8000**. Click the overlapping-requests button on a fresh
database: Store A receives 30 yogurts and Store B receives the remaining 10.
Use **Collect** to mark a transfer collected. Click **Refresh** to see current
stock and reservation status. Ctrl+C stops both services.

Stock persists in `data/stock.db`. To restart the simulation with fresh stock and
expiry times, stop the services, delete `data/stock.db`, and start again.

## Concurrent demo and tests

With the services running, open another terminal in this folder:

```bash
python3 demo.py
```

This sends requests for 30 and 25 yogurts concurrently. On fresh stock, their
combined allocation is 40. Which request arrives first can change the split.
Requests reserve real simulation stock, so reset between independent demos.

Tests start their own services on temporary ports with temporary databases:

```bash
python3 -m unittest discover -s tests -v
```

## Services and distributed-systems concepts

| Component | Responsibility | Default port |
| --- | --- | --- |
| Allocation service | Browser dashboard, request ranking, calls stock API | 8000 |
| Stock service | Owns stock, reservations, collection and SQLite transactions | 8001 |

The services run in separate processes and communicate using HTTP/JSON. The
allocation service has no local reservation state and can run multiple replicas.
The stock service is the single authority: it checks availability and writes each
reservation in one `BEGIN IMMEDIATE` SQLite transaction. Concurrent requests are
serialized there, preventing two callers from reserving the same units. A
coordinator receiving a stock conflict re-reads stock and retries up to five times.

To demonstrate a second allocation replica, keep the normal services running and
start this in another terminal:

```bash
PORT=8002 python3 -m stock_rescue.coordinator
```

Then send competing requests through both replicas:

```bash
COORDINATOR_URLS=http://localhost:8000,http://localhost:8002 python3 demo.py
```

This demonstrates scaling the allocation tier. The stock service remains a single
instance and a single point of failure. SQLite is suitable for this mini project;
multi-host database replication, automatic failover, authentication and real
retailer integrations are outside its scope.

## Decision rules

1. Reject transfers that cannot arrive before expiry, allowing 10 minutes to unload.
2. Rank each batch by `50 × demand + 30 × urgency + 20 × travel_score`.
   Demand is simulated from 0 to 1; `urgency = max(0, 1 - hours_left / 24)`;
   `travel_score = max(0, 1 - travel_minutes / 120)`. Ties keep input order.
3. Reserve the smaller of requested and available quantity. A result explains
   whether it was full, partial, or unavailable.
4. Collection deadline is the earlier of 30 minutes from reservation and
   `expiry - travel time - 10 minutes`.
5. Uncollected reservations expire and release stock. Cleanup happens on the next
   stock read or write, so no background worker is needed. Collected stock stays
   consumed. An expired product is never offered for allocation.

Ranking is per submitted batch, not a global queue. Different simultaneous
batches compete in arrival order at the stock service. A batch commits each
reservation separately, so it is not an all-or-nothing transaction. If a network
timeout occurs after a reservation is committed, inspect the dashboard before
retrying: request deduplication is not implemented.

## Handoff for containerization

No Docker or Kubernetes files are included. These are the two entry points your
friend can containerize using the same project source:

```bash
python3 -m stock_rescue.inventory
python3 -m stock_rescue.coordinator
```

| Service | Environment variable | Default |
| --- | --- | --- |
| Both | `HOST` | `0.0.0.0` |
| Stock | `STOCK_PORT` | `8001` |
| Stock | `STOCK_DB` | `data/stock.db` |
| Allocation | `PORT` | `8000` |
| Allocation | `STOCK_URL` | `http://127.0.0.1:8001` |

In separate containers, set `STOCK_URL` to the stock container's service address
(for example, `http://stock:8001`). Mount persistent storage at the stock database
location. Run one stock instance; scale allocation replicas behind a load balancer.
`GET /health` is available on each service; allocation health also checks stock.
The local `run.py` convenience launcher expects the default port configuration.

## API overview

| Service | Method and path | Purpose |
| --- | --- | --- |
| Allocation | `GET /api/state` | Stock, reservations, simulated stores |
| Allocation | `POST /api/allocate` | Rank and allocate a batch |
| Allocation | `POST /api/collect/{id}` | Collect reservation through stock API |
| Stock | `GET /stock` | Authoritative stock and reservations |
| Stock | `POST /reservations` | Atomically reserve stock |
| Stock | `POST /collect/{id}` | Mark a reservation collected |

Example allocation request body:

```json
{"requests": [
  {"store_id": "store-a", "lot_id": "yogurt-01", "quantity": 30},
  {"store_id": "store-b", "lot_id": "yogurt-01", "quantity": 25}
]}
```

See `flow.md` for the file-by-file explanation.
