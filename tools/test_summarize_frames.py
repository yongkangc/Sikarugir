import unittest
from summarize_frames import summarize


class FrameTests(unittest.TestCase):
    def test_documented_pair_layout_and_duplicates(self):
        line = "wine[123:abcdef] metal-HUD: 10,0,500,10,2,20,4\n"
        result = summarize(line + line)
        self.assertEqual(result["logged_frames"], 2)
        self.assertAlmostEqual(result["fps_from_logged_intervals"], 1000 / 15)
        self.assertEqual(result["gpu_ms_mean"], 3)
        self.assertEqual(result["presentation_ms_median"], 15)

    def test_mixed_processes_fail(self):
        with self.assertRaises(ValueError):
            summarize("wine[1:a] metal-HUD: 0,0,5,10,2\nwine[2:b] metal-HUD: 0,0,5,10,2")

    def test_empty_clipped_and_nonfinite_data_fail(self):
        for text in ["Timestamp headings only", "wine[1:a] metal-HUD: 0,0,5,10",
                     "wine[1:a] metal-HUD: 0,0,5,nan,2", "wine[1:a] metal-HUD: 0,0,5,0,2"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                summarize(text)

    def test_conflicting_duplicate_fails(self):
        with self.assertRaises(ValueError):
            summarize("wine[1:a] metal-HUD: 0,0,5,10,2\nwine[1:a] metal-HUD: 0,0,5,10,3")

    def test_explicitly_exclude_a_disputed_boundary(self):
        line = "wine[1:a] metal-HUD: 0,0,5," + ",".join(["10,2"] * 200)
        result = summarize(line + "\nwine[1:a] metal-HUD: 199,0,5,20,3", exclude_ambiguous=True)
        self.assertEqual(result["logged_frames"], 199)
        self.assertEqual(result["excluded_ambiguous_frame_markers"], 1)
        self.assertEqual(result["fps_from_logged_intervals"], 100)


if __name__ == "__main__":
    unittest.main()
