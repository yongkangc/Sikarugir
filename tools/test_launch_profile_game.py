import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from launch_profile_game import main, running_launch_targets


class LaunchTests(unittest.TestCase):
    def test_windows_steam_blocks_environment_handoff(self):
        rows = "11 C:\\Program Files (x86)\\Steam\\Steam.exe\n12 /Applications/Steam.app/Steam"
        self.assertEqual(running_launch_targets(rows, "Risk of Rain 2.exe"), [(11, "Steam.exe")])

    def test_exact_game_basename_case_and_spaces(self):
        rows = "13 C:\\Games\\RISK OF RAIN 2.EXE\n14 C:\\Games\\Risk of Rain 2.exe.helper\n"
        self.assertEqual(running_launch_targets(rows, "Risk of Rain 2.exe"),
                         [(13, "RISK OF RAIN 2.EXE")])

    def test_launcher_passes_frame_environment_to_real_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "Test Game.app"
            macos = bundle / "Contents/MacOS"
            macos.mkdir(parents=True)
            (bundle / "Contents/Info.plist").write_bytes(
                plistlib.dumps({"CFBundleExecutable": "entry"}))
            entry = macos / "entry"
            entry.write_text(f"#!{sys.executable}\nimport json, os\n" +
                             "print(json.dumps({k: os.environ[k] for k in " +
                             "['MTL_HUD_ENABLED', 'MTL_HUD_LOG_ENABLED', " +
                             "'MTL_HUD_LOGGING_ENABLED', 'MTL_HUD_LOG_SHADER_ENABLED']}))\n")
            entry.chmod(0o700)
            real_popen = subprocess.Popen
            children = []
            def start(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child
            for shader_logs in [False, True]:
                output = root / ("shaders" if shader_logs else "frames")
                argv = ["launch_profile_game.py", str(bundle), "--output", str(output)]
                if shader_logs:
                    argv.append("--shader-logs")
                with patch.object(sys, "argv", argv), \
                     patch("launch_profile_game.subprocess.run", return_value=type("PS", (), {"stdout": ""})()), \
                     patch("launch_profile_game.subprocess.Popen", side_effect=start), \
                     contextlib.redirect_stdout(io.StringIO()):
                    main()
                self.assertEqual(children[-1].wait(timeout=5), 0)
                env = json.loads((output / "launch.log").read_text())
                self.assertEqual(env, {"MTL_HUD_ENABLED": "1", "MTL_HUD_LOG_ENABLED": "1",
                                       "MTL_HUD_LOGGING_ENABLED": "1",
                                       "MTL_HUD_LOG_SHADER_ENABLED": "1" if shader_logs else "0"})
                self.assertTrue((output / "launcher.pid").exists())


if __name__ == "__main__":
    unittest.main()
