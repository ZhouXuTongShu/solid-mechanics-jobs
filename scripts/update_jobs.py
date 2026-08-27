#!/usr/bin/env python3
"""Verify employer-owned job pages and discover matching roles from official sites.

The script intentionally uses only Python's standard library so that it can run in
GitHub Actions or on a clean macOS installation without dependency management.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "data" / "jobs.js"
JSON_MIRROR = PROJECT_ROOT / "data" / "jobs.json"
WATCH_FILE = PROJECT_ROOT / "data" / "watch_sources.json"
MAX_RESPONSE_BYTES = 2_500_000
CHINA_TIMEZONE = dt.timezone(dt.timedelta(hours=8))
MIN_REACHABLE_DIVISOR = 5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "SolidMechanicsJobRadar/1.0"
)

EXPIRED_MARKERS = (
    "该职位已下线",
    "该职位已关闭",
    "职位已下线",
    "职位已停止招聘",
    "招聘已结束",
    "申请已关闭",
    "position has been filled",
    "job is no longer available",
    "this job has expired",
)
OPEN_MARKERS = (
    "2027届",
    "2027校招",
    "校园招聘",
    "立即投递",
    "申请职位",
    "投递简历",
    "查看职位",
)
ROLE_KEYWORDS = (
    "固体力学",
    "计算力学",
    "工程力学",
    "有限元",
    "fea",
    "cae",
    "cfd",
    "结构仿真",
    "力学仿真",
    "多物理场",
    "流固耦合",
    "热力耦合",
    "热结构",
    "结构强度",
    "强度工程师",
    "结构动力学",
    "冲击",
    "碰撞安全",
    "断裂",
    "疲劳",
    "nvh",
    "振动噪声",
    "声学仿真",
    "可靠性仿真",
    "封装仿真",
    "求解器",
    "仿真开发",
)
HIGH_MATCH_KEYWORDS = (
    "固体力学",
    "计算力学",
    "有限元",
    "fea",
    "cae",
    "流固耦合",
    "断裂",
    "疲劳",
    "冲击",
    "结构动力学",
    "求解器",
)
RECRUITMENT_URL_MARKERS = (
    "career",
    "careers",
    "campus",
    "graduate",
    "join",
    "job",
    "jobs",
    "position",
    "recruit",
    "talent",
    "zhaopin",
    "rczp",
    "xyzp",
)

CHINESE_DEADLINE_RE = re.compile(
    r"(?:申请截止|投递截止|网申截止|报名截止|截止日期|截止时间|申请截至|投递截至)"
    r"[^0-9]{0,24}(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?",
    re.I,
)
ENGLISH_DEADLINE_RE = re.compile(
    r"Apply\s+By\s+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(20\d{2})",
    re.I,
)
ENGLISH_MONTHS = {
    month.lower(): index
    for index, month in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self._in_ignored = 0
        self._in_title = False
        self._active_href: str | None = None
        self._active_anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._in_ignored += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and self._in_ignored == 0:
            href = dict(attrs).get("href")
            if href:
                self._active_href = href
                self._active_anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._in_ignored:
            self._in_ignored -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._active_href:
            anchor_text = " ".join(self._active_anchor_parts).strip()
            if anchor_text:
                self.links.append((anchor_text, self._active_href))
            self._active_href = None
            self._active_anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_ignored:
            return
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        self.text_parts.append(clean)
        if self._in_title:
            self.title_parts.append(clean)
        if self._active_href:
            self._active_anchor_parts.append(clean)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts)


@dataclass
class FetchResult:
    url: str
    ok: bool
    status_code: int | None = None
    content_type: str = ""
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] | None = None
    content_hash: str = ""
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the solid-mechanics jobs dataset")
    parser.add_argument("--dry-run", action="store_true", help="Check pages but do not write files")
    parser.add_argument("--export-only", action="store_true", help="Only create the JSON mirror")
    parser.add_argument("--skip-discovery", action="store_true", help="Do not scan watch sources")
    parser.add_argument(
        "--skip-if-updated-today",
        action="store_true",
        help="Exit without fetching when the dataset was already refreshed today in Asia/Shanghai",
    )
    parser.add_argument("--timeout", type=float, default=12.0, help="Per-request timeout in seconds")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel fetch workers")
    return parser.parse_args()


def load_dataset() -> dict[str, Any]:
    raw = DATA_FILE.read_text(encoding="utf-8")
    match = re.fullmatch(r"\s*window\.SOLID_MECHANICS_JOBS\s*=\s*(\{.*\})\s*;\s*", raw, re.S)
    if not match:
        raise ValueError(f"Cannot parse {DATA_FILE}")
    data = json.loads(match.group(1))
    if not isinstance(data.get("jobs"), list):
        raise ValueError("Dataset does not contain a jobs list")
    return data


def load_watch_sources() -> list[dict[str, Any]]:
    if not WATCH_FILE.exists():
        return []
    payload = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else payload.get("sources", [])


def decode_body(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates.extend(["utf-8", "gb18030", "big5"])
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_page(url: str, timeout: float) -> FetchResult:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept-Encoding": "identity",
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(MAX_RESPONSE_BYTES)
            content_type = response.headers.get("Content-Type", "")
            status_code = getattr(response, "status", 200)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        return FetchResult(url=url, ok=False, status_code=exc.code, error=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        return FetchResult(url=url, ok=False, error=str(exc.reason if hasattr(exc, "reason") else exc))
    except Exception as exc:  # defensive: one bad portal must not stop the whole update
        return FetchResult(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

    digest = hashlib.sha256(body).hexdigest()[:16]
    if "pdf" in content_type.lower() or body.startswith(b"%PDF"):
        return FetchResult(
            url=final_url,
            ok=True,
            status_code=status_code,
            content_type=content_type,
            content_hash=digest,
        )

    source = decode_body(body, content_type)
    parser = PageParser()
    try:
        parser.feed(source)
    except Exception:
        # Broken HTML is common on recruitment portals; the visible text is still useful.
        visible = re.sub(r"<[^>]+>", " ", source)
        visible = html.unescape(re.sub(r"\s+", " ", visible))
        return FetchResult(
            url=final_url,
            ok=True,
            status_code=status_code,
            content_type=content_type,
            text=visible,
            content_hash=digest,
        )
    return FetchResult(
        url=final_url,
        ok=True,
        status_code=status_code,
        content_type=content_type,
        title=parser.title,
        text=parser.text,
        links=parser.links,
        content_hash=digest,
    )


def fetch_many(urls: Iterable[str], timeout: float, workers: int) -> dict[str, FetchResult]:
    unique_urls = list(dict.fromkeys(url for url in urls if url))
    results: dict[str, FetchResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(fetch_page, url, timeout): url for url in unique_urls}
        for future in concurrent.futures.as_completed(future_map):
            original_url = future_map[future]
            try:
                results[original_url] = future.result()
            except Exception as exc:
                results[original_url] = FetchResult(url=original_url, ok=False, error=str(exc))
    return results


def apply_deadline(job: dict[str, Any], today: dt.date) -> bool:
    deadline_raw = job.get("deadline")
    if not deadline_raw:
        return False
    try:
        deadline = dt.date.fromisoformat(deadline_raw)
    except ValueError:
        return False
    if deadline < today and job.get("status") != "closed":
        job["previousStatus"] = job.get("status")
        job["status"] = "closed"
        job["closedReason"] = "deadline"
        return True
    return False


def update_deadline_from_official_page(job: dict[str, Any], page_text: str) -> bool:
    """Extract only dates explicitly attached to a deadline label on a job detail page."""
    if job.get("deadlineTracking") != "detail" or not page_text:
        return False

    deadline: dt.date | None = None
    match = CHINESE_DEADLINE_RE.search(page_text)
    if match:
        try:
            deadline = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            deadline = None

    if deadline is None:
        match = ENGLISH_DEADLINE_RE.search(page_text)
        if match:
            try:
                deadline = dt.date(int(match.group(3)), ENGLISH_MONTHS[match.group(1).lower()], int(match.group(2)))
            except (KeyError, ValueError):
                deadline = None

    if deadline is not None:
        value = deadline.isoformat()
        changed = job.get("deadline") != value or job.get("deadlineStatus") != "dated"
        job["deadline"] = value
        job["deadlineStatus"] = "dated"
        job["deadlineSource"] = "招聘单位官网"
        job["deadlineEvidence"] = "官网明确标注截止日期"
        return changed

    if any(marker in page_text for marker in ("招满即止", "长期有效")) and not job.get("deadline"):
        changed = job.get("deadlineStatus") != "rolling"
        job["deadlineStatus"] = "rolling"
        job["deadlineSource"] = "招聘单位官网"
        job["deadlineEvidence"] = "官网标注招满即止或长期有效"
        return changed
    return False


def verify_job(job: dict[str, Any], result: FetchResult, today: dt.date) -> bool:
    changed = update_deadline_from_official_page(job, result.text) if result.ok else False
    changed = apply_deadline(job, today) or changed
    if not result.ok:
        if job.get("sourceState") != "official-unreachable" or job.get("lastError") != result.error:
            changed = True
        job["sourceState"] = "official-unreachable"
        job["lastError"] = result.error[:180]
        job["lastAttempt"] = today.isoformat()
        return changed

    old_hash = job.get("contentHash")
    if old_hash and result.content_hash and old_hash != result.content_hash:
        job["changedAt"] = today.isoformat()
        changed = True
    if result.content_hash and result.content_hash != old_hash:
        job["contentHash"] = result.content_hash
        changed = True

    if job.get("lastChecked") != today.isoformat() or job.get("sourceState") != "official-verified":
        changed = True
    job["lastChecked"] = today.isoformat()
    job["sourceState"] = "official-verified"
    job.pop("lastError", None)
    job.pop("lastAttempt", None)
    if result.title:
        job["pageTitle"] = result.title[:180]

    page_text = result.text.casefold()
    if any(marker.casefold() in page_text for marker in EXPIRED_MARKERS):
        if job.get("status") != "closed":
            job["previousStatus"] = job.get("status")
            job["status"] = "closed"
            job["closedReason"] = "page-marker"
            changed = True
    elif job.get("status") == "closed" and job.get("closedReason") == "page-marker":
        if any(marker.casefold() in page_text for marker in OPEN_MARKERS):
            job["status"] = job.pop("previousStatus", "open")
            job.pop("closedReason", None)
            changed = True
    return changed


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    filtered_query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "from", "source", "spread"))
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(filtered_query), "")
    )


def is_employer_owned_link(source: dict[str, Any], target_url: str) -> bool:
    """Keep discovery on the employer's own host or explicitly allowed subdomains."""
    target_host = (urllib.parse.urlsplit(target_url).hostname or "").lower()
    if not target_host:
        return False
    allowed_hosts = source.get("allowedHosts") or [urllib.parse.urlsplit(source.get("url", "")).hostname or ""]
    for allowed_host in allowed_hosts:
        allowed = str(allowed_host).lower().removeprefix("www.").strip(".")
        target = target_host.removeprefix("www.")
        if allowed and (target == allowed or target.endswith("." + allowed)):
            return True
    return False


