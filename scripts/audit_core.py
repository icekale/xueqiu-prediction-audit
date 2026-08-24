#!/usr/bin/env python3
"""Cookie, prices, scoring, and light HTML report. Never print secrets."""
from __future__ import annotations

import html
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree as ET

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


def read_cookie() -> str:
    env = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env:
        return env
    file_env = os.environ.get("XUEQIU_COOKIE_FILE", "").strip()
    paths = []
    if file_env:
        paths.append(Path(file_env))
    paths.append(COOKIE_PATH)
    vpush = os.environ.get("VPUSH_CONFIG", "").strip()
    if vpush:
        paths.append(Path(vpush))
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if "xq_a_token" in raw and ("cookie:" in raw or path.suffix in {".yaml", ".yml"}):
            match = re.search(r'cookie:\s*["\']([^"\']*xq_a_token[^"\']*)["\']', raw)
            if match:
                return match.group(1).strip()
        if "xq_a_token" in raw or "u=" in raw:
            return raw.strip().splitlines()[0].strip()
    return ""


def write_cookie(cookie: str) -> Path:
    if not cookie.strip():
        raise ValueError("empty cookie")
    config_dir()
    COOKIE_PATH.write_text(cookie.strip() + "\n", encoding="utf-8")
    COOKIE_PATH.chmod(0o600)
    return COOKIE_PATH


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
            text = raw.decode("utf-8", "replace")
            if text.lstrip().startswith("<") or "aliyun_waf" in text[:500]:
                raise RuntimeError("blocked_html")
            if text.startswith("kline_") and "=" in text[:40]:
                text = text.split("=", 1)[1]
            return json.loads(text)
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
    retweet = status.get("retweeted_status") or {}
    created = status.get("created_at")
    created_str = ""
    try:
        ts = int(created)
        if ts > 1e12:
            ts /= 1000
        created_str = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        created_str = str(created or "")
    return {
        "id": status.get("id"),
        "created_at": created,
        "created_str": created_str,
        "title": status.get("title") or "",
        "text": status.get("text") or "",
        "description": status.get("description") or "",
        "like_count": status.get("like_count") or status.get("fav_count"),
        "view_count": status.get("view_count"),
        "type": status.get("type"),
        "retweeted_text": retweet.get("text") or retweet.get("description") or "",
        "user": user.get("screen_name"),
    }


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
) -> dict:
    if len(nav) < 5:
        return {
            "symbol": meta.get("symbol"),
            "name": meta.get("name"),
            "skip": "净值点太少",
        }
    start, end = nav[0][0], nav[-1][0]
    asof = asof or end
    path = path_return_nav(nav, start, end)
    if not path:
        return {"symbol": meta.get("symbol"), "name": meta.get("name"), "skip": "无法计算收益"}
    desc = str(meta.get("description") or "")
    paper = any(key in desc for key in ("不重合", "不建议跟", "模拟", "非实盘"))
    stopped = (asof - end).days >= 30
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
        cubes.append(analyze_cube(meta, block["nav"], bench_navs, asof=asof))
    usable = sum(1 for cube in cubes if not cube.get("skip"))
    who = account or (f"UID {uid}" if uid else "")
    return {
        "title": title or (f"{who}公开组合量化" if who else "雪球组合量化"),
        "account": account,
        "uid": uid,
        "home": home or (f"https://xueqiu.com/u/{uid}" if uid else ""),
        "asof": str(asof),
        "usable": usable,
        "cubes": cubes,
    }


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
        text = item.get("text") or item.get("description") or item.get("content") or ""
        text = re.sub(r"<[^>]+>", " ", str(text))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        out.append(
            {
                "id": item.get("id") or item.get("status_id"),
                "created_at": item.get("created_at") or item.get("created"),
                "created_str": item.get("created_str") or item.get("date") or "",
                "title": item.get("title") or "",
                "text": text,
                "description": item.get("description") or text,
                "user": item.get("user") or item.get("screen_name"),
            }
        )
    return out


