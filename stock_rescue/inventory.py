"""Authoritative stock service. Run with python -m stock_rescue.inventory."""
import os
from http.server import ThreadingHTTPServer
from pathlib import Path

from .common import APIError, Handler
from .database import StockDB


def make_server(host, port, database):
    class StockHandler(Handler):
        def route(self):
            if self.command == "GET" and self.path == "/health":
                return self.send({"service": "stock", "status": "ok"})
            if self.command == "GET" and self.path == "/stock":
                return self.send(database.snapshot())
            if self.command == "POST" and self.path == "/stock":
                return self.send(database.add_stock(self.read_json()), 201)
            if self.command == "POST" and self.path == "/reservations":
                return self.send(database.reserve(self.read_json()), 201)
            if self.command == "POST" and self.path.startswith("/collect/"):
                return self.send(database.collect(self.path.removeprefix("/collect/")))
            raise APIError(404, "Route not found")
    return ThreadingHTTPServer((host, port), StockHandler)


if __name__ == "__main__":
    path = Path(os.getenv("STOCK_DB", "data/stock.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    make_server(os.getenv("HOST", "0.0.0.0"), int(os.getenv("STOCK_PORT", "8001")),
                StockDB(path)).serve_forever()
