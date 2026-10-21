"""Only the stock service accesses this database."""
import sqlite3
import uuid
from contextlib import contextmanager

from .common import APIError, now, positive_int


class StockDB:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lots (
                    id TEXT PRIMARY KEY, product TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity >= 0),
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, lot_id TEXT NOT NULL REFERENCES lots(id),
                    store_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
                    deadline REAL NOT NULL, status TEXT NOT NULL
                );
            """)
            if db.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == 0:
                timestamp = now()
                db.executemany("INSERT INTO lots VALUES (?, ?, ?, ?)", [
                    ("yogurt-01", "Yogurt", 40, timestamp + 18 * 3600),
                    ("bread-01", "Bread", 25, timestamp + 5 * 3600),
                ])

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            # All stock checks and mutations use one serialized transaction.
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def expire(db, timestamp):
        db.execute("UPDATE reservations SET status='expired' "
                   "WHERE status='reserved' AND deadline <= ?", (timestamp,))

    @staticmethod
    def available(db, lot_id):
        return db.execute("""
            SELECT quantity - COALESCE((
                SELECT SUM(quantity) FROM reservations
                WHERE lot_id=lots.id AND status IN ('reserved', 'collected')
            ), 0) FROM lots WHERE id=?
        """, (lot_id,)).fetchone()[0]

    def snapshot(self):
        with self.connection() as db:
            timestamp = now()
            self.expire(db, timestamp)
            lots = []
            for row in db.execute("SELECT * FROM lots ORDER BY id"):
                lot = dict(row)
                lot["available"] = (self.available(db, row["id"])
                                    if row["expires_at"] > timestamp else 0)
                lots.append(lot)
            reservations = [dict(row) for row in db.execute(
                "SELECT * FROM reservations ORDER BY rowid DESC")]
            return {"lots": lots, "reservations": reservations}

    def reserve(self, data):
        quantity = positive_int(data.get("quantity"), "quantity")
        travel = positive_int(data.get("travel_minutes"), "travel_minutes")
        store = data.get("store_id")
        if not isinstance(store, str) or not store.strip() or len(store) > 80:
            raise APIError(400, "store_id must be a nonempty string (up to 80 characters)")
        lot_id = data.get("lot_id")
        if not isinstance(lot_id, str):
            raise APIError(400, "lot_id must be a string")
        with self.connection() as db:
            timestamp = now()
            self.expire(db, timestamp)
            lot = db.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
            if lot is None:
                raise APIError(404, "Stock lot not found")
            # Allow 10 minutes for unloading; collection must happen within 30 minutes.
            deadline = min(timestamp + 30 * 60,
                           lot["expires_at"] - (travel + 10) * 60)
            if deadline <= timestamp:
                raise APIError(409, "Transfer cannot arrive before expiry")
            if quantity > self.available(db, lot_id):
                raise APIError(409, "Insufficient available stock")
            reservation = {"id": uuid.uuid4().hex, "lot_id": lot_id,
                           "store_id": store, "quantity": quantity,
                           "deadline": deadline, "status": "reserved"}
            db.execute("INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?)",
                       tuple(reservation.values()))
            return reservation

    def collect(self, reservation_id):
        with self.connection() as db:
            self.expire(db, now())
            row = db.execute("SELECT * FROM reservations WHERE id=?",
                             (reservation_id,)).fetchone()
            if row is None:
                raise APIError(404, "Reservation not found")
            if row["status"] == "expired":
                raise APIError(409, "Collection deadline has passed")
            db.execute("UPDATE reservations SET status='collected' WHERE id=?",
                       (reservation_id,))
            return {**dict(row), "status": "collected"}
