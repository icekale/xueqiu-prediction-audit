"""V Push 雪球抓取桥：读 waf-bot sidecar cookie，规范化 UID / 正文。

不内置挑战求解器。waf-bot 只作为已运行 sidecar 的 cookie 来源。
禁止打印 cookie / token。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "xueqiu-prediction-audit"
DEFAULT_SIDECAR = CONFIG_DIR / "waf_cookies.json"
DOCKER_SIDECAR = Path("/data/waf_cookies.json")
WAF_MARKERS = ("aliyun_waf", "renderData", "acw_sc__v2")


def cookie_sha256(cookie: str) -> str:
    return hashlib.sha256((cookie or "").strip().encode()).hexdigest()


def sidecar_candidates() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("WAF_COOKIE_FILE", "").strip()
    if env:
        paths.append(Path(env).expanduser())
    paths.append(DEFAULT_SIDECAR)
    paths.append(DOCKER_SIDECAR)
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        resolved = path if not path.exists() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def find_sidecar() -> Path | None:
    for path in sidecar_candidates():
        if path.is_file():
            return path
    return None


def load_sidecar(path: Path | None = None) -> dict[str, Any] | None:
    target = path or find_sidecar()
    if target is None or not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    cookies = data.get("cookies")
    if not isinstance(cookies, list):
        return None
    return data


def sidecar_cookie_string(data: dict[str, Any]) -> str:
    parts = []
    for item in data.get("cookies") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def merge_waf_cookie(cookie: str, sidecar_path: Path | None = None) -> str:
    """有匹配 sidecar 时整套使用；seed 对不上则沿用登录串；无登录串则用 sidecar。"""
    data = load_sidecar(sidecar_path)
    if not data:
        return cookie or ""
    sidecar = sidecar_cookie_string(data)
    if not sidecar:
        return cookie or ""
    seed = data.get("seed_sha256") or ""
    if cookie and seed and seed != cookie_sha256(cookie):
        return cookie
    return sidecar


def sidecar_status(login_cookie: str = "") -> str:
    path = find_sidecar()
    if path is None:
        return "missing"
    data = load_sidecar(path)
    if not data:
        return f"unreadable ({path})"
    fetched = data.get("fetched_at")
    try:
        age = max(0, int(time.time()) - int(fetched or 0))
        age_s = f"{age}s"
    except (TypeError, ValueError):
        age_s = "unknown"
    n = len(
        [
            c
            for c in (data.get("cookies") or [])
            if isinstance(c, dict) and c.get("name")
        ]
    )
    seed = data.get("seed_sha256") or ""
    if login_cookie and seed:
        match = "yes" if seed == cookie_sha256(login_cookie) else "no"
    else:
        match = "na"
    return f"ok age={age_s} cookies={n} seed_match={match}"


def write_sidecar(data: dict[str, Any], dest: Path | None = None) -> Path:
    target = dest or DEFAULT_SIDECAR
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass
    payload = {
        "fetched_at": int(data.get("fetched_at") or time.time()),
        "seed_sha256": data.get("seed_sha256") or "",
        "cookies": data.get("cookies") or [],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def parse_cookie_payload(raw: str) -> tuple[str, dict[str, Any] | None]:
    """接受 cookie 文本或 waf-bot 的 waf_cookies.json。"""
    text = (raw or "").strip()
    if not text:
        return "", None
    if text[:1] in "{[":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            cookie = sidecar_cookie_string(data)
            return cookie, data
        if isinstance(data, list):
            wrapped = {"cookies": data, "fetched_at": int(time.time())}
            return sidecar_cookie_string(wrapped), wrapped
    if "cookie:" in text and "xq_a_token" in text:
        match = re.search(r'cookie:\s*["\']([^"\']*xq_a_token[^"\']*)["\']', text)
        if match:
            return match.group(1).strip(), None
    return text.splitlines()[0].strip(), None


def normalize_xueqiu_id(external_id: str | None) -> str:
    value = (external_id or "").strip()
    match = re.search(r"xueqiu\.com/u/(\d+)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d+", value):
        return value
    return value


def classify_status(status: dict) -> str | None:
    desc = (status.get("description") or "").lstrip()
    if desc.startswith("回复") and status.get("commentId"):
        return "reply"
    if status.get("retweeted_status"):
        return None
    return "post"


def prefer_full_text(item: dict) -> str:
    desc = str(item.get("description") or "")
    text = str(item.get("text") or item.get("content") or "")
    if desc.rstrip().endswith(("…", "...")) and len(text) > len(desc):
        return text
    return text or desc


def is_waf_html(text: str, content_type: str = "") -> bool:
    sample = text[:4000] if text else ""
    if any(marker in sample for marker in WAF_MARKERS):
        return True
    ctype = (content_type or "").lower()
    if "text/html" in ctype and sample.lstrip().startswith("<"):
        return True
    return bool(sample.lstrip().startswith("<") and ("html" in sample[:200].lower() or "aliyun" in sample.lower()))
