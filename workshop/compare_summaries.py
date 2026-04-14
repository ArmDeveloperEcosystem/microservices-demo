#!/usr/bin/env python3
"""Compare the fixed-batch summaries from the N4A and C4A runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_summary(path: Path) -> dict:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUMMARY_JSON "):
            return json.loads(line.split(" ", 1)[1])
    raise ValueError(f"No SUMMARY_JSON line found in {path}")


def fmt_number(value: object, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def fmt_seconds_from_ms(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) / 1000.0:.2f}s"


def success_count(summary: dict) -> int:
    return int(summary.get("status_counts", {}).get("200", 0))


def build_rows(left: dict, right: dict) -> list[tuple[str, str, str]]:
    return [
        ("Pool", fmt_number(left.get("pool")), fmt_number(right.get("pool"))),
        (
            "Successful requests",
            f"{success_count(left)}/{fmt_number(left.get('attempted_requests'))}",
            f"{success_count(right)}/{fmt_number(right.get('attempted_requests'))}",
        ),
        (
            "Batch duration",
            fmt_seconds_from_ms(left.get("batch_duration_ms")),
            fmt_seconds_from_ms(right.get("batch_duration_ms")),
        ),
        (
            "Average latency",
            fmt_seconds_from_ms(left.get("avg_latency_ms")),
            fmt_seconds_from_ms(right.get("avg_latency_ms")),
        ),
        (
            "Max latency",
            fmt_seconds_from_ms(left.get("max_latency_ms")),
            fmt_seconds_from_ms(right.get("max_latency_ms")),
        ),
        (
            "Peak cores",
            fmt_number(left.get("peak_cores"), 3),
            fmt_number(right.get("peak_cores"), 3),
        ),
        (
            "Avg in-batch cores",
            fmt_number(left.get("avg_cores_during_batch"), 3),
            fmt_number(right.get("avg_cores_during_batch"), 3),
        ),
        (
            "Time to idle",
            fmt_seconds_from_ms(left.get("time_to_idle_ms")),
            fmt_seconds_from_ms(right.get("time_to_idle_ms")),
        ),
    ]


def print_table(label_one: str, label_two: str, left: dict, right: dict) -> None:
    rows = build_rows(left, right)
    metric_width = max(len(metric) for metric, _, _ in rows)
    left_width = max(len(label_one), *(len(left_value) for _, left_value, _ in rows))
    right_width = max(len(label_two), *(len(right_value) for _, _, right_value in rows))

    header = (
        f"{'Metric'.ljust(metric_width)}  "
        f"{label_one.ljust(left_width)}  "
        f"{label_two.ljust(right_width)}"
    )
    print(header)
    print("-" * len(header))
    for metric, left_value, right_value in rows:
        print(
            f"{metric.ljust(metric_width)}  "
            f"{left_value.ljust(left_width)}  "
            f"{right_value.ljust(right_width)}"
        )


def print_takeaway(label_one: str, label_two: str, left: dict, right: dict) -> None:
    left_batch = left.get("batch_duration_ms")
    right_batch = right.get("batch_duration_ms")
    left_latency = left.get("avg_latency_ms")
    right_latency = right.get("avg_latency_ms")

    print()
    print("Interpretation")
    print("-------------")
    print(
        f"The same application, prompts, and model were run twice. "
        f"Only the shopping assistant placement changed from {label_one} to {label_two}."
    )

    if left_batch and right_batch and float(right_batch) > 0:
        speedup = float(left_batch) / float(right_batch)
        print(f"- {label_two} completed the batch about {speedup:.2f}x faster.")
    if left_latency and right_latency:
        delta = (float(left_latency) - float(right_latency)) / 1000.0
        direction = "lower" if delta > 0 else "higher"
        print(f"- {label_two} average latency was {abs(delta):.2f}s {direction}.")

    print(
        f"- Use this comparison to explain mixed placement: "
        f"`N4A` is the steady application tier and `C4A` is the AI reasoning tier."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary_one", type=Path, help="First summary log path.")
    parser.add_argument("summary_two", type=Path, help="Second summary log path.")
    parser.add_argument("--label-one", default="N4A")
    parser.add_argument("--label-two", default="C4A")
    args = parser.parse_args()

    left = load_summary(args.summary_one)
    right = load_summary(args.summary_two)
    print_table(args.label_one, args.label_two, left, right)
    print_takeaway(args.label_one, args.label_two, left, right)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
