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


class CubeMathTests(unittest.TestCase):
    def test_boyan_formulas(self):
        days = (date(2026, 8, 24) - date(2023, 9, 18)).days
        self.assertEqual(days, 1071)
        self.assertAlmostEqual(core.annualized_pct(319.96, days), 63.13, places=1)
        self.assertEqual(core.wealth_multiple(319.96, 95.56), 2.15)
        self.assertEqual(core.wealth_multiple(319.96, 78.54), 2.35)
        self.assertAlmostEqual(319.96 - 95.56, 224.40, places=2)
        self.assertAlmostEqual(319.96 - 78.54, 241.42, places=2)

    def test_short_window_has_no_ann(self):
        self.assertIsNone(core.annualized_pct(31.89, 103))

    def test_aligned_pair_shifts_late_bench(self):
        cube = [(date(2019, 7, 11), 1.0), (date(2020, 1, 2), 3.0), (date(2020, 11, 6), 6.0)]
        bench = [(date(2020, 1, 2), 100.0), (date(2020, 11, 6), 141.36)]
        pair = core.aligned_pair(cube, bench, date(2019, 7, 11), date(2020, 11, 6))
        self.assertIsNotNone(pair)
        cube_path, bench_path = pair
        self.assertEqual(cube_path["from"], "2020-01-02")
        self.assertEqual(cube_path["ret"], 100.0)
        self.assertEqual(bench_path["ret"], 41.36)

    def test_analyze_and_render(self):
        nav = [(date(2023, 9, 18), 1.0)]
        for i in range(1, 8):
            nav.append((date(2023, 9, 18 + i), 1.0 + i * 0.01))
        nav.append((date(2026, 8, 24), 4.1996))
        qqq = [(date(2023, 9, 18), 100.0), (date(2026, 8, 24), 195.56)]
        spy = [(date(2023, 9, 18), 100.0), (date(2026, 8, 24), 178.54)]
        cube = core.analyze_cube(
            {"symbol": "ZH1", "name": "伯言-美股", "market": "us", "description": ""},
            nav,
            {"QQQ": ("QQQ", qqq), "SPY": ("SPY", spy)},
            asof=date(2026, 8, 24),
        )
        self.assertEqual(cube["ret"], 319.96)
        self.assertAlmostEqual(cube["ann"], 63.13, places=1)
        self.assertEqual(cube["headline"], "伯言-美股的超额确实很强")
        html = core.render_cubes_html({"title": "组合量化", "account": "伯言", "cubes": [cube]})
        self.assertIn("+319.96%", html)
        self.assertIn("2.15", html)
        self.assertNotIn("v1", html)


class DraftTests(unittest.TestCase):
    def test_draft_keeps_direction_drops_mood(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "draft_posts.json"
        posts = core.normalize_posts(json.loads(path.read_text(encoding="utf-8")))
        payload = core.draft_candidates(posts)
        ids = [c["id"] for c in payload["calls"]]
        self.assertEqual(ids, ["in-baotuan", "in-housing", "in-tactical"])
        first = payload["calls"][0]
        self.assertEqual(first["symbol"], "SZ399997")
        self.assertEqual(first["side"], -1)
        self.assertEqual(first["horizon_m"], 60)
        self.assertEqual(first["kind"], "structure")
        self.assertTrue(payload["draft"])
        tactical = payload["calls"][2]
        self.assertEqual(tactical["kind"], "tactical")
        self.assertEqual(tactical["symbol"], "SH000300")

    def test_validate_rejects_draft_and_accepts_mini(self):
        draft = core.draft_candidates(
            [{"created_str": "2024-02-05", "text": "看多 $沪深300(SH000300)$", "id": "x"}]
        )
        self.assertTrue(core.validate_calls(draft))
        mini = json.loads(
            (Path(__file__).resolve().parents[1] / "examples" / "mini_calls.json").read_text(encoding="utf-8")
        )
        self.assertEqual(core.validate_calls(mini), [])
        bundled = json.loads(
            (Path(__file__).resolve().parents[1] / "examples" / "metalslime_calls.json").read_text(encoding="utf-8")
        )
        self.assertEqual(core.validate_calls(bundled), [])


class CubeWindowTests(unittest.TestCase):
    def test_custom_window_uses_overlap(self):
        nav = [
            (date(2019, 7, 11), 1.0),
            (date(2020, 1, 2), 3.0),
            (date(2020, 6, 1), 4.0),
            (date(2020, 11, 6), 6.0),
            (date(2020, 11, 7), 6.1),
        ]
        cube = core.analyze_cube(
            {"symbol": "ZH1", "name": "演示", "market": "cn", "description": "不建议跟票"},
            nav,
            {"SH000300": ("沪深300", [(date(2020, 1, 2), 100.0), (date(2020, 11, 6), 129.07)])},
            asof=date(2026, 8, 24),
            window_start=date(2020, 1, 2),
            window_end=date(2020, 11, 6),
        )
        self.assertEqual(cube["from"], "2020-01-02")
        self.assertEqual(cube["to"], "2020-11-06")
        self.assertEqual(cube["ret"], 100.0)
        self.assertTrue(cube["custom_window"])
        self.assertTrue(cube["paper_only"])
        self.assertIn("指定观察期", cube["blurb"])


class BundleTests(unittest.TestCase):
    def test_example_scorecard_renders(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "metalslime_scorecard.json"
        sc = json.loads(path.read_text(encoding="utf-8"))
        html = core.render_html(sc)
        self.assertIn("药神公开预测审计", html)
        self.assertIn("14 / 16", html)
        self.assertNotIn("v1", html)
        self.assertNotIn("v2", html)

    def test_example_cubes_render(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "metalslime_cubes.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        html = core.render_cubes_html(payload)
        self.assertIn("大票为主", html)
        self.assertIn("公开模拟盘", html)
        self.assertNotIn("v1", html)


if __name__ == "__main__":
    unittest.main()
