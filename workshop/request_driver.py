#!/usr/bin/env python3
"""Generate controlled burst traffic against the frontend /bot endpoint."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path

DEFAULT_OUTPUT_FILE = Path("workshop/request_driver.jsonl")


def resolve_jsonl_path(raw_value: str | None, fallback: Path) -> Path | None:
    value = (raw_value or "").strip()
    if not value:
        return fallback
    if value.lower() in {"none", "null", "off"}:
        return None
    if value in {".", "./"}:
        return fallback
    path = Path(value)
    if value.endswith("/") or (path.exists() and path.is_dir()):
        return path / fallback.name
    return path

DEFAULT_PROMPTS = [
    "Find one mug that works well for a clean desk setup and explain why it fits.",
    "Compare the candle holder and the bamboo glass jar for a minimalist workspace.",
    "Suggest one giftable item for a calm desk setup and explain why it works.",
    "Find one desk accessory that looks clean and minimalist for a home office.",
    "Add that item to my cart.",
    "yes",
    "What is in my cart now?",
]


@dataclass
class DriverConfig:
    base_url: str
    concurrency: int
    delay_seconds: float
    burst_seconds: float
    idle_seconds: float
    request_timeout_seconds: float
    max_prompts_per_worker: int | None
    output_file: Path | None


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def require_base_url(base_url: str, parser: argparse.ArgumentParser | None = None) -> str:
    value = (base_url or "").strip()
    if not value:
        message = (
            "Frontend base URL is empty. Pass --base-url http://34.44.98.106 "
            "or export FRONTEND_URL before running the driver."
        )
        if parser is not None:
            parser.error(message)
        raise ValueError(message)
    if not value.startswith(("http://", "https://")):
        message = (
            f"Frontend base URL must start with http:// or https://, got: {value!r}"
        )
        if parser is not None:
            parser.error(message)
        raise ValueError(message)
    return normalize_base_url(value)


def make_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def warm_session(opener: urllib.request.OpenerDirector, base_url: str) -> None:
    request = urllib.request.Request(f"{base_url}/assistant", method="GET")
    with opener.open(request, timeout=30):
        return


def unpack_response(payload: dict) -> tuple[str, list[str], bool]:
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    message = str(payload.get("message") or payload.get("content") or "").strip()
    product_ids = payload.get("product_ids") or details.get("product_ids") or []
    requires_confirmation = payload.get("requires_confirmation")
    if requires_confirmation is None:
        requires_confirmation = details.get("requires_confirmation", False)
    return message, list(product_ids), bool(requires_confirmation)


def send_prompt(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    prompt: str,
    timeout_seconds: float,
) -> tuple[int, dict, float]:
    data = json.dumps({"message": prompt}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/bot",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with opener.open(request, timeout=timeout_seconds) as response:
        body = response.read()
        status = response.status
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return status, json.loads(body.decode("utf-8")), latency_ms


def wait_for_frontend_assistant(
    base_url: str,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 3.0,
    probe_prompt: str = "Find one desk item that looks minimal and practical.",
) -> None:
    opener = make_opener()
    deadline = time.monotonic() + timeout_seconds
    last_error = "assistant warm-up did not complete"

    while time.monotonic() < deadline:
        try:
            warm_session(opener, base_url)
            status, payload, _latency_ms = send_prompt(
                opener,
                base_url,
                probe_prompt,
                timeout_seconds=max(30.0, poll_seconds * 10),
            )
            message, _product_ids, _requires_confirmation = unpack_response(payload)
            if status == 200 and message:
                return
            last_error = f"status={status} message={message!r}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(max(1.0, poll_seconds))

    raise TimeoutError(
        "Timed out waiting for the storefront assistant path to return a visible response: "
        f"{last_error}"
    )


def append_jsonl(path: Path, record: dict, lock: threading.Lock) -> None:
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def in_active_burst(started_at: float, config: DriverConfig) -> bool:
    cycle_seconds = config.burst_seconds + config.idle_seconds
    if config.burst_seconds <= 0 or cycle_seconds <= 0:
        return True
    elapsed = time.monotonic() - started_at
    return (elapsed % cycle_seconds) < config.burst_seconds


def wait_for_active_burst(
    started_at: float,
    config: DriverConfig,
    stop_event: threading.Event,
) -> bool:
    if config.burst_seconds <= 0 or config.idle_seconds <= 0:
        return not stop_event.is_set()

    cycle_seconds = config.burst_seconds + config.idle_seconds
    while not stop_event.is_set():
        elapsed = time.monotonic() - started_at
        phase = elapsed % cycle_seconds
        if phase < config.burst_seconds:
            return True
        stop_event.wait(min(cycle_seconds - phase, 0.2))
    return False


def worker(
    worker_id: int,
    prompts: list[str],
    config: DriverConfig,
    started_at: float,
    stop_event: threading.Event,
    output_lock: threading.Lock,
) -> None:
    opener = make_opener()
    base_url = normalize_base_url(config.base_url)

    while not stop_event.is_set():
        try:
            warm_session(opener, base_url)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[worker {worker_id}] warmup_error={exc}", flush=True)
            stop_event.wait(2.0)
    if stop_event.is_set():
        return

    prompt_index = 0

    while not stop_event.is_set():
        if (
            config.max_prompts_per_worker is not None
            and prompt_index >= config.max_prompts_per_worker
        ):
            break

        if not wait_for_active_burst(started_at, config, stop_event):
            break

        prompt = prompts[prompt_index % len(prompts)]
        prompt_index += 1
        timestamp = datetime.now(timezone.utc).isoformat()
        burst_active = in_active_burst(started_at, config)

        try:
            status, payload, latency_ms = send_prompt(
                opener,
                base_url,
                prompt,
                config.request_timeout_seconds,
            )
            message, product_ids, requires_confirmation = unpack_response(payload)
            record = {
                "timestamp": timestamp,
                "worker_id": worker_id,
                "status": status,
                "latency_ms": latency_ms,
                "burst_active": burst_active,
                "prompt": prompt,
                "message": message,
                "product_ids": product_ids,
                "requires_confirmation": requires_confirmation,
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
                "status": exc.code,
                "latency_ms": None,
                "burst_active": burst_active,
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
                "status": "exception",
                "latency_ms": None,
                "burst_active": burst_active,
                "prompt": prompt,
                "message": str(exc),
                "product_ids": [],
                "requires_confirmation": False,
            }
            print(f"[worker {worker_id}] error={exc}", flush=True)

        if config.output_file:
            append_jsonl(config.output_file, record, output_lock)

        stop_event.wait(config.delay_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="Frontend base URL.")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of worker threads.")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.15,
        help="Delay between requests per worker during the active burst.",
    )
    parser.add_argument(
        "--burst-seconds",
        type=float,
        default=18.0,
        help="Active load window length before the driver pauses.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=8.0,
        help="Idle window length between active bursts.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=90.0,
        help="Per-request timeout to avoid long hung sessions.",
    )
    parser.add_argument(
        "--max-prompts-per-worker",
        type=int,
        default=0,
        help="Optional fixed prompt count per worker. Use 0 for unlimited streaming.",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_FILE),
        help="Optional JSONL output file for request logs.",
    )
    args = parser.parse_args()

    config = DriverConfig(
        base_url=require_base_url(args.base_url, parser),
        concurrency=max(1, args.concurrency),
        delay_seconds=max(0.0, args.delay_seconds),
        burst_seconds=max(0.0, args.burst_seconds),
        idle_seconds=max(0.0, args.idle_seconds),
        request_timeout_seconds=max(5.0, args.request_timeout_seconds),
        max_prompts_per_worker=args.max_prompts_per_worker if args.max_prompts_per_worker > 0 else None,
        output_file=resolve_jsonl_path(args.output_file, DEFAULT_OUTPUT_FILE),
    )

    stop_event = threading.Event()
    output_lock = threading.Lock()
    started_at = time.monotonic()
    threads = [
        threading.Thread(
            target=worker,
            args=(worker_id, DEFAULT_PROMPTS, config, started_at, stop_event, output_lock),
            daemon=True,
        )
        for worker_id in range(config.concurrency)
    ]

    def handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Waiting for storefront assistant readiness at {config.base_url} ...", flush=True)
    wait_for_frontend_assistant(
        config.base_url,
        timeout_seconds=max(180.0, config.request_timeout_seconds),
    )
    print("Storefront assistant path is ready.", flush=True)

    print(
        f"Starting request driver against {config.base_url} "
        f"with concurrency={config.concurrency} delay={config.delay_seconds}s "
        f"burst={config.burst_seconds}s idle={config.idle_seconds}s "
        f"timeout={config.request_timeout_seconds}s "
        f"max_prompts_per_worker={config.max_prompts_per_worker or 'unlimited'}",
        flush=True,
    )
    for thread in threads:
        thread.start()

    try:
        while not stop_event.is_set():
            if all(not thread.is_alive() for thread in threads):
                break
            time.sleep(0.5)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
