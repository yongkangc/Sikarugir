#!/usr/bin/env python3
"""Summarize the Metal HUD CSV format documented in Apple's Tech Talk 110339.

Values are presentation intervals and GPU durations, not CPU frame execution
times. Unknown or malformed telemetry fails rather than producing guessed FPS.
"""
import argparse
import json
import math
from pathlib import Path
import re
import statistics


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(text, exclude_ambiguous=False):
    frames = {}
    ambiguous = set()
    processes = set()
    batches = 0
    for line in text.splitlines():
        if "metal-HUD:" not in line:
            continue
        identity = re.search(r"\[(\d+):[0-9a-fA-F]+\]", line)
        if not identity:
            raise ValueError("HUD event has no process identity")
        pid = int(identity.group(1)); processes.add(pid)
        values = [float(value) for value in line.split("metal-HUD:", 1)[1].split(",")]
        if len(values) < 5 or (len(values) - 3) % 2 or not all(map(math.isfinite, values)):
            raise ValueError("Unrecognized Metal HUD payload")
        first = values[0]
        if first < 0 or first != int(first):
            raise ValueError("Invalid frame marker")
        batches += 1
        for i in range(3, len(values), 2):
            interval, gpu = values[i:i + 2]
            if interval <= 0 or gpu < 0:
                raise ValueError("Invalid presentation interval or GPU duration")
            key = (pid, int(first) + (i - 3) // 2)
            pair = (interval, gpu)
            if key in frames and frames[key] != pair:
                if not exclude_ambiguous:
                    raise ValueError("Conflicting duplicate frame data")
                ambiguous.add(key)
            frames[key] = pair
    if len(processes) != 1 or not frames:
        raise ValueError("Expected nonempty telemetry from exactly one process")
    if len(ambiguous) > len(frames) / 100:
        raise ValueError("More than one percent of frame markers are ambiguous")
    for key in ambiguous:
        del frames[key]
    intervals = [pair[0] for pair in frames.values()]
    gpu_times = [pair[1] for pair in frames.values()]
    return {"process_pid": next(iter(processes)), "log_batches": batches,
            "logged_frames": len(frames),
            "excluded_ambiguous_frame_markers": len(ambiguous),
            "logged_presentation_seconds": sum(intervals) / 1000,
            "fps_from_logged_intervals": 1000 / statistics.mean(intervals),
            "presentation_ms_mean": statistics.mean(intervals),
            "presentation_ms_median": statistics.median(intervals),
            "presentation_ms_p95": percentile(intervals, .95),
            "presentation_ms_p99": percentile(intervals, .99),
            "presentation_ms_max": max(intervals),
            "gpu_ms_mean": statistics.mean(gpu_times),
            "gpu_ms_p95": percentile(gpu_times, .95),
            "gpu_ms_p99": percentile(gpu_times, .99),
            "frames_over_16_67_ms": sum(value > 1000 / 60 for value in intervals),
            "frames_over_33_33_ms": sum(value > 1000 / 30 for value in intervals),
            "scope": "logged frame intervals; logging gaps are not reconstructed",
            "cpu_attribution": "presentation interval minus GPU time is not measured CPU cost"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--exclude-ambiguous-frames", action="store_true",
                        help="Exclude both readings of conflicting boundary markers, up to 1 percent")
    args = parser.parse_args()
    result = summarize(args.log.read_text(), exclude_ambiguous=args.exclude_ambiguous_frames)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