def looks_like_recruitment_link(target_url: str) -> bool:
    parsed = urllib.parse.urlsplit(target_url)
    searchable = f"{parsed.hostname or ''}{parsed.path}".casefold()
    return any(marker in searchable for marker in RECRUITMENT_URL_MARKERS)


def discovered_id(company: str, role: str, url: str) -> str:
    seed = f"{company}|{role}|{canonical_url(url)}".encode("utf-8")
    return f"auto-{hashlib.sha1(seed).hexdigest()[:12]}"


def role_match(title: str) -> str:
    lowered = title.casefold()
    if any(keyword.casefold() in lowered for keyword in HIGH_MATCH_KEYWORDS):
        return "S"
    if any(keyword.casefold() in lowered for keyword in ROLE_KEYWORDS):
        return "A"
    return "B"


def discover_jobs(
    sources: list[dict[str, Any]],
    results: dict[str, FetchResult],
    existing_jobs: list[dict[str, Any]],
    today: dt.date,
) -> list[dict[str, Any]]:
    known_urls = {canonical_url(job.get("url", "")) for job in existing_jobs}
    known_pairs = {(job.get("company", "").casefold(), job.get("role", "").casefold()) for job in existing_jobs}
    discovered: list[dict[str, Any]] = []

    for source in sources:
        source_url = source.get("url", "")
        result = results.get(source_url)
        if not result or not result.ok or not result.links:
            continue
        company = source.get("company", "待确认单位")
        for anchor_text, href in result.links:
            clean_title = re.sub(r"\s+", " ", anchor_text).strip()
            lowered = clean_title.casefold()
            if len(clean_title) < 4 or len(clean_title) > 90:
                continue
            if not any(keyword.casefold() in lowered for keyword in ROLE_KEYWORDS):
                continue
            absolute_url = urllib.parse.urljoin(result.url, href)
            if not absolute_url.startswith(("http://", "https://")):
                continue
            if not is_employer_owned_link(source, absolute_url):
                continue
            if not looks_like_recruitment_link(absolute_url):
                continue
            canonical = canonical_url(absolute_url)
            pair = (company.casefold(), clean_title.casefold())
            if canonical in known_urls or pair in known_pairs:
                continue
            job = {
                "id": discovered_id(company, clean_title, absolute_url),
                "company": company,
                "employerType": source.get("employerType", "私营企业"),
                "role": clean_title,
                "city": source.get("city", "待确认"),
                "salaryMin": 0,
                "salaryMax": 0,
                "salaryText": "待沟通",
                "match": role_match(clean_title),
                "status": "open",
                "priority": 2,
                "keywords": [keyword for keyword in ROLE_KEYWORDS if keyword.casefold() in lowered][:3],
                "reputation": "自动发现的新岗位，尚未完成人工背调；请以官方页面为准。",
                "url": absolute_url,
                "lastChecked": today.isoformat(),
                "discoveredAt": today.isoformat(),
                "sourceState": "official-discovered",
                "officialStatus": "verified",
                "officialKind": "employer-job",
                "officialVerifiedAt": today.isoformat(),
                "deadlineStatus": "not-published",
                "deadlineTracking": "detail",
            }
            discovered.append(job)
            known_urls.add(canonical)
            known_pairs.add(pair)
    return discovered


