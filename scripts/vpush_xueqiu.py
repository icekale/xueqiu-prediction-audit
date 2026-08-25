"""V Push 雪球抓取桥：读 waf-bot sidecar cookie，规范化 UID / 正文。

不内置挑战求解器。waf-bot 只作为已运行 sidecar 的 cookie 来源。
禁止打印 cookie / token。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))

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


def format_created(value: Any) -> str:
    try:
        ts = int(value)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value or "")


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
    return cleaned


def prefer_full_text(item: dict) -> str:
    desc = str(item.get("description") or "")
    text = str(item.get("text") or item.get("content") or "")
    if desc.rstrip().endswith(("…", "...")) and len(text) > len(desc):
        return text
    if len(text) > len(desc):
        return text
    return text or desc


def extract_images(status: dict) -> list[str]:
    out: list[str] = []
    for key in ("original_pictures", "pics"):
        for pic in status.get(key) or []:
            url = (pic or {}).get("url") or ""
            if url.startswith("//"):
                url = f"https:{url}"
            if url and url not in out:
                out.append(url)
            if len(out) >= 8:
                return out
    for url in str(status.get("pic") or "").split(","):
        url = url.strip()
        if url.startswith("//"):
            url = f"https:{url}"
        if "!" in url:
            url = url.split("!", 1)[0]
        if url and url not in out:
            out.append(url)
        if len(out) >= 8:
            break
    return out


def status_url(status: dict) -> str:
    target = str(status.get("target") or status.get("url") or "")
    if target.startswith("http"):
        return target
    if target.startswith("/"):
        return f"https://xueqiu.com{target}"
    sid = status.get("id")
    user = status.get("user") or {}
    uid = user.get("id") if isinstance(user, dict) else None
    if sid and uid:
        return f"https://xueqiu.com/{uid}/{sid}"
    if sid:
        return f"https://xueqiu.com/{sid}"
    return ""


def comment_user_id(comment: dict) -> str:
    if comment.get("user_id") not in (None, ""):
        return str(comment.get("user_id"))
    user = comment.get("user")
    if isinstance(user, dict) and user.get("id") not in (None, ""):
        return str(user.get("id"))
    return ""


def slim_comment(comment: dict, author_uid: str = "") -> dict:
    user = comment.get("user") or {}
    if not isinstance(user, dict):
        user = {"screen_name": user}
    parent = comment.get("reply_comment") or comment.get("in_reply_to_comment") or {}
    if not isinstance(parent, dict):
        parent = {}
    parent_user = parent.get("user") or {}
    if not isinstance(parent_user, dict):
        parent_user = {"screen_name": parent_user}
    uid = comment_user_id(comment)
    return {
        "id": comment.get("id"),
        "status_id": comment.get("status_id") or comment.get("statusId"),
        "created_at": comment.get("created_at") or comment.get("created"),
        "created_str": format_created(comment.get("created_at") or comment.get("created") or comment.get("created_str")),
        "text": prefer_full_text(comment),
        "user": user.get("screen_name") or comment.get("screen_name") or "",
        "user_id": uid,
        "like_count": comment.get("like_count") or comment.get("fav_count") or 0,
        "parent_text": prefer_full_text(parent) if parent else comment.get("parent_text") or "",
        "parent_user": parent_user.get("screen_name") or comment.get("parent_user") or "",
        "is_author": bool(author_uid) and uid == str(author_uid),
    }


def parse_comments_payload(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("comments", "list", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def select_comment_targets(posts: list[dict], limit: int = 80) -> list[dict]:
    ranked = []
    for post in posts:
        if not post.get("id"):
            continue
        if post.get("post_type") is None and post.get("retweeted_status"):
            continue
        count = int(post.get("comment_count") or post.get("reply_count") or 0)
        ranked.append((count, post.get("created_at") or 0, post))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    picked = [post for count, _, post in ranked if count > 0][:limit]
    if len(picked) >= limit:
        return picked
    have = {p.get("id") for p in picked}
    for _, _, post in ranked:
        if post.get("id") in have:
            continue
        picked.append(post)
        if len(picked) >= limit:
            break
    return picked


def author_comment_items(comments: list[dict], author_uid: str) -> list[dict]:
    out = []
    for raw in comments:
        comment = raw if "is_author" in raw else slim_comment(raw, author_uid)
        if author_uid and not comment.get("is_author") and comment_user_id(comment) != str(author_uid):
            continue
        if not author_uid and not comment.get("is_author"):
            continue
        body = strip_html(comment.get("text") or "")
        parent = strip_html(comment.get("parent_text") or "")
        if parent:
            who = comment.get("parent_user") or "他人"
            body = f"{body} （回复 @{who}：{parent}）"
        if not body:
            continue
        out.append(
            {
                "id": f"c-{comment.get('id')}",
                "source": "comment",
                "status_id": comment.get("status_id"),
                "created_at": comment.get("created_at"),
                "created_str": comment.get("created_str") or "",
                "title": "",
                "text": body,
                "description": body,
                "user": comment.get("user"),
                "user_id": comment.get("user_id"),
                "post_type": "comment",
            }
        )
    return out


def is_waf_html(text: str, content_type: str = "") -> bool:
    sample = text[:4000] if text else ""
    if any(marker in sample for marker in WAF_MARKERS):
        return True
    ctype = (content_type or "").lower()
    if "text/html" in ctype and sample.lstrip().startswith("<"):
        return True
    return bool(sample.lstrip().startswith("<") and ("html" in sample[:200].lower() or "aliyun" in sample.lower()))