def parse_day(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


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


def fetch_yahoo(symbol: str) -> list[tuple]:
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


def score_calls(payload: dict, price_dir: Path, asof: date | None = None) -> dict:
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
    missing = []
    for call in calls:
        sym = call["symbol"]
        try:
            stats = window_stats(series(sym), parse_day(call["date"]), float(call["horizon_m"]), asof)
        except FileNotFoundError:
            missing.append(sym)
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
    if missing:
        raise FileNotFoundError("缺少行情: " + ", ".join(dict.fromkeys(missing)))

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

    return {
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
    card_html = "".join(
        f'<div class="card"><h4>{html.escape(r.get("theme") or r.get("symbol") or "")}</h4>'
        f"<p>窗口 {pct(r.get('copy_window'))}。至今 {pct(r.get('copy_todate'))}。高点回撤 {pct(r.get('giveback'))}。</p></div>"
        for r in cards
    )
    meta = " · ".join(
        x
        for x in [
            html.escape(sc.get("account") or "") + (f"（UID {html.escape(uid)}）" if uid else ""),
            f"{sc.get('n')} 条可证伪方向",
            f"价格截止 {html.escape(str(sc.get('asof') or ''))}",
            html.escape(sc.get("price_basis") or ""),
            "薄样本" if sc.get("coverage") == "thin" else "",
        ]
        if x and x not in {"（UID ）"}
    )
    tbl_base = table(["做法", "结果"], base_rows, right={1})
    tbl_year = table(["年", "条数", "对", "命中", "照做"], year_rows, right={1, 2, 3, 4})
    tbl_cat = table(["主题", "条数", "命中", "照做"], cat_rows, right={1, 2, 3})
    tbl_flip = table(["日期", "说法", "结果"], flip_rows) if flip_rows else "<p class='small'>本样本没有单独计分的数字价位。</p>"
    tbl_detail = table(["日期", "向", "类", "判断", "窗口", "照做", "至今"], detail, right={5, 6})
    bar_chart = bar_svg(year_hits, list(years.keys()))
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
  html,body {{ background:var(--bg); color:var(--fg); }}
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
  svg.plot {{ display:block; width:100%; }}
  svg.plot .grid line {{ stroke:var(--stroke); }}
  svg.plot .tick {{ fill:var(--fg-3); font-size:10px; font-family:-apple-system,"PingFang SC",sans-serif; }}
  svg.plot .bar {{ fill:#6b6b86; }}
  @media print {{
    html,body {{ background:#fff !important; color:#141414 !important; }}
    .sheet {{ width:auto; padding:0; }}
    .callout,.stat,.card {{ break-inside:avoid; }}
    tr {{ break-inside:avoid; }}
    -webkit-print-color-adjust:exact; print-color-adjust:exact;
  }}
  @page {{ size:A4; margin:14mm 12mm; }}
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
  <div class="stack">
    <h2>照做与对照</h2>
    <p>{sc.get('n')} 条等权窗口：均值 {html.escape(pct(s.get('copy_window_mean')))}，中位 {html.escape(pct(s.get('copy_window_median')))}。只做结构中位 {html.escape(pct(st.get('copy_window_median')))}；只做战术中位 {html.escape(pct(ta.get('copy_window_median')))}。</p>
    {tbl_base}
  </div>
  <div class="stack">
    <h2>分年</h2>
    <p class="small">命中 = 窗口方向对 / 当年条数</p>
    {tbl_year}
    {bar_chart}
  </div>
  <div class="stack">
    <h2>分主题</h2>
    {tbl_cat}
  </div>
  <div class="stack">
    <h2>价位与立场翻转</h2>
    {tbl_flip}
  </div>
  <div class="stack">
    <h2>窗口对、拿到现在不是一回事</h2>
    <div class="grid-3">{card_html or "<p class='small'>没有需要单独强调的高点回撤。</p>"}</div>
  </div>
  <div class="stack">
    <h2>{len(rows)} 条明细</h2>
    <p class="small">同一论点只记首次清楚表述。照做 = 多空符号 × 窗口涨跌。</p>
    {tbl_detail}
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
    meta = " · ".join(x for x in [account + (f"（UID {uid}）" if uid else ""), f"{len(cubes)} 个可计算组合", "公开模拟盘，不是实盘"] if x)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
  :root {{ --bg:#fff; --fg:#141414; --fg-2:rgba(20,20,20,.74); --stroke:rgba(20,20,20,.12); --fill:rgba(20,20,20,.06); --link:#2e79b5; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  html,body {{ background:var(--bg); color:var(--fg); }}
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
  @media print {{ html,body {{ background:#fff; }} .sheet {{ width:auto; padding:0; }} -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  @page {{ size:A4; margin:14mm 12mm; }}
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
