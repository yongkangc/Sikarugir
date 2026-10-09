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
