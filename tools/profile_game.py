#!/usr/bin/env python3
"""Read-only macOS Wine game profiling using libproc, IORegistry and sample.

No wrapper settings are changed. GPU counters cover the whole device. Statistical
stack samples include sleeping threads and must not be interpreted as CPU shares.
"""

import argparse
import ctypes
import datetime
import json
from pathlib import Path
import plistlib
import signal
import statistics
import subprocess
import time

from summarize_frames import summarize


class Usage(ctypes.Structure):
    # rusage_info_v0 from the macOS SDK's sys/resource.h.
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in
        ["user_time", "system_time", "idle_wakeups", "interrupt_wakeups",
         "pageins", "wired_size", "resident_size", "phys_footprint",
         "start_abstime", "exit_abstime"]
    ]


LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(Usage)]
LIBPROC.proc_pid_rusage.restype = ctypes.c_int


class Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


_timebase = Timebase()
_libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
_libsystem.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
_libsystem.mach_timebase_info.restype = ctypes.c_int
if _libsystem.mach_timebase_info(ctypes.byref(_timebase)) or not _timebase.denom:
    raise RuntimeError("Cannot determine Mach absolute-time conversion")
# libproc CPU totals are Mach ticks, not nanoseconds. This differs between Intel
# and Apple Silicon; assuming a 1:1 timebase substantially underreports M4 CPU use.
TICK_SECONDS = _timebase.numer / _timebase.denom / 1e9


def usage(pid):
    result = Usage()
    if LIBPROC.proc_pid_rusage(pid, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_errno(), f"Cannot read resource usage for PID {pid}")
    if result.exit_abstime:
        raise RuntimeError(f"Process {pid} has exited")
    return result


def find_game(match, pid=None):
    rows = subprocess.run(["ps", "-A", "-o", "pid=,comm="], check=True,
                          capture_output=True, text=True).stdout.splitlines()
    candidates = []
    for row in rows:
        number, command = row.strip().split(maxsplit=1)
        name = command.replace("\\", "/").rsplit("/", 1)[-1]
        if name.casefold() == match.casefold() and (pid is None or int(number) == pid):
            candidates.append((int(number), name))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one running {match!r}; found {len(candidates)}")
    return candidates[0]


def gpu_counters():
    result = subprocess.run(["ioreg", "-r", "-c", "IOAccelerator", "-d", "1", "-a"],
                            check=True, capture_output=True, timeout=5)
    devices = []
    for device in plistlib.loads(result.stdout):
        counters = device.get("PerformanceStatistics", {})
        devices.append({key: counters[key] for key in
                        ["Device Utilization %", "Renderer Utilization %", "Tiler Utilization %"]
                        if key in counters})
    return devices


def redact_paths(text):
    return text.replace(str(Path.home()), "~")


def loaded_components(sample):
    # Only inspect Binary Images; stack counts are not CPU-time measurements.
    if "Binary Images:" not in sample:
        return []
    images = sample.split("Binary Images:", 1)[-1]
    return sorted({component for component, marker in {
        "DXMT Metal bridge": "/renderer/dxmt/",
        "DXVK": "/renderer/dxvk/",
        "D3DMetal supporting library": "/renderer/d3dmetal/",
        "MoltenVK": "libMoltenVK.dylib",
    }.items() if marker in images})


def frame_telemetry(text, pid, exclude_ambiguous=False):
    """Keep resource results usable when HUD data is missing or invalid."""
    try:
        frames = summarize(text, exclude_ambiguous=exclude_ambiguous)
        if frames["process_pid"] != pid:
            raise ValueError("HUD process identity differs from the resource target")
    except ValueError as error:
        return {"frame_times": "unavailable: HUD data failed validation",
                "frame_validation_error": str(error)}
    return {"frame_times": "validated Metal HUD presentation/GPU intervals",
            "frame_summary": frames}


