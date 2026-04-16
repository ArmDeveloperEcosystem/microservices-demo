#!/usr/bin/env python3
"""Serve a lightweight dashboard for workshop telemetry and request logs."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Axion Workshop Dashboard</title>
  <style>
    :root {
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #132238;
      --muted: #5f7288;
      --line: #d8e1ec;
      --accent: #1565c0;
      --accent-soft: #dbeafe;
      --ok: #0f766e;
      --warn: #b45309;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #eff5fb 0%, var(--bg) 100%);
      color: var(--ink);
    }
    .wrap {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 2rem;
      line-height: 1.1;
    }
    .subtitle {
      margin: 0 0 22px;
      color: var(--muted);
      font-size: 1rem;
    }
    .status-row {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 20px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--panel);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.95rem;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--ok);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }
    .card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 10px 24px rgba(19, 34, 56, 0.06);
    }
    .card {
      padding: 16px 18px;
      min-height: 116px;
    }
    .label {
      color: var(--muted);
      font-size: 0.88rem;
      margin-bottom: 12px;
    }
    .value {
      font-size: 1.9rem;
      font-weight: 700;
      line-height: 1;
      margin-bottom: 8px;
      word-break: break-word;
    }
    .meta {
      color: var(--muted);
      font-size: 0.9rem;
    }
    .panel {
      padding: 18px;
    }
    .panel h2 {
      margin: 0 0 6px;
      font-size: 1.2rem;
    }
    .panel p {
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 0.95rem;
    }
    .chart {
      width: 100%;
      height: 260px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background:
        linear-gradient(to top, rgba(21, 101, 192, 0.04), rgba(21, 101, 192, 0.01)),
        repeating-linear-gradient(
          to top,
          transparent 0,
          transparent 50px,
          rgba(95, 114, 136, 0.08) 50px,
          rgba(95, 114, 136, 0.08) 51px
        );
      overflow: hidden;
      position: relative;
    }
    .chart svg {
      width: 100%;
      height: 100%;
      display: block;
    }
    .chart-empty {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 0.95rem;
      text-align: center;
      padding: 24px;
    }
    .foot {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.9rem;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    @media (max-width: 640px) {
      .wrap { padding: 20px 14px 28px; }
      .value { font-size: 1.6rem; }
      .chart { height: 220px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Axion Workshop Dashboard</h1>
    <p class="subtitle">Live view of the assistant workload, placement, and request behavior during the workshop benchmark.</p>

    <div class="status-row">
      <div class="pill"><span class="dot"></span><span id="state">Waiting for telemetry...</span></div>
      <div class="pill">Last sample: <span class="mono" id="last-sample">n/a</span></div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="label">Current pool</div>
        <div class="value" id="current-pool">n/a</div>
        <div class="meta">Where the assistant is running right now</div>
      </div>
      <div class="card">
        <div class="label">Current cores</div>
        <div class="value" id="current-cores">0.0</div>
        <div class="meta"><span id="utilization">0.0%</span> of <span id="capacity">0.0</span> available cores</div>
      </div>
      <div class="card">
        <div class="label">Peak cores</div>
        <div class="value" id="peak-cores">0.0</div>
        <div class="meta">Highest observed assistant CPU during this run</div>
      </div>
      <div class="card">
        <div class="label">Requests completed</div>
        <div class="value" id="request-count">0</div>
        <div class="meta"><span id="success-count">0</span> success / <span id="error-count">0</span> other</div>
      </div>
      <div class="card">
        <div class="label">Average latency</div>
        <div class="value" id="avg-latency">n/a</div>
        <div class="meta">Current mean request time</div>
      </div>
      <div class="card">
        <div class="label">Max latency</div>
        <div class="value" id="max-latency">n/a</div>
        <div class="meta">Slowest request seen so far</div>
      </div>
    </div>

    <div class="panel">
      <h2>Assistant CPU over time</h2>
      <p>This chart updates from the live telemetry feed collected from <span class="mono">kubectl top</span>.</p>
      <div class="chart">
        <svg id="chart" viewBox="0 0 1000 260" preserveAspectRatio="none" aria-label="Assistant CPU over time"></svg>
        <div class="chart-empty" id="chart-empty">Waiting for enough telemetry samples to draw the chart.</div>
      </div>
      <div class="foot">
        <span>Low activity before the benchmark is normal.</span>
        <span class="mono" id="point-count">0 samples</span>
      </div>
    </div>
  </div>

  <script>
    const ids = {
      state: document.getElementById("state"),
      lastSample: document.getElementById("last-sample"),
      currentPool: document.getElementById("current-pool"),
      currentCores: document.getElementById("current-cores"),
      utilization: document.getElementById("utilization"),
      capacity: document.getElementById("capacity"),
      peakCores: document.getElementById("peak-cores"),
      requestCount: document.getElementById("request-count"),
      successCount: document.getElementById("success-count"),
      errorCount: document.getElementById("error-count"),
      avgLatency: document.getElementById("avg-latency"),
      maxLatency: document.getElementById("max-latency"),
      chart: document.getElementById("chart"),
      chartEmpty: document.getElementById("chart-empty"),
      pointCount: document.getElementById("point-count"),
    };

    function fmtNumber(value, digits = 2) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(digits) : "n/a";
    }

    function fmtLatency(value) {
      const num = Number(value);
      return Number.isFinite(num) ? `${num.toFixed(2)}s` : "n/a";
    }

    function renderChart(points) {
      ids.chart.innerHTML = "";
      if (!Array.isArray(points) || points.length < 2) {
        ids.chartEmpty.style.display = "flex";
        ids.pointCount.textContent = `${points ? points.length : 0} samples`;
        return;
      }

      ids.chartEmpty.style.display = "none";
      ids.pointCount.textContent = `${points.length} samples`;

      const width = 1000;
      const height = 260;
      const left = 30;
      const right = 20;
      const top = 14;
      const bottom = 28;
      const innerWidth = width - left - right;
      const innerHeight = height - top - bottom;
      const maxValue = Math.max(...points.map((p) => Number(p.value) || 0), 0.1);

      const coords = points.map((point, index) => {
        const x = left + (innerWidth * index) / (points.length - 1);
        const y = top + innerHeight - ((Number(point.value) || 0) / maxValue) * innerHeight;
        return { x, y, label: point.label, value: Number(point.value) || 0 };
      });

      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", "#1565c0");
      line.setAttribute("stroke-width", "4");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("stroke-linejoin", "round");
      line.setAttribute("points", coords.map((p) => `${p.x},${p.y}`).join(" "));

      const area = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const first = coords[0];
      const last = coords[coords.length - 1];
      const areaPath = [
        `M ${first.x} ${height - bottom}`,
        ...coords.map((p, i) => `${i === 0 ? "L" : "L"} ${p.x} ${p.y}`),
        `L ${last.x} ${height - bottom}`,
        "Z",
      ].join(" ");
      area.setAttribute("d", areaPath);
      area.setAttribute("fill", "rgba(21, 101, 192, 0.18)");

      ids.chart.appendChild(area);
      ids.chart.appendChild(line);

      coords.forEach((p, index) => {
        if (index !== coords.length - 1) return;
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", p.x);
        dot.setAttribute("cy", p.y);
        dot.setAttribute("r", "6");
        dot.setAttribute("fill", "#1565c0");
        ids.chart.appendChild(dot);
      });
    }

    async function refresh() {
      try {
        const response = await fetch("/api/status", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const status = await response.json();

        ids.state.textContent = "Live telemetry connected";
        ids.lastSample.textContent = status.last_sample || "n/a";
        ids.currentPool.textContent = status.current_pool || "not-running";
        ids.currentCores.textContent = fmtNumber(status.current_cores, 3);
        ids.utilization.textContent = `${fmtNumber(status.utilization_pct, 2)}%`;
        ids.capacity.textContent = fmtNumber(status.current_capacity_cores, 2);
        ids.peakCores.textContent = fmtNumber(status.peak_cores, 3);
        ids.requestCount.textContent = String(status.request_count ?? 0);
        ids.successCount.textContent = String(status.success_count ?? 0);
        ids.errorCount.textContent = String(status.error_count ?? 0);
        ids.avgLatency.textContent = fmtLatency(status.avg_latency_seconds);
        ids.maxLatency.textContent = fmtLatency(status.max_latency_seconds);
        renderChart(status.points || []);
      } catch (_error) {
        ids.state.textContent = "Waiting for dashboard data...";
        renderChart([]);
      }
    }

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""


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
