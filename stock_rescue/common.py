"""Small HTTP helpers shared by the services and demo."""
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler


def now():
    return datetime.now(timezone.utc).timestamp()


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def call(url, method="GET", data=None):
    body = None if data is None else json.dumps(data).encode()
    request = Request(url, data=body, method=method,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        payload = json.loads(error.read())
        raise APIError(error.code, payload.get("error", "Request failed")) from error
    except (URLError, TimeoutError) as error:
        raise APIError(503, "Stock service unavailable; retry shortly") from error


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise APIError(400, f"{name} must be a positive integer")
    return value


class Handler(BaseHTTPRequestHandler):
    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size <= 0 or size > 65536:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise APIError(400, "Expected a JSON object (maximum 64 KB)")

    def send(self, data, status=200, content_type="application/json"):
        body = (json.dumps(data).encode() if content_type == "application/json"
                else data.encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        try:
            self.route()
        except APIError as error:
            self.send({"error": str(error)}, error.status)
        except Exception:
            import traceback
            traceback.print_exc()
            self.send({"error": "Internal server error"}, 500)

    do_GET = dispatch
    do_POST = dispatch
