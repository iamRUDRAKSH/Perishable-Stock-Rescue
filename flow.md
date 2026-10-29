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

### Adding stock: dashboard → coordinator → inventory → database

The **Add stock** form takes a product name, quantity, and expiry in whole hours
from now. `dashboard.html` sends these as JSON to `POST /api/stock` in
`coordinator.py`. The coordinator forwards the request over HTTP using
`common.py:call()` to `POST /stock` in `inventory.py`.

`inventory.py` calls `database.py:add_stock()`. It validates the fields, generates
a unique lot ID, calculates the expiry timestamp, and inserts the lot inside a
database transaction. Each addition is a new lot, even for the same product name;
existing lots and reservations remain intact. The response returns the saved lot.
The supermarket stock table shows the full Lot ID beside each product, allowing
you to match separate batches to the lot IDs shown in reservations.
The dashboard refreshes both the stock table and transfer dropdown, preserving
the previous transfer selections. Both the single-transfer and overlapping-request
dropdowns show each lot's full ID, available quantity and expiry to distinguish
batches of the same product. Errors appear beside the add-stock form.

## 4. Allocation: dashboard → `coordinator.py` → `simulation.py`

The **Run overlapping requests** form lets the user select one lot, two different
stores, and their requested quantities. `dashboard.html` validates that the stores
are different and sends two independent `/api/allocate` calls using
`Promise.allSettled()`. Both calls start before either is awaited. Each contains
one store's request; both outcomes are displayed even if one request fails.
After completion the stock and reservation tables refresh. Newly added lots are
available in this form too, so the demo can continue after yogurt runs out.

Concurrent requests compete in arrival order at inventory. There is no shared
ranking between them. For example, requests for 30 and 25 units from a lot of 40
may receive 30/10 or 15/25, depending on which reserves first.

For API clients, `/api/allocate` also supports multiple requests in a single batch.
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

For an API batch requesting 30 and 25 yogurts from the initial lot, A reserves 30,
then B sees 10 left and reserves 10. The concurrent dashboard form instead uses
two single-request batches; arrival order determines which reserves first.
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
Like the dashboard's overlapping requests form, these independent requests have
no shared ranking step; arrival order determines who gets stock first.

## 8. Verification: `tests/test_system.py`

Each test starts a real stock HTTP server and two allocation HTTP servers using
an isolated temporary SQLite database. The tests cover batch priority, partial
allocation, concurrent requests across replicas, deadline release, repeat
collection, infeasible travel, invalid input, persistence, expired lots, and
direct stock over-allocation rejection. They also check that added stock is
persistent, visible through another coordinator, reservable, and validated before
insertion. No running local demo is required.
An additional concurrent scenario exhausts the yogurt lot, adds a new Milk lot,
and submits custom quantities from two stores to verify the demo is reusable.

## Supporting files

- `common.py`: JSON HTTP client/server helpers, input validation, UTC clock,
  and structured API errors. Network failures return HTTP 503 to the caller.
- `simulation.py`: the three receiving stores, demand values, and travel minutes.
- `__init__.py`: marks `stock_rescue` as a Python package.
- `.gitignore`: excludes generated databases, Python cache, and virtual environments.
- `README.md`: setup, demo, decision rules, limitations, and container handoff.
