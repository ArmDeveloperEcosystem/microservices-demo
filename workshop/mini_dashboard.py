#!/usr/bin/env python3
"""Serve a lightweight dashboard for workshop telemetry and request logs."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HTML = """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Axion Workshop Dashboard</title></head><body><h1>Axion Workshop Dashboard</h1></body></html>"""


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return rows[-limit:] if limit else rows


def build_status(telemetry_path: Path, request_path: Path) -> dict:
    telemetry = [row for row in read_jsonl(telemetry_path, limit=120) if row.get("telemetry_ok")]
    requests = read_jsonl(request_path)
    current = telemetry[-1] if telemetry else {}
    peak_cores = max((float(row.get("total_cores", 0.0)) for row in telemetry), default=0.0)
    latencies = [float(row["latency_ms"]) / 1000.0 for row in requests if isinstance(row.get("latency_ms"), (int, float))]
    success_count = sum(1 for row in requests if str(row.get("status")) == "200")
    error_count = max(len(requests) - success_count, 0)
    return {
        "current_pool": current.get("current_pool", "not-running"),
        "current_cores": round(float(current.get("total_cores", 0.0)), 4),
        "peak_cores": round(peak_cores, 4),
        "current_capacity_cores": round(float(current.get("current_capacity_cores", 0.0)), 2),
        "utilization_pct": round(float(current.get("utilization_pct", 0.0)), 2),
        "request_count": len(requests),
        "success_count": success_count,
        "error_count": error_count,
        "avg_latency_seconds": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "max_latency_seconds": round(max(latencies), 2) if latencies else None,
        "last_sample": current.get("timestamp", "")[11:19] if current else "n/a",
        "points": [
            {"label": row.get("timestamp", "")[11:19], "value": round(float(row.get("total_cores", 0.0)), 4)}
            for row in telemetry
        ],
    }


class Handler(BaseHTTPRequestHandler):
    telemetry_path: Path
    request_path: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            payload = json.dumps(build_status(self.telemetry_path, self.request_path)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry-file", default="workshop/telemetry-live.jsonl")
    parser.add_argument("--request-file", default="workshop/request-live.jsonl")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    Handler.telemetry_path = Path(args.telemetry_file)
    Handler.request_path = Path(args.request_file)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Dashboard listening on http://0.0.0.0:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
