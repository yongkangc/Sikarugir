#!/usr/bin/env python3
"""Compile and measure DXMT's actual UpdateStatistics method in isolation.

Uses production statistics headers and a stand-in HUD sink, not Metal or Wine.
This validates removed formatting and preserved debug text, not game FPS.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import tempfile

REFERENCE = "7c8dee1c2d73415301ceb7d1fa810861cef4cd67"
SOURCE = "src/d3d11/d3d11_swapchain.cpp"


def method(source):
    start = source.index("  void UpdateStatistics(")
    end = source.index("\n  BOOL STDMETHODCALLTYPE IsTemporaryMonoSupported", start)
    return source[start:end]


HEADER = r'''
#include <algorithm>
#include <format>
#include <string>
#include <vector>
#include "dxmt_statistics.hpp"
using namespace dxmt;
extern uint64_t hud_calls;
extern std::vector<std::string> hud_lines;
class HUDState {
public:
  void begin();
  void printLine(const char*);
  void printLine(const std::string& s) { printLine(s.c_str()); }
  void end();
};
void invoke(FrameStatisticsContainer&, uint64_t);
'''

SINK = r'''
#include "probe.hpp"
uint64_t hud_calls = 0;
std::vector<std::string> hud_lines;
void HUDState::begin() { ++hud_calls; }
void HUDState::end() { ++hud_calls; }
void HUDState::printLine(const char* s) {
  ++hud_calls;
#ifdef DXMT_DEBUG
  hud_lines.emplace_back(s);
#endif
}
'''

MAIN = r'''
#include "probe.hpp"
#include <chrono>
#include <iostream>
int main() {
  FrameStatisticsContainer stats;
  for (unsigned i = 0; i < kFrameStatisticsCount; ++i) {
    auto& f = stats.at(i);
    f.command_buffer_count = 2 + i;
    f.sync_count = i;
    f.event_stall = i;
    f.latency = 3;
    f.commit_interval = std::chrono::milliseconds(1 + i);
    f.sync_interval = std::chrono::milliseconds(2 + i);
    f.encode_prepare_interval = std::chrono::milliseconds(3);
    f.encode_flush_interval = std::chrono::milliseconds(4);
    f.drawable_blocking_interval = std::chrono::milliseconds(1);
    f.present_latency_interval = std::chrono::milliseconds(2);
    f.render_pass_count = 7; f.render_pass_optimized = 2;
    f.clear_pass_count = 4; f.clear_pass_optimized = 1;
  }
  stats.compute(0);
#ifdef DXMT_DEBUG
  for (int kind = 0; kind < 3; ++kind) {
    for (int flag = 0; flag < 9; ++flag) {
      auto& frame = stats.at(0);
      frame.compatibility_flags.clrAll();
      if (flag < 8) frame.compatibility_flags.set(static_cast<FeatureCompatibility>(flag));
      auto& s = frame.last_scaler_info;
      s.type = static_cast<ScalerType>(kind);
      s.input_width = 1728; s.input_height = 1080;
      s.output_width = 3456; s.output_height = 2160;
      s.auto_exposure = flag % 2; s.motion_vector_highres = flag % 3;
      invoke(stats, 1);
    }
  }
  for (const auto& line : hud_lines) std::cout << line << '\n';
#else
  constexpr int count = 20000;
  for (int i = 0; i < 100; ++i) invoke(stats, 1);
  hud_calls = 0;
  auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < count; ++i) invoke(stats, 1);
  auto t1 = std::chrono::steady_clock::now();
  double ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / count;
  std::cout << ns << ' ' << hud_calls << '\n';
#endif
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Patched DXMT checkout")
    parser.add_argument("--reference", default=REFERENCE)
    parser.add_argument("--arch", choices=["arm64", "x86_64"], default="x86_64")
    parser.add_argument("--experimental-format", action="store_true",
                        help="Enable older libc++'s std::format without exception support (valid inputs only)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.source.resolve(strict=True)
    original = subprocess.run(["git", "show", f"{args.reference}:{SOURCE}"], cwd=root,
                              check=True, capture_output=True, text=True).stdout
    sources = {"original": method(original), "patched": method((root / SOURCE).read_text())}
    with tempfile.TemporaryDirectory(prefix="dxmt-hud-probe-") as folder:
        folder = Path(folder)
        (folder / "probe.hpp").write_text(HEADER)
        (folder / "sink.cpp").write_text(SINK)
        (folder / "main.cpp").write_text(MAIN)
        programs = {}
        for name, body in sources.items():
            file = folder / f"{name}.cpp"
            file.write_text('#include "probe.hpp"\nclass Harness { public: HUDState hud;\n' +
                            body + '\n};\nvoid invoke(FrameStatisticsContainer& s, uint64_t id) '
                            '{ Harness h; h.UpdateStatistics(s, id); }\n')
            for debug in [False, True]:
                binary = folder / f"{name}-{'debug' if debug else 'release'}"
                cmd = ["xcrun", "clang++", "-std=c++20", "-O2", "-arch", args.arch,
                       "-mmacosx-version-min=14.6", "-I", str(root / "src/dxmt"),
                       "-I", str(root / "src/util")]
                if debug:
                    cmd.append("-DDXMT_DEBUG=1")
                if args.experimental_format:
                    cmd.extend(["-D_LIBCPP_ENABLE_EXPERIMENTAL=1", "-fno-exceptions"])
                built = subprocess.run(cmd + [str(file), str(folder / "sink.cpp"),
                                               str(folder / "main.cpp"), "-o", str(binary)],
                                       capture_output=True, text=True)
                if built.returncode:
                    raise RuntimeError(f"Probe compilation failed:\n{built.stderr}")
                programs[(name, debug)] = binary
        def run(name, debug=False):
            return subprocess.run([str(programs[(name, debug)])], check=True,
                                  capture_output=True, text=True).stdout
        debug_original = run("original", True)
        if not debug_original or debug_original != run("patched", True):
            raise RuntimeError("Patched debug HUD output differs from the reference")
        samples = {"original": [], "patched": []}
        for repeat in range(10):
            for name in (["original", "patched"] if repeat % 2 == 0 else ["patched", "original"]):
                ns, calls = run(name).split()
                if name == "patched" and int(calls) != 0:
                    raise RuntimeError("Patched non-debug path still calls the HUD")
                if name == "original" and int(calls) == 0:
                    raise RuntimeError("Reference did not exercise formatting")
                samples[name].append(float(ns))
        result = {"reference": args.reference, "architecture": args.arch,
                  "experimental_libcxx_format": args.experimental_format,
                  "exception_support": not args.experimental_format,
                  "fixture": "actual method and statistics headers; substitute HUD sink",
                  "debug_cases": 27, "debug_output_matches": True,
                  "non_debug_hud_calls_removed": True,
                  "ns_per_call_median": {k: statistics.median(v) for k, v in samples.items()},
                  "scope": "isolated formatting benchmark; not game FPS or full DXMT validation"}
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
