#!/usr/bin/env python3
"""Collect shoppingassistantservice CPU telemetry and map it to N4A or C4A."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT_FILE = Path("workshop/telemetry-live.jsonl")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def cpu_to_cores(raw: str) -> float:
    raw = raw.strip()
    if not raw:
        return 0.0
    if raw.endswith("m"):
        return round(float(raw[:-1]) / 1000.0, 4)
    return round(float(raw), 4)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def collect_sample(namespace: str = "default") -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    sample_epoch_ms = int(time.time() * 1000)
    pods = json.loads(
        run(
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app=shoppingassistantservice",
            "-o",
            "json",
        )
    ).get("items", [])

    if not pods:
        return {
            "timestamp": timestamp,
            "sample_epoch_ms": sample_epoch_ms,
            "telemetry_ok": True,
            "current_pool": "not-running",
            "current_capacity_cores": 0.0,
            "utilization_pct": 0.0,
            "n4a_cores": 0.0,
            "c4a_cores": 0.0,
            "total_cores": 0.0,
        }

    pod_map = {}
    node_cache = {}
    for pod in pods:
        namespace = pod["metadata"]["namespace"]
        name = pod["metadata"]["name"]
        node_name = pod["spec"].get("nodeName", "")
        if not node_name:
            continue
        if node_name not in node_cache:
            node = json.loads(run("kubectl", "get", "node", node_name, "-o", "json"))
            labels = node.get("metadata", {}).get("labels", {})
            capacity = node.get("status", {}).get("capacity", {}).get("cpu", "0")
            node_cache[node_name] = {
                "pool": labels.get("cloud.google.com/gke-nodepool", node_name),
                "capacity_cores": float(capacity),
            }
        pod_map[(namespace, name)] = node_cache[node_name]

    top_output = run(
        "kubectl",
        "top",
        "pod",
        "-n",
        namespace,
        "-l",
        "app=shoppingassistantservice",
        "--containers",
        "--no-headers",
    )

    n4a_cores = 0.0
    c4a_cores = 0.0
    current_pool = "unknown"
    current_capacity_cores = 0.0
    pod_pools: list[str] = []

    for line in top_output.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            namespace, pod_name, _container, cpu_raw, _memory = parts[:5]
        elif len(parts) == 4:
            pod_name, _container, cpu_raw, _memory = parts[:4]
            namespace = namespace
        else:
            continue
        info = pod_map.get((namespace, pod_name), {})
        pool = str(info.get("pool", "unknown"))
        pod_pools.append(pool)
        current_capacity_cores = max(current_capacity_cores, float(info.get("capacity_cores", 0.0)))
        cpu_cores = cpu_to_cores(cpu_raw)
        if "n4a" in pool:
            n4a_cores += cpu_cores
        elif "c4a" in pool:
            c4a_cores += cpu_cores

    total_cores = round(n4a_cores + c4a_cores, 4)
    if n4a_cores > 0 and c4a_cores > 0:
        current_pool = "mixed"
    elif n4a_cores > 0:
        current_pool = next((pool for pool in pod_pools if "n4a" in pool), "arm64-pool-n4a2")
    elif c4a_cores > 0:
        current_pool = next((pool for pool in pod_pools if "c4a" in pool), "arm64-pool-c4a")
    elif pod_pools:
        current_pool = Counter(pod_pools).most_common(1)[0][0]
    utilization_pct = round((total_cores / current_capacity_cores) * 100.0, 2) if current_capacity_cores else 0.0

    return {
        "timestamp": timestamp,
        "sample_epoch_ms": sample_epoch_ms,
        "telemetry_ok": True,
        "current_pool": current_pool,
        "current_capacity_cores": round(current_capacity_cores, 2),
        "utilization_pct": utilization_pct,
        "n4a_cores": round(n4a_cores, 4),
        "c4a_cores": round(c4a_cores, 4),
        "total_cores": total_cores,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE))
    parser.add_argument("--namespace", default="default")
    args = parser.parse_args()

    output_file = Path(args.output_file)
    print(f"Writing telemetry to {output_file}")

    try:
        while True:
            try:
                sample = collect_sample(args.namespace)
            except Exception as exc:  # noqa: BLE001
                sample = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sample_epoch_ms": int(time.time() * 1000),
                    "telemetry_ok": False,
                    "collector_error": str(exc),
                }
            append_jsonl(output_file, sample)
            print(json.dumps(sample), flush=True)
            time.sleep(max(0.5, args.interval_seconds))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
