# Performance work

The objective is to improve measured game performance and compatibility, using
CrossOver as a comparison when the same game, scene and hardware can be tested.
No CrossOver comparison has been performed yet.

## Source boundaries

The parent Sikarugir repository provides project documentation and distribution
links. Its public [configurator source](https://github.com/Sikarugir-App/Sikarugir-foss-sources)
is a separate component. Game execution and rendering belong to
[Sikarugir's Wine tree](https://github.com/Sikarugir-App/wine) and graphics layers,
including [its DXMT tree](https://github.com/Sikarugir-App/dxmt).
The installed launcher's source has not been located in these repositories.
Improving the configurator alone does not demonstrate improved game frame times.

## Capture a baseline on macOS

Use a fixed scene, game version, resolution, quality preset, frame cap and VSync
setting. Keep foreground/background state and power mode consistent. Capture an
initial traversal and a warmed repeat separately to distinguish compilation
stutter from steady-state rendering. Record these settings with each result.

The scripts require Python 3 and the macOS system tools. No administrator access
or Xcode is needed for the resource measurements and statistical stack sampler.

```sh
python3 tools/test_profile_game.py
python3 tools/profile_game.py --scene first-stage-gameplay \
  --seconds 20 --sample-seconds 5 --output profiling-results/run-01
```

The default target is the exact process basename `Risk of Rain 2.exe`. Use
`--match` for another game and `--pid` to distinguish two copies. Ambiguous targets
fail instead of silently profiling the wrong process. Process start identity is
checked so measurements cannot mix a reused PID.

The capture produces CPU/physical-footprint measurements, whole-device GPU
counters, a stack sample, and a summary. CPU percentage uses the host's Mach
timebase: 100% means one core. GPU counters include every application on the
device. Stack samples include sleeping threads and incomplete Windows/Rosetta
unwinds; their sample counts are not measured CPU-time percentages. Sampling is
performed after the resource intervals to limit its effect on those measurements.

These tools do not measure FPS unless actual frame telemetry is collected.
Unknown Windows frames require symbols or a more suitable profiler before
attributing costs to specific Wine/graphics functions.

## Enable process-local Metal telemetry

Quit the game normally, then launch its custom Sikarugir entry with:

```sh
python3 tools/launch_profile_game.py '/path/to/wrapper.app/Contents/Game.app' \
  --output profiling-results/telemetry-launch
```

This sets Metal HUD, frame logging and shader logging variables for that launcher
process and its children. It does not edit the wrapper or alter global launchctl
settings. A wrapper may override inherited variables; verify that the HUD appears
and that telemetry events are present. Restarting an existing game is necessary
to inherit the environment. The launch helper has not yet been tested end to end
with Metal telemetry in this wrapper.

```sh
python3 tools/profile_game.py --scene first-stage-gameplay --seconds 20 \
  --metal-logs --output profiling-results/run-02
```

`metal-hud.log` is restricted to the target PID. A running collector or a file
containing only column headings is not evidence of frame telemetry. The Metal
log collector was exercised locally, but the current game was launched without
HUD logging and emitted no HUD events. Do not derive frame rates from an empty
log. Environment variables and log formats follow
[Apple's Metal HUD documentation](https://developer.apple.com/documentation/xcode/monitoring-your-metal-apps-graphics-performance).
Older HUD runtimes use the `MTL_HUD_LOGGING_ENABLED` spelling; the launch helper
sets it alongside `MTL_HUD_LOG_ENABLED`.

## Choose the improvement from evidence

| Observation in an uncapped repeatable scene | Next investigation |
|---|---|
| Lower resolution reduces GPU time and improves frame times | GPU/rendering workload and graphics backend |
| Lower resolution has little effect; a critical CPU thread stays busy | Game simulation, translation and draw submission; obtain symbols |
| Long frames correlate with shader or pipeline compilation | Cache behavior, compilation scheduling, first versus repeated traversal |
| Long frames correlate with submission or synchronization waits | DXMT command batching, drawable waits and Wine synchronization |
| Footprint growth, paging or pressure correlates with stutter | Resource lifetime and memory traffic |

A frame cap, background throttling or VSync can explain idle CPU/GPU time without
an emulation bottleneck. Change one factor at a time, repeat multiple runs, and
compare median/p95/p99 frame times, stutter and visual correctness. Only retain a
runtime patch if the measured improvement repeats without compatibility loss.

Building the current DXMT source requires Xcode with its Metal toolchain, LLVM 15,
Wine build tools and a Windows cross-compiler according to
[DXMT's build instructions](https://github.com/Sikarugir-App/dxmt/blob/main/docs/DEVELOPMENT.md).
This Mac currently has Command Line Tools only, so no replacement engine or DXMT
binary has been built or installed.

Machine traces remain in ignored `profiling-results/` directories. Raw stack and
launch logs can contain local paths; inspect them before sharing. The repository
includes only the tools and reviewed aggregate results.
