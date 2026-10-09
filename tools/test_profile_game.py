#!/usr/bin/env python3
"""Check CPU accounting against a separate OS clock, plus sample attribution."""
import os
import time
import unittest

from profile_game import TICK_SECONDS, frame_telemetry, loaded_components, redact_paths, usage


class ProfileTests(unittest.TestCase):
    def test_cpu_units_match_process_clock(self):
        before = usage(os.getpid())
        start = time.process_time()
        while time.process_time() - start < 0.15:
            pass
        elapsed = time.process_time() - start
        after = usage(os.getpid())
        measured = ((after.user_time - before.user_time) +
                    (after.system_time - before.system_time)) * TICK_SECONDS
        self.assertAlmostEqual(measured, elapsed, delta=0.02)
        self.assertEqual(after.start_abstime, before.start_abstime)

    def test_bad_pid_fails(self):
        with self.assertRaises(OSError):
            usage(99999999)

    def test_bridge_detection_requires_image_list(self):
        self.assertEqual(loaded_components("stack in /renderer/dxmt/"), [])
        self.assertEqual(loaded_components("Binary Images:\n/renderer/dxmt/wine/winemetal.so"),
                         ["DXMT Metal bridge"])

    def test_home_path_redaction(self):
        self.assertEqual(redact_paths(os.path.expanduser("~/test")), "~/test")

    def test_no_fps_from_empty_log_or_wrong_process(self):
        for text in ["Timestamp headings only", "wine[456:a] metal-HUD: 0,0,5,10,2"]:
            with self.subTest(text=text):
                result = frame_telemetry(text, 123)
                self.assertNotIn("frame_summary", result)
                self.assertIn("frame_validation_error", result)

    def test_verified_frames_are_included(self):
        result = frame_telemetry("wine[123:a] metal-HUD: 0,0,5,10,2", 123)
        self.assertEqual(result["frame_summary"]["fps_from_logged_intervals"], 100)


if __name__ == "__main__":
    unittest.main()
