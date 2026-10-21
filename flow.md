# How the code works: file to file

## 1. Startup: `run.py`

`python3 run.py` starts `stock_rescue/inventory.py` in one Python process, waits
for its health endpoint, then starts `stock_rescue/coordinator.py` in another.
It keeps both running until Ctrl+C. The separate entry points can also be run
independently on different machines or inside containers.

## 2. Stock setup: `inventory.py` → `database.py`

`inventory.py` reads its port and database path from environment variables and
creates `StockDB`. `database.py` creates two SQLite tables: `lots` and
`reservations`. If there are no lots, it seeds 40 yogurts expiring in 18 hours
and 25 breads expiring in 5 hours. Existing stock survives a service restart.
The service uses `ThreadingHTTPServer` to accept concurrent HTTP requests.

## 3. Browser: `coordinator.py` → `dashboard.html`

Opening port 8000 sends `GET /` to `coordinator.py`, which serves
`dashboard.html`. The dashboard's small JavaScript section calls `/api/state`.
The coordinator fetches `/stock` from inventory using `common.py:call()` and
adds store information from `simulation.py`. The browser draws stock and
reservations from this response. Python implements all allocation rules;
JavaScript only sends actions and updates the page.

## 4. Allocation: dashboard → `coordinator.py` → `simulation.py`

The overlapping-request button submits one `/api/allocate` batch containing
Store A's request for 30 yogurts and Store B's request for 25.
`coordinator.py:allocate()` validates the whole batch, reads stock over HTTP,
and obtains each store's simulated demand and journey duration from
`simulation.py`. It calculates priority from demand, expiry urgency, and travel
time, then processes requests by descending score. For the initial yogurt lot,
Store A scores higher because of greater demand and shorter travel.

## 5. Reservation: `coordinator.py` → `common.py` → `inventory.py` → `database.py`

For each request, the coordinator reads current stock, checks travel feasibility,
and chooses `min(requested, available)`. `common.py:call()` sends an HTTP POST
to the stock service's `/reservations` route.

`inventory.py` passes the request to `database.py:reserve()`. This function:

1. Opens a database connection and takes a write transaction with `BEGIN IMMEDIATE`.
2. Marks overdue reservations expired.
3. Checks the lot exists and enough time remains for travel and unloading.
4. Calculates available units: listed quantity minus reserved and collected units.
5. Rejects insufficient stock with HTTP 409, or inserts a reservation with a
   unique ID and collection deadline, then commits.

The availability check and insert happen inside the same transaction. If two
coordinators read 40 units simultaneously, their inserts cannot both consume
those same units: the second transaction sees the first committed reservation.
The coordinator handles HTTP 409 by reading current availability and trying again.

For the initial batch, A reserves 30, then B sees 10 left and reserves 10.
The allocation response returns quantities, scores, reasons and reservation
details. The dashboard displays the result and refreshes its tables.

## 6. Collection and expiry: dashboard → coordinator → inventory → database

Clicking Collect sends `/api/collect/{id}` to the coordinator, which forwards it
to inventory's `/collect/{id}` endpoint. `database.py:collect()` rejects overdue
reservations and marks a valid reservation collected. Repeating collection
returns collected again without consuming more stock.

On the next stock read or reservation operation, overdue uncollected holds become
expired and stop counting against availability. Expiry uses UTC timestamps;
the dashboard displays them in the viewer's local time. There is no expiry worker.
Expired product lots show zero availability even if units remain physically present.

## 7. Concurrency demo: `demo.py`

`demo.py` uses two threads to send competing allocations over HTTP. If
`COORDINATOR_URLS` lists two allocation replicas, one request goes to each.
Both replicas talk to the same stock service. The demo prints the allocations
and checks that total reserved plus collected yogurt never exceeds the lot size.
Unlike the dashboard batch, these independent requests have no shared ranking
step; arrival order determines who gets stock first.

## 8. Verification: `tests/test_system.py`

Each test starts a real stock HTTP server and two allocation HTTP servers using
an isolated temporary SQLite database. The tests cover batch priority, partial
allocation, concurrent requests across replicas, deadline release, repeat
collection, infeasible travel, invalid input, persistence, expired lots, and
direct stock over-allocation rejection. No running local demo is required.

## Supporting files

- `common.py`: JSON HTTP client/server helpers, input validation, UTC clock,
  and structured API errors. Network failures return HTTP 503 to the caller.
- `simulation.py`: the three receiving stores, demand values, and travel minutes.
- `__init__.py`: marks `stock_rescue` as a Python package.
- `.gitignore`: excludes generated databases, Python cache, and virtual environments.
- `README.md`: setup, demo, decision rules, limitations, and container handoff.
