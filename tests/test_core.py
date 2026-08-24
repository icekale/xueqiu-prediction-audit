#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(HERE))
import audit_core as core  # noqa: E402


class DirectionTests(unittest.TestCase):
    def test_long(self):
        self.assertEqual(core.direction_label(1, 12), "对")
        self.assertEqual(core.direction_label(1, 0), "平")
        self.assertEqual(core.direction_label(1, -7), "偏错")
        self.assertEqual(core.direction_label(1, -20), "错")

    def test_short(self):
        self.assertEqual(core.direction_label(-1, -12), "对")
        self.assertEqual(core.direction_label(-1, 0), "平")
        self.assertEqual(core.direction_label(-1, 7), "偏错")
        self.assertEqual(core.direction_label(-1, 20), "错")


class WindowTests(unittest.TestCase):
    def test_window(self):
        series = [
            (date(2024, 1, 2), 100, 101, 99, 100),
            (date(2024, 2, 1), 108, 110, 107, 110),
            (date(2024, 7, 1), 90, 92, 88, 90),
        ]
        stats = core.window_stats(series, date(2024, 1, 2), 8, date(2024, 8, 1))
        self.assertIsNotNone(stats)
        self.assertEqual(stats["window"]["ret"], -10.0)
        self.assertEqual(core.direction_label(1, stats["window"]["ret"]), "偏错")


class BundleTests(unittest.TestCase):
    def test_example_scorecard_renders(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "metalslime_scorecard.json"
        sc = json.loads(path.read_text(encoding="utf-8"))
        html = core.render_html(sc)
        self.assertIn("药神公开预测审计", html)
        self.assertIn("14 / 16", html)
        self.assertNotIn("v1", html)
        self.assertNotIn("v2", html)


if __name__ == "__main__":
    unittest.main()
