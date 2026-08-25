#!/usr/bin/env python3
"""Cookie, prices, scoring, and light HTML report. Never print secrets."""
from __future__ import annotations

import html
import json
import os
import re
import ssl
import struct
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree as ET

import vpush_xueqiu as vpush

TZ = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
CONFIG_DIR = Path.home() / ".config" / "xueqiu-prediction-audit"
COOKIE_PATH = CONFIG_DIR / "cookie"
TLS = ssl.create_default_context()

# East Money kline: date,open,close,high,low,...
# Yahoo: unix ts + ohlc
# Xueqiu: ts, volume, open, high, low, close


def config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    return CONFIG_DIR


def cookie_status(cookie: str) -> str:
    text = (cookie or "").strip()
    if not text:
        return "missing"
    has_token = "xq_a_token" in text
    return f"ok ({len(text)} chars, token={'yes' if has_token else 'no'})"


def read_login_cookie() -> str:
    """登录串本身，不叠加 sidecar。供 doctor / seed 比对。"""
    env = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env:
        cookie, _ = vpush.parse_cookie_payload(env)
        return cookie
    file_env = os.environ.get("XUEQIU_COOKIE_FILE", "").strip()
    paths = []
    if file_env:
        paths.append(Path(file_env).expanduser())
    paths.append(COOKIE_PATH)
    vpush_cfg = os.environ.get("VPUSH_CONFIG", "").strip()
    if vpush_cfg:
        paths.append(Path(vpush_cfg).expanduser())
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        cookie, _ = vpush.parse_cookie_payload(raw)
        if cookie:
            return cookie
        if "xq_a_token" in raw or "u=" in raw:
            return raw.strip().splitlines()[0].strip()
    return ""


def read_cookie() -> str:
    return vpush.merge_waf_cookie(read_login_cookie())


def write_cookie(cookie: str) -> Path:
    if not cookie.strip():
        raise ValueError("empty cookie")
    config_dir()
    COOKIE_PATH.write_text(cookie.strip() + "\n", encoding="utf-8")
    COOKIE_PATH.chmod(0o600)
    return COOKIE_PATH


def write_waf_sidecar(data: dict) -> Path:
    config_dir()
    return vpush.write_sidecar(data, CONFIG_DIR / "waf_cookies.json")


def sidecar_status(login_cookie: str | None = None) -> str:
    return vpush.sidecar_status(login_cookie if login_cookie is not None else read_login_cookie())


normalize_xueqiu_id = vpush.normalize_xueqiu_id


def import_browser_cookie() -> str:
    try:
        import browser_cookie3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("未安装 browser-cookie3。pip install browser-cookie3") from exc
    loaders = []
    for name in ("chrome", "chromium", "safari", "firefox", "edge"):
        fn = getattr(browser_cookie3, name, None)
        if fn:
            loaders.append((name, fn))
    last = None
    for name, fn in loaders:
        try:
            jar = fn(domain_name="xueqiu.com")
            parts = [f"{c.name}={c.value}" for c in jar if c.value]
            if any("xq_a_token" in p or p.startswith("u=") for p in parts):
                return "; ".join(parts)
        except Exception as exc:  # noqa: BLE001
            last = f"{name}: {type(exc).__name__}"
    raise RuntimeError(
        "本机浏览器里没有可用的雪球登录态。"
        + (f" 最后一次：{last}" if last else "")
        + " 请先在浏览器打开并登录 xueqiu.com，或改用 --from-file。"
    )


DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 25, retries: int = 3) -> Any:
    req_headers = headers or {"User-Agent": DESKTOP_UA, "Accept": "application/json"}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout, context=TLS) as resp:
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
            text = raw.decode("utf-8", "replace")
            if vpush.is_waf_html(text, content_type):
                raise RuntimeError("waf_blocked")
            if text.lstrip().startswith("<"):
                raise RuntimeError("blocked_html")
            if text.startswith("kline_") and "=" in text[:40]:
                text = text.split("=", 1)[1]
            return json.loads(text)
        except RuntimeError as exc:
            if str(exc) in {"waf_blocked", "blocked_html"}:
                raise
            last = exc
            time.sleep(0.4 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise last or RuntimeError("http_json failed")


def http_bytes(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=TLS) as resp:
        return resp.read()


def xueqiu_headers(cookie: str, referer: str) -> dict[str, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://xueqiu.com",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def slim_status(status: dict) -> dict:
    user = status.get("user") or {}
    if not isinstance(user, dict):
        user = {"screen_name": user}
    retweet = status.get("retweeted_status") or {}
    text = vpush.prefer_full_text(status)
    plain = vpush.strip_html(text)
    created = status.get("created_at")
    return {
        "id": status.get("id"),
        "created_at": created,
        "created_str": vpush.format_created(created or status.get("created_str")),
        "title": status.get("title") or "",
        "text": text,
        "description": status.get("description") or "",
        "like_count": status.get("like_count") or status.get("fav_count") or 0,
        "view_count": status.get("view_count") or 0,
        "comment_count": int(status.get("reply_count") or status.get("comment_count") or 0),
        "type": status.get("type"),
        "post_type": vpush.classify_status(status),
        "comment_id": status.get("commentId") or status.get("comment_id"),
        "retweeted_text": retweet.get("text") or retweet.get("description") or "",
        "user": user.get("screen_name"),
        "user_id": str(user.get("id") or status.get("user_id") or ""),
        "url": vpush.status_url(status),
        "images": vpush.extract_images(status),
        "symbols": extract_symbols(plain) if plain else [],
        "source": "reply" if vpush.classify_status(status) == "reply" else "post",
    }


def fetch_status_comments(
    status_id: str,
    cookie: str,
    author_uid: str = "",
    max_pages: int = 5,
    pause: float = 0.2,
    get_json=None,
) -> list[dict]:
    getter = get_json or (lambda url, headers=None: http_json(url, headers or {}))
    items: list[dict] = []
    seen: set[Any] = set()
    page = 1
    while page <= max_pages:
        params = {"id": status_id, "count": 20, "page": page, "asc": "0", "type": 0}
        url = "https://xueqiu.com/statuses/comments.json?" + urllib.parse.urlencode(params)
        data = getter(url, xueqiu_headers(cookie, f"https://xueqiu.com/{status_id}"))
        rows = vpush.parse_comments_payload(data)
        if not rows:
            break
        added = 0
        for row in rows:
            cid = row.get("id")
            if cid in seen:
                continue
            seen.add(cid)
            if not row.get("status_id"):
                row = {**row, "status_id": status_id}
            items.append(vpush.slim_comment(row, author_uid))
            added += 1
        if added == 0:
            break
        page += 1
        if pause:
            time.sleep(pause)
    return items


def build_audit_corpus(posts: list[dict], comments: list[dict], author_uid: str = "") -> list[dict]:
    corpus = normalize_posts(posts)
    for row in vpush.author_comment_items(comments, author_uid):
        if not row.get("created_str") and row.get("created_at"):
            row["created_str"] = vpush.format_created(row["created_at"])
        corpus.append(row)
    return corpus


def load_audit_corpus(raw: Any) -> list[dict]:
    if isinstance(raw, dict) and raw.get("corpus"):
        return normalize_posts(raw["corpus"])
    if isinstance(raw, dict) and raw.get("comments"):
        uid = str(raw.get("uid") or raw.get("author_uid") or "")
        posts = raw.get("posts") or raw.get("statuses") or raw.get("items") or []
        if not isinstance(posts, list):
            posts = []
        return build_audit_corpus(posts, raw.get("comments") or [], uid)
    return normalize_posts(raw)


def fetch_xueqiu_timeline(
    uid: str,
    cookie: str,
    kind: str,
    extra: dict[str, Any],
    max_pages: int,
    pause: float = 0.15,
) -> list[dict]:
    items: list[dict] = []
    seen: set[Any] = set()
    page = 1
    while page <= max_pages:
        params = {"user_id": uid, "page": page, "count": 20, **extra}
        url = "https://xueqiu.com/statuses/user_timeline.json?" + urllib.parse.urlencode(params)
        data = http_json(url, xueqiu_headers(cookie, f"https://xueqiu.com/u/{uid}"))
        statuses = data.get("statuses") or []
        for status in statuses:
            sid = status.get("id")
            if sid in seen:
                continue
            seen.add(sid)
            items.append(slim_status(status))
        max_page = int(data.get("maxPage") or page)
        if not statuses or page >= max_page:
            break
        page += 1
        time.sleep(pause)
    return items


def fetch_profile(uid: str, cookie: str) -> dict:
    url = "https://xueqiu.com/user/show.json?" + urllib.parse.urlencode({"id": uid})
    return http_json(url, xueqiu_headers(cookie, f"https://xueqiu.com/u/{uid}"))


def fetch_cubes(uid: str, cookie: str) -> dict:
    url = "https://xueqiu.com/cubes/list.json?" + urllib.parse.urlencode(
        {"user_id": uid, "page": 1, "count": 50}
    )
    return http_json(url, xueqiu_headers(cookie, f"https://xueqiu.com/u/{uid}"))


def fetch_cube_nav(symbol: str, cookie: str) -> Any:
    url = "https://xueqiu.com/cubes/nav_daily/all.json?" + urllib.parse.urlencode({"cube_symbol": symbol})
    return http_json(url, xueqiu_headers(cookie, f"https://xueqiu.com/P/{symbol}"))


CUBE_BENCHMARKS = {
    "cn": [("SH000300", "沪深300"), ("SH000905", "中证500"), ("SH000688", "科创50")],
    "us": [("QQQ", "QQQ"), ("SPY", "SPY")],
    "hk": [("HKHSI", "恒生指数"), ("HKHSTECH", "恒生科技")],
}
MARKET_LABEL = {"cn": "A股", "us": "美股", "hk": "港股"}
BENCH_NAMES = {code: name for pairs in CUBE_BENCHMARKS.values() for code, name in pairs}


def parse_nav_payload(data: Any) -> dict[str, dict]:
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    blocks = data if isinstance(data, list) else (data.get("list") if isinstance(data, dict) else []) or []
    out: dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        rows = []
        for item in block.get("list") or []:
            raw_day = item.get("date")
            if not raw_day and item.get("time"):
                try:
                    raw_day = datetime.fromtimestamp(int(item["time"]) / 1000, TZ).date().isoformat()
                except Exception:
                    continue
            try:
                rows.append((parse_day(str(raw_day)), float(item.get("value"))))
            except Exception:
                continue
        rows.sort(key=lambda x: x[0])
        symbol = str(block.get("symbol") or "")
        if symbol:
            out[symbol] = {"name": block.get("name") or symbol, "nav": rows}
    return out


def nav_on_or_after(nav: list, day: date):
    for item in nav:
        if item[0] >= day:
            return item
    return None


def nav_on_or_before(nav: list, day: date):
    last = None
    for item in nav:
        if item[0] <= day:
            last = item
        else:
            break
    return last


def path_return_nav(nav: list, start: date, end: date) -> dict | None:
    a = nav_on_or_after(nav, start)
    b = nav_on_or_before(nav, end)
    if not a or not b or b[0] <= a[0] or a[1] == 0:
        return None
    return {
        "from": str(a[0]),
        "to": str(b[0]),
        "px0": round(a[1], 6),
        "px1": round(b[1], 6),
        "ret": round((b[1] / a[1] - 1) * 100, 2),
        "days": (b[0] - a[0]).days,
    }


def aligned_pair(cube_nav: list, bench_nav: list, start: date, end: date) -> tuple[dict, dict] | None:
    cube0 = nav_on_or_after(cube_nav, start)
    bench0 = nav_on_or_after(bench_nav, start)
    if not cube0 or not bench0:
        return None
    start = max(cube0[0], bench0[0])
    cube_path = path_return_nav(cube_nav, start, end)
    bench_path = path_return_nav(bench_nav, start, end)
    if not cube_path or not bench_path:
        return None
    return cube_path, bench_path


def close_series(ohlc: list) -> list:
    return [(row[0], row[4]) for row in ohlc]


def annualized_pct(ret_pct: float, days: int) -> float | None:
    if days < 365 or ret_pct is None:
        return None
    wealth = 1 + ret_pct / 100
    if wealth <= 0:
        return None
    return round((wealth ** (365.25 / days) - 1) * 100, 2)


def wealth_multiple(cube_ret_pct: float, bench_ret_pct: float) -> float | None:
    denom = 1 + bench_ret_pct / 100
    if denom <= 0:
        return None
    return round((1 + cube_ret_pct / 100) / denom, 2)


def cube_blurb(cube: dict) -> str:
    days = cube.get("days") or 0
    months = max(1, round(days / 30.4))
    ret = cube.get("ret")
    benches = cube.get("benchmarks") or []
    parts = []
    if days >= 365:
        parts.append(f"观察期 {days} 天（约 {round(days / 365.25, 1)} 年），跨过不同市场阶段，不能简单归因于一周运气。")
    elif days < 20:
        parts.append(f"观察期只有 {days} 天，不足以证明任何能力。")
    else:
        parts.append(f"观察期只有约 {months} 个月，不足以证明长期能力。")
    if ret is not None and benches:
        down = [b for b in benches if (b.get("ret") or 0) < 0]
        if ret > 0 and down and len(down) == len(benches):
            parts.append("区间内基准普遍下跌，组合仍录得正收益，说明这段里的行业选择或交易至少有效。")
        for bench in benches:
            if bench.get("excess_pp") is None:
                continue
            window_note = f"（{bench['from']} 起同窗）" if bench.get("shifted") else ""
            bit = f"相对{bench['name']}{window_note}超额 {bench['excess_pp']:+.0f} 个百分点"
            if bench.get("wealth_multiple") is not None:
                bit += f"，期末财富约 {bench['wealth_multiple']} 倍"
            parts.append(bit + "。")
        if days >= 365 and any((bench.get("excess_pp") or 0) >= 40 for bench in benches):
            parts.append("这是组合层面的显著超额。")
    if cube.get("custom_window"):
        parts.append("本表是指定观察期，不是组合全寿命。")
    if cube.get("paper_only"):
        parts.append("作者写明与实盘不重合或不建议跟票，净值只当公开模拟盘。")
    if cube.get("stopped"):
        parts.append(f"净值停在 {cube.get('to')}，之后没有更新。")
    shown = cube.get("xueqiu_total_gain")
    if shown is not None and ret is not None and abs(float(shown) - float(ret)) > 1:
        parts.append(f"雪球页展示累计 {float(shown):+.2f}%，本表用区间首末净值。")
    return "".join(parts)


def analyze_cube(
    meta: dict,
    nav: list,
    bench_navs: dict[str, tuple[str, list]],
    asof: date | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
) -> dict:
    custom = bool(window_start or window_end)
    if not custom and len(nav) < 5:
        return {
            "symbol": meta.get("symbol"),
            "name": meta.get("name"),
            "skip": "净值点太少",
        }
    nav_start, nav_end = nav[0][0], nav[-1][0]
    start = window_start or nav_start
    end = window_end or nav_end
    asof = asof or nav_end
    path = path_return_nav(nav, start, end)
    if not path:
        return {
            "symbol": meta.get("symbol"),
            "name": meta.get("name"),
            "skip": "指定观察期无重叠净值" if custom else "无法计算收益",
        }
    desc = str(meta.get("description") or "")
    paper = any(key in desc for key in ("不重合", "不建议跟", "模拟", "非实盘"))
    stopped = (asof - nav_end).days >= 30
    benches = []
    for symbol, (name, series) in bench_navs.items():
        pair = aligned_pair(nav, series, start, end)
        if not pair:
            continue
        cube_path, bpath = pair
        excess = round(cube_path["ret"] - bpath["ret"], 2)
        shifted = cube_path["from"] != path["from"] or cube_path["to"] != path["to"]
        benches.append(
            {
                "symbol": symbol,
                "name": name,
                "ret": bpath["ret"],
                "ann": annualized_pct(bpath["ret"], bpath["days"]),
                "excess_pp": excess,
                "wealth_multiple": wealth_multiple(cube_path["ret"], bpath["ret"]),
                "from": bpath["from"],
                "to": bpath["to"],
                "cube_ret_aligned": cube_path["ret"] if shifted else None,
                "shifted": shifted,
            }
        )
    cube = {
        "symbol": meta.get("symbol"),
        "name": meta.get("name") or meta.get("symbol"),
        "market": meta.get("market") or "cn",
        "description": desc,
        "followers": meta.get("follower_count"),
        "xueqiu_total_gain": meta.get("total_gain"),
        "from": path["from"],
        "to": path["to"],
        "days": path["days"],
        "ret": path["ret"],
        "ann": annualized_pct(path["ret"], path["days"]),
        "paper_only": paper,
        "stopped": stopped,
        "custom_window": custom,
        "benchmarks": benches,
    }
    cube["blurb"] = cube_blurb(cube)
    if cube["ann"] is not None and benches and max(b.get("excess_pp") or 0 for b in benches) >= 40:
        cube["headline"] = f"{cube['name']}的超额确实很强"
    elif path["days"] >= 60 and path["days"] < 365 and path["ret"] > 0:
        cube["headline"] = f"{cube['name']}虽然短，但表现也很突出"
    else:
        cube["headline"] = f"{cube['name']}对基准"
    return cube


def default_benchmarks(market: str) -> list[tuple[str, str]]:
    return list(CUBE_BENCHMARKS.get((market or "cn").lower(), CUBE_BENCHMARKS["cn"]))


def slim_cube_meta(item: dict) -> dict:
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "market": item.get("market") or "cn",
        "description": item.get("description") or "",
        "follower_count": item.get("follower_count"),
        "total_gain": item.get("total_gain"),
        "net_value": item.get("net_value"),
        "closed_at": item.get("closed_at"),
        "annualized_gain_rate": item.get("annualized_gain_rate"),
    }


def cube_list_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict) and item.get("symbol")]
    if isinstance(payload, dict):
        for key in ("list", "cubes"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict) and item.get("symbol")]
    return []