def write_dataset(data: dict[str, Any]) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    js_payload = f"window.SOLID_MECHANICS_JOBS = {serialized.rstrip()};\n"
    temp_js = DATA_FILE.with_suffix(".js.tmp")
    temp_json = JSON_MIRROR.with_suffix(".json.tmp")
    temp_js.write_text(js_payload, encoding="utf-8")
    temp_json.write_text(serialized, encoding="utf-8")
    temp_js.replace(DATA_FILE)
    temp_json.replace(JSON_MIRROR)


def bump_version(meta: dict[str, Any], now: dt.datetime) -> None:
    today = now.date()
    prefix = today.strftime("%Y.%m.%d")
    old_version = str(meta.get("version", ""))
    suffix = 1
    if old_version.startswith(prefix + "."):
        try:
            suffix = int(old_version.rsplit(".", 1)[1]) + 1
        except ValueError:
            pass
    meta["version"] = f"{prefix}.{suffix}"
    meta["updatedAt"] = today.isoformat()
    meta["updatedTime"] = now.strftime("%H:%M")


def main() -> int:
    args = parse_args()
    data = load_dataset()
    now = dt.datetime.now(CHINA_TIMEZONE)
    today = now.date()

    if args.export_only:
        JSON_MIRROR.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Exported {len(data['jobs'])} jobs to {JSON_MIRROR.relative_to(PROJECT_ROOT)}")
        return 0

    if args.skip_if_updated_today and data.get("meta", {}).get("updatedAt") == today.isoformat():
        print(f"Already refreshed on {today.isoformat()} Asia/Shanghai; skipping backup run")
        return 0

    watch_sources = [] if args.skip_discovery else load_watch_sources()
    job_urls = [job.get("url", "") for job in data["jobs"]]
    urls = list(job_urls)
    urls.extend(source.get("url", "") for source in watch_sources)
    results = fetch_many(urls, timeout=args.timeout, workers=args.workers)

    unique_official_urls = {url for url in job_urls if url}
    reachable_probe = sum(bool(results.get(url) and results[url].ok) for url in unique_official_urls)
    minimum_reachable = max(1, (len(unique_official_urls) + MIN_REACHABLE_DIVISOR - 1) // MIN_REACHABLE_DIVISOR)
    if unique_official_urls and reachable_probe < minimum_reachable:
        print(
            f"Connectivity guard: only {reachable_probe}/{len(unique_official_urls)} official pages reachable; "
            f"need at least {minimum_reachable}. Dataset was preserved for a later retry.",
            file=sys.stderr,
        )
        return 2

    changed_jobs = 0
    reachable = 0
    unreachable = 0
    official_unavailable = 0
    for job in data["jobs"]:
        job_url = job.get("url", "")
        if not job_url:
            official_unavailable += 1
            changed = apply_deadline(job, today)
            if job.get("sourceState") != "official-unavailable" or job.get("officialStatus") != "unavailable":
                job["sourceState"] = "official-unavailable"
                job["officialStatus"] = "unavailable"
                changed = True
            if changed:
                changed_jobs += 1
            continue
        result = results.get(job_url, FetchResult(url=job_url, ok=False, error="missing result"))
        reachable += int(result.ok)
        unreachable += int(not result.ok)
        if verify_job(job, result, today):
            changed_jobs += 1

    new_jobs = discover_jobs(watch_sources, results, data["jobs"], today)
    if new_jobs:
        data["jobs"].extend(new_jobs)

    bump_version(data.setdefault("meta", {}), now)
    data["meta"]["lastRun"] = {
        "reachable": reachable,
        "unreachable": unreachable,
        "officialUnavailable": official_unavailable,
        "changedJobs": changed_jobs,
        "discoveredJobs": len(new_jobs),
    }
    data["meta"]["officialLinkCount"] = sum(bool(job.get("url")) for job in data["jobs"])
    data["meta"]["officialUnavailableCount"] = sum(not bool(job.get("url")) for job in data["jobs"])
    data["meta"]["officialDeadlineCount"] = sum(job.get("deadlineStatus") == "dated" for job in data["jobs"])

    if args.dry_run:
        print(
            f"Dry run: {len(data['jobs'])} jobs, {reachable} official pages reachable, "
            f"{unreachable} unreachable, {official_unavailable} unavailable, "
            f"{changed_jobs} changed, {len(new_jobs)} discovered"
        )
        return 0

    write_dataset(data)
    print(
        f"Updated {len(data['jobs'])} jobs: {reachable} official pages reachable, "
        f"{unreachable} unreachable, {official_unavailable} unavailable, "
        f"{changed_jobs} changed, {len(new_jobs)} discovered"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
