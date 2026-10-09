#!/usr/bin/env python3
"""Launch one Sikarugir custom game entry with process-local Metal telemetry.

Does not edit the wrapper or set global launchctl environment variables. Quit the
game before using this launcher: an existing process cannot inherit new variables.
"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_app", type=Path, help="Path to the game's custom .app inside its wrapper")
    parser.add_argument("--output", type=Path, required=True, help="New directory for local launch logs")
    args = parser.parse_args()
    bundle = args.game_app.resolve(strict=True)
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    entry = (bundle / "Contents/MacOS" / info["CFBundleExecutable"]).resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update({"MTL_HUD_ENABLED": "1", "MTL_HUD_LOG_ENABLED": "1",
                "MTL_HUD_LOGGING_ENABLED": "1", "MTL_HUD_LOG_SHADER_ENABLED": "1"})
    # The LOGGING spelling supports older HUD runtimes. Both are process-local.
    with (args.output / "launch.log").open("wb") as log:
        child = subprocess.Popen([str(entry)], env=env, stdout=log, stderr=log,
                                 start_new_session=True)
    (args.output / "launcher.pid").write_text(str(child.pid) + "\n")
    print(f"Started custom game launcher (PID {child.pid}). Verify that the HUD appears.")


if __name__ == "__main__":
    main()
