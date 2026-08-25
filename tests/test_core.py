#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(HERE))
import audit_core as core  # noqa: E402
import vpush_xueqiu as vpush  # noqa: E402


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

    def test_extract_quad_splits_stock_side_price_time(self):
        quad = core.extract_quad("见底时茅台 1350–1400，维持看多 $贵州茅台(SH600519)$，一年内完成")
        self.assertEqual(quad["side"], 1)
        self.assertEqual(quad["symbols"][0][0], "SH600519")
        self.assertEqual(quad["price_target"]["lo"], 1350)
        self.assertEqual(quad["price_target"]["hi"], 1400)
        self.assertEqual(quad["horizon_m"], 12)
        self.assertTrue(quad["horizon_explicit"])
        self.assertEqual(quad["quad"], {"stock": True, "direction": True, "price": True, "time": True})
        self.assertFalse(quad["needs_llm"])

    def test_extract_quad_keeps_index_level_and_ignores_years(self):
        quad = core.extract_quad("逢低上证券ETF，下次发力带大盘过3721，看多 $上证指数(SH000001)$")
        self.assertEqual(quad["side"], 1)
        self.assertEqual(quad["price_target"]["lo"], 3721)
        self.assertEqual(quad["price_target"]["symbol"], "SH000001")
        years = core.extract_quad("2024-2025 还是那套框架，供需自己看")
        self.assertIsNone(years["price_target"])
        self.assertFalse(years["quad"]["direction"])

    def test_draft_uses_parent_for_fragment_and_flags_llm(self):
        payload = core.draft_candidates(
            [
                {
                    "id": "c-9",
                    "created_str": "2021-03-16",
                    "source": "comment",
                    "text": "维持看空",
                    "parent_text": "茅台是不是还能涨 $贵州茅台(SH600519)$",
                }
            ]
        )
        row = payload["calls"][0]
        self.assertEqual(row["symbol"], "SH600519")
        self.assertEqual(row["side"], -1)
        self.assertTrue(row["needs_llm"])
        self.assertTrue(row["quad"]["stock"])
        self.assertTrue(row["quad"]["direction"])
        self.assertFalse(row["quad"]["price"])
        self.assertIn("四元组", payload["note"])

    def test_draft_attaches_price_target_without_implying_hit(self):
        payload = core.draft_candidates(
            [
                {
                    "id": "moutai-pt",
                    "created_str": "2022-10-31",
                    "text": "见底时茅台 1350-1400，维持看多 $贵州茅台(SH600519)$",
                }
            ]
        )
        row = payload["calls"][0]
        self.assertEqual(row["price_target"]["lo"], 1350)
        self.assertEqual(row["price_target"]["hi"], 1400)
        self.assertIn("价位另判", row["note"])
        self.assertTrue(row["draft"])


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


class VpushBridgeTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpobj = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpobj.cleanup)
        self._tmpdir = self._tmpobj.name

    def _tmp(self) -> str:
        return self._tmpdir

    def test_normalize_xueqiu_id(self):
        self.assertEqual(vpush.normalize_xueqiu_id("https://xueqiu.com/u/4514680565"), "4514680565")
        self.assertEqual(vpush.normalize_xueqiu_id("https://xueqiu.com/u/4514680565/"), "4514680565")
        self.assertEqual(vpush.normalize_xueqiu_id("https://www.xueqiu.com/u/123?foo=bar"), "123")
        self.assertEqual(vpush.normalize_xueqiu_id("4514680565"), "4514680565")
        self.assertEqual(vpush.normalize_xueqiu_id("https://xueqiu.com/Syedc"), "https://xueqiu.com/Syedc")
        self.assertEqual(vpush.normalize_xueqiu_id(""), "")
        self.assertEqual(vpush.normalize_xueqiu_id(None), "")

    def test_merge_waf_cookie_uses_sidecar_when_seed_matches(self):
        login = "xq_a_token=abc; u=123"
        waf = Path(self._tmp()) / "waf_cookies.json"
        waf.write_text(
            json.dumps(
                {
                    "fetched_at": 1,
                    "seed_sha256": vpush.cookie_sha256(login),
                    "cookies": [
                        {"name": "acw_tc", "value": "NEW_ACW"},
                        {"name": "u", "value": "999"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        merged = vpush.merge_waf_cookie(login, waf)
        self.assertIn("acw_tc=NEW_ACW", merged)
        self.assertIn("u=999", merged)
        self.assertNotIn("xq_a_token=abc", merged)

    def test_merge_waf_cookie_keeps_new_login_when_seed_stale(self):
        waf = Path(self._tmp()) / "waf_cookies.json"
        waf.write_text(
            json.dumps(
                {
                    "seed_sha256": vpush.cookie_sha256("xq_a_token=old"),
                    "cookies": [{"name": "acw_tc", "value": "challenge"}],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(vpush.merge_waf_cookie("xq_a_token=new", waf), "xq_a_token=new")

    def test_merge_waf_cookie_uses_sidecar_without_login(self):
        waf = Path(self._tmp()) / "waf_cookies.json"
        waf.write_text(
            json.dumps({"cookies": [{"name": "xq_a_token", "value": "from-bot"}]}),
            encoding="utf-8",
        )
        self.assertEqual(vpush.merge_waf_cookie("", waf), "xq_a_token=from-bot")

    def test_merge_waf_cookie_missing_falls_back(self):
        missing = Path(self._tmp()) / "nope.json"
        self.assertEqual(vpush.merge_waf_cookie("xq_a_token=keep", missing), "xq_a_token=keep")

    def test_parse_waf_json_and_prefer_full_text(self):
        raw = json.dumps(
            {
                "fetched_at": 10,
                "cookies": [{"name": "xq_a_token", "value": "tok"}, {"name": "u", "value": "1"}],
            }
        )
        cookie, sidecar = vpush.parse_cookie_payload(raw)
        self.assertEqual(cookie, "xq_a_token=tok; u=1")
        self.assertIsNotNone(sidecar)
        long_text = "完整长文" * 20
        body = vpush.prefer_full_text({"description": "开头…", "text": long_text})
        self.assertEqual(body, long_text)
        self.assertEqual(vpush.prefer_full_text({"description": "短的", "text": ""}), "短的")

    def test_classify_and_waf_html(self):
        self.assertEqual(vpush.classify_status({"description": "原创"}), "post")
        self.assertIsNone(vpush.classify_status({"description": "转发", "retweeted_status": {"id": 1}}))
        self.assertEqual(
            vpush.classify_status({"description": "回复 @foo ", "commentId": 9}),
            "reply",
        )
        self.assertTrue(vpush.is_waf_html("<html>aliyun_waf</html>"))
        self.assertTrue(vpush.is_waf_html("<html>var renderData = {}</html>", "text/html"))
        self.assertFalse(vpush.is_waf_html('{"statuses":[]}'))

    def test_sidecar_status_never_prints_secrets(self):
        secret = "xq_a_token=SUPERSECRET"
        waf = Path(self._tmp()) / "waf_cookies.json"
        waf.write_text(
            json.dumps(
                {
                    "fetched_at": 1,
                    "seed_sha256": vpush.cookie_sha256(secret),
                    "cookies": [{"name": "xq_a_token", "value": "SUPERSECRET"}],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(vpush, "find_sidecar", return_value=waf):
            status = vpush.sidecar_status(secret)
        self.assertIn("seed_match=yes", status)
        self.assertNotIn("SUPERSECRET", status)
        self.assertNotIn("xq_a_token=", status)

    def test_slim_status_uses_full_text(self):
        long_text = "完整正文超过截断"
        slim = core.slim_status(
            {
                "id": 1,
                "created_at": 1700000000000,
                "description": "开头…",
                "text": long_text,
                "user": {"screen_name": "foo"},
            }
        )
        self.assertEqual(slim["text"], long_text)
        self.assertEqual(slim["post_type"], "post")

    def test_fetch_rejects_non_numeric_uid(self):
        import xueqiu_audit

        self.assertEqual(xueqiu_audit.main(["fetch", "https://xueqiu.com/Syedc"]), 2)


class DeepCorpusTests(unittest.TestCase):
    def test_prefer_full_text_uses_longer_body(self):
        body = vpush.prefer_full_text(
            {"description": "截断摘要", "text": "这是完整长文，比摘要长很多，应作为正文。"}
        )
        self.assertIn("完整长文", body)

    def test_slim_status_keeps_url_counts_and_symbols(self):
        slim = core.slim_status(
            {
                "id": 88,
                "created_at": 1700000000000,
                "title": "看空茅台",
                "description": "维持看空 $贵州茅台(SH600519)$",
                "text": "维持看空 $贵州茅台(SH600519)$ 五年内",
                "target": "/2292705444/88",
                "reply_count": 12,
                "like_count": 3,
                "view_count": 100,
                "commentId": 0,
                "user": {"id": 2292705444, "screen_name": "药神"},
            }
        )
        self.assertEqual(slim["url"], "https://xueqiu.com/2292705444/88")
        self.assertEqual(slim["comment_count"], 12)
        self.assertEqual(slim["user_id"], "2292705444")
        self.assertIn("SH600519", [s for s, _ in slim["symbols"]])

    def test_slim_comment_marks_author_and_parent(self):
        comment = vpush.slim_comment(
            {
                "id": 9,
                "status_id": 88,
                "text": "看空 $贵州茅台(SH600519)$",
                "user_id": 2292705444,
                "user": {"id": 2292705444, "screen_name": "药神"},
                "created_at": 1700000001000,
                "reply_comment": {
                    "text": "现在可以买吗",
                    "user": {"screen_name": "球迷"},
                },
            },
            author_uid="2292705444",
        )
        self.assertTrue(comment["is_author"])
        self.assertEqual(comment["parent_text"], "现在可以买吗")
        self.assertEqual(comment["parent_user"], "球迷")

    def test_author_comments_enter_draft_corpus(self):
        posts = [
            {
                "id": 88,
                "created_str": "2021-03-15",
                "text": "随便聊聊天气",
                "user": "药神",
            }
        ]
        comments = [
            {
                "id": 9,
                "status_id": 88,
                "created_str": "2021-03-16",
                "text": "维持看空 $中证白酒(SZ399997)$",
                "user": "药神",
                "user_id": "2292705444",
                "is_author": True,
                "parent_text": "茅台是不是还能涨",
            },
            {
                "id": 10,
                "status_id": 88,
                "created_str": "2021-03-16",
                "text": "看多 $中证白酒(SZ399997)$",
                "user": "球迷",
                "user_id": "1",
                "is_author": False,
            },
        ]
        corpus = core.build_audit_corpus(posts, comments, author_uid="2292705444")
        ids = [row["id"] for row in corpus]
        self.assertIn("c-9", ids)
        self.assertNotIn("c-10", ids)
        author = next(row for row in corpus if row["id"] == "c-9")
        self.assertEqual(author["source"], "comment")
        self.assertIn("回复", author["text"])
        payload = core.draft_candidates(corpus)
        self.assertEqual(payload["calls"][0]["source_id"], "c-9")
        self.assertIn("评论", payload["calls"][0]["note"])

    def test_select_comment_targets_prefers_busy_posts(self):
        posts = [
            {"id": 1, "comment_count": 0, "created_at": 3},
            {"id": 2, "comment_count": 40, "created_at": 1},
            {"id": 3, "comment_count": 8, "created_at": 2},
        ]
        picked = [p["id"] for p in vpush.select_comment_targets(posts, limit=2)]
        self.assertEqual(picked, [2, 3])

    def test_fetch_comments_paginates_and_stops(self):
        pages = {
            "1": {
                "comments": [
                    {
                        "id": 1,
                        "text": "问",
                        "user_id": 1,
                        "user": {"id": 1, "screen_name": "fan"},
                        "created_at": 1,
                    }
                ]
            },
            "2": {
                "comments": [
                    {
                        "id": 2,
                        "text": "看空茅台",
                        "user_id": 2292705444,
                        "user": {"id": 2292705444, "screen_name": "药神"},
                        "created_at": 2,
                        "reply_comment": {"text": "问", "user": {"screen_name": "fan"}},
                    }
                ]
            },
            "3": {"comments": []},
        }

        def get_json(url, headers=None):
            import urllib.parse

            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return pages[qs["page"][0]]

        items = core.fetch_status_comments(
            "88",
            cookie="xq_a_token=x",
            author_uid="2292705444",
            max_pages=5,
            pause=0,
            get_json=get_json,
        )
        self.assertEqual([c["id"] for c in items], [1, 2])
        self.assertTrue(items[1]["is_author"])


class PersonaTests(unittest.TestCase):
    def _row(self, **kw):
        row = {
            "date": "2024-01-02",
            "side": 1,
            "symbol": "SH000300",
            "kind": "structure",
            "theme": "看多沪深300",
            "dir_window": "对",
            "copy_window": 12,
            "copy_todate": 10,
            "giveback": -8,
        }
        row.update(kw)
        return row

    def test_marks_draft_when_sample_is_thin(self):
        sc = {"rows": [self._row()], "summary": {"n": 1, "structure": {"n": 1, "dir": {"对": 1}}, "tactical": {}, "price_targets": []}}
        persona = core.auto_persona(sc)
        self.assertTrue(persona["draft"])
        self.assertIn("不是人格测写", persona["headline"])
        self.assertLessEqual(len(persona["traits"]), 5)

    def test_flags_flips_round_numbers_and_giveback(self):
        rows = [
            self._row(date="2024-02-05", symbol="SZ300308", theme="开仓中际做反弹", kind="tactical"),
            self._row(date="2024-11-25", symbol="SZ300308", theme="明年硅光机会很大", kind="structure"),
            self._row(date="2025-02-04", symbol="SH000688", theme="清仓科创", side=-1, kind="tactical", dir_window="错"),
            self._row(date="2025-05-07", symbol="SH000688", theme="科创即将历史新高", kind="tactical"),
            self._row(date="2025-07-28", symbol="SZ300308", theme="光模块三浪", kind="structure"),
            self._row(date="2025-11-03", symbol="SH688256", theme="百倍寒王一定能看到", kind="structure", dir_window="错", copy_window=-28),
            self._row(date="2026-02-04", symbol="SH000688", theme="结束征程清仓，后来承认卖飞", side=-1, kind="tactical", dir_window="错"),
            self._row(
                date="2025-04-13",
                symbol="SH588200",
                theme="加仓科创芯片ETF",
                kind="tactical",
                dir_window="对",
                copy_window=58,
                copy_todate=-27,
                giveback=-78,
            ),
        ]
        sc = {
            "coverage": "full",
            "rows": rows,
            "summary": {
                "n": len(rows),
                "structure": {"n": 3, "dir": {"对": 2, "错": 1}, "copy_window_median": 20},
                "tactical": {"n": 5, "dir": {"对": 2, "错": 3}, "copy_window_median": 2},
                "price_targets": [{"label": "百倍寒王", "verdict": "窗口不足"}],
            },
        }
        persona = core.auto_persona(sc)
        self.assertFalse(persona["draft"])
        blob = persona["headline"] + " " + " ".join(t["evidence"] for t in persona["traits"])
        self.assertTrue(any("翻案" in t["name"] or "翻案" in t["evidence"] for t in persona["traits"]))
        self.assertTrue(any("十倍" in blob or "百倍" in blob or "数量级" in t["name"] for t in persona["traits"]))
        self.assertTrue(any("回撤" in t["name"] or "拿不住" in t["name"] or "回撤" in t["evidence"] for t in persona["traits"]))
        self.assertNotRegex(blob, r"大五人格|星座|心理诊断")
        self.assertEqual(persona["level"], "portrait")
        self.assertIn("不是人格测写", persona["headline"])

    def test_profile_requires_four_years_and_twenty_calls(self):
        rows = [self._row(date=f"202{4 + i // 8}-0{1 + i % 8}-01", symbol=f"S{i:02d}") for i in range(20)]
        portrait = core.auto_persona(
            {"coverage": "full", "rows": rows[:12], "summary": {"n": 12, "structure": {}, "tactical": {}, "price_targets": []}}
        )
        self.assertEqual(portrait["level"], "portrait")
        self.assertIn("不是人格测写", portrait["headline"])
        self.assertNotIn("人格侧写", portrait["headline"])
        profile_rows = [
            self._row(date=f"{2019 + i // 3}-0{1 + (i % 3)}-01", symbol=f"P{i:02d}") for i in range(20)
        ]
        profile = core.auto_persona(
            {"coverage": "full", "rows": profile_rows, "summary": {"n": 20, "structure": {}, "tactical": {}, "price_targets": []}}
        )
        self.assertEqual(profile["level"], "profile")
        self.assertIn("人格侧写", profile["headline"])

    def test_render_includes_persona_module(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "metalslime_scorecard.json"
        sc = json.loads(path.read_text(encoding="utf-8"))
        html = core.render_html(sc)
        self.assertIn("行为画像", html)
        self.assertIn("不是心理诊断", html)
        self.assertIn("表述对照", html)
        self.assertIn("不是测谎", html)
        self.assertIn("MBTI", html)
        self.assertIn("不是量表", html)
        self.assertNotIn("测谎仪", html)
        self.assertNotIn("星座", html)

    def test_consistency_flags_unexplained_flip_and_admission(self):
        rows = [
            self._row(date="2025-03-04", symbol="SH601689", theme="机器人领头羊", side=1),
            self._row(date="2025-10-14", symbol="SH601689", theme="机器人全部卖出", side=-1, kind="tactical"),
            self._row(date="2026-02-04", symbol="SH000688", theme="清仓科创", side=-1, kind="tactical", dir_window="错"),
            self._row(date="2026-08-03", symbol="SH000688", theme="本轮卖飞，纠错能力差", side=-1, kind="tactical", dir_window="平"),
        ]
        sc = {"rows": rows, "summary": {"n": 4, "structure": {}, "tactical": {}, "price_targets": []}}
        check = core.auto_consistency(sc)
        blob = json.dumps(check, ensure_ascii=False)
        self.assertTrue(any(i.get("verdict") == "对不上" for i in check["items"]))
        self.assertTrue(any(i.get("verdict") == "对得上" and "卖飞" in i.get("claim", "") for i in check["items"]))
        self.assertNotIn("测谎", check["headline"])
        self.assertIn("不是测谎", check["note"])
        self.assertNotIn("撒谎", blob)

    def test_consistency_marks_early_claim_after_the_fact(self):
        rows = [self._row(date="2025-03-16", symbol="SH000688", theme="科创还能翻倍")]
        posts = [
            {
                "created_str": "2026-06-29 10:00:00",
                "text": "两年不到，当初没人信啊。科创50见底之前的月线10连阴",
            }
        ]
        check = core.auto_consistency({"rows": rows, "summary": {"n": 1}}, posts)
        self.assertTrue(any(i.get("kind") == "事后叙事" for i in check["items"]))
        self.assertTrue(any("当初没人信" in (i.get("claim") or "") for i in check["items"]))

    def test_consistency_skips_casual_i_said_so(self):
        rows = [self._row(date="2025-03-16", symbol="SH000688", theme="科创还能翻倍")]
        posts = [
            {
                "created_str": "2026-08-08 10:00:00",
                "text": "回复 @某人 : 你天天问一堆我说过好多次的东西，就不会翻翻帖子？",
            }
        ]
        check = core.auto_consistency({"rows": rows, "summary": {"n": 1}}, posts)
        self.assertFalse(any(i.get("kind") == "事后叙事" for i in check["items"]))

    def test_mbti_draft_when_sample_is_thin(self):
        mbti = core.auto_mbti({"rows": [self._row()], "summary": {"n": 1, "structure": {}, "tactical": {}, "price_targets": []}})
        self.assertTrue(mbti["draft"])
        self.assertFalse(mbti.get("type"))
        self.assertIn("不是量表", mbti["headline"] + mbti["note"])
        self.assertEqual(len(mbti.get("axes") or []), 0)

    def test_mbti_reads_public_axes(self):
        rows = [
            self._row(date="2024-02-05", symbol="SZ300308", theme="开仓中际做反弹", kind="tactical"),
            self._row(date="2024-11-25", symbol="SZ300308", theme="明年硅光周期浪潮", kind="structure"),
            self._row(date="2025-02-04", symbol="SH000688", theme="清仓科创", side=-1, kind="tactical", dir_window="错"),
            self._row(date="2025-05-07", symbol="SH000688", theme="科创即将历史新高", kind="tactical"),
            self._row(date="2025-07-28", symbol="SZ300308", theme="光模块三浪业绩主升", kind="structure"),
            self._row(date="2025-11-03", symbol="SH688256", theme="百倍寒王一定能看到", kind="structure", dir_window="错"),
            self._row(date="2026-02-04", symbol="SH000688", theme="结束征程清仓，后来承认卖飞", side=-1, kind="tactical", dir_window="错"),
            self._row(date="2025-04-13", symbol="SH588200", theme="加仓科创芯片ETF", kind="tactical", dir_window="对", copy_window=58, giveback=-78),
        ]
        posts = [{"text": "回复 @球友 : 老登又来对线"}] * 12 + [{"text": "硅光产业链，业绩浪"}] * 3
        sc = {
            "coverage": "full",
            "rows": rows,
            "summary": {
                "n": len(rows),
                "structure": {"n": 3, "dir": {"对": 2, "错": 1}},
                "tactical": {"n": 5, "dir": {"对": 2, "错": 3}},
                "price_targets": [],
            },
        }
        mbti = core.auto_mbti(sc, posts)
        blob = json.dumps(mbti, ensure_ascii=False)
        self.assertFalse(mbti["draft"])
        self.assertEqual(len(mbti["type"]), 4)
        self.assertTrue(mbti["type"].isalpha())
        self.assertEqual(len(mbti["axes"]), 4)
        self.assertTrue(all(axis.get("evidence") for axis in mbti["axes"]))
        self.assertEqual(mbti["type"][0], "E")
        self.assertEqual(mbti["type"][3], "P")
        self.assertIn("不是量表", mbti["note"])
        self.assertNotRegex(blob, r"星座|人格障碍|临床诊断")


class BundleTests(unittest.TestCase):
    def test_example_scorecard_renders(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "metalslime_scorecard.json"
        sc = json.loads(path.read_text(encoding="utf-8"))
        html = core.render_html(sc)
        self.assertIn("药神公开预测审计", html)
        self.assertIn("14 / 16", html)
        self.assertIn("行为画像", html)
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
