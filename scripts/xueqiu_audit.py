#!/usr/bin/env python3
"""开箱即用的公开预测审计 CLI。Cookie 只读写本地配置，绝不打印。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import audit_core as core  # noqa: E402
import vpush_xueqiu as vpush  # noqa: E402


def out_dir(path: str | None, default: str) -> Path:
    dest = Path(path or default).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("python", sys.version.split()[0])
    print("skill", ROOT)
    login = core.read_login_cookie()
    print("cookie", core.cookie_status(core.read_cookie()))
    print("cookie_file", core.COOKIE_PATH if core.COOKIE_PATH.exists() else "not created")
    print("waf_sidecar", core.sidecar_status(login))
    chrome = core.chrome_bin()
    print("chrome", chrome or "missing（HTML 仍可用，PDF 需要 Chrome）")
    extras = []
    for name in ("browser_cookie3",):
        try:
            __import__(name)
            extras.append(f"{name}=yes")
        except ImportError:
            extras.append(f"{name}=no")
    print("optional", " ".join(extras))
    try:
        import ai_profile as aip

        print("llm", aip.llm_status())
    except Exception:
        print("llm", "none")
    print()
    print("下一步（任选一条）：")
    print("  python3 scripts/xueqiu_audit.py example")
    print("  python3 scripts/xueqiu_audit.py cubes --example")
    print("  python3 scripts/xueqiu_audit.py profile --example")
    print("  python3 scripts/xueqiu_audit.py cookie")
    print("  python3 scripts/xueqiu_audit.py cookie --from-file waf_cookies.json")
    print("  python3 scripts/xueqiu_audit.py fetch 2292705444")
    print("  python3 scripts/xueqiu_audit.py import-posts posts.json")
    print("  python3 scripts/xueqiu_audit.py profile work/2292705444")
    print("  python3 scripts/xueqiu_audit.py draft work/2292705444/posts.json")
    print("深语料：有 sidecar / 登录态再跑默认 fetch（deep），作者评论才会进 posts.json")
    return 0


def cmd_cookie(args: argparse.Namespace) -> int:
    if args.from_file:
        raw = Path(args.from_file).expanduser().read_text(encoding="utf-8", errors="replace").strip()
        cookie, sidecar = vpush.parse_cookie_payload(raw)
        if not cookie:
            print("文件里没有可用 cookie。", file=sys.stderr)
            return 2
        path = core.write_cookie(cookie)
        if sidecar:
            side_path = core.write_waf_sidecar(sidecar)
            print("saved", path, core.cookie_status(cookie))
            print("waf_sidecar", side_path, core.sidecar_status(cookie))
        else:
            print("saved", path, core.cookie_status(cookie))
        return 0
    try:
        cookie = core.import_browser_cookie()
    except Exception as exc:
        print("无法从浏览器导入：", exc, file=sys.stderr)
        print(
            "改用：在已登录的 xueqiu.com 复制 Cookie，或把 waf-bot 的 waf_cookies.json 拿来：\n"
            "  python3 scripts/xueqiu_audit.py cookie --from-file ./my-cookie.txt\n"
            "  python3 scripts/xueqiu_audit.py cookie --from-file ./waf_cookies.json",
            file=sys.stderr,
        )
        return 1
    path = core.write_cookie(cookie)
    print("saved", path, core.cookie_status(cookie))
    return 0


def _fetch_err(label: str, exc: Exception, manifest: dict) -> str:
    detail = str(exc) if str(exc) in {"waf_blocked", "blocked_html"} else type(exc).__name__
    manifest["errors"].append(f"{label}:{detail}")
    print(label, "failed:", detail)
    return detail


def cmd_fetch(args: argparse.Namespace) -> int:
    uid = core.normalize_xueqiu_id(args.uid)
    if not uid.isdigit():
        print("UID 无法识别。请用数字或 https://xueqiu.com/u/数字", file=sys.stderr)
        return 2
    dest = out_dir(args.out, f"work/{uid}")
    login = core.read_login_cookie()
    cookie = vpush.merge_waf_cookie(login)
    manifest = {"uid": uid, "mode": args.mode, "sources": [], "errors": []}
    if not cookie:
        manifest["cookie"] = "none"
    elif cookie != login:
        manifest["cookie"] = "sidecar"
    else:
        manifest["cookie"] = "login"

    if cookie:
        try:
            profile = core.fetch_profile(uid, cookie)
            (dest / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            user = profile.get("user") or profile
            manifest["profile"] = {
                "screen_name": user.get("screen_name"),
                "followers": user.get("followers_count"),
                "status": user.get("status_count"),
            }
            manifest["sources"].append("xueqiu_profile")
            print("profile", user.get("screen_name"), "followers", user.get("followers_count"))
        except Exception as exc:
            _fetch_err("profile", exc, manifest)
        try:
            cubes = core.fetch_cubes(uid, cookie)
            (dest / "cubes.json").write_text(json.dumps(cubes, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["cubes"] = cubes.get("totalCount")
            manifest["sources"].append("xueqiu_cubes")
            print("cubes", cubes.get("totalCount"), "（净值不进预测加权）")
            nav_ok = 0
            for item in core.cube_list_items(cubes):
                symbol = item.get("symbol")
                if not symbol:
                    continue
                try:
                    nav = core.fetch_cube_nav(symbol, cookie)
                    (dest / f"cube_{symbol}_nav.json").write_text(
                        json.dumps(nav, ensure_ascii=False), encoding="utf-8"
                    )
                    nav_ok += 1
                    time.sleep(0.25)
                except Exception as exc:
                    _fetch_err(f"nav:{symbol}", exc, manifest)
                    time.sleep(0.4)
            if nav_ok:
                manifest["sources"].append(f"xueqiu_cube_nav:{nav_ok}")
                print("cube_nav", nav_ok)
        except Exception as exc:
            _fetch_err("cubes", exc, manifest)
        if args.mode == "thin":
            kinds = [("long", {"type": 2}), ("hot", {"type": 9}), ("original", {"type": 0})]
            pages = {"long": 20, "hot": 20, "original": 15}
        elif args.mode == "full":
            kinds = [("long", {"type": 2}), ("hot", {"type": 9}), ("original", {"type": 0})]
            pages = {"long": 40, "hot": 20, "original": 900}
        else:
            kinds = [
                ("all", {"type": 10}),
                ("original", {"type": 0}),
                ("long", {"type": 2}),
                ("qa", {"type": 4}),
                ("hot", {"type": 9}),
            ]
            pages = {"all": 200, "original": 200, "long": 40, "qa": 20, "hot": 20}
        posts = []
        seen_posts: set = set()
        for kind, extra in kinds:
            try:
                items = core.fetch_xueqiu_timeline(uid, cookie, kind, extra, pages[kind])
                (dest / f"timeline_{kind}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                kept = 0
                for item in items:
                    sid = item.get("id")
                    if sid in seen_posts:
                        continue
                    seen_posts.add(sid)
                    posts.append(item)
                    kept += 1
                manifest["sources"].append(f"xueqiu_{kind}:{len(items)}")
                print(kind, len(items), "unique", kept)
            except Exception as exc:
                _fetch_err(kind, exc, manifest)
        comments: list = []
        want_comments = args.mode == "deep" and not args.no_comments and args.comment_posts > 0
        if want_comments and posts:
            targets = vpush.select_comment_targets(posts, limit=args.comment_posts)
            print("comments", "posts", len(targets), "pages<=", args.comment_pages)
            for item in targets:
                sid = str(item.get("id") or "")
                if not sid:
                    continue
                try:
                    rows = core.fetch_status_comments(
                        sid,
                        cookie,
                        author_uid=uid,
                        max_pages=args.comment_pages,
                        pause=0.2,
                    )
                    comments.extend(rows)
                    print("  comments", sid, len(rows))
                    time.sleep(0.2)
                except Exception as exc:
                    detail = _fetch_err(f"comments:{sid}", exc, manifest)
                    if detail == "waf_blocked":
                        print("评论拉取遇到防护页，已停下。帖子仍保留。")
                        break
                    time.sleep(0.4)
            (dest / "comments.json").write_text(
                json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            author_n = sum(1 for c in comments if c.get("is_author"))
            manifest["sources"].append(f"xueqiu_comments:{len(comments)}")
            manifest["author_comments"] = author_n
            print("comments_total", len(comments), "author", author_n)
        if posts:
            corpus = core.build_audit_corpus(posts, comments, author_uid=uid)
            (dest / "posts.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
            (dest / "corpus.json").write_text(
                json.dumps({"uid": uid, "posts": posts, "comments": comments, "corpus": corpus}, ensure_ascii=False),
                encoding="utf-8",
            )
            manifest["posts"] = len(corpus)
            manifest["timeline_posts"] = len(posts)
            manifest["coverage"] = args.mode
        elif any("waf_blocked" in err for err in manifest["errors"]):
            print("被防护页挡住。换 WAF_COOKIE_FILE / cookie --from-file，或 import-posts。不要启动 solver。")
    else:
        print("没有登录态，改走公开 RSS（历史短，只够薄样本）")
        try:
            items = core.fetch_rsshub(uid)
            posts = core.normalize_posts(items)
            (dest / "posts.json").write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
            (dest / "timeline_rss.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["sources"].append(f"rsshub:{len(posts)}")
            manifest["posts"] = len(posts)
            manifest["coverage"] = "thin"
            print("rsshub", len(posts))
        except Exception as exc:
            manifest["errors"].append(f"rsshub:{type(exc).__name__}")
            print("rsshub failed:", type(exc).__name__)
            print(
                "\n雪球挡住了匿名抓取。任选其一继续：\n"
                "  1. 已有 V Push waf-bot sidecar：export WAF_COOKIE_FILE=.../waf_cookies.json 后重试 fetch\n"
                "  2. 浏览器登录 xueqiu.com 后：python3 scripts/xueqiu_audit.py cookie\n"
                "  3. 自己导出时间线：python3 scripts/xueqiu_audit.py import-posts posts.json\n"
                "  4. 先看离线样例：python3 scripts/xueqiu_audit.py example\n"
                "不要在本仓库启动 WAF solver。\n"
            )
            (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return 2

    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("out", dest)
    print("下一步：python3 scripts/xueqiu_audit.py profile", dest)
    print("或 draft", dest / "posts.json", "再按 examples/inclusion.md 改成 calls.json，然后 score / report")
    print("组合量化：python3 scripts/xueqiu_audit.py cubes", uid, "--from-dir", dest)
    if manifest.get("coverage") == "thin":
        print("覆盖：薄样本。深挖帖子+评论请用 sidecar 后默认 fetch --mode deep")
    elif manifest.get("coverage") == "full":
        print("覆盖：全原创时间线，没有作者评论线程。有 sidecar 再跑默认 deep，问答里的首次表述才会进来。")
    elif manifest.get("coverage") == "deep":
        print("覆盖：全部时间线 + 问答 + 评论线程。draft 会扫大V自己的评论。")
    return 0 if manifest.get("posts") else 2


def cmd_import_posts(args: argparse.Namespace) -> int:
    dest = out_dir(args.out, "work/import")
    raw = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
    posts = core.load_audit_corpus(raw)
    (dest / "posts.json").write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    print("posts", len(posts), dest / "posts.json")
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    dest = out_dir(args.out, "work/prices")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.calls:
        payload = json.loads(Path(args.calls).expanduser().read_text(encoding="utf-8"))
        for call in payload.get("calls") or []:
            symbols.append(call["symbol"])
            pt = call.get("price_target") or {}
            if pt.get("symbol"):
                symbols.append(pt["symbol"])
        symbols.append("SH000300")
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        print("没有标的。用 --symbols SH000300,SZ000002 或 --calls calls.json", file=sys.stderr)
        return 2
    cookie = core.read_cookie()
    ok = 0
    for i, sym in enumerate(symbols, 1):
        try:
            series, source = core.fetch_one_price(sym, cookie)
            core.save_price(dest / f"{sym}.json", sym, source, series)
            print(f"{i:02d}/{len(symbols)} {sym} {source} n={len(series)}")
            ok += 1
            time.sleep(0.25)
        except Exception as exc:
            print(f"{i:02d}/{len(symbols)} {sym} FAIL {exc}")
            time.sleep(0.4)
    print("ok", ok, "/", len(symbols), dest)
    return 0 if ok else 1


def ensure_prices(payload: dict, price_dir: Path) -> list[str]:
    needed = []
    for call in payload.get("calls") or []:
        needed.append(call["symbol"])
        if (call.get("price_target") or {}).get("symbol"):
            needed.append(call["price_target"]["symbol"])
    cookie = core.read_cookie()
    failed = []
    for sym in dict.fromkeys(needed):
        if (price_dir / f"{sym}.json").exists():
            continue
        print("fetch price", sym)
        try:
            series, source = core.fetch_one_price(sym, cookie)
            core.save_price(price_dir / f"{sym}.json", sym, source, series)
            print(" ", source, "n=", len(series))
        except Exception as exc:
            print(" ", "skip", exc)
            failed.append(sym)
    return failed


def cmd_draft(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
    posts = core.load_audit_corpus(raw)
    payload = core.draft_candidates(posts, limit=args.limit)
    dest = Path(args.out or "work/candidates.json").expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("scanned", payload["scanned"], "candidates", payload["kept"], dest)
    print("这是草稿，不是 calls.json。按 examples/inclusion.md 入选后再 score。")
    return 0 if payload["kept"] else 2


def cmd_score(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.calls).expanduser().read_text(encoding="utf-8"))
    problems = core.validate_calls(payload)
    if problems:
        print("calls.json 未通过校验：", file=sys.stderr)
        for item in problems[:12]:
            print(" ", item, file=sys.stderr)
        print("字段见 references/calls.md，入选见 examples/inclusion.md", file=sys.stderr)
        return 2
    dest = Path(args.out or "work/scorecard.json").expanduser()
    if dest.exists():
        try:
            prev = json.loads(dest.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        if prev.get("conclusion") and not payload.get("conclusion"):
            payload["conclusion"] = prev["conclusion"]
        if prev.get("playbook") and not payload.get("playbook"):
            payload["playbook"] = prev["playbook"]
        if prev.get("briefs") and not payload.get("briefs"):
            payload["briefs"] = prev["briefs"]
    price_dir = Path(args.prices or "work/prices").expanduser()
    price_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(args.calls).expanduser().parent
    posts = core.load_sibling_posts(work_dir)
    registered = payload.get("registered") or core.registered_year_from_profile_path(work_dir / "profile.json")
    depth = core.infer_corpus_depth(work_dir, payload)
    if not args.no_fetch:
        failed = ensure_prices(payload, price_dir)
        if failed:
            print("缺行情，这些标的先不打分：", ", ".join(failed))
    asof = core.parse_day(args.asof) if args.asof else None
    scorecard = core.score_calls(
        payload,
        price_dir,
        asof=asof,
        posts=posts,
        registered=registered,
        corpus_depth=depth.get("depth") or None,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    s = scorecard["summary"]
    print("n", s["n"], "dir", s["dir_window"])
    print("copy median/mean", s["copy_window_median"], s["copy_window_mean"])
    if scorecard.get("unscored"):
        print("unscored", len(scorecard["unscored"]), [row.get("symbol") for row in scorecard["unscored"]])
    if scorecard.get("conclusion_source") == "auto":
        print("结论是自动兜底。按 examples/inclusion.md 写 conclusion / playbook 后再 report。")
    if depth.get("depth") == "posts_only":
        print("语料是帖子全量，没有作者评论线程。有 sidecar 再跑 fetch --mode deep。")
    print("wrote", dest)
    return 0


def _chrome_common(chrome: str) -> list[str]:
    return [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--hide-scrollbars",
        "--virtual-time-budget=4000",
        "--timeout=18000",
        f"--user-data-dir={tempfile.mkdtemp(prefix='xq-audit-')}",
    ]


def measure_html_height_px(html_path: Path, chrome: str) -> int:
    raw = html_path.read_text(encoding="utf-8")
    probe = Path(tempfile.mkdtemp(prefix="xq-audit-h-")) / "probe.html"
    probe.write_text(core.inject_measure_script(raw), encoding="utf-8")
    try:
        proc = subprocess.run(
            [
                *_chrome_common(chrome),
                "--window-size=760,2400",
                "--dump-dom",
                probe.resolve().as_uri(),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        dom = proc.stdout.decode("utf-8", "replace")
        match = core.PAGE_HEIGHT_RE.search(dom)
        if match:
            return max(800, int(match.group(1)), core.estimate_html_height_px(raw))
    except subprocess.TimeoutExpired:
        pass
    return core.estimate_html_height_px(raw)


def export_long_pdf(chrome: str, html_path: Path, pdf: Path, height_px: int) -> None:
    print_html = core.apply_long_page_css(html_path.read_text(encoding="utf-8"), height_px + 48)
    print_path = Path(tempfile.mkdtemp(prefix="xq-audit-p-")) / "print.html"
    print_path.write_text(print_html, encoding="utf-8")
    try:
        subprocess.run(
            [
                *_chrome_common(chrome),
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf}",
                print_path.resolve().as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass


def png_to_long_pdf(png_path: Path, pdf: Path) -> bool:
    """Embed the full PNG as one page. sips PDF keeps a Letter box and Preview crops."""
    try:
        width_px, height_px = core.png_pixel_size(png_path)
    except ValueError:
        return False
    jpeg = Path(tempfile.mkdtemp(prefix="xq-audit-j-")) / "page.jpg"
    try:
        proc = subprocess.run(
            [
                "sips",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "90",
                str(png_path),
                "--out",
                str(jpeg),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if proc.returncode != 0 or not jpeg.exists() or jpeg.stat().st_size < 100:
        return False
    core.write_single_image_pdf(jpeg.read_bytes(), width_px, height_px, pdf)
    boxes = core.pdf_media_boxes(pdf) if pdf.exists() else []
    return (
        pdf.exists()
        and pdf.stat().st_size > 1000
        and core.pdf_page_count(pdf) == 1
        and len(boxes) == 1
        and boxes[0][3] >= 800
    )


SCREEN_SCALE = 2
MAX_SHOT_CSS = 3600
MAX_VIEW_CSS = 8000


def _chrome_screenshot(chrome: str, html_path: Path, png_path: Path, window_h: int) -> bool:
    try:
        subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-first-run",
                "--hide-scrollbars",
                "--virtual-time-budget=8000",
                "--timeout=35000",
                f"--user-data-dir={tempfile.mkdtemp(prefix='xq-audit-')}",
                f"--force-device-scale-factor={SCREEN_SCALE}",
                f"--window-size=760,{max(800, int(window_h))}",
                f"--screenshot={png_path}",
                html_path.resolve().as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=40,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    return png_path.exists() and png_path.stat().st_size > 1000


def capture_full_png(chrome: str, html_path: Path, png_path: Path, height_px: int) -> bool:
    want = max(1200, int(height_px) + 200)
    if want <= MAX_VIEW_CSS and _chrome_screenshot(chrome, html_path, png_path, want):
        try:
            _w, got = core.png_pixel_size(png_path)
        except ValueError:
            got = 0
        if got >= want * SCREEN_SCALE * 0.9:
            return True
    if want <= MAX_SHOT_CSS:
        return False
    tiles_dir = Path(tempfile.mkdtemp(prefix="xq-audit-t-"))
    raw = html_path.read_text(encoding="utf-8")
    tiles: list[Path] = []
    offset = 0
    while offset < want:
        tile_h = min(MAX_SHOT_CSS, want - offset)
        tile_html = tiles_dir / f"tile-{offset}.html"
        tile_png = tiles_dir / f"tile-{offset}.png"
        tile_html.write_text(core.inject_clip_css(raw, offset, tile_h), encoding="utf-8")
        if not _chrome_screenshot(chrome, tile_html, tile_png, tile_h):
            return False
        width, height, channels, pixels = core.read_png(tile_png)
        expect = int(tile_h) * SCREEN_SCALE
        if height > expect:
            core.write_png(tile_png, width, expect, channels, pixels[: expect * width * channels])
        tiles.append(tile_png)
        offset += tile_h
    if not tiles:
        return False
    if len(tiles) == 1:
        png_path.write_bytes(tiles[0].read_bytes())
        return True
    core.vstack_pngs(tiles, png_path)
    return png_path.exists() and png_path.stat().st_size > 1000


def export_pdf_png(html_path: Path, dest: Path, png: bool) -> None:
    chrome = core.chrome_bin()
    if not chrome:
        print("未找到 Chrome，只留下 HTML")
        return
    pdf = dest / (html_path.stem + ".pdf")
    png_path = dest / (html_path.stem + ".png")
    height = measure_html_height_px(html_path, chrome)
    if capture_full_png(chrome, html_path, png_path, height):
        if png:
            print("png", png_path, png_path.stat().st_size)
        if png_to_long_pdf(png_path, pdf):
            pages = core.pdf_page_count(pdf)
            print("pdf", pdf, pdf.stat().st_size, f"pages={pages}")
            return
        if png:
            print("png 已写出，PDF 嵌入失败，改试 Chrome 打印")
    elif png:
        print("png 未写出，HTML 仍可用")
    export_long_pdf(chrome, html_path, pdf, height)
    pages = core.pdf_page_count(pdf) if pdf.exists() else 0
    if pdf.exists() and pdf.stat().st_size > 1000:
        print("pdf", pdf, pdf.stat().st_size, f"pages={pages or '?'}")
        if pages != 1:
            print("pdf 仍分页，请看 HTML / PNG")
    else:
        print("pdf 未写出（Chrome headless 有时会挂），HTML 仍可用")


def cmd_report(args: argparse.Namespace) -> int:
    sc = json.loads(Path(args.scorecard).expanduser().read_text(encoding="utf-8"))
    dest = out_dir(args.out, "work/report")
    html_path = dest / "report.html"
    posts = None
    sibling = Path(args.scorecard).expanduser().parent / "posts.json"
    if sibling.exists():
        raw = json.loads(sibling.read_text(encoding="utf-8"))
        posts = raw if isinstance(raw, list) else raw.get("posts") or raw.get("corpus")
        if not isinstance(posts, list):
            posts = None
    work_dir = Path(args.scorecard).expanduser().parent
    if not sc.get("registered"):
        sc["registered"] = core.registered_year_from_profile_path(work_dir / "profile.json")
    if not sc.get("corpus_depth"):
        sc["corpus_depth"] = (core.infer_corpus_depth(work_dir, sc) or {}).get("depth") or ""
    if not sc.get("persona"):
        sc["persona"] = core.auto_persona(sc, posts)
    if not sc.get("consistency"):
        sc["consistency"] = core.auto_consistency(sc, posts)
    if not sc.get("mbti"):
        sc["mbti"] = core.auto_mbti(sc, posts)
    ai_path = work_dir / "ai_profile.json"
    if ai_path.exists() and not sc.get("ai_profile"):
        import ai_profile as aip

        ai = aip.load_ai_profile(ai_path)
        if ai:
            sc = aip.merge_into_scorecard(sc, ai)
    if sc.get("conclusion_source") == "auto" and not sc.get("conclusion"):
        print("结论仍是自动兜底。客户稿请按入选表手写 conclusion / playbook。")
    html_path.write_text(core.render_html(sc), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=args.png)
    _deliver_main(dest, sc.get("account") or "", sc.get("asof") or "", "预测审计", "report")
    return 0


def _deliver_main(src_dir: Path, account: str, asof: str, kind: str, src_stem: str, example: bool = False) -> None:
    copied = core.deliver_client_artifacts(
        src_dir, account, asof, kind=kind, example=example, src_stem=src_stem
    )
    if copied:
        print("main", copied[0].parent)
        return
    if core.should_deliver_client(src_dir, account, example) and not core.CLIENT_DELIVER_ROOT.is_dir():
        print("main skipped: /Volumes/main 未挂载")


def write_cube_report(payload: dict, dest: Path, png: bool, example: bool = False) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    html_path = dest / "cubes.html"
    html_path.write_text(core.render_cubes_html(payload), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=png)
    _deliver_main(
        dest,
        payload.get("account") or "",
        str(payload.get("asof") or ""),
        "组合量化",
        "cubes",
        example=example,
    )
    return html_path


def cmd_cubes(args: argparse.Namespace) -> int:
    dest = out_dir(args.out, "work/cubes")
    if args.example:
        bundled = ROOT / "examples" / "metalslime_cubes.json"
        payload = json.loads(bundled.read_text(encoding="utf-8"))
        (dest / "cubes_scorecard.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_cube_report(payload, dest, png=True, example=True)
        print("这是离线组合样例，净值不进预测命中。")
        return 0

    asof = core.parse_day(args.asof) if args.asof else date.today()
    cookie = core.read_cookie()
    metas: list[dict] = []
    navs: dict = {}
    account = ""
    uid = core.normalize_xueqiu_id(args.uid or "")
    from_dir = Path(args.from_dir).expanduser() if args.from_dir else None

    if from_dir:
        if not from_dir.is_dir():
            print("目录不存在：", from_dir, file=sys.stderr)
            return 2
        cubes_path = from_dir / "cubes.json"
        if cubes_path.exists():
            listed = core.cube_list_items(json.loads(cubes_path.read_text(encoding="utf-8")))
            metas.extend(core.slim_cube_meta(item) for item in listed)
            owner = (listed[0].get("owner") or {}) if listed else {}
            account = account or owner.get("screen_name") or ""
            uid = uid or str(listed[0].get("owner_id") or "")
        navs.update(core.discover_nav_files(from_dir))

    if args.symbol:
        wanted = [s.strip() for s in args.symbol.split(",") if s.strip()]
        have = {m.get("symbol") for m in metas}
        for symbol in wanted:
            if symbol not in have:
                metas.append(core.slim_cube_meta({"symbol": symbol}))
        if cookie:
            for symbol in wanted:
                if symbol in navs:
                    continue
                try:
                    navs[symbol] = core.fetch_cube_nav(symbol, cookie)
                    time.sleep(0.25)
                except Exception as exc:
                    print("nav failed", symbol, type(exc).__name__)

    if args.uid and cookie:
        try:
            listed = core.cube_list_items(core.fetch_cubes(uid, cookie))
            if not metas:
                metas = [core.slim_cube_meta(item) for item in listed]
            owner = (listed[0].get("owner") or {}) if listed else {}
            account = account or owner.get("screen_name") or ""
            uid = uid or core.normalize_xueqiu_id(args.uid or "")
            print("cubes", len(listed))
            for item in listed:
                symbol = item.get("symbol")
                if not symbol or symbol in navs:
                    continue
                if args.symbol and symbol not in {s.strip() for s in args.symbol.split(",") if s.strip()}:
                    continue
                try:
                    navs[symbol] = core.fetch_cube_nav(symbol, cookie)
                    print("nav", symbol)
                    time.sleep(0.25)
                except Exception as exc:
                    print("nav failed", symbol, type(exc).__name__)
                    time.sleep(0.4)
        except Exception as exc:
            print("cubes list failed:", type(exc).__name__, file=sys.stderr)
            if not metas:
                return 1

    if not metas and navs:
        metas = [core.slim_cube_meta({"symbol": symbol}) for symbol in navs]

    if not metas:
        print(
            "没有组合可算。任选其一：\n"
            "  python3 scripts/xueqiu_audit.py cubes --example\n"
            "  python3 scripts/xueqiu_audit.py cubes --from-dir work/UID\n"
            "  python3 scripts/xueqiu_audit.py cookie && python3 scripts/xueqiu_audit.py cubes UID",
            file=sys.stderr,
        )
        return 2

    fetch_price = None
    if not args.no_fetch:
        fetch_price = lambda symbol: core.fetch_one_price(symbol, cookie)

    payload = core.score_cubes(
        metas,
        navs,
        asof=asof,
        fetch_price=fetch_price,
        account=account,
        uid=uid,
        home=f"https://xueqiu.com/u/{uid}" if uid else "",
        window_start=core.parse_day(args.start) if args.start else None,
        window_end=core.parse_day(args.end) if args.end else None,
    )
    score_path = dest / "cubes_scorecard.json"
    score_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("usable", payload.get("usable"), "/", len(payload.get("cubes") or []), score_path)
    write_cube_report(payload, dest, png=args.png)
    print("组合净值不进入预测命中或照做加权。")
    return 0 if payload.get("usable") else 2


def _write_profile_report(profile: dict, pack: dict, dest: Path, png: bool, example: bool = False) -> Path:
    import ai_profile as aip

    dest.mkdir(parents=True, exist_ok=True)
    html_path = dest / "profile.html"
    html_path.write_text(aip.render_html(profile, pack), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=png)
    _deliver_main(
        dest,
        profile.get("account") or "",
        str(profile.get("asof") or pack.get("asof") or ""),
        "公开画像",
        "profile",
        example=example,
    )
    return html_path


def cmd_profile(args: argparse.Namespace) -> int:
    import ai_profile as aip

    if args.example:
        dest = out_dir(args.out, "work/example")
        sc = json.loads((ROOT / "examples" / "metalslime_scorecard.json").read_text(encoding="utf-8"))
        pack = aip.build_pack(scorecard=sc, posts=[], comments=[], mode="deep")
        profile = aip.normalize_ai_profile(
            json.loads((ROOT / "examples" / "metalslime_ai_profile.json").read_text(encoding="utf-8")),
            pack,
        )
        profile["source"] = "agent"
        (dest / "ai_profile_pack.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (dest / "ai_profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_profile_report(profile, pack, dest / "profile", png=True, example=True)
        print("这是离线画像样例，不进入预测命中。")
        return 0

    if not args.target:
        print("用法：python3 scripts/xueqiu_audit.py profile work/UID  或  profile --example", file=sys.stderr)
        return 2
    try:
        work = aip.resolve_work_dir(args.target, ROOT)
    except ValueError:
        print("无法识别目标。请用 work/UID、UID 或 https://xueqiu.com/u/数字", file=sys.stderr)
        return 2
    if not work.is_dir():
        print("目录不存在：", work, file=sys.stderr)
        print("先 fetch / import-posts，或 profile --example", file=sys.stderr)
        return 2

    posts = aip.load_posts(work)
    comments = aip.load_comments(work)
    scorecard = aip.load_scorecard(work)
    if not posts and not scorecard:
        print("没有 posts.json 也没有 scorecard.json。先 fetch / import-posts / score。", file=sys.stderr)
        return 2

    mode = args.mode or ("deep" if scorecard else "fast")
    pack = aip.build_pack(work, posts=posts, comments=comments, scorecard=scorecard, mode=mode)
    pack_path = work / "ai_profile_pack.json"
    pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    print("pack", pack.get("counts", {}).get("sampled"), "items", pack_path)

    existing = aip.load_ai_profile(work / "ai_profile.json")
    if args.render:
        if not existing:
            print("没有 ai_profile.json。先跑 profile，或按 references/ai_profile.md 手写。", file=sys.stderr)
            return 2
        profile = aip.normalize_ai_profile(existing, pack)
        if existing.get("source") in {"agent", "llm"}:
            profile["source"] = existing["source"]
    elif existing and not args.force:
        profile = aip.normalize_ai_profile(existing, pack)
        if existing.get("source") in {"agent", "llm"}:
            profile["source"] = existing["source"]
        print("reuse", work / "ai_profile.json", "source", profile.get("source"))
    else:
        profile = aip.generate_ai_profile(
            pack,
            scorecard=scorecard,
            posts=posts,
            use_llm=bool(args.llm),
            goal=args.goal or "",
        )
        if profile.pop("llm_error", None):
            print("llm failed, fallback rules")

    dest_json = work / "ai_profile.json"
    dest_json.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print("source", profile.get("source"), dest_json)
    if profile.get("source") == "rules":
        print("占位规则稿。agent 按 references/ai_profile.md 读", pack_path, "写", dest_json, "（source=agent），再 --render。")
        print("不要为了画像去找外部 API。加 --llm 才调 OpenAI 兼容接口。")

    out = Path(args.out).expanduser() if args.out else work / "profile"
    _write_profile_report(profile, pack, out, png=args.png, example=False)

    if args.with_report:
        if not scorecard:
            print("没有 scorecard.json，跳过 --with-report。先 score。", file=sys.stderr)
            return 0
        merged = aip.merge_into_scorecard(scorecard, profile)
        (work / "scorecard.json").write_text(
            json.dumps(merged, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        report_ns = argparse.Namespace(
            scorecard=str(work / "scorecard.json"),
            out=str(work / "report"),
            png=args.png,
        )
        return cmd_report(report_ns)
    return 0


def cmd_example(args: argparse.Namespace) -> int:
    bundled = ROOT / "examples" / "metalslime_scorecard.json"
    dest = out_dir(args.out, "work/example")
    sc = json.loads(bundled.read_text(encoding="utf-8"))
    ai_path = ROOT / "examples" / "metalslime_ai_profile.json"
    if ai_path.exists():
        import ai_profile as aip

        pack = aip.build_pack(scorecard=sc, posts=[], comments=[], mode="deep")
        profile = aip.normalize_ai_profile(json.loads(ai_path.read_text(encoding="utf-8")), pack)
        sc = aip.merge_into_scorecard(sc, profile)
        (dest / "ai_profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_profile_report(profile, pack, dest / "profile", png=True, example=True)
    sc["conclusion"] = (
        "长周期产业判断准，短线买卖点和数字价位不准。"
        "16 条结构里 14 条窗口方向对；23 条战术里 7 条对，照做中位 −4%。"
        "茅台 1350–1400、飞天 1200 两条价位都没打中。"
        "2021-03 看空抱团、2022-09 看空一线住宅，拿到现在对应白酒 −56%、万科 −82%。"
        "这两条单独拿住，已经好于等权照做全部 39 条。"
    )
    sc["playbook"] = [
        "产业框架可以参考。公开组合和当天情绪帖不能跟。",
        "最稳的空头是金融化溢价在消失的东西：抱团茅、一线住宅。最稳的多头是刚被证伪的制造业周期，但要接受名创、大金从高点腰斩。",
        "数字价位和「现在可以买 / 卖」作废。2022-10 空在大底上；2026 年恒科、油、储能、锂矿、梭回旭创，加上白酒左右横跳，都是近端反例。",
    ]
    html_path = dest / "药神-预测审计.html"
    html_path.write_text(core.render_html(sc), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=True)
    cubes = json.loads((ROOT / "examples" / "metalslime_cubes.json").read_text(encoding="utf-8"))
    write_cube_report(cubes, dest, png=True, example=True)
    print("这是离线样例，不需要雪球登录。审计新账号请 fetch 或 import-posts。组合量化见 cubes.html。公开画像见 profile/profile.html。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="公开预测审计：取数 / 行情 / 打分 / 画像 / 组合量化 / 浅色报告")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="检查 Python、cookie、Chrome")

    c = sub.add_parser("cookie", help="从本机浏览器、cookie 文本或 waf-bot sidecar JSON 导入登录态")
    c.add_argument("--from-file", help="Cookie 文本或 waf_cookies.json，不要提交到 git")

    f = sub.add_parser("fetch", help="拉公开主页、组合列表/净值和时间线")
    f.add_argument("uid", help="雪球 UID 或主页，如 2292705444 / https://xueqiu.com/u/2292705444")
    f.add_argument(
        "--mode",
        choices=("thin", "full", "deep"),
        default="deep",
        help="thin=长文+热门+近页；full=全原创；deep=全部时间线+问答+评论（默认）",
    )
    f.add_argument("--out", help="输出目录，默认 work/<uid>")
    f.add_argument("--comment-posts", type=int, default=80, help="deep 下最多拉多少条帖子的评论")
    f.add_argument("--comment-pages", type=int, default=5, help="每条帖子评论最多翻几页，每页 20 条")
    f.add_argument("--no-comments", action="store_true", help="deep 也只拉帖子，不拉评论线程")

    i = sub.add_parser("import-posts", help="导入已有时间线 JSON，不爬雪球")
    i.add_argument("file")
    i.add_argument("--out", default="work/import")

    d = sub.add_parser("draft", help="按股票/方向/价格/时间扫候选，须人工入选后才能 score")
    d.add_argument("file")
    d.add_argument("--out", default="work/candidates.json")
    d.add_argument("--limit", type=int, default=80)

    pr = sub.add_parser("prices", help="拉前复权日 K（东财 / Yahoo，有 cookie 时再用雪球）")
    pr.add_argument("--symbols", default="", help="逗号分隔，如 SH000300,SZ000002")
    pr.add_argument("--calls", help="从 calls.json 收集标的")
    pr.add_argument("--out", default="work/prices")

    s = sub.add_parser("score", help="按方向/价位/照做打分，缺行情会自动拉")
    s.add_argument("calls")
    s.add_argument("--prices", default="work/prices")
    s.add_argument("--out", default="work/scorecard.json")
    s.add_argument("--asof")
    s.add_argument("--no-fetch", action="store_true")

    r = sub.add_parser("report", help="浅色 HTML，有 Chrome 再出 PDF")
    r.add_argument("scorecard")
    r.add_argument("--out", default="work/report")
    r.add_argument("--png", action="store_true")

    e = sub.add_parser("example", help="离线复刻药神样例报告，零配置")
    e.add_argument("--out", default="work/example")

    pf = sub.add_parser("profile", help="公开文本 AI 画像，可单独出，也可和审计报告一起出")
    pf.add_argument("target", nargs="?", help="work/UID、UID 或主页链接")
    pf.add_argument("--example", action="store_true", help="离线复刻药神画像样例")
    pf.add_argument("--mode", choices=("fast", "deep"), help="fast=近期为主；deep=拉开历史，有 scorecard 时默认 deep")
    pf.add_argument("--out", help="画像 HTML 目录，默认 work/UID/profile")
    pf.add_argument("--goal", default="", help="自定义观察重点，不能覆盖安全边界")
    pf.add_argument("--llm", action="store_true", help="调用外部 OpenAI 兼容接口；默认由 agent 写终稿")
    pf.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    pf.add_argument("--force", action="store_true", help="忽略已有 ai_profile.json，重新生成")
    pf.add_argument("--render", action="store_true", help="只渲染已有 ai_profile.json")
    pf.add_argument("--with-report", action="store_true", help="有 scorecard 时合并进审计报告")
    pf.add_argument("--png", action="store_true")

    cu = sub.add_parser("cubes", help="组合净值对基准：累计 / 年化 / 超额 / 财富倍数")
    cu.add_argument("uid", nargs="?", help="雪球 UID，如 2292705444")
    cu.add_argument("--symbol", help="逗号分隔 ZH 代码，如 ZH2001629")
    cu.add_argument("--from-dir", help="已有 cubes.json 与 cube_*_nav.json 的目录")
    cu.add_argument("--example", action="store_true", help="离线复刻药神组合样例")
    cu.add_argument("--out", default="work/cubes")
    cu.add_argument("--asof")
    cu.add_argument("--from", dest="start", help="观察期起，YYYY-MM-DD，默认组合首日")
    cu.add_argument("--to", dest="end", help="观察期止，YYYY-MM-DD，默认组合末日")
    cu.add_argument("--png", action="store_true")
    cu.add_argument("--no-fetch", action="store_true", help="不补拉基准行情")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn = {
        "doctor": cmd_doctor,
        "cookie": cmd_cookie,
        "fetch": cmd_fetch,
        "import-posts": cmd_import_posts,
        "draft": cmd_draft,
        "prices": cmd_prices,
        "score": cmd_score,
        "report": cmd_report,
        "example": cmd_example,
        "profile": cmd_profile,
        "cubes": cmd_cubes,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
