#!/usr/bin/env python3
"""Run a fixed-count assistant batch and capture telemetry in one process."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from request_driver import (
    DEFAULT_PROMPTS,
    make_opener,
    require_base_url,
    send_prompt,
    warm_session,
)
from telemetry_collector import append_jsonl, collect_sample


@dataclass
class TrialConfig:
    base_url: str
    concurrency: int
    prompts_per_worker: int
    delay_seconds: float
    request_timeout_seconds: float
    telemetry_interval_seconds: float
    idle_threshold_cores: float
    idle_stable_samples: int
    idle_timeout_seconds: float
    request_output_file: Path | None
    telemetry_output_file: Path | None


def resolve_output_path(raw: str | None) -> Path | None:
    value = (raw or "").strip()
    if not value or value.lower() in {"none", "null", "off"}:
        return None
    path = Path(value)
    if value.endswith("/") or (path.exists() and path.is_dir()):
        raise ValueError(f"output path must be a file, got directory-like value: {value}")
    return path


def request_worker(
    worker_id: int,
    config: TrialConfig,
    started_event: threading.Event,
    output_lock: threading.Lock,
    records: list[dict],
) -> None:
    opener = make_opener()
    base_url = config.base_url

    while True:
        try:
            warm_session(opener, base_url)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[worker {worker_id}] warmup_error={exc}", flush=True)
            time.sleep(2.0)

    started_event.wait()

    for prompt_index in range(config.prompts_per_worker):
        prompt = DEFAULT_PROMPTS[prompt_index % len(DEFAULT_PROMPTS)]
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            status, payload, latency_ms = send_prompt(
                opener,
                base_url,
                prompt,
                config.request_timeout_seconds,
            )
            record = {
                "timestamp": timestamp,
                "worker_id": worker_id,
                "prompt_index": prompt_index,
                "status": status,
                "latency_ms": latency_ms,
                "prompt": prompt,
                "message": payload.get("message", ""),
                "product_ids": payload.get("product_ids", []),
                "requires_confirmation": payload.get("requires_confirmation", False),
            }
            print(
                f"[worker {worker_id}] status={status} latency_ms={latency_ms} "
                f"products={len(record['product_ids'])} confirm={record['requires_confirmation']} "
                f"prompt={prompt[:72]}",
                flush=True,
            )
        except urllib.error.HTTPError as exc:
            record = {
                "timestamp": timestamp,
                "worker_id": worker_id,
                "prompt_index": prompt_index,
                "status": exc.code,
                "latency_ms": None,
                "prompt": prompt,
                "message": exc.read().decode("utf-8", errors="replace"),
                "product_ids": [],
                "requires_confirmation": False,
            }
            print(f"[worker {worker_id}] http_error={exc.code} prompt={prompt[:72]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            record = {
                "timestamp": timestamp,
                "worker_id": worker_id,
                "prompt_index": prompt_index,
                "status": "exception",
                "latency_ms": None,
                "prompt": prompt,
                "message": str(exc),
                "product_ids": [],
                "requires_confirmation": False,
            }
            print(f"[worker {worker_id}] error={exc}", flush=True)

        with output_lock:
            records.append(record)
            if config.request_output_file:
                append_jsonl(config.request_output_file, record)

        if prompt_index < config.prompts_per_worker - 1 and config.delay_seconds > 0:
            time.sleep(config.delay_seconds)


def telemetry_loop(
    config: TrialConfig,
    stop_event: threading.Event,
    output_lock: threading.Lock,
    samples: list[dict],
) -> None:
    while not stop_event.is_set():
        try:
            sample = collect_sample()
        except Exception as exc:  # noqa: BLE001
            sample = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sample_epoch_ms": int(time.time() * 1000),
                "telemetry_ok": False,
                "collector_error": str(exc),
            }

        with output_lock:
            samples.append(sample)
            if config.telemetry_output_file:
                append_jsonl(config.telemetry_output_file, sample)

        if sample.get("telemetry_ok", False):
            print(
                f"{sample['timestamp']} pool={sample['current_pool']} "
                f"used={sample['total_cores']}/{sample['current_capacity_cores']} "
                f"utilization={sample['utilization_pct']}% "
                f"n4a={sample['n4a_cores']} c4a={sample['c4a_cores']}",
                flush=True,
            )
        else:
            print(f"{sample['timestamp']} collector_error={sample['collector_error']}", flush=True)

        stop_event.wait(config.telemetry_interval_seconds)


def is_idle_sequence(samples: list[dict], threshold: float, stable_count: int, after_ms: int) -> bool:
    good = [s for s in samples if s.get("telemetry_ok") and int(s.get("sample_epoch_ms", 0)) >= after_ms]
    if len(good) < stable_count:
        return False
    tail = good[-stable_count:]
    return all(float(s.get("total_cores", 0.0)) <= threshold for s in tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Frontend base URL.")
    parser.add_argument("--concurrency", type=int, default=2, help="Number of worker sessions.")
    parser.add_argument(
        "--prompts-per-worker",
        type=int,
        default=4,
        help="Exact number of prompts each worker will send.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.2,
        help="Delay between prompts for a worker.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=120.0,
        help="Per-request timeout.",
    )
    parser.add_argument(
        "--telemetry-interval-seconds",
        type=float,
        default=0.5,
        help="Sampling interval for telemetry.",
    )
    parser.add_argument(
        "--idle-threshold-cores",
        type=float,
        default=1.0,
        help="Telemetry total_cores threshold for considering the run idle again.",
    )
    parser.add_argument(
        "--idle-stable-samples",
        type=int,
        default=3,
        help="How many consecutive telemetry samples must be below the idle threshold.",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=180.0,
        help="How long to wait after the batch for the system to return to idle.",
    )
    parser.add_argument(
        "--request-output-file",
        default="workshop/request-fixed-batch.jsonl",
        help="Optional JSONL file for request records. Use 'off' to disable.",
    )
    parser.add_argument(
        "--telemetry-output-file",
        default="workshop/telemetry-fixed-batch.jsonl",
        help="Optional JSONL file for telemetry records. Use 'off' to disable.",
    )
    args = parser.parse_args()

    config = TrialConfig(
        base_url=require_base_url(args.base_url, parser),
        concurrency=max(1, args.concurrency),
        prompts_per_worker=max(1, args.prompts_per_worker),
        delay_seconds=max(0.0, args.delay_seconds),
        request_timeout_seconds=max(5.0, args.request_timeout_seconds),
        telemetry_interval_seconds=max(0.25, args.telemetry_interval_seconds),
        idle_threshold_cores=max(0.0, args.idle_threshold_cores),
        idle_stable_samples=max(1, args.idle_stable_samples),
        idle_timeout_seconds=max(5.0, args.idle_timeout_seconds),
        request_output_file=resolve_output_path(args.request_output_file),
        telemetry_output_file=resolve_output_path(args.telemetry_output_file),
    )

    output_lock = threading.Lock()
    request_records: list[dict] = []
    telemetry_samples: list[dict] = []
    telemetry_stop_event = threading.Event()
    started_event = threading.Event()

    if config.request_output_file:
        config.request_output_file.parent.mkdir(parents=True, exist_ok=True)
        config.request_output_file.write_text("", encoding="utf-8")
    if config.telemetry_output_file:
        config.telemetry_output_file.parent.mkdir(parents=True, exist_ok=True)
        config.telemetry_output_file.write_text("", encoding="utf-8")

    telemetry_thread = threading.Thread(
        target=telemetry_loop,
        args=(config, telemetry_stop_event, output_lock, telemetry_samples),
        daemon=True,
    )
    telemetry_thread.start()

    workers = [
        threading.Thread(
            target=request_worker,
            args=(worker_id, config, started_event, output_lock, request_records),
            daemon=True,
        )
        for worker_id in range(config.concurrency)
    ]

    for worker in workers:
        worker.start()

    print(
        f"Running fixed batch against {config.base_url} "
        f"concurrency={config.concurrency} prompts_per_worker={config.prompts_per_worker} "
        f"delay={config.delay_seconds}s timeout={config.request_timeout_seconds}s",
        flush=True,
    )
    batch_start_ms = int(time.time() * 1000)
    batch_started_monotonic = time.perf_counter()
    started_event.set()

    for worker in workers:
        worker.join()

    batch_end_ms = int(time.time() * 1000)
    batch_duration_ms = round((time.perf_counter() - batch_started_monotonic) * 1000, 2)

    idle_deadline = time.monotonic() + config.idle_timeout_seconds
    idle_recovered_ms: int | None = None
    while time.monotonic() < idle_deadline:
        with output_lock:
            if is_idle_sequence(
                telemetry_samples,
                config.idle_threshold_cores,
                config.idle_stable_samples,
                batch_end_ms,
            ):
                successful = [
                    s
                    for s in telemetry_samples
                    if s.get("telemetry_ok") and int(s.get("sample_epoch_ms", 0)) >= batch_end_ms
                ]
                if successful:
                    idle_recovered_ms = int(successful[-1]["sample_epoch_ms"])
                break
        time.sleep(config.telemetry_interval_seconds)

    telemetry_stop_event.set()
    telemetry_thread.join(timeout=2.0)

    with output_lock:
        request_copy = list(request_records)
        telemetry_copy = list(telemetry_samples)

    run_telemetry = [
        sample
        for sample in telemetry_copy
        if sample.get("telemetry_ok") and batch_start_ms <= int(sample.get("sample_epoch_ms", 0)) <= batch_end_ms
    ]
    total_cores = [float(sample.get("total_cores", 0.0)) for sample in run_telemetry]
    pool_counts = Counter(sample.get("current_pool", "unknown") for sample in run_telemetry)
    pool_name = pool_counts.most_common(1)[0][0] if pool_counts else "unknown"
    current_capacity = max((float(sample.get("current_capacity_cores", 0.0)) for sample in run_telemetry), default=0.0)
    latencies = [float(record["latency_ms"]) for record in request_copy if record.get("latency_ms") is not None]
    status_counts = Counter(str(record.get("status")) for record in request_copy)

    summary = {
        "pool": pool_name,
        "capacity_cores": round(current_capacity, 3),
        "concurrency": config.concurrency,
        "prompts_per_worker": config.prompts_per_worker,
        "attempted_requests": config.concurrency * config.prompts_per_worker,
        "recorded_requests": len(request_copy),
        "status_counts": dict(status_counts),
        "batch_start_ms": batch_start_ms,
        "batch_end_ms": batch_end_ms,
        "batch_duration_ms": batch_duration_ms,
        "peak_cores": round(max(total_cores), 3) if total_cores else None,
        "avg_cores_during_batch": round(sum(total_cores) / len(total_cores), 3) if total_cores else None,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "max_latency_ms": round(max(latencies), 2) if latencies else None,
        "idle_threshold_cores": config.idle_threshold_cores,
        "idle_stable_samples": config.idle_stable_samples,
        "idle_recovered_ms": idle_recovered_ms,
        "time_to_idle_ms": (idle_recovered_ms - batch_end_ms) if idle_recovered_ms is not None else None,
    }

    print("SUMMARY_JSON", json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