def discover_nav_files(folder: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in folder.glob("cube_*_nav.json"):
        symbol = path.name[len("cube_") : -len("_nav.json")]
        out[symbol] = json.loads(path.read_text(encoding="utf-8"))
    return out


def _has_bench(bench_navs: dict[str, tuple[str, list]], code: str, name: str) -> bool:
    for symbol, (label, _series) in bench_navs.items():
        if symbol.upper() == code.upper() or label == name:
            return True
    return False


def score_cubes(
    metas: list[dict],
    nav_payloads: dict[str, Any],
    *,
    extra_benches: dict[str, tuple[str, list]] | None = None,
    asof: date | None = None,
    fetch_price=None,
    account: str = "",
    uid: str = "",
    home: str = "",
    title: str = "",
    window_start: date | None = None,
    window_end: date | None = None,
) -> dict:
    cache = dict(extra_benches or {})
    asof = asof or date.today()
    cubes = []
    for meta in metas:
        symbol = str(meta.get("symbol") or "")
        raw = nav_payloads.get(symbol)
        if raw is None:
            cubes.append({"symbol": symbol, "name": meta.get("name") or symbol, "skip": "缺少净值"})
            continue
        parsed = parse_nav_payload(raw)
        block = parsed.get(symbol) or next((parsed[k] for k in parsed if str(k).startswith("ZH")), None)
        if not block:
            cubes.append({"symbol": symbol, "name": meta.get("name") or symbol, "skip": "净值无法解析"})
            continue
        if not meta.get("name"):
            meta = {**meta, "name": block.get("name") or symbol}
        bench_navs: dict[str, tuple[str, list]] = {}
        for other, item in parsed.items():
            if other == symbol or str(other).startswith("ZH"):
                continue
            bench_navs[other] = (item.get("name") or BENCH_NAMES.get(other, other), item["nav"])
        for code, name in default_benchmarks(meta.get("market") or "cn"):
            if _has_bench(bench_navs, code, name):
                continue
            if code in cache:
                bench_navs[code] = cache[code]
                continue
            if not fetch_price:
                continue
            try:
                ohlc, _source = fetch_price(code)
                series = close_series(ohlc)
                cache[code] = (name, series)
                bench_navs[code] = (name, series)
            except Exception:
                continue
        cubes.append(
            analyze_cube(
                meta,
                block["nav"],
                bench_navs,
                asof=asof,
                window_start=window_start,
                window_end=window_end,
            )
        )
    usable = sum(1 for cube in cubes if not cube.get("skip"))
    who = account or (f"UID {uid}" if uid else "")
    payload = {
        "title": title or (f"{who}公开组合量化" if who else "雪球组合量化"),
        "account": account,
        "uid": uid,
        "home": home or (f"https://xueqiu.com/u/{uid}" if uid else ""),
        "asof": str(asof),
        "usable": usable,
        "cubes": cubes,
    }
    if window_start or window_end:
        payload["window"] = {
            "from": str(window_start) if window_start else None,
            "to": str(window_end) if window_end else None,
        }
    return payload


def fetch_rsshub(uid: str, route: str = "user") -> list[dict]:
    url = f"https://rsshub.app/xueqiu/{route}/{uid}"
    raw = http_bytes(url, {"User-Agent": UA, "Accept": "application/rss+xml,application/xml"})
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("a:entry", ns) or root.findall("channel/item"):
        if entry.tag.endswith("entry"):
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
            updated = entry.findtext("a:updated", default="", namespaces=ns) or ""
            link_el = entry.find("a:link", ns)
            link = (link_el.get("href") if link_el is not None else "") or ""
        else:
            title = (entry.findtext("title") or "").strip()
            summary = (entry.findtext("description") or "").strip()
            updated = entry.findtext("pubDate") or ""
            link = entry.findtext("link") or ""
        items.append(
            {
                "id": link or title,
                "created_str": updated,
                "title": title,
                "text": summary,
                "description": summary,
                "source": "rsshub",
            }
        )
    return items


def normalize_posts(raw: Any) -> list[dict]:
    if isinstance(raw, dict):
        for key in ("posts", "statuses", "items", "list"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raise ValueError("JSON 里没有 posts/statuses/items")
    if not isinstance(raw, list):
        raise ValueError("帖子文件必须是数组或带 posts 的对象")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = vpush.strip_html(vpush.prefer_full_text(item))
        user = item.get("user") or item.get("screen_name")
        if isinstance(user, dict):
            user = user.get("screen_name")
        out.append(
            {
                "id": item.get("id") or item.get("status_id"),
                "created_at": item.get("created_at") or item.get("created"),
                "created_str": item.get("created_str") or item.get("date") or "",
                "title": item.get("title") or "",
                "text": text,
                "description": vpush.strip_html(item.get("description") or text),
                "user": user,
                "user_id": str(item.get("user_id") or ""),
                "source": item.get("source") or ("comment" if str(item.get("id") or "").startswith("c-") else "post"),
                "status_id": item.get("status_id"),
                "url": item.get("url") or "",
                "post_type": item.get("post_type"),
                "comment_count": item.get("comment_count") or 0,
                "parent_text": item.get("parent_text") or "",
                "parent_user": item.get("parent_user") or "",
            }
        )
    return out


def parse_day(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def post_day(post: dict) -> date | None:
    for key in ("created_str", "date"):
        raw = str(post.get(key) or "").strip()
        if len(raw) >= 10:
            try:
                return parse_day(raw)
            except Exception:
                pass
        if raw:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y"):
                try:
                    return datetime.strptime(raw[:31], fmt).date()
                except Exception:
                    continue
    ts = post.get("created_at") or post.get("created")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    if isinstance(ts, (int, float)) and ts > 0:
        if ts > 1e12:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts, TZ).date()
        except Exception:
            return None
    return None


STOCK_TICKER_RE = re.compile(r"\$([^$()]+)\(([A-Za-z]{1,5}\d{0,6})\)\$")
STOCK_BARE_RE = re.compile(r"\$([A-Z]{1,5})\$")
REPLY_SPLIT_RE = re.compile(r"（回复 @[^：:]{1,40}[：:]")
XQ_QUOTE_RE = re.compile(r"//\s*@")
REPLY_PREFIX_RE = re.compile(r"^回复\s*@[^：:\s]{1,40}\s*[：:]\s*")
MENTION_RE = re.compile(r"@[\w.\-一-龥]{1,40}")
PRICE_CUE_RE = re.compile(r"目标价|目标|见底|见顶|过")
PRICE_RANGE_RE = re.compile(
    r"(?<![\d.])(\d{3,5}(?:\.\d{1,2})?)\s*[-~～—－–−到至]\s*(\d{3,5}(?:\.\d{1,2})?)(?![\d.%％])"
)
PRICE_LABELED_RE = re.compile(
    r"(?:目标价|目标|见底|见顶)\s*(\d{3,5}(?:\.\d{1,2})?)(?![\d.%％])"
)
INDEX_LEVEL_RE = re.compile(r"(?:过|突破|站上|守住)\s*(\d{4})(?!\d)")
HORIZON_RULES = (
    (re.compile(r"五年|5年"), 60, "五年"),
    (re.compile(r"三年|3年"), 36, "三年"),
    (re.compile(r"两年|2年"), 24, "两年"),
    (re.compile(r"一年|12个月|年底"), 12, "一年"),
    (re.compile(r"半年|6个月"), 6, "半年"),
    (re.compile(r"一两个月|1-2个月|两个月|2个月"), 2, "一两个月"),
    (re.compile(r"本月|月底|一个月|1个月"), 1, "一个月"),
    (re.compile(r"本周|这周|一周"), 1, "一周"),
)
SYMBOL_ALIASES = (
    ("中证白酒", "SZ399997"),
    ("沪深300", "SH000300"),
    ("科创50", "SH000688"),
    ("创业板指", "SZ399006"),
    ("上证指数", "SH000001"),
    ("中际旭创", "SZ300308"),
    ("贵州茅台", "SH600519"),
    ("万科A", "SZ000002"),
    ("万科", "SZ000002"),
    ("茅台", "SH600519"),
)
LONG_HINTS = (
    "看多",
    "坚决看好",
    "现在可以买",
    "可以买了",
    "死多",
    "梭回",
    "上车",
    "开仓",
    "逢低上",
    "越跌越兴奋",
    "继续持有",
    "中线反弹",
)
SHORT_HINTS = (
    "看空",
    "死空",
    "拒绝抄底",
    "不是底",
    "现在可以卖",
    "维持看空",
    "双见顶",
    "预计见顶",
    "还会有回调",
    "应该清仓",
    "全部清仓",
    "现在清仓",
)
TACTICAL_HINTS = ("现在可以", "拒绝抄底", "见底", "预计见顶", "梭回", "点位", "开仓", "全部清仓", "现在清仓")
MOOD_HINTS = ("今天心情", "这周看着难受", "今天好难受")
FRAME_HINTS = ("IRR", "供需", "去金融化")
CUBE_HINTS = ("不构成方向判断", "组合调仓")


def _first_hit(text: str, keys: tuple[str, ...], honor_negation: bool = False) -> str | None:
    text = text or ""
    for key in keys:
        start = 0
        while True:
            pos = text.find(key, start)
            if pos < 0:
                break
            if honor_negation and any(token in text[max(0, pos - 8) : pos] for token in ("没", "未", "不", "别", "勿")):
                start = pos + len(key)
                continue
            return key
    return None


def split_reply_context(text: str, parent_text: str = "") -> tuple[str, str]:
    text = str(text or "")
    parent = str(parent_text or "").strip()
    match = REPLY_SPLIT_RE.search(text)
    if match:
        body = text[: match.start()].strip()
        if not parent:
            tail = text[match.end() :]
            parent = tail[:-1].strip() if tail.endswith("）") else tail.strip()
        return body, parent
    return text.strip(), parent


def author_body(text: str, parent_text: str = "") -> tuple[str, str]:
    body, parent = split_reply_context(text, parent_text)
    if XQ_QUOTE_RE.search(body):
        body = XQ_QUOTE_RE.split(body, maxsplit=1)[0].strip()
    body = REPLY_PREFIX_RE.sub("", body).strip()
    return body, parent


def mask_mentions(text: str) -> str:
    return MENTION_RE.sub(" ", text or "")


def guess_horizon(text: str, kind: str) -> int:
    months, _ = extract_horizon(text)
    if months:
        return months
    return 6 if kind == "tactical" else 12


def extract_horizon(text: str) -> tuple[int | None, str | None]:
    for pattern, months, label in HORIZON_RULES:
        if pattern.search(text or ""):
            return months, label
    return None, None


def _looks_like_year(value: float) -> bool:
    return 1990 <= value <= 2035 and float(value).is_integer()


def _near_price_cue(text: str, start: int) -> bool:
    return bool(PRICE_CUE_RE.search(text[max(0, start - 16) : start + 2]))


def extract_price_target(text: str, symbol: str = "") -> dict | None:
    text = mask_mentions(text or "")
    for match in PRICE_RANGE_RE.finditer(text):
        if not _near_price_cue(text, match.start()):
            continue
        lo, hi = float(match.group(1)), float(match.group(2))
        if _looks_like_year(lo) and _looks_like_year(hi):
            continue
        if hi < lo:
            lo, hi = hi, lo
        if hi / max(lo, 1e-9) > 8:
            continue
        return {
            "symbol": symbol,
            "lo": int(lo) if lo.is_integer() else lo,
            "hi": int(hi) if hi.is_integer() else hi,
            "label": f"{match.group(1)}-{match.group(2)}",
        }
    for match in PRICE_LABELED_RE.finditer(text):
        value = float(match.group(1))
        if _looks_like_year(value):
            continue
        raw = match.group(1)
        return {
            "symbol": symbol,
            "lo": int(value) if value.is_integer() else value,
            "hi": int(value) if value.is_integer() else value,
            "label": raw,
        }
    for match in INDEX_LEVEL_RE.finditer(text):
        value = float(match.group(1))
        if _looks_like_year(value):
            continue
        raw = match.group(1)
        return {
            "symbol": symbol,
            "lo": int(value),
            "hi": int(value),
            "label": f"过{raw}" if f"过{raw}" in text else raw,
        }
    return None


def extract_alias_symbols(text: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for name, symbol in SYMBOL_ALIASES:
        if name in (text or "") and symbol not in seen:
            seen.add(symbol)
            found.append((symbol, name))
    return found


def extract_symbols(text: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for name, symbol in STOCK_TICKER_RE.findall(text or ""):
        symbol = symbol.upper()
        if symbol not in seen:
            seen.add(symbol)
            found.append((symbol, name.strip()))
    for symbol in STOCK_BARE_RE.findall(text or ""):
        symbol = symbol.upper()
        if symbol not in seen:
            seen.add(symbol)
            found.append((symbol, symbol))
    for symbol, name in extract_alias_symbols(text or ""):
        if symbol not in seen:
            seen.add(symbol)
            found.append((symbol, name))
    return found


def extract_quad(text: str, parent_text: str = "") -> dict:
    body, parent = author_body(text, parent_text)
    cube = bool(_first_hit(body, CUBE_HINTS))
    long_hit = _first_hit(body, LONG_HINTS, honor_negation=True)
    short_hit = _first_hit(body, SHORT_HINTS, honor_negation=True)
    side = None
    if short_hit and not long_hit:
        side = -1
    elif long_hit and not short_hit:
        side = 1
    elif short_hit and long_hit:
        side = -1 if body.rfind(short_hit) > body.rfind(long_hit) else 1
    symbols = extract_symbols(body) or extract_symbols(parent)
    price = extract_price_target(body, symbols[0][0] if symbols else "")
    horizon_m, horizon_hit = extract_horizon(body)
    kind = "tactical" if _first_hit(body, TACTICAL_HINTS) or price or (horizon_m or 99) <= 3 else "structure"
    if horizon_m is None:
        horizon_m = 6 if kind == "tactical" else 12
    fragment = bool(parent) or len(body) < 24
    mixed = bool(long_hit and short_hit)
    quad = {
        "stock": bool(symbols),
        "direction": side is not None and not cube,
        "price": bool(price),
        "time": bool(horizon_hit),
    }
    return {
        "body": body,
        "parent": parent,
        "side": None if cube else side,
        "symbols": symbols,
        "price_target": None if cube else price,
        "horizon_m": horizon_m,
        "horizon_explicit": bool(horizon_hit),
        "horizon_hit": horizon_hit,
        "kind": kind,
        "direction_hit": None if cube else (short_hit or long_hit),
        "cube": cube,
        "fragment": fragment,
        "mixed": mixed,
        "quad": quad,
        "needs_llm": (not cube) and (fragment or mixed or (side is not None and not symbols)),
    }


def draft_candidates(posts: list[dict], limit: int = 80) -> dict:
    candidates = []
    seen = set()
    skipped = {"no_direction": 0, "mood": 0, "dup": 0, "cube": 0}
    missing = {"stock": 0, "price": 0, "time": 0}
    for post in posts:
        text = str(post.get("text") or post.get("title") or "")
        if not text and not post.get("parent_text"):
            continue
        day = post_day(post)
        quad = extract_quad(text, str(post.get("parent_text") or ""))
        if quad["cube"]:
            skipped["cube"] += 1
            continue
        if not quad["quad"]["direction"]:
            if _first_hit(quad["body"], MOOD_HINTS):
                skipped["mood"] += 1
            else:
                skipped["no_direction"] += 1
            continue
        if _first_hit(quad["body"], MOOD_HINTS) and not quad["direction_hit"]:
            skipped["mood"] += 1
            continue
        kind = quad["kind"]
        symbols = quad["symbols"] or [("", "")]
        for symbol, name in symbols:
            key = (str(day), symbol or name, quad["side"])
            if key in seen:
                skipped["dup"] += 1
                continue
            seen.add(key)
            quote = (quad["body"] or text)[:120]
            why = f"四元组：方向「{quad['direction_hit']}」"
            if symbol:
                why += f"，股票 {symbol}"
            else:
                why += "，股票待补"
                missing["stock"] += 1
            if quad["price_target"]:
                why += f"，价位 {quad['price_target'].get('label')}（价位另判，不随方向命中）"
            else:
                missing["price"] += 1
            if quad["horizon_explicit"]:
                why += f"，时间 {quad['horizon_hit']}"
            else:
                why += "，时间未写，先按默认窗口"
                missing["time"] += 1
            if post.get("source") == "comment" or str(post.get("id") or "").startswith("c-"):
                why += "；来自大V评论，核对是否首次清楚表述"
            if quad["needs_llm"]:
                why += "；碎片/问答，可先 LLM 补候选，仍须人工入选"
            if _first_hit(quad["body"], FRAME_HINTS) and kind == "structure":
                why += "；文中有框架词，确认是否另有明确多空"
            row = {
                "draft": True,
                "needs_review": True,
                "needs_llm": quad["needs_llm"],
                "id": str(post.get("id") or f"draft-{len(candidates)+1}"),
                "date": str(day) if day else "",
                "theme": (name or quote[:24]) or "待定",
                "side": quad["side"],
                "symbol": symbol,
                "horizon_m": quad["horizon_m"],
                "kind": kind,
                "cat": "",
                "note": why,
                "quote": quote,
                "source_id": post.get("id"),
                "quad": quad["quad"],
            }
            if quad["price_target"]:
                pt = dict(quad["price_target"])
                if symbol and not pt.get("symbol"):
                    pt["symbol"] = symbol
                row["price_target"] = pt
            candidates.append(row)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return {
        "draft": True,
        "title": "预测候选（未入选）",
        "note": "这是草稿，不是 calls.json。按股票/方向/价格/时间四元组核对，缺项不要脑补；碎片评论可先 LLM 补候选，仍须按 examples/inclusion.md 入选后再 score。",
        "scanned": len(posts),
        "kept": len(candidates),
        "needs_llm": sum(1 for row in candidates if row.get("needs_llm")),
        "skipped": skipped,
        "quad_missing": missing,
        "calls": candidates,
    }


def validate_calls(payload: dict) -> list[str]:
    errors = []
    if payload.get("draft") is True:
        errors.append("这是 draft 候选，不是 calls.json。按 examples/inclusion.md 改完再 score")
    calls = payload.get("calls") or payload.get("rows") or []
    if not calls:
        errors.append("没有 calls")
        return errors
    for i, call in enumerate(calls):
        loc = call.get("id") or f"#{i+1}"
        if call.get("draft"):
            errors.append(f"{loc}: 仍是草稿，不能直接打分")
        for key in ("date", "side", "symbol", "horizon_m", "kind"):
            if call.get(key) in (None, ""):
                errors.append(f"{loc}: 缺少 {key}")
        if call.get("date"):
            try:
                parse_day(str(call["date"]))
            except Exception:
                errors.append(f"{loc}: date 无法解析")
        if call.get("side") not in (1, -1, "1", "-1"):
            errors.append(f"{loc}: side 必须是 1 或 -1")
        if call.get("kind") not in ("structure", "tactical"):
            errors.append(f"{loc}: kind 必须是 structure 或 tactical")
        try:
            if call.get("horizon_m") is not None and float(call["horizon_m"]) <= 0:
                errors.append(f"{loc}: horizon_m 必须 > 0")
        except (TypeError, ValueError):
            errors.append(f"{loc}: horizon_m 不是数字")
        pt = call.get("price_target")
        if isinstance(pt, dict) and pt:
            if pt.get("lo") is not None and not pt.get("symbol"):
                errors.append(f"{loc}: price_target 有 lo 但没有 symbol")
    return errors


def to_series(rows: list[tuple[date, float, float, float, float]]) -> list[tuple]:
    rows = sorted(rows, key=lambda x: x[0])
    return rows


def load_price_file(path: Path) -> list[tuple[date, float, float, float, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items")
    if items:
        out = []
        for row in items:
            out.append((parse_day(str(row[0])), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        return to_series(out)
    xq = (data.get("data") or {}).get("item") if isinstance(data.get("data"), dict) else None
    if xq:
        out = []
        for row in xq:
            ts = datetime.fromtimestamp(row[0] / 1000, TZ).date()
            out.append((ts, float(row[2]), float(row[3]), float(row[4]), float(row[5])))
        return to_series(out)
    raise ValueError(f"unrecognized price file: {path.name}")


def save_price(path: Path, symbol: str, source: str, series: list[tuple]) -> None:
    path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "source": source,
                "items": [[d.isoformat(), o, h, l, c] for d, o, h, l, c in series],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def eastmoney_secid(symbol: str) -> str | None:
    s = symbol.upper()
    if s.startswith("SH"):
        return "1." + s[2:]
    if s.startswith("SZ"):
        return "0." + s[2:]
    if s.startswith("BJ"):
        return "0." + s[2:]
    return None


def fetch_eastmoney(symbol: str) -> list[tuple]:
    secid = eastmoney_secid(symbol)
    if not secid:
        raise RuntimeError("not_a_share")
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(
        {
            "secid": secid,
            "klt": "101",
            "fqt": "1",
            "lmt": "10000",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        }
    )
    data = http_json(
        url,
        {"User-Agent": DESKTOP_UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"},
    )
    lines = ((data.get("data") or {}).get("klines")) or []
    if not lines:
        raise RuntimeError("empty_eastmoney")
    out = []
    for line in lines:
        parts = str(line).split(",")
        # date, open, close, high, low
        out.append(
            (
                parse_day(parts[0]),
                float(parts[1]),
                float(parts[3]),
                float(parts[4]),
                float(parts[2]),
            )
        )
    return to_series(out)


def yahoo_ticker(symbol: str) -> str:
    s = symbol.upper()
    aliases = {"HKHSI": "^HSI", "HKHSTECH": "3032.HK"}
    if s in aliases:
        return aliases[s]
    if s.startswith("SH"):
        return s[2:] + ".SS"
    if s.startswith("SZ"):
        return s[2:] + ".SZ"
    if s.startswith("HK") and s[2:].isdigit():
        return s[2:].zfill(4) + ".HK"
    return s


def _fetch_yahoo_once(symbol: str) -> list[tuple]:
    ticker = urllib.parse.quote(yahoo_ticker(symbol), safe="^.")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        "?interval=1d&range=max&events=div%2Csplit"
    )
    data = http_json(url, {"User-Agent": UA, "Accept": "application/json"})
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("empty_yahoo")
    ts_list = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes = quote.get("open"), quote.get("high"), quote.get("low"), quote.get("close")
    out = []
    for i, ts in enumerate(ts_list):
        if closes[i] is None:
            continue
        day = datetime.fromtimestamp(ts, TZ).date()
        o = float(opens[i] if opens[i] is not None else closes[i])
        h = float(highs[i] if highs[i] is not None else closes[i])
        low = float(lows[i] if lows[i] is not None else closes[i])
        c = float(closes[i])
        out.append((day, o, h, low, c))
    if not out:
        raise RuntimeError("empty_yahoo")
    return to_series(out)


def fetch_yahoo(symbol: str, attempts: int = 3) -> list[tuple]:
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return _fetch_yahoo_once(symbol)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in {429, 502, 503} and i < attempts - 1:
                time.sleep(1.5 * (2 ** i))
                continue
            raise
    raise last or RuntimeError("empty_yahoo")


def fetch_xueqiu_kline(symbol: str, cookie: str, begin: str = "20180101") -> list[tuple]:
    begin_ts = int(datetime.strptime(begin, "%Y%m%d").replace(tzinfo=TZ).timestamp() * 1000)
    end = int(datetime.now(TZ).timestamp() * 1000)
    url = "https://stock.xueqiu.com/v5/stock/chart/kline.json?" + urllib.parse.urlencode(
        {
            "symbol": symbol,
            "begin": begin_ts,
            "end": end,
            "period": "day",
            "type": "before",
            "count": -8000,
            "indicator": "kline",
        }
    )
    data = http_json(url, xueqiu_headers(cookie, "https://xueqiu.com/"))
    rows = (data.get("data") or {}).get("item") or []
    if not rows:
        raise RuntimeError("empty_xueqiu")
    out = []
    for row in rows:
        ts = datetime.fromtimestamp(row[0] / 1000, TZ).date()
        out.append((ts, float(row[2]), float(row[3]), float(row[4]), float(row[5])))
    return to_series(out)


def tencent_code(symbol: str) -> str | None:
    s = symbol.upper()
    if s.startswith("SH"):
        return "sh" + s[2:]
    if s.startswith("SZ"):
        return "sz" + s[2:]
    return None


def fetch_tencent(symbol: str) -> list[tuple]:
    code = tencent_code(symbol)
    if not code:
        raise RuntimeError("not_a_share")
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode(
        {"param": f"{code},day,,,3200,qfq"}
    )
    data = http_json(url, {"User-Agent": DESKTOP_UA, "Referer": "https://gu.qq.com/"})
    node = (data.get("data") or {}).get(code) or {}
    lines = node.get("qfqday") or node.get("day") or []
    if not lines:
        raise RuntimeError("empty_tencent")
    out = []
    for row in lines:
        out.append((parse_day(str(row[0])), float(row[1]), float(row[3]), float(row[4]), float(row[2])))
    return to_series(out)


def fetch_sina(symbol: str) -> list[tuple]:
    code = tencent_code(symbol)
    if not code:
        raise RuntimeError("not_a_share")
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?" + urllib.parse.urlencode(
        {"symbol": code, "scale": "240", "ma": "no", "datalen": "2048"}
    )
    data = http_json(url, {"User-Agent": DESKTOP_UA, "Referer": "https://finance.sina.com.cn/"})
    if not isinstance(data, list) or not data:
        raise RuntimeError("empty_sina")
    out = []
    for row in data:
        out.append(
            (
                parse_day(str(row["day"])),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            )
        )
    return to_series(out)


def fetch_one_price(symbol: str, cookie: str = "") -> tuple[list[tuple], str]:
    errors = []
    if eastmoney_secid(symbol):
        for name, fn in (("eastmoney", fetch_eastmoney), ("tencent", fetch_tencent), ("sina", fetch_sina)):
            try:
                return fn(symbol), name
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}:{exc}")
    try:
        return fetch_yahoo(symbol), "yahoo"
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yahoo:{exc}")
    if cookie:
        try:
            return fetch_xueqiu_kline(symbol, cookie), "xueqiu"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"xueqiu:{exc}")
    raise RuntimeError(" | ".join(errors) or "no_source")


def bar_on_or_after(series, day):
    for row in series:
        if row[0] >= day:
            return row
    return None


def bar_on_or_before(series, day):
    last = None
    for row in series:
        if row[0] <= day:
            last = row
        else:
            break
    return last


def window_stats(series, start: date, months: float, asof: date) -> dict | None:
    a = bar_on_or_after(series, start)
    if not a:
        return None
    target = start + timedelta(days=int(months * 30.4))
    last = series[-1]
    end_cap = min(target, asof, last[0])
    b = bar_on_or_before(series, end_cap) or last
    tod = bar_on_or_before(series, asof) or last
    if b[0] <= a[0]:
        return None
    peak = trough = a[4]
    peak_d = trough_d = a[0]
    for ts, _o, h, low, _c in series:
        if ts <= a[0]:
            continue
        if ts > tod[0]:
            break
        if h > peak:
            peak, peak_d = h, ts
        if low < trough:
            trough, trough_d = low, ts

    def pack(end):
        return {
            "from": str(a[0]),
            "to": str(end[0]),
            "px0": round(a[4], 4),
            "px1": round(end[4], 4),
            "ret": round((end[4] / a[4] - 1) * 100, 2),
            "days": (end[0] - a[0]).days,
        }

    return {
        "window": pack(b),
        "todate": pack(tod),
        "peak": {"date": str(peak_d), "px": round(peak, 4)},
        "trough": {"date": str(trough_d), "px": round(trough, 4)},
        "mfe": round((peak / a[4] - 1) * 100, 2),
        "mae": round((trough / a[4] - 1) * 100, 2),
        "giveback": round((tod[4] / peak - 1) * 100, 2) if peak else None,
    }


def direction_label(side: int, ret: float | None) -> str:
    if ret is None:
        return "无法评分"
    if side > 0:
        if ret > 5:
            return "对"
        if ret < -10:
            return "错"
        if ret < -5:
            return "偏错"
        return "平"
    if ret < -8:
        return "对"
    if ret > 10:
        return "错"
    if ret > 5:
        return "偏错"
    return "平"


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _median(xs):
    vals = [x for x in xs if x is not None]
    return round(float(median(vals)), 2) if vals else None


def bucket(rows: list[dict], key: str) -> dict:
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(str(row.get(key)), []).append(row)
    out = {}
    for name, items in sorted(groups.items()):
        dw = [r["dir_window"] for r in items]
        out[name] = {
            "n": len(items),
            "对": dw.count("对"),
            "偏错": dw.count("偏错"),
            "错": dw.count("错"),
            "平": dw.count("平"),
            "copy_window": _mean([r["copy_window"] for r in items]),
            "copy_todate": _mean([r["copy_todate"] for r in items]),
            "hit_rate": round(dw.count("对") / len(items), 3) if items else None,
        }
    return out


def call_span(rows: list) -> str:
    years = _row_years(rows)
    if not years:
        return ""
    lo, hi = min(years), max(years)
    return str(lo) if lo == hi else f"{lo}–{hi}"


def coverage_kicker(sc: dict) -> str:
    parts = []
    registered = sc.get("registered")
    if registered:
        parts.append(f"注册 {registered}")
    span = sc.get("call_span") or call_span(sc.get("rows") or [])
    if span:
        parts.append(f"可证伪判断 {span}")
    depth = sc.get("corpus_depth")
    if depth == "posts_only":
        parts.append("帖子全量，无作者评论线程")
    elif depth == "thin":
        parts.append("薄样本")
    elif depth == "deep":
        parts.append("含作者评论")
    return " · ".join(parts)


def load_sibling_posts(work_dir: Path) -> list | None:
    path = Path(work_dir) / "posts.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    posts = raw.get("posts") or raw.get("corpus")
    return posts if isinstance(posts, list) else None


def registered_year_from_profile(profile: dict | None) -> int | None:
    if not isinstance(profile, dict):
        return None
    user = profile.get("user") if isinstance(profile.get("user"), dict) else profile
    for key in ("created_at", "created"):
        ts = user.get(key)
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if isinstance(ts, (int, float)) and ts > 0:
            if ts > 1e12:
                ts = ts / 1000
            try:
                return datetime.fromtimestamp(ts, TZ).year
            except Exception:
                return None
    raw = user.get("created_str") or profile.get("registered")
    if raw:
        text = str(raw)
        if len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
    return None


def registered_year_from_profile_path(path: Path) -> int | None:
    dest = Path(path)
    if not dest.exists():
        return None
    try:
        return registered_year_from_profile(json.loads(dest.read_text(encoding="utf-8")))
    except Exception:
        return None


def infer_corpus_depth(work_dir: Path, payload: dict | None = None) -> dict:
    info = {"depth": "", "author_comments": 0, "has_comments_file": False}
    dest = Path(work_dir)
    comments = dest / "comments.json"
    manifest_path = dest / "manifest.json"
    if comments.exists():
        info["has_comments_file"] = True
        try:
            data = json.loads(comments.read_text(encoding="utf-8"))
        except Exception:
            data = []
        rows = data if isinstance(data, list) else (data.get("comments") or [])
        info["author_comments"] = sum(1 for row in rows if isinstance(row, dict) and row.get("is_author"))
        info["depth"] = "deep" if info["author_comments"] else "posts_only"
        return info
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        sources = [str(item) for item in (manifest.get("sources") or [])]
        coverage = str(manifest.get("coverage") or "")
        if coverage == "thin" or any("rss" in item for item in sources):
            info["depth"] = "thin"
        elif coverage == "deep" or any("comment" in item for item in sources):
            info["depth"] = "deep"
        elif coverage in {"full", "vpush-full"} or sources:
            info["depth"] = "posts_only"
        return info
    if (payload or {}).get("coverage") == "thin":
        info["depth"] = "thin"
    return info


def score_calls(
    payload: dict,
    price_dir: Path,
    asof: date | None = None,
    posts: list | None = None,
    registered: int | None = None,
    corpus_depth: str | None = None,
) -> dict:
    problems = validate_calls(payload)
    if problems:
        raise ValueError("；".join(problems[:8]))
    calls = payload.get("calls") or payload.get("rows") or []
    if not calls:
        raise ValueError("calls.json 里没有 calls")
    asof = asof or parse_day(str(payload.get("asof") or date.today().isoformat()))
    cache: dict[str, list] = {}

    def series(sym: str):
        if sym not in cache:
            path = price_dir / f"{sym}.json"
            if not path.exists():
                raise FileNotFoundError(sym)
            cache[sym] = load_price_file(path)
        return cache[sym]

    rows = []
    unscored = []
    for call in calls:
        sym = call["symbol"]
        try:
            stats = window_stats(series(sym), parse_day(call["date"]), float(call["horizon_m"]), asof)
        except FileNotFoundError:
            unscored.append(
                {
                    "id": call.get("id"),
                    "date": call.get("date"),
                    "symbol": sym,
                    "theme": call.get("theme"),
                    "reason": "missing_price",
                }
            )
            continue
        wret = (stats or {}).get("window", {}).get("ret")
        tret = (stats or {}).get("todate", {}).get("ret")
        side = int(call["side"])
        price = None
        pt = call.get("price_target")
        if isinstance(pt, dict) and pt.get("lo") is not None and pt.get("symbol"):
            try:
                px = series(pt["symbol"])
            except FileNotFoundError:
                px = []
            start = parse_day(call["date"])
            end = start + timedelta(days=int(pt.get("window_days") or 10))
            lows = [row[3] for row in px if start <= row[0] <= end]
            min_low = min(lows) if lows else None
            hit = min_low is not None and float(pt["lo"]) <= min_low <= float(pt["hi"])
            price = {
                "label": pt.get("label") or f"{pt['symbol']} {pt['lo']}-{pt['hi']}",
                "min_low": round(min_low, 2) if min_low is not None else None,
                "verdict": "对" if hit else "错",
            }
        elif isinstance(pt, dict):
            price = {
                "label": pt.get("label") or "价位",
                "verdict": pt.get("hit") is True and "对" or pt.get("verdict") or "错",
                "note": pt.get("note") or pt.get("label"),
            }
        rows.append(
            {
                **{k: call.get(k) for k in ("id", "date", "theme", "side", "symbol", "horizon_m", "kind", "cat", "note")},
                "dir_window": direction_label(side, wret),
                "dir_todate": direction_label(side, tret),
                "copy_window": round(side * wret, 2) if wret is not None else None,
                "copy_todate": round(side * tret, 2) if tret is not None else None,
                "window_ret": wret,
                "todate_ret": tret,
                "giveback": (stats or {}).get("giveback"),
                "price": price,
                "short_window": float(call["horizon_m"]) < 3,
                "stats": stats,
            }
        )

    struct = [r for r in rows if r.get("kind") == "structure"]
    tact = [r for r in rows if r.get("kind") == "tactical"]

    def pack_kind(items):
        dw = [r["dir_window"] for r in items]
        return {
            "n": len(items),
            "dir": {k: dw.count(k) for k in ("对", "平", "偏错", "错")},
            "copy_window": _mean([r["copy_window"] for r in items]),
            "copy_window_median": _median([r["copy_window"] for r in items]),
            "copy_todate": _mean([r["copy_todate"] for r in items]),
        }

    dw = [r["dir_window"] for r in rows]
    summary = {
        "n": len(rows),
        "dir_window": {k: dw.count(k) for k in ("对", "平", "偏错", "错")},
        "copy_window_mean": _mean([r["copy_window"] for r in rows]),
        "copy_window_median": _median([r["copy_window"] for r in rows]),
        "copy_todate_mean": _mean([r["copy_todate"] for r in rows]),
        "copy_todate_median": _median([r["copy_todate"] for r in rows]),
        "structure": pack_kind(struct),
        "tactical": pack_kind(tact),
        "by_year": bucket([{**r, "year": str(r.get("date", ""))[:4]} for r in rows], "year"),
        "by_cat": bucket(rows, "cat"),
        "price_targets": [{"id": r.get("id"), "date": r.get("date"), **r["price"]} for r in rows if r.get("price")],
    }

    baselines = {}
    for call in struct:
        if call.get("copy_todate") is None:
            continue
        key = f"{'long' if call['side'] > 0 else 'short'}_{call['symbol']}_{call['date']}"
        baselines[key] = {
            "desc": f"{call['date']} 起一直{'多' if call['side'] > 0 else '空'}{call.get('theme') or call['symbol']}",
            "copy": call.get("copy_todate"),
            "ret": call.get("todate_ret"),
        }

    scored = {
        "title": payload.get("title") or "公开预测审计",
        "account": payload.get("account") or "",
        "uid": payload.get("uid") or "",
        "home": payload.get("home") or (f"https://xueqiu.com/u/{payload['uid']}" if payload.get("uid") else ""),
        "generated": datetime.now(TZ).isoformat(),
        "asof": str(asof),
        "n": len(rows),
        "price_basis": payload.get("price_basis") or "前复权收盘",
        "corpus": payload.get("corpus") or "",
        "coverage": payload.get("coverage") or "scored",
        "registered": registered or payload.get("registered"),
        "call_span": call_span(rows),
        "corpus_depth": corpus_depth or payload.get("corpus_depth") or "",
        "unscored": unscored,
        "rules": {
            "include": "有日期、有明确多空、能对流动标的；同一论点只取首次清楚表述，翻案或新价位另计",
            "exclude": "段子、复述、当天情绪、纯框架无方向",
            "direction": "多头窗口>+5%为对、<-10%为错；空头窗口<-8%为对、>+10%为错",
            "copy": "照做=方向符号×标的涨跌，等权，不自动对冲沪深300",
        },
        "summary": summary,
        "baselines": baselines,
        "rows": rows,
    }
    if payload.get("conclusion"):
        scored["conclusion"] = payload["conclusion"]
        scored["conclusion_source"] = "author"
    else:
        scored["conclusion_source"] = "auto"
    if payload.get("playbook"):
        scored["playbook"] = payload["playbook"]
        scored["playbook_source"] = "author"
    else:
        scored["playbook_source"] = "auto"
    if payload.get("briefs"):
        scored["briefs"] = payload["briefs"]
        scored["briefs_source"] = "author"
    else:
        scored["briefs"] = auto_briefs(scored)
        scored["briefs_source"] = "auto"
    scored["persona"] = auto_persona(scored, posts)
    scored["consistency"] = auto_consistency(scored, posts)
    scored["mbti"] = auto_mbti(scored, posts)
    return scored


def pct(value) -> str:
    if value is None:
        return "—"
    return f"{value:+.0f}%" if abs(value) >= 10 or float(value).is_integer() else f"{value:+.1f}%".replace(".0%", "%")


def pct_fine(value) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def auto_conclusion(sc: dict) -> str:
    s = sc["summary"]
    st, ta = s.get("structure") or {}, s.get("tactical") or {}
    pts = s.get("price_targets") or []
    hit_pt = sum(1 for p in pts if p.get("verdict") == "对")
    parts = [
        f"长周期产业判断和短线买卖点要分开看。{st.get('n', 0)} 条结构里 {(st.get('dir') or {}).get('对', 0)} 条窗口方向对；",
        f"{ta.get('n', 0)} 条战术里 {(ta.get('dir') or {}).get('对', 0)} 条对，照做中位 {pct(ta.get('copy_window_median'))}。",
    ]
    if pts:
        parts.append(f"数字价位 {hit_pt} / {len(pts)} 打中。")
    bases = [b for b in (sc.get("baselines") or {}).values() if b.get("copy") is not None]
    bases.sort(key=lambda x: x.get("copy") or 0, reverse=True)
    if bases:
        top = "、".join(f"{b['desc']}（{pct(b['copy'])}）" for b in bases[:2])
        parts.append(f"朴素对照里最稳的是{top}。")
    parts.append(
        f"{s.get('n', 0)} 条等权照做中位 {pct(s.get('copy_window_median'))}，均值 {pct(s.get('copy_window_mean'))}，均值会被单笔大票拉高。"
    )
    if sc.get("coverage") == "thin":
        parts.append("本次是薄样本（长文/热门/近页），不是全时间线生涯审计。")
    return "".join(parts)


ROUND_HINTS = ("十倍", "百倍", "翻倍", "千亿", "万亿")
ADMIT_HINTS = ("卖飞", "认错", "反思", "纠错")
RETRO_HINTS = ("当初没人信", "早就说过", "我说的没错", "又验证了", "我一直看多", "我一直看空")


def persona_level(sc: dict) -> str:
    rows = list(sc.get("rows") or [])
    years = _row_years(rows)
    span = (max(years) - min(years) + 1) if years else 0
    n = len(rows)
    if n < 8 or span < 2 or sc.get("coverage") == "thin":
        return "draft"
    if n >= 20 and span >= 4 and sc.get("coverage") != "thin":
        return "profile"
    return "portrait"


def _row_years(rows: list[dict]) -> list[int]:
    years = []
    for row in rows:
        year = str(row.get("date") or "")[:4]
        if year.isdigit():
            years.append(int(year))
    return years


def flip_events(rows: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    flips: list[dict] = []
    for row in sorted(rows, key=lambda item: item.get("date") or ""):
        symbol = row.get("symbol")
        try:
            side = int(row.get("side") or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or side not in {1, -1}:
            continue
        prev = latest.get(symbol)
        if prev and int(prev.get("side") or 0) != side:
            flips.append({"from": prev, "to": row})
        latest[symbol] = row
    return flips


def auto_persona(sc: dict, posts: list | None = None) -> dict:
    rows = list(sc.get("rows") or [])
    summary = sc.get("summary") or {}
    structure = summary.get("structure") or {}
    tactical = summary.get("tactical") or {}
    targets = summary.get("price_targets") or []
    years = _row_years(rows)
    span = (max(years) - min(years) + 1) if years else 0
    n = len(rows)
    level = persona_level(sc)
    draft = level == "draft"
    traits: list[dict] = []

    st_n = int(structure.get("n") or 0)
    ta_n = int(tactical.get("n") or 0)
    st_hit = int((structure.get("dir") or {}).get("对") or 0)
    ta_hit = int((tactical.get("dir") or {}).get("对") or 0)
    if st_n or ta_n:
        st_rate = st_hit / st_n if st_n else 0
        ta_rate = ta_hit / ta_n if ta_n else 0
        if st_n >= 3 and st_rate >= ta_rate + 0.15:
            name = "结构比点位稳"
        elif ta_n >= 3 and ta_rate >= st_rate + 0.15:
            name = "点位比结构稳"
        else:
            name = "结构和点位要分开看"
        traits.append(
            {
                "name": name,
                "evidence": (
                    f"结构 {st_hit}/{st_n} 对，战术 {ta_hit}/{ta_n} 对。"
                    f"战术照做中位 {pct(tactical.get('copy_window_median'))}。"
                ),
            }
        )

    flips = flip_events(rows)
    if flips:
        examples = "、".join(
            f"{item['from'].get('date')} "
            f"{'多' if int(item['from'].get('side') or 0) > 0 else '空'}→"
            f"{item['to'].get('date')} "
            f"{'多' if int(item['to'].get('side') or 0) > 0 else '空'} "
            f"{item['to'].get('symbol')}"
            for item in flips[:3]
        )
        traits.append({"name": "同一标的会翻案", "evidence": f"{len(flips)} 次方向对调。例如 {examples}。"})

    roundish: list[dict] = []
    seen_round: set[tuple] = set()
    for row in rows:
        text = f"{row.get('theme') or ''} {row.get('note') or ''}"
        if any(hint in text for hint in ROUND_HINTS):
            key = (row.get("date"), row.get("theme"))
            if key not in seen_round:
                seen_round.add(key)
                roundish.append(row)
    for item in targets:
        text = f"{item.get('label') or ''} {item.get('note') or ''}"
        if any(hint in text for hint in ROUND_HINTS):
            key = (item.get("date"), item.get("label"))
            if key not in seen_round:
                seen_round.add(key)
                roundish.append({"date": item.get("date"), "theme": item.get("label")})
    if roundish:
        shown = "、".join(f"{row.get('date')} {row.get('theme')}" for row in roundish[:3])
        traits.append({"name": "爱喊数量级", "evidence": shown + "。"})

    givebacks = [
        row
        for row in rows
        if (row.get("giveback") is not None and row["giveback"] <= -40)
        or (
            row.get("dir_window") == "对"
            and row.get("copy_todate") is not None
            and row.get("copy_window")
            and row["copy_todate"] < row["copy_window"] - 30
        )
    ]
    if givebacks:
        worst = min(givebacks, key=lambda row: row.get("giveback") if row.get("giveback") is not None else 0)
        traits.append(
            {
                "name": "窗口对也常拿不住",
                "evidence": (
                    f"{worst.get('date')} {worst.get('theme')} 窗口 {pct(worst.get('copy_window'))}，"
                    f"高点回撤 {pct(worst.get('giveback'))}。"
                ),
            }
        )

    admits = [
        row
        for row in rows
        if any(hint in f"{row.get('theme') or ''}{row.get('note') or ''}" for hint in ADMIT_HINTS)
    ]
    if admits:
        last = admits[-1]
        traits.append({"name": "事后会认卖飞", "evidence": f"{last.get('date')} {last.get('theme')}。"})

    if posts:
        blob = "".join(str(post.get("text") or post.get("description") or "") for post in posts)
        voice = [f"「{word}」{blob.count(word)} 次" for word in ("老登", "一定能看到", "卖飞", "击球") if blob.count(word) >= 3]
        if voice:
            traits.append(
                {
                    "name": "公开话语有固定口头禅",
                    "evidence": "语料里出现 " + "、".join(voice[:4]) + "。只统计用词，不当性格量表。",
                }
            )

    traits = traits[:5]
    names = "，".join(trait["name"] for trait in traits[:3]) or "公开判断有迹可循"
    if draft:
        headline = "样本偏短或偏薄，下面只是跟单习惯草稿，不是人格测写。"
    elif level == "profile":
        headline = f"样本够跨年，可做公开人格侧写：{names}。跨 {span} 个自然年、{n} 条可证伪方向。"
    else:
        headline = f"公开行为上更像：{names}。跨 {span} 个自然年、{n} 条可证伪方向。不是人格测写。"
    return {
        "draft": draft,
        "level": level,
        "headline": headline,
        "traits": traits,
        "note": "这是公开行为画像，不是心理诊断或人格量表。",
    }


def _consistency_relevant(text: str, rows: list[dict]) -> bool:
    symbols = {row.get("symbol") for row in rows if row.get("symbol")}
    if any(symbol and symbol in text for symbol in symbols):
        return True
    keys = ("科创", "创新药", "硅光", "光模块", "万科", "寒武纪", "机器人", "光伏")
    return any(key in text for key in keys)


def auto_consistency(sc: dict, posts: list | None = None) -> dict:
    rows = list(sc.get("rows") or [])
    flips: list[dict] = []
    admits: list[dict] = []
    retros: list[dict] = []
    for event in flip_events(rows):
        later = event["to"]
        earlier = event["from"]
        text = f"{later.get('theme') or ''}{later.get('note') or ''}"
        admitted = any(hint in text for hint in ADMIT_HINTS)
        flips.append(
            {
                "kind": "翻案",
                "claim": f"{later.get('date')} {later.get('theme')}",
                "record": (
                    f"{earlier.get('date')} 起是"
                    f"{'多' if int(earlier.get('side') or 0) > 0 else '空'} {earlier.get('symbol')}"
                ),
                "verdict": "对得上" if admitted else "对不上",
            }
        )
    for row in sorted(rows, key=lambda item: item.get("date") or ""):
        text = f"{row.get('theme') or ''}{row.get('note') or ''}"
        if not any(hint in text for hint in ADMIT_HINTS):
            continue
        prior = [
            other
            for other in rows
            if other.get("symbol") == row.get("symbol")
            and (other.get("date") or "") < (row.get("date") or "")
            and other.get("dir_window") == "错"
        ]
        if prior:
            last = prior[-1]
            admits.append(
                {
                    "kind": "事后改口",
                    "claim": f"{row.get('date')} {row.get('theme')}",
                    "record": f"{last.get('date')} {last.get('theme')} 窗口方向{last.get('dir_window')}",
                    "verdict": "对得上",
                }
            )
    if posts:
        if not admits:
            dated = sorted(
                (
                    (post_day(post), vpush.strip_html(str(post.get("text") or post.get("description") or "")))
                    for post in posts
                ),
                key=lambda item: item[0].isoformat() if item[0] else "",
            )
            for day, text in dated:
                if not day or not any(hint in text for hint in ADMIT_HINTS):
                    continue
                if not _consistency_relevant(text, rows):
                    continue
                prior = [
                    other
                    for other in rows
                    if (other.get("date") or "") < day.isoformat() and other.get("dir_window") == "错"
                ]
                if not prior:
                    continue
                last = prior[-1]
                admits.append(
                    {
                        "kind": "事后改口",
                        "claim": f"{day} {text[:48]}",
                        "record": f"{last.get('date')} {last.get('theme')} 窗口方向{last.get('dir_window')}",
                        "verdict": "对得上",
                    }
                )
                break
        for post in posts:
            text = vpush.strip_html(str(post.get("text") or post.get("description") or ""))
            hit = next((hint for hint in RETRO_HINTS if hint in text), None)
            if not hit:
                continue
            if not _consistency_relevant(text, rows):
                continue
            day = post_day(post)
            retros.append(
                {
                    "kind": "事后叙事",
                    "claim": f"{day or ''} {hit}：{text[:48]}",
                    "record": "对照首次入选日，不要把复盘当成当时判断",
                    "verdict": "需对照",
                }
            )
            if len(retros) >= 3:
                break
    unexplained = [item for item in flips if item.get("verdict") == "对不上"]
    unexplained.sort(key=lambda item: item.get("claim") or "", reverse=True)
    items = admits[:2] + unexplained[:4] + retros[:2]
    mismatch = sum(1 for item in items if item.get("verdict") == "对不上")
    if mismatch:
        headline = f"{mismatch} 处公开表述和计分表对不上。"
    elif items:
        headline = "有翻案或事后叙事，先对表再下结论。"
    else:
        headline = "这批样本里没有自动对上的表述冲突。"
    return {
        "headline": headline,
        "items": items,
        "note": "对照的是公开表述和计分表，不是测谎。",
    }


MBTI_LEAN = {"E": "外向", "I": "内向", "S": "实感", "N": "直觉", "T": "思考", "F": "情感", "J": "判断", "P": "知觉"}
MBTI_S = ("点位", "均线", "开仓", "清仓", "止盈", "反弹", "波段", "现在可以")
MBTI_N = ("硅光", "周期", "浪潮", "产业", "牛市", "朱格拉", "主升", "领头羊", "硬核")
MBTI_T = ("业绩", "ETF", "市值", "计算器", "仓位", "净利润")
MBTI_F = ("心态", "卖飞", "纠错", "杂念", "认知不足")
MBTI_J = ("拿到结束", "一定", "注定", "终点", "计划", "锁仓")
MBTI_P = ("逢低", "反弹", "击球", "现在可以", "波段")


def _hits(text: str, words: tuple[str, ...]) -> int:
    return sum(text.count(word) for word in words)


def _mbti_pick(left: str, right: str, left_n: int, right_n: int, tie: str) -> tuple[str, int, int]:
    if left_n > right_n:
        return left, left_n, right_n
    if right_n > left_n:
        return right, right_n, left_n
    return tie, left_n, right_n


def auto_mbti(sc: dict, posts: list | None = None) -> dict:
    rows = list(sc.get("rows") or [])
    summary = sc.get("summary") or {}
    note = "这是公开发帖和计分表的 MBTI 风格对照，不是量表，也不是心理诊断。"
    if persona_level(sc) == "draft":
        return {"draft": True, "type": "", "headline": "样本偏短或偏薄，不做 MBTI 对照。", "axes": [], "note": note}
    themes = " ".join(f"{row.get('theme') or ''} {row.get('note') or ''}" for row in rows)
    reply_n = 0
    orig_n = 0
    blob = themes
    if posts:
        chunks = []
        for post in posts:
            text = vpush.strip_html(str(post.get("text") or post.get("description") or ""))
            chunks.append(text)
            if text.startswith("回复") or "回复 @" in text[:24]:
                reply_n += 1
            else:
                orig_n += 1
        blob = themes + " " + " ".join(chunks)
    st_n = int((summary.get("structure") or {}).get("n") or 0) or sum(1 for row in rows if row.get("kind") == "structure")
    ta_n = int((summary.get("tactical") or {}).get("n") or 0) or sum(1 for row in rows if row.get("kind") == "tactical")
    e_n = reply_n + blob.count("老登") + blob.count("对线") + themes.count("喷")
    i_n = orig_n + themes.count("框架")
    if not posts:
        i_n += st_n
    s_n = ta_n + _hits(themes, MBTI_S)
    n_n = st_n + _hits(themes, MBTI_N) + sum(1 for hint in ROUND_HINTS if hint in themes)
    t_n = _hits(themes, MBTI_T) + len(summary.get("price_targets") or [])
    f_n = _hits(themes, MBTI_F)
    flips = flip_events(rows)
    j_n = _hits(themes, MBTI_J)
    p_n = len(flips) * 2 + _hits(themes, MBTI_P)
    ei, _, _ = _mbti_pick("E", "I", e_n, i_n, "E" if reply_n > orig_n else "I")
    sn, _, _ = _mbti_pick("S", "N", s_n, n_n, "N" if st_n >= ta_n else "S")
    tf, _, _ = _mbti_pick("T", "F", t_n, f_n, "T")
    jp, _, _ = _mbti_pick("J", "P", j_n, p_n, "P" if flips else "J")
    typ = f"{ei}{sn}{tf}{jp}"
    axes = [
        {
            "axis": "E/I",
            "letter": ei,
            "lean": MBTI_LEAN[ei],
            "evidence": (
                f"语料回复 {reply_n}、原创 {orig_n}，"
                f"「老登」{blob.count('老登')} 次、「对线」{blob.count('对线')} 次。"
                if posts
                else f"公开判断里结构 {st_n}、战术 {ta_n}，对线词 {themes.count('对线')} 次。"
            ),
        },
        {
            "axis": "S/N",
            "letter": sn,
            "lean": MBTI_LEAN[sn],
            "evidence": f"战术 {ta_n} / 结构 {st_n}。点位词 {_hits(themes, MBTI_S)}，产业/浪潮词 {_hits(themes, MBTI_N)}。",
        },
        {
            "axis": "T/F",
            "letter": tf,
            "lean": MBTI_LEAN[tf],
            "evidence": f"业绩/仓位词 {t_n}，心态/卖飞词 {f_n}。",
        },
        {
            "axis": "J/P",
            "letter": jp,
            "lean": MBTI_LEAN[jp],
            "evidence": f"同一标的翻案 {len(flips)} 次。计划/终点词 {j_n}，逢低/波段词 {p_n}。",
        },
    ]
    return {
        "draft": False,
        "type": typ,
        "headline": f"公开文本对照偏 {typ}。不是量表。",
        "axes": axes,
        "note": note,
    }


def auto_playbook(sc: dict) -> list[str]:
    rows = sc.get("rows") or []
    recent_bad = [
        r
        for r in rows
        if str(r.get("date", "")).startswith("202")
        and r.get("kind") == "tactical"
        and r.get("dir_window") in {"错", "偏错"}
    ]
    recent_bad.sort(key=lambda x: x.get("date") or "", reverse=True)
    examples = "、".join((r.get("theme") or "")[:16] for r in recent_bad[:4]) or "近端买卖点"
    return [
        "产业框架可以参考。公开组合和当天情绪帖不能跟。",
        "最稳的仓位看对照表，不看粉丝和组合净值。结构对了也要接受从高点腰斩。",
        f"数字价位和「现在可以买 / 卖」作废。近端反例：{examples}。",
    ]


def auto_briefs(sc: dict) -> dict:
    s = sc.get("summary") or {}
    st, ta = s.get("structure") or {}, s.get("tactical") or {}
    rows = list(sc.get("rows") or [])
    years = s.get("by_year") or {}
    cats = s.get("by_cat") or {}
    pts = s.get("price_targets") or []
    bases = [b for b in (sc.get("baselines") or {}).values() if b.get("copy") is not None]
    bases.sort(key=lambda item: item.get("copy") or 0, reverse=True)
    top = "、".join(f"{b.get('desc')}（{pct(b.get('copy'))}）" for b in bases[:2])
    drag = "、".join(f"{b.get('desc')}（{pct(b.get('copy'))}）" for b in bases[-2:] if (b.get("copy") or 0) < 0)
    copy = (
        f"{s.get('n', 0)} 条等权中位 {pct(s.get('copy_window_median'))}，均值 {pct(s.get('copy_window_mean'))}。"
        f"只做结构中位 {pct(st.get('copy_window_median'))}，只做战术中位 {pct(ta.get('copy_window_median'))}。"
        "均值会被单笔大票拉开，跟单看中位。"
    )
    if top:
        copy += f"对照表最稳的是{top}。"
    if drag:
        copy += f"结构里拖累的是{drag}。"

    year_bits = [
        f"{name} 年 {item.get('n')} 条、命中 {round((item.get('hit_rate') or 0) * 100)}%、照做 {pct(item.get('copy_window'))}"
        for name, item in years.items()
    ]
    year = "；".join(year_bits) + "。" if year_bits else "分年样本不够，不单写趋势。"
    names = list(years)
    if len(names) >= 2:
        first, last = years[names[0]], years[names[-1]]
        a, b = first.get("hit_rate") or 0, last.get("hit_rate") or 0
        if b < a - 0.05:
            year += f"命中从 {names[0]} 滑到 {names[-1]}，近端窗口还没走完，不能外推。"
        elif b > a + 0.05:
            year += f"命中从 {names[0]} 升到 {names[-1]}，仍要看近端窗口是否走完。"

    scored_cats = [(name, item) for name, item in cats.items() if item.get("n")]
    theme = "主题样本偏少，分主题只作对照，不单独加权。"
    if scored_cats:
        best = max(scored_cats, key=lambda item: item[1].get("copy_window") or 0)
        miss = [item for item in scored_cats if (item[1].get("n") or 0) >= 2 and (item[1].get("hit_rate") or 0) <= 0.34]
        theme = f"照做最正的主题是{best[0]}（{pct(best[1].get('copy_window'))}，{best[1].get('n')} 条）。"
        if miss:
            theme += "命中偏低的是" + "、".join(f"{name} {item.get('对')}/{item.get('n')}" for name, item in miss[:3]) + "。"
        theme += "主题不能和方向命中混成一句准。"

    hit_pt = sum(1 for item in pts if item.get("verdict") == "对")
    price = "本样本没有单独计分的数字价位。"
    if pts:
        price = f"数字价位 {hit_pt} / {len(pts)} 打中。方向对了不代表价位对；没打中的不要用后来的大行情回改。"

    scored = [row for row in rows if row.get("dir_window") not in {None, "无法评分"}]
    hits = [row for row in scored if row.get("dir_window") == "对"]
    misses = [row for row in scored if row.get("dir_window") == "错"]
    best_row = max(scored, key=lambda row: row.get("copy_window") or 0) if scored else None
    worst_row = min(scored, key=lambda row: row.get("copy_window") or 0) if scored else None
    give = min(scored, key=lambda row: row.get("giveback") if row.get("giveback") is not None else 0) if scored else None
    detail = f"明细 {len(scored)} 条里窗口对 {len(hits)}、错 {len(misses)}。"
    if best_row:
        detail += f"最大正贡献是 {best_row.get('date')} {best_row.get('theme')}（照做 {pct(best_row.get('copy_window'))}）。"
    if worst_row and (worst_row.get("copy_window") or 0) < 0:
        detail += f"最拖累的是 {worst_row.get('date')} {worst_row.get('theme')}（照做 {pct(worst_row.get('copy_window'))}）。"
    if give and give.get("giveback") is not None and give["giveback"] <= -40:
        detail += f"回吐最大的是 {give.get('date')} {give.get('theme')}，高点回撤 {pct(give.get('giveback'))}。"
    detail += "近端窗口不足的先当未完成，不要写成已经错或已经对。"
    return {"copy": copy, "year": year, "theme": theme, "price": price, "detail": detail}


def giveback_cards(sc: dict) -> list[dict]:
    cards = []
    for row in sc.get("rows") or []:
        gb = row.get("giveback")
        if gb is None:
            continue
        if gb <= -40 or (
            row.get("dir_window") == "对"
            and row.get("copy_todate") is not None
            and row.get("copy_window")
            and row["copy_todate"] < row["copy_window"] - 30
        ):
            cards.append(row)
    cards.sort(key=lambda r: (r.get("giveback") or 0))
    return cards[:3]


def render_html(sc: dict) -> str:
    s = sc["summary"]
    st, ta = s.get("structure") or {}, s.get("tactical") or {}
    pts = s.get("price_targets") or []
    hit_pt = sum(1 for p in pts if p.get("verdict") == "对")
    years = s.get("by_year") or {}
    cats = s.get("by_cat") or {}
    rows = sc.get("rows") or []
    bases = sc.get("baselines") or {}
    home = sc.get("home") or ""
    uid = sc.get("uid") or ""
    title = html.escape(sc.get("title") or "公开预测审计")
    conclusion = html.escape(sc.get("conclusion") or auto_conclusion(sc))
    playbook = sc.get("playbook") or auto_playbook(sc)
    briefs = sc.get("briefs") or auto_briefs(sc)
    persona = sc.get("persona") or auto_persona(sc)
    consistency = sc.get("consistency") or auto_consistency(sc)
    mbti = sc.get("mbti") or auto_mbti(sc)
    cards = giveback_cards(sc)
    year_hits = [round((v.get("hit_rate") or 0) * 100) for _, v in years.items()]

    def bar_svg(values: list[int], labels: list[str]) -> str:
        if not values:
            return ""
        w, h, left, top, bottom = 680, 150, 28, 20, 30
        inner_h = h - top - bottom
        gap = 18
        bw = max(12, int((680 - left - 20 - gap * (len(values) - 1)) / len(values)))
        parts = [
            f'<svg class="plot" viewBox="0 0 {w} {h}" role="img" aria-label="各年命中率">',
            '<g class="grid">',
            f'<line x1="{left}" y1="{top}" x2="670" y2="{top}" /><text class="tick" x="{left-4}" y="{top+4}" text-anchor="end">100</text>',
            f'<line x1="{left}" y1="{top+inner_h/2}" x2="670" y2="{top+inner_h/2}" /><text class="tick" x="{left-4}" y="{top+inner_h/2+4}" text-anchor="end">50</text>',
            f'<line x1="{left}" y1="{top+inner_h}" x2="670" y2="{top+inner_h}" /><text class="tick" x="{left-4}" y="{top+inner_h+4}" text-anchor="end">0</text>',
            "</g>",
        ]
        x = left + 12
        for val, lab in zip(values, labels):
            bh = inner_h * max(0, min(100, val)) / 100
            parts.append(f'<rect class="bar" x="{x}" y="{top+inner_h-bh}" width="{bw}" height="{bh}" />')
            parts.append(f'<text class="tick" x="{x+bw/2}" y="{h-8}" text-anchor="middle">{html.escape(str(lab)[-2:])}</text>')
            x += bw + gap
        parts.append("</svg>")
        return "\n".join(parts)

    def table(headers, body_rows, right=None):
        right = set(right or [])
        th = "".join(
            f'<th class="r">{html.escape(h)}</th>' if i in right else f"<th>{html.escape(h)}</th>"
            for i, h in enumerate(headers)
        )
        trs = []
        for row in body_rows:
            tds = []
            for i, cell in enumerate(row):
                cls = ' class="r"' if i in right else ""
                tds.append(f"<td{cls}>{html.escape(str(cell))}</td>")
            trs.append("<tr>" + "".join(tds) + "</tr>")
        return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

    year_rows = [
        [y, v["n"], v["对"], f"{round((v.get('hit_rate') or 0)*100)}%", pct(v.get("copy_window"))]
        for y, v in years.items()
    ]
    cat_rows = [
        [c, v["n"], f"{round((v.get('hit_rate') or 0)*100)}%", pct(v.get("copy_window"))]
        for c, v in cats.items()
    ]
    base_rows = [[b.get("desc") or k, pct(b.get("copy"))] for k, b in bases.items()]
    if st:
        base_rows.insert(0, [f"只做 {st.get('n', 0)} 条结构（中位）", pct(st.get("copy_window_median"))])
    if ta:
        base_rows.append([f"只做 {ta.get('n', 0)} 条战术（中位）", pct(ta.get("copy_window_median"))])
    flip_rows = []
    for p in pts:
        flip_rows.append([p.get("date") or "", p.get("label") or "", p.get("note") or p.get("verdict") or ""])
    detail = []
    for r in rows:
        detail.append(
            [
                r.get("date") or "",
                "多" if r.get("side", 0) > 0 else "空",
                "结构" if r.get("kind") == "structure" else "战术",
                r.get("theme") or "",
                f"{r.get('symbol')} {pct(r.get('window_ret'))}",
                pct(r.get("copy_window")),
                pct(r.get("copy_todate")),
            ]
        )
    pills = "".join(
        f'<div class="row"><span class="pill">{i}</span><p>{html.escape(text)}</p></div>'
        for i, text in enumerate(playbook, 1)
    )
    persona_rows = [[t.get("name") or "", t.get("evidence") or ""] for t in persona.get("traits") or []]
    tbl_persona = (
        table(["习惯", "证据"], persona_rows)
        if persona_rows
        else "<p class='small'>样本不够，不写行为画像。</p>"
    )
    mbti_rows = [
        [a.get("axis") or "", f"{a.get('letter') or ''} {a.get('lean') or ''}".strip(), a.get("evidence") or ""]
        for a in mbti.get("axes") or []
    ]
    tbl_mbti = (
        table(["维度", "倾向", "证据"], mbti_rows)
        if mbti_rows
        else "<p class='small'>样本不够，不做 MBTI 对照。</p>"
    )
    persona_html = (
        f'<div class="stack"><h2>行为画像</h2>'
        f'<p>{html.escape(persona.get("headline") or "")}</p>'
        f"{tbl_persona}"
        f'<p>{html.escape(mbti.get("headline") or "")}</p>'
        f"{tbl_mbti}"
        f'<p class="small">{html.escape(persona.get("note") or "这是公开行为画像，不是心理诊断或人格量表。")}</p>'
        f'<p class="small">{html.escape(mbti.get("note") or "这是公开发帖和计分表的 MBTI 风格对照，不是量表，也不是心理诊断。")}</p>'
        "</div>"
    )
    consist_rows = [
        [i.get("kind") or "", i.get("claim") or "", i.get("record") or "", i.get("verdict") or ""]
        for i in consistency.get("items") or []
    ]
    tbl_consist = (
        table(["类型", "后来怎么说", "当时怎么写", "对照"], consist_rows)
        if consist_rows
        else "<p class='small'>这批样本里没有自动对上的表述冲突。</p>"
    )
    consist_html = (
        f'<div class="stack"><h2>表述对照</h2>'
        f'<p>{html.escape(consistency.get("headline") or "")}</p>'
        f"{tbl_consist}"
        f'<p class="small">{html.escape(consistency.get("note") or "对照的是公开表述和计分表，不是测谎。")}</p>'
        "</div>"
    )
    card_html = "".join(
        f'<div class="card"><h4>{html.escape(r.get("theme") or r.get("symbol") or "")}</h4>'
        f"<p>窗口 {pct(r.get('copy_window'))}。至今 {pct(r.get('copy_todate'))}。高点回撤 {pct(r.get('giveback'))}。</p></div>"
        for r in cards
    )
    kicker = coverage_kicker(sc)
    unscored_n = len(sc.get("unscored") or [])
    meta = " · ".join(
        x
        for x in [
            html.escape(sc.get("account") or "") + (f"（UID {html.escape(uid)}）" if uid else ""),
            html.escape(kicker) if kicker else "",
            f"{sc.get('n')} 条可证伪方向",
            f"{unscored_n} 条缺行情未打分" if unscored_n else "",
            f"价格截止 {html.escape(str(sc.get('asof') or ''))}",
            html.escape(sc.get("price_basis") or ""),
            "薄样本" if sc.get("coverage") == "thin" and "薄样本" not in kicker else "",
        ]
        if x and x not in {"（UID ）"}
    )
    tbl_base = table(["做法", "结果"], base_rows, right={1})
    tbl_year = table(["年", "条数", "对", "命中", "照做"], year_rows, right={1, 2, 3, 4})
    tbl_cat = table(["主题", "条数", "命中", "照做"], cat_rows, right={1, 2, 3})
    tbl_flip = table(["日期", "说法", "结果"], flip_rows) if flip_rows else "<p class='small'>本样本没有单独计分的数字价位。</p>"
    tbl_detail = table(["日期", "向", "类", "判断", "窗口", "照做", "至今"], detail, right={5, 6})
    bar_chart = bar_svg(year_hits, list(years.keys()))

    def note(key: str) -> str:
        text = (briefs or {}).get(key) or ""
        return f'<p class="brief">{html.escape(text)}</p>' if text else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
  :root {{
    --bg:#ffffff; --fg:#141414; --fg-2:rgba(20,20,20,.74); --fg-3:rgba(20,20,20,.5);
    --stroke:rgba(20,20,20,.12); --stroke-2:rgba(20,20,20,.08);
    --fill:rgba(20,20,20,.06); --fill-2:rgba(20,20,20,.04); --link:#2e79b5;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  html,body {{ background:var(--bg); color:var(--fg); height:auto; overflow:visible; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB",sans-serif; font-size:14px; line-height:20px; }}
  .sheet {{ width:720px; margin:0 auto; padding:20px 20px 28px; display:flex; flex-direction:column; gap:20px; }}
  h1 {{ font-size:24px; line-height:30px; font-weight:590; letter-spacing:-.02em; }}
  h2 {{ font-size:18px; line-height:24px; font-weight:590; }}
  h3 {{ font-size:16px; line-height:22px; font-weight:590; }}
  .sec,.small {{ color:var(--fg-2); }}
  .small {{ font-size:12px; line-height:16px; }}
  a {{ color:var(--link); }}
  .callout,.stat {{ background:var(--fill); border-radius:6px; }}
  .callout {{ border:1px solid var(--stroke); padding:12px 14px; }}
  .callout strong {{ display:block; font-weight:590; margin-bottom:4px; }}
  .grid-4 {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
  .grid-3 {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
  .stat {{ padding:10px 12px; }}
  .stat b {{ display:block; font-size:22px; line-height:28px; font-weight:590; font-variant-numeric:tabular-nums; }}
  .stat span {{ display:block; margin-top:2px; font-size:12px; color:var(--fg-2); }}
  .row {{ display:flex; align-items:center; gap:8px; }}
  .pill {{ min-width:22px; height:22px; padding:0 7px; border-radius:999px; background:var(--fill); border:1px solid var(--stroke); font-size:12px; line-height:20px; text-align:center; font-weight:590; }}
  .card {{ background:#fff; border:1px solid var(--stroke); border-radius:8px; overflow:hidden; }}
  .card h4 {{ font-size:14px; font-weight:590; padding:10px 12px 0; }}
  .card p {{ padding:6px 12px 12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; line-height:16px; font-variant-numeric:tabular-nums; }}
  th {{ text-align:left; font-weight:590; color:var(--fg-2); border-bottom:1px solid var(--stroke); padding:6px 8px 6px 0; }}
  td {{ padding:5px 8px 5px 0; border-bottom:1px solid var(--stroke-2); vertical-align:top; }}
  tr:nth-child(even) td {{ background:var(--fill-2); }}
  .r {{ text-align:right; }}
  .stack {{ display:flex; flex-direction:column; gap:8px; }}
  .brief {{ color:var(--fg); }}
  svg.plot {{ display:block; width:100%; }}
  svg.plot .grid line {{ stroke:var(--stroke); }}
  svg.plot .tick {{ fill:var(--fg-3); font-size:10px; font-family:-apple-system,"PingFang SC",sans-serif; }}
  svg.plot .bar {{ fill:#6b6b86; }}
  @media print {{
    html,body {{ background:#fff !important; color:#141414 !important; height:auto !important; overflow:visible !important; }}
    .sheet {{ width:720px; margin:0 auto; padding:20px; }}
    thead {{ display:table-row-group; }}
    .callout,.stat,.card,tr {{ break-inside:auto; page-break-inside:auto; }}
    -webkit-print-color-adjust:exact; print-color-adjust:exact;
  }}
</style>
</head>
<body>
<main class="sheet">
  <div class="stack" style="gap:6px">
    <h1>{title}</h1>
    <p class="sec">{meta}</p>
  </div>
  <div class="callout"><strong>结论</strong>{conclusion}</div>
  <div class="grid-4">
    <div class="stat"><b>{(st.get('dir') or {}).get('对', 0)} / {st.get('n', 0)}</b><span>结构方向对</span></div>
    <div class="stat"><b>{(ta.get('dir') or {}).get('对', 0)} / {ta.get('n', 0)}</b><span>战术方向对</span></div>
    <div class="stat"><b>{html.escape(pct(ta.get('copy_window_median')))}</b><span>战术照做中位</span></div>
    <div class="stat"><b>{hit_pt} / {len(pts)}</b><span>数字价位打中</span></div>
  </div>
  <div class="stack"><h2>跟单口径</h2>{pills}</div>
  {persona_html}
  {consist_html}
  <div class="stack">
    <h2>照做与对照</h2>
    <p>{sc.get('n')} 条等权窗口：均值 {html.escape(pct(s.get('copy_window_mean')))}，中位 {html.escape(pct(s.get('copy_window_median')))}。只做结构中位 {html.escape(pct(st.get('copy_window_median')))}；只做战术中位 {html.escape(pct(ta.get('copy_window_median')))}。</p>
    {tbl_base}
    {note("copy")}
  </div>
  <div class="stack">
    <h2>分年</h2>
    <p class="small">命中 = 窗口方向对 / 当年条数</p>
    {tbl_year}
    {bar_chart}
    {note("year")}
  </div>
  <div class="stack">
    <h2>分主题</h2>
    {tbl_cat}
    {note("theme")}
  </div>
  <div class="stack">
    <h2>价位与立场翻转</h2>
    {tbl_flip}
    {note("price")}
  </div>
  <div class="stack">
    <h2>窗口对、拿到现在不是一回事</h2>
    <div class="grid-3">{card_html or "<p class='small'>没有需要单独强调的高点回撤。</p>"}</div>
  </div>
  <div class="stack">
    <h2>{len(rows)} 条明细</h2>
    <p class="small">同一论点只记首次清楚表述。照做 = 多空符号 × 窗口涨跌。</p>
    {tbl_detail}
    {note("detail")}
  </div>
  <div class="callout"><strong>方法</strong>入选：有日期、有明确多空、能对流动标的。排除：段子、复述、当天情绪。方向：多头窗口 &gt;+5% 为对、&lt;−10% 为错；空头 &lt;−8% 为对、&gt;+10% 为错。照做等权，不自动对冲沪深300，不是实盘成交。不是投资建议。</div>
  <p class="small">{f'<a href="{html.escape(home)}">{html.escape(home.replace("https://", ""))}</a>' if home else ""}{" · " + html.escape(sc.get("corpus") or "") if sc.get("corpus") else ""}</p>
</main>
</body>
</html>
"""


def render_cubes_html(payload: dict) -> str:
    title = html.escape(payload.get("title") or "雪球组合量化")
    account = html.escape(payload.get("account") or "")
    uid = html.escape(str(payload.get("uid") or ""))
    home = payload.get("home") or ""
    cubes = [c for c in (payload.get("cubes") or []) if not c.get("skip")]
    skipped = [c for c in (payload.get("cubes") or []) if c.get("skip")]
    show_ann = any(c.get("ann") is not None for c in cubes)
    sections = []
    for cube in cubes:
        rows = [[cube.get("name") or cube.get("symbol"), pct_fine(cube.get("ret")), pct_fine(cube.get("ann"))]]
        for b in cube.get("benchmarks") or []:
            rows.append([b.get("name"), pct_fine(b.get("ret")), pct_fine(b.get("ann"))])
        if show_ann:
            th = "<tr><th>标的</th><th class='r'>累计收益</th><th class='r'>年化收益</th></tr>"
            body = "".join(
                f"<tr><td>{html.escape(str(a))}</td><td class='r'>{html.escape(str(b))}</td><td class='r'>{html.escape(str(c))}</td></tr>"
                for a, b, c in rows
            )
        else:
            th = "<tr><th>标的</th><th class='r'>累计收益</th></tr>"
            body = "".join(
                f"<tr><td>{html.escape(str(a))}</td><td class='r'>{html.escape(str(b))}</td></tr>"
                for a, b, _c in rows
            )
        market = MARKET_LABEL.get(str(cube.get("market") or "").lower(), cube.get("market") or "")
        flags = []
        if market:
            flags.append(str(market))
        if cube.get("stopped"):
            flags.append("已停更")
        if cube.get("paper_only"):
            flags.append("非实盘")
        flag = (" · " + " · ".join(flags)) if flags else ""
        sections.append(
            f"""
  <div class="stack">
    <h2>{html.escape(cube.get('headline') or cube.get('name') or '')}</h2>
    <p class="small">{html.escape(str(cube.get('from')))} 至 {html.escape(str(cube.get('to')))} · {cube.get('days')} 天{html.escape(flag)}</p>
    <table><thead>{th}</thead><tbody>{body}</tbody></table>
    <p>{html.escape(cube.get('blurb') or '')}</p>
  </div>"""
        )
    skip_html = ""
    if skipped:
        items = "、".join(f"{html.escape(c.get('name') or c.get('symbol') or '')}（{html.escape(c.get('skip') or '')}）" for c in skipped)
        skip_html = f"<p class='small'>未纳入：{items}</p>"
    window = payload.get("window") or {}
    window_bit = ""
    if window.get("from") or window.get("to"):
        window_bit = f"指定观察期 {window.get('from') or '组合首日'} 至 {window.get('to') or '组合末日'}"
    meta = " · ".join(
        x
        for x in [
            account + (f"（UID {uid}）" if uid else ""),
            f"{len(cubes)} 个可计算组合",
            window_bit,
            "公开模拟盘，不是实盘",
        ]
        if x
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
  :root {{ --bg:#fff; --fg:#141414; --fg-2:rgba(20,20,20,.74); --stroke:rgba(20,20,20,.12); --fill:rgba(20,20,20,.06); --link:#2e79b5; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  html,body {{ background:var(--bg); color:var(--fg); height:auto; overflow:visible; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB",sans-serif; font-size:14px; line-height:20px; }}
  .sheet {{ width:720px; margin:0 auto; padding:20px 20px 28px; display:flex; flex-direction:column; gap:20px; }}
  h1 {{ font-size:24px; line-height:30px; font-weight:590; }}
  h2 {{ font-size:18px; line-height:24px; font-weight:590; }}
  .small,.sec {{ color:var(--fg-2); font-size:12px; line-height:16px; }}
  a {{ color:var(--link); }}
  .callout {{ background:var(--fill); border:1px solid var(--stroke); border-radius:6px; padding:12px 14px; }}
  .stack {{ display:flex; flex-direction:column; gap:8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; font-variant-numeric:tabular-nums; }}
  th {{ text-align:left; color:var(--fg-2); border-bottom:1px solid var(--stroke); padding:6px 8px 6px 0; }}
  td {{ padding:5px 8px 5px 0; border-bottom:1px solid rgba(20,20,20,.08); }}
  .r {{ text-align:right; }}
  @media print {{ html,body {{ background:#fff; height:auto !important; overflow:visible !important; }} .sheet {{ width:720px; margin:0 auto; padding:20px; }} thead {{ display:table-row-group; }} -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
</style>
</head>
<body>
<main class="sheet">
  <div class="stack" style="gap:6px"><h1>{title}</h1><p class="sec">{meta}</p></div>
  <div class="callout"><strong>口径</strong>雪球组合是公开模拟盘。作者写明与实盘不重合的，净值不当实盘战绩，也不并进预测命中。对照基准用同一起止日的前复权/指数点位。不是投资建议。</div>
  {''.join(sections) or "<p>没有可计算的组合净值。</p>"}
  {skip_html}
  <p class="small">{f'<a href="{html.escape(home)}">{html.escape(home.replace("https://", ""))}</a>' if home else ""}</p>
</main>
</body>
</html>
"""


PAGE_HEIGHT_RE = re.compile(r'data-page-height="(\d+)"')
PAGE_RULE_RE = re.compile(r"@page\s*\{[^}]*\}")


def inject_measure_script(html: str) -> str:
    style = (
        "<style>html,body{height:auto!important;overflow:visible!important;}"
        "@page{size:auto;margin:0;}</style>"
    )
    script = (
        "<script>(function(){"
        "var sheet=document.querySelector('main.sheet,.sheet,main');"
        "var h=0;"
        "if(sheet){var r=sheet.getBoundingClientRect();"
        "h=Math.ceil(r.height+r.top+(window.scrollY||0)+24);}"
        "h=Math.max(h,document.documentElement.scrollHeight||0,"
        "document.body&&document.body.scrollHeight||0);"
        'document.documentElement.setAttribute("data-page-height",String(h));'
        "})();</script>"
    )
    out = html
    if "</head>" in out:
        out = out.replace("</head>", style + "\n</head>", 1)
    if "</body>" in out:
        return out.replace("</body>", script + "\n</body>", 1)
    return out + style + script


def inject_clip_css(html: str, offset_px: int, tile_h: int) -> str:
    css = (
        f"<style>html,body{{height:{int(tile_h)}px!important;overflow:hidden!important;}}"
        f".xq-clip{{width:760px;height:{int(tile_h)}px;overflow:hidden;}}"
        f".xq-inner{{position:relative;top:-{int(offset_px)}px;}}</style>"
    )
    if "</head>" in html:
        html = html.replace("</head>", css + "</head>", 1)
    else:
        html = css + html
    html = re.sub(r"<body([^>]*)>", r"<body\1><div class='xq-clip'><div class='xq-inner'>", html, count=1)
    if "</body>" in html:
        html = html.replace("</body>", "</div></div></body>", 1)
    return html


def apply_long_page_css(html: str, height_px: int, width_px: int = 760) -> str:
    height_px = max(800, int(height_px))
    rule = f"@page {{ size: {width_px}px {height_px}px; margin: 0; }}"
    if PAGE_RULE_RE.search(html):
        return PAGE_RULE_RE.sub(rule, html, count=2)
    return html.replace("</style>", rule + "\n</style>", 1)


def estimate_html_height_px(html: str) -> int:
    rows = html.count("<tr>")
    heads = html.count("<h2") + html.count("<h1")
    return 720 + rows * 48 + heads * 80


def _png_paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path: Path) -> tuple[int, int, int, bytes]:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk[:13])
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
    if width is None or bit_depth != 8 or color_type not in {2, 6} or interlace:
        raise ValueError("unsupported png")
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[bytes] = []
    index = 0
    prev = bytearray(stride)
    for _ in range(height):
        filt = raw[index]
        index += 1
        scan = bytearray(raw[index : index + stride])
        index += stride
        if filt == 1:
            for x in range(stride):
                left = scan[x - channels] if x >= channels else 0
                scan[x] = (scan[x] + left) & 255
        elif filt == 2:
            for x in range(stride):
                scan[x] = (scan[x] + prev[x]) & 255
        elif filt == 3:
            for x in range(stride):
                left = scan[x - channels] if x >= channels else 0
                scan[x] = (scan[x] + ((left + prev[x]) // 2)) & 255
        elif filt == 4:
            for x in range(stride):
                left = scan[x - channels] if x >= channels else 0
                up = prev[x]
                ul = prev[x - channels] if x >= channels else 0
                scan[x] = (scan[x] + _png_paeth(left, up, ul)) & 255
        elif filt != 0:
            raise ValueError(f"png filter {filt}")
        rows.append(bytes(scan))
        prev = scan
    return width, height, channels, b"".join(rows)


def write_png(path: Path, width: int, height: int, channels: int, pixels: bytes) -> None:
    color = 2 if channels == 3 else 6

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    raw = bytearray()
    stride = width * channels
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : (y + 1) * stride]
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def vstack_pngs(paths: list[Path], dest: Path) -> None:
    frames = [read_png(p) for p in paths]
    width, _h0, channels, _p0 = frames[0]
    if any(item[0] != width or item[2] != channels for item in frames):
        raise ValueError("png stack mismatch")
    height = sum(item[1] for item in frames)
    write_png(dest, width, height, channels, b"".join(item[3] for item in frames))


def pdf_page_count(path: Path) -> int:
    raw = Path(path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))


def pdf_media_boxes(path: Path) -> list[tuple[float, float, float, float]]:
    raw = Path(path).read_bytes()
    boxes = []
    for match in re.finditer(
        rb"/MediaBox\s*\[\s*([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s*\]",
        raw,
    ):
        boxes.append(tuple(float(part) for part in match.groups()))
    return boxes


def png_pixel_size(path: Path) -> tuple[int, int]:
    raw = Path(path).read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    return struct.unpack(">II", raw[16:24])


def write_single_image_pdf(
    image: bytes,
    width_px: int,
    height_px: int,
    dest: Path,
    page_width_pt: float = 760.0,
    filter_name: str = "DCTDecode",
) -> None:
    """One-page PDF. MediaBox follows the image, never a leftover Letter box."""
    width_px = max(1, int(width_px))
    height_px = max(1, int(height_px))
    page_h = page_width_pt * height_px / width_px
    page_w_s = f"{page_width_pt:.2f}".rstrip("0").rstrip(".")
    page_h_s = f"{page_h:.2f}".rstrip("0").rstrip(".")
    content = f"q\n{page_w_s} 0 0 {page_h_s} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w_s} {page_h_s}] "
            f"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width_px} /Height {height_px} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /{filter_name} "
            f"/Length {len(image)} >>"
        ).encode("ascii"),
        f"<< /Length {len(content)} >>".encode("ascii"),
    ]
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{index} 0 obj\n".encode("ascii")
        buf += body
        if index == 4:
            buf += b"\nstream\n"
            buf += image
            buf += b"\nendstream"
        elif index == 5:
            buf += b"\nstream\n"
            buf += content
            buf += b"endstream"
        buf += b"\nendobj\n"
    xref_at = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets[1:]:
        buf += f"{offset:010d} 00000 n \n".encode("ascii")
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    dest.write_bytes(buf)


def chrome_bin() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ]
    for item in candidates:
        path = Path(item)
        if path.exists():
            return str(path)
        if "/" not in item:
            from shutil import which

            found = which(item)
            if found:
                return found
    return None
