# Configurator patch

`configurator-performance.patch` targets
[Sikarugir-foss-sources](https://github.com/Sikarugir-App/Sikarugir-foss-sources)
at `4be1b048f8df14b073a6e39e8245bbb52c6a71c0` (Configure 1.0.4).

From a clean checkout at that revision:

```sh
git apply --check /path/to/configurator-performance.patch
git apply /path/to/configurator-performance.patch
python3 Tests/run_performance_checks.py
python3 Tests/profile_winetricks.py
```

The latter can collect CPU samples with `--profile-directory /path/to/local/profiles`.
Tests use Command Line Tools and compile isolated production methods. They do
not launch Wine or modify a wrapper. The optional `--wrapper-drive` argument to
the first test script performs read-only filename enumeration.

The source carries its existing LGPL notices. This patch does not modify or
distribute game files, Wine/DXMT binaries or Apple's graphics libraries.

## DXMT HUD formatting candidate

`dxmt-hud-formatting.patch` guards per-frame debug text formatting with
`DXMT_DEBUG`, matching the existing guard inside the HUD sink. It applies to the
installed version stamp's source revision
`7c8dee1c2d73415301ceb7d1fa810861cef4cd67` and the inspected newer revision
`e94c312f5c054263acf261cfa109edf13e757587` of
[Sikarugir-App/dxmt](https://github.com/Sikarugir-App/dxmt). It preserves that source's
LGPL-2.1-or-later license. No runtime binary is included or installed.

From that DXMT checkout:

```sh
git apply --check /path/to/Sikarugir/patches/dxmt-hud-formatting.patch
git apply /path/to/Sikarugir/patches/dxmt-hud-formatting.patch
python3 /path/to/Sikarugir/tools/profile_dxmt_hud.py --source .
```

The probe compiles the actual `UpdateStatistics` method and statistics headers
with a substitute HUD sink, checks identical debug output for 27 cases, and
measures the non-debug path in alternating passes. It needs a C++20 compiler
with `std::format`. With older Apple Command Line Tools, `--experimental-format`
enables experimental libc++ formatting without exceptions for valid inputs.
The probe uses x86-64 by default; `--arch arm64` is also available.

This isolated validation does not replace a full DXMT build or game testing.
The local saving was approximately 0.94 microseconds per call, far below the
observed 10 ms presentation interval. It is a small cleanup rather than an
explanation for the game's performance limit.
