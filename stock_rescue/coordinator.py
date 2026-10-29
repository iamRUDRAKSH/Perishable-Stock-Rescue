"""Stateless transfer decisions; stock is always reserved through HTTP."""

import os
from http.server import ThreadingHTTPServer
from pathlib import Path

from .common import APIError, Handler, call, now, positive_int
from .simulation import STORES


def allocate(stock_url, requests):
    if not isinstance(requests, list) or not 1 <= len(requests) <= 20:
        raise APIError(400, "Provide between 1 and 20 requests")
    # Validate the entire batch before making any reservations.
    for request in requests:
        if not isinstance(request, dict):
            raise APIError(400, "Each request must be a JSON object")
        if (
            not isinstance(request.get("store_id"), str)
            or request["store_id"] not in STORES
        ):
            raise APIError(400, "Unknown store_id")
        if not isinstance(request.get("lot_id"), str):
            raise APIError(400, "lot_id must be a string")
        positive_int(request.get("quantity"), "quantity")
    snapshot = call(stock_url + "/stock")
    lots = {lot["id"]: lot for lot in snapshot["lots"]}
    if any(request["lot_id"] not in lots for request in requests):
        raise APIError(400, "Unknown lot_id")
    ranked = []
    for index, request in enumerate(requests):
        store = STORES[request["store_id"]]
        hours_left = max(0, (lots[request["lot_id"]]["expires_at"] - now()) / 3600)
        urgency = max(0, 1 - hours_left / 24)
        travel_score = max(0, 1 - store["travel_minutes"] / 120)
        score = round(50 * store["demand"] + 30 * urgency + 20 * travel_score, 2)
        ranked.append(
            {
                **request,
                "request_index": index,
                "score": score,
                "travel_minutes": store["travel_minutes"],
            }
        )
    results = []
    for request in sorted(ranked, key=lambda item: -item["score"]):
        result = {**request, "allocated": 0}
        # Another coordinator may reserve after our read. Re-read on conflict.
        for attempt in range(5):
            lot = next(
                lot
                for lot in call(stock_url + "/stock")["lots"]
                if lot["id"] == request["lot_id"]
            )
            if lot["expires_at"] <= now() + (request["travel_minutes"] + 10) * 60:
                result["reason"] = "Cannot arrive before expiry"
                break
            quantity = min(request["quantity"], lot["available"])
            if quantity == 0:
                result["reason"] = "No stock available"
                break
            try:
                reservation = call(
                    stock_url + "/reservations",
                    "POST",
                    {
                        "lot_id": request["lot_id"],
                        "store_id": request["store_id"],
                        "quantity": quantity,
                        "travel_minutes": request["travel_minutes"],
                    },
                )
                result.update(
                    allocated=quantity,
                    reservation=reservation,
                    reason=(
                        "Reserved"
                        if quantity == request["quantity"]
                        else "Partially reserved: limited stock"
                    ),
                )
                break
            except APIError as error:
                if error.status != 409:
                    raise
                result["reason"] = "Stock changed; please retry"
        results.append(result)
    return {"results": results}


def make_server(host, port, stock_url):
    class AllocationHandler(Handler):
        def route(self):
            if self.command == "GET" and self.path == "/":
                html = Path(__file__).with_name("dashboard.html").read_text()
                return self.send(html, content_type="text/html")
            if self.command == "GET" and self.path == "/health":
                call(stock_url + "/health")
                return self.send({"service": "allocation", "status": "ok"})
            if self.command == "GET" and self.path == "/api/state":
                return self.send({**call(stock_url + "/stock"), "stores": STORES})
            if self.command == "POST" and self.path == "/api/stock":
                return self.send(
                    call(stock_url + "/stock", "POST", self.read_json()), 201
                )
            if self.command == "POST" and self.path == "/api/allocate":
                return self.send(allocate(stock_url, self.read_json().get("requests")))
            if self.command == "POST" and self.path.startswith("/api/collect/"):
                reservation_id = self.path.removeprefix("/api/collect/")
                return self.send(
                    call(stock_url + "/collect/" + reservation_id, "POST", {})
                )
            raise APIError(404, "Route not found")

    return ThreadingHTTPServer((host, port), AllocationHandler)


if __name__ == "__main__":
    make_server(
        os.getenv("HOST", "0.0.0.0"),
        int(os.getenv("PORT", "8000")),
        os.getenv("STOCK_URL", "http://127.0.0.1:8001").rstrip("/"),
    ).serve_forever()