def capture(pid, seconds, directory):
    previous = usage(pid)
    identity = previous.start_abstime
    previous_time = time.monotonic()
    rows = []
    with (directory / "metrics.jsonl").open("w") as output:
        for i in range(seconds):
            time.sleep(max(0, previous_time + 1 - time.monotonic()))
            current = usage(pid)
            now = time.monotonic()
            if current.start_abstime != identity:
                raise RuntimeError("Target exited and its PID was reused; refusing to combine measurements")
            interval = now - previous_time
            user = (current.user_time - previous.user_time) * TICK_SECONDS / interval * 100
            system = (current.system_time - previous.system_time) * TICK_SECONDS / interval * 100
            row = {"interval": i + 1, "interval_seconds": interval,
                   "process_cpu_percent": user + system, "user_cpu_percent": user,
                   "system_cpu_percent": system, "footprint_bytes": current.phys_footprint,
                   "resident_bytes": current.resident_size,
                   "pageins_delta": current.pageins - previous.pageins,
                   "global_gpu_counters": gpu_counters()}
            output.write(json.dumps(row) + "\n"); output.flush()
            rows.append(row)
            previous, previous_time = current, now
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", default="Risk of Rain 2.exe", help="Exact process basename")
    parser.add_argument("--pid", type=int, help="Disambiguate two copies of the same game")
    parser.add_argument("--scene", required=True, help="For example menu or first-stage-gameplay")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--sample-seconds", type=int, default=5,
                        help="CPU stack sampling AFTER the resource measurements; 0 disables it")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metal-logs", action="store_true",
                        help="Collect this process's Metal HUD messages while measuring resources")
    parser.add_argument("--exclude-ambiguous-frames", action="store_true",
                        help="Exclude conflicting HUD boundary markers (at most 1 percent)")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or not 0 <= args.sample_seconds <= 15:
        parser.error("Resource duration must be 1-60 seconds; stack sample duration 0-15 seconds")
    if args.exclude_ambiguous_frames and not args.metal_logs:
        parser.error("--exclude-ambiguous-frames requires --metal-logs")
    pid, name = find_game(args.match, args.pid)
    # Fresh directories prevent accidental overwrite and mixing different scenes.
    args.output.mkdir(parents=True, exist_ok=False)
    initial = usage(pid)
    if args.metal_logs:
        predicate = (f'processIdentifier == {pid} AND '
                     '(subsystem == "com.apple.metal.hud" OR eventMessage CONTAINS "metal-HUD")')
        with (args.output / "metal-hud.log").open("w") as log:
            stream = subprocess.Popen(["/usr/bin/log", "stream", "--level", "info", "--style", "compact",
                                       "--predicate", predicate], stdout=log, stderr=log)
            try:
                rows = capture(pid, args.seconds, args.output)
                if stream.poll() is not None:
                    raise RuntimeError("Metal log collector exited early; inspect metal-hud.log")
            finally:
                if stream.poll() is None:
                    stream.send_signal(signal.SIGINT)
                    try:
                        stream.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        stream.kill(); stream.wait()
    else:
        rows = capture(pid, args.seconds, args.output)
    summary = {"scene": args.scene, "process": name, "pid": pid,
               "captured_at": datetime.datetime.now().astimezone().isoformat(),
               "resource_intervals": len(rows),
               "cpu_percent_mean": statistics.mean(row["process_cpu_percent"] for row in rows),
               "footprint_bytes_max": max(row["footprint_bytes"] for row in rows),
               "gpu_counter_scope": "whole device, includes other applications",
               "cpu_percent_units": "100 percent equals one CPU core",
               "mach_timebase": {"numer": _timebase.numer, "denom": _timebase.denom},
               "frame_times": "not captured; no FPS or frame-time conclusion is justified"}
    if args.metal_logs:
        summary["metal_hud_log"] = "metal-hud.log"
        summary.update(frame_telemetry((args.output / "metal-hud.log").read_text(), pid,
                                      args.exclude_ambiguous_frames))
        if "frame_summary" in summary:
            (args.output / "frame-summary.json").write_text(
                json.dumps(summary["frame_summary"], indent=2) + "\n")
    # Save resource/frame results before sampling, so a sampler failure does not
    # discard an otherwise valid baseline.
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if args.sample_seconds:
        if usage(pid).start_abstime != initial.start_abstime:
            raise RuntimeError("Target identity changed before the stack sample")
        file = args.output / "cpu-sample.txt"
        subprocess.run(["/usr/bin/sample", str(pid), str(args.sample_seconds), "1", "-file", str(file)],
                       check=True, capture_output=True, text=True, timeout=args.sample_seconds + 20)
        if usage(pid).start_abstime != initial.start_abstime:
            raise RuntimeError("Target identity changed during the stack sample")
        sample = redact_paths(file.read_text())
        file.write_text(sample)
        summary["loaded_components"] = loaded_components(sample)
        summary["translated"] = "X86-64 (translated)" in sample
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
