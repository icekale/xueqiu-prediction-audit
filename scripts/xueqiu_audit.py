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


def out_dir(path: str | None, default: str) -> Path:
    dest = Path(path or default).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("python", sys.version.split()[0])
    print("skill", ROOT)
    print("cookie", core.cookie_status(core.read_cookie()))
    print("cookie_file", core.COOKIE_PATH if core.COOKIE_PATH.exists() else "not created")
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
    print()
    print("下一步（任选一条）：")
    print("  python3 scripts/xueqiu_audit.py example")
    print("  python3 scripts/xueqiu_audit.py cubes --example")
    print("  python3 scripts/xueqiu_audit.py cookie")
    print("  python3 scripts/xueqiu_audit.py fetch 2292705444")
    print("  python3 scripts/xueqiu_audit.py import-posts posts.json")
    print("  python3 scripts/xueqiu_audit.py draft work/2292705444/posts.json")
    return 0


def cmd_cookie(args: argparse.Namespace) -> int:
    if args.from_file:
        raw = Path(args.from_file).expanduser().read_text(encoding="utf-8", errors="replace").strip()
        path = core.write_cookie(raw)
        print("saved", path, core.cookie_status(raw))
        return 0
    try:
        cookie = core.import_browser_cookie()
    except Exception as exc:
        print("无法从浏览器导入：", exc, file=sys.stderr)
        print(
            "改用：在已登录的 xueqiu.com 复制 Cookie，写入文件后执行\n"
            "  python3 scripts/xueqiu_audit.py cookie --from-file ./my-cookie.txt",
            file=sys.stderr,
        )
        return 1
    path = core.write_cookie(cookie)
    print("saved", path, core.cookie_status(cookie))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    uid = args.uid
    dest = out_dir(args.out, f"work/{uid}")
    cookie = core.read_cookie()
    manifest = {"uid": uid, "mode": args.mode, "sources": [], "errors": []}

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
            manifest["errors"].append(f"profile:{type(exc).__name__}")
            print("profile failed:", type(exc).__name__)
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
                    manifest["errors"].append(f"nav:{symbol}:{type(exc).__name__}")
                    time.sleep(0.4)
            if nav_ok:
                manifest["sources"].append(f"xueqiu_cube_nav:{nav_ok}")
                print("cube_nav", nav_ok)
        except Exception as exc:
            manifest["errors"].append(f"cubes:{type(exc).__name__}")
        pages = {"long": 20, "hot": 20, "original": 15 if args.mode == "thin" else 900}
        kinds = [("long", {"type": 2}), ("hot", {"type": 9}), ("original", {"type": 0})]
        posts = []
        for kind, extra in kinds:
            try:
                items = core.fetch_xueqiu_timeline(uid, cookie, kind, extra, pages[kind])
                (dest / f"timeline_{kind}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                posts.extend(items)
                manifest["sources"].append(f"xueqiu_{kind}:{len(items)}")
                print(kind, len(items))
            except Exception as exc:
                manifest["errors"].append(f"{kind}:{type(exc).__name__}")
                print(kind, "failed:", type(exc).__name__)
        if posts:
            (dest / "posts.json").write_text(json.dumps(core.normalize_posts(posts), ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["posts"] = len(posts)
            manifest["coverage"] = "thin" if args.mode == "thin" else "full"
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
                "  1. 浏览器登录 xueqiu.com 后：python3 scripts/xueqiu_audit.py cookie\n"
                "  2. 自己导出时间线：python3 scripts/xueqiu_audit.py import-posts posts.json\n"
                "  3. 先看离线样例：python3 scripts/xueqiu_audit.py example\n"
            )
            (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return 2

    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("out", dest)
    print("下一步：python3 scripts/xueqiu_audit.py draft", dest / "posts.json")
    print("再按 examples/inclusion.md 改成 calls.json，然后 score / report")
    print("组合量化：python3 scripts/xueqiu_audit.py cubes", uid, "--from-dir", dest)
    if manifest.get("coverage") == "thin":
        print("覆盖：薄样本。生涯审计请加 --mode full（需要登录态，耗时更长）")
    return 0 if manifest.get("posts") else 2


def cmd_import_posts(args: argparse.Namespace) -> int:
    dest = out_dir(args.out, "work/import")
    raw = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
    posts = core.normalize_posts(raw)
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


def ensure_prices(payload: dict, price_dir: Path) -> None:
    needed = []
    for call in payload.get("calls") or []:
        needed.append(call["symbol"])
        if (call.get("price_target") or {}).get("symbol"):
            needed.append(call["price_target"]["symbol"])
    cookie = core.read_cookie()
    for sym in dict.fromkeys(needed):
        if (price_dir / f"{sym}.json").exists():
            continue
        print("fetch price", sym)
        series, source = core.fetch_one_price(sym, cookie)
        core.save_price(price_dir / f"{sym}.json", sym, source, series)
        print(" ", source, "n=", len(series))


def cmd_draft(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.file).expanduser().read_text(encoding="utf-8"))
    posts = core.normalize_posts(raw)
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
    price_dir = Path(args.prices or "work/prices").expanduser()
    price_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_fetch:
        try:
            ensure_prices(payload, price_dir)
        except Exception as exc:
            print("行情拉取失败：", exc, file=sys.stderr)
            return 1
    asof = core.parse_day(args.asof) if args.asof else None
    try:
        scorecard = core.score_calls(payload, price_dir, asof=asof)
    except FileNotFoundError as exc:
        print("缺少行情文件：", exc, file=sys.stderr)
        print("先运行 python3 scripts/xueqiu_audit.py prices --calls", args.calls, file=sys.stderr)
        return 1
    dest = Path(args.out or "work/scorecard.json").expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    s = scorecard["summary"]
    print("n", s["n"], "dir", s["dir_window"])
    print("copy median/mean", s["copy_window_median"], s["copy_window_mean"])
    print("wrote", dest)
    return 0


def export_pdf_png(html_path: Path, dest: Path, png: bool) -> None:
    chrome = core.chrome_bin()
    if not chrome:
        print("未找到 Chrome，只留下 HTML")
        return
    pdf = dest / (html_path.stem + ".pdf")
    common = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--hide-scrollbars",
        "--virtual-time-budget=4000",
        "--timeout=18000",
        f"--user-data-dir={tempfile.mkdtemp(prefix='xq-audit-')}",
    ]
    try:
        subprocess.run(
            [*common, "--no-pdf-header-footer", f"--print-to-pdf={pdf}", html_path.resolve().as_uri()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    if pdf.exists() and pdf.stat().st_size > 1000:
        print("pdf", pdf, pdf.stat().st_size)
    else:
        print("pdf 未写出（Chrome headless 有时会挂），HTML 仍可用")
    if png:
        png_path = dest / (html_path.stem + ".png")
        try:
            subprocess.run(
                [
                    *common,
                    "--force-device-scale-factor=2",
                    "--window-size=760,4000",
                    f"--screenshot={png_path}",
                    html_path.resolve().as_uri(),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pass
        if png_path.exists() and png_path.stat().st_size > 1000:
            print("png", png_path, png_path.stat().st_size)
        else:
            print("png 未写出，HTML 仍可用")


def cmd_report(args: argparse.Namespace) -> int:
    sc = json.loads(Path(args.scorecard).expanduser().read_text(encoding="utf-8"))
    dest = out_dir(args.out, "work/report")
    html_path = dest / "report.html"
    html_path.write_text(core.render_html(sc), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=args.png)
    return 0


def write_cube_report(payload: dict, dest: Path, png: bool) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    html_path = dest / "cubes.html"
    html_path.write_text(core.render_cubes_html(payload), encoding="utf-8")
    print("html", html_path)
    export_pdf_png(html_path, dest, png=png)
    return html_path


def cmd_cubes(args: argparse.Namespace) -> int:
    dest = out_dir(args.out, "work/cubes")
    if args.example:
        bundled = ROOT / "examples" / "metalslime_cubes.json"
        payload = json.loads(bundled.read_text(encoding="utf-8"))
        (dest / "cubes_scorecard.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_cube_report(payload, dest, png=True)
        print("这是离线组合样例，净值不进预测命中。")
        return 0

    asof = core.parse_day(args.asof) if args.asof else date.today()
    cookie = core.read_cookie()
    metas: list[dict] = []
    navs: dict = {}
    account = ""
    uid = args.uid or ""
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
            listed = core.cube_list_items(core.fetch_cubes(args.uid, cookie))
            if not metas:
                metas = [core.slim_cube_meta(item) for item in listed]
            owner = (listed[0].get("owner") or {}) if listed else {}
            account = account or owner.get("screen_name") or ""
            uid = uid or args.uid
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


def cmd_example(args: argparse.Namespace) -> int:
    bundled = ROOT / "examples" / "metalslime_scorecard.json"
    dest = out_dir(args.out, "work/example")
    sc = json.loads(bundled.read_text(encoding="utf-8"))
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
    write_cube_report(cubes, dest, png=True)
    print("这是离线样例，不需要雪球登录。审计新账号请 fetch 或 import-posts。组合量化见 cubes.html。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="公开预测审计：取数 / 行情 / 打分 / 组合量化 / 浅色报告")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="检查 Python、cookie、Chrome")

    c = sub.add_parser("cookie", help="从本机浏览器或文件导入自己的雪球登录态")
    c.add_argument("--from-file", help="Cookie 文本文件，不要提交到 git")

    f = sub.add_parser("fetch", help="拉公开主页、组合列表/净值和时间线")
    f.add_argument("uid", help="雪球 UID，如 2292705444")
    f.add_argument("--mode", choices=("thin", "full"), default="thin", help="thin=长文+热门+近页；full=全原创")
    f.add_argument("--out", help="输出目录，默认 work/<uid>")

    i = sub.add_parser("import-posts", help="导入已有时间线 JSON，不爬雪球")
    i.add_argument("file")
    i.add_argument("--out", default="work/import")

    d = sub.add_parser("draft", help="从 posts.json 扫出预测候选，须人工入选后才能 score")
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
        "cubes": cmd_cubes,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
