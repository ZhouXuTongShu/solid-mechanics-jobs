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
CANDIDATE_FILE = PROJECT_ROOT / "data" / "job_candidates.json"
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
ENTERPRISE_TYPES = {"央国企", "私营企业", "外资企业", "上市企业"}
ACADEMIC_GROUP_MARKERS = (
    "课题组",
    "实验室",
    "博士后",
    "项目组",
    "教授团队",
    "大学·",
)
PROTECTED_PAGE_MARKERS = (
    "just a moment",
    "enable javascript and cookies to continue",
    "cf-chl",
    "challenge-platform",
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
MECHANICS_EVIDENCE_TERMS = (
    "固体力学",
    "工程力学",
    "计算力学",
    "结构力学",
    "有限元",
    "fea",
    "cae",
    "cfd",
    "结构仿真",
    "结构强度",
    "高性能结构",
    "复材工程结构",
    "振动控制",
    "结构振动",
    "振动噪声",
    "疲劳",
    "断裂",
    "热/应力仿真",
    "热应力仿真",
    "应力仿真",
    "力、热仿真",
    "力/热仿真",
    "流体仿真",
    "流动与传热",
    "流体力学",
    "热管理",
    "流固耦合",
    "多物理场",
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
    source_text: str = ""
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


def is_enterprise_record(record: dict[str, Any]) -> bool:
    """Hard gate: academic groups and postdoc teams never reach the homepage."""
    if record.get("employerType") not in ENTERPRISE_TYPES:
        return False
    identity = f"{record.get('company', '')} {record.get('role', '')}".casefold()
    return not any(marker.casefold() in identity for marker in ACADEMIC_GROUP_MARKERS)


def contains_mechanics_evidence(value: str) -> bool:
    normalized = re.sub(r"\s+", "", value.casefold())
    return any(term.casefold() in normalized for term in MECHANICS_EVIDENCE_TERMS)


def load_candidates() -> list[dict[str, Any]]:
    payload = json.loads(CANDIDATE_FILE.read_text(encoding="utf-8"))
    candidates = payload if isinstance(payload, list) else payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError(f"Candidate catalog in {CANDIDATE_FILE} is not a list")
    required = {"id", "company", "role", "city", "url", "fitEvidence", "officialEvidence", "evidenceAll"}
    seen_ids: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"Candidate #{index} is not an object")
        missing = sorted(required - candidate.keys())
        if missing:
            raise ValueError(f"Candidate #{index} is missing fields: {', '.join(missing)}")
        candidate_id = str(candidate["id"])
        if candidate_id in seen_ids:
            raise ValueError(f"Duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)
        if candidate.get("officialKind") != "employer-job":
            raise ValueError(f"Candidate {candidate_id} must link to an employer-owned job page")
        if not is_enterprise_record(candidate):
            raise ValueError(f"Candidate {candidate_id} is not an allowed enterprise record")
        if not str(candidate.get("url", "")).startswith(("https://", "http://")):
            raise ValueError(f"Candidate {candidate_id} has an invalid official URL")
        evidence_text = " ".join(
            [
                str(candidate.get("role", "")),
                str(candidate.get("fitEvidence", "")),
                " ".join(str(item) for item in candidate.get("keywords", [])),
            ]
        )
        if not contains_mechanics_evidence(evidence_text):
            raise ValueError(f"Candidate {candidate_id} lacks explicit mechanics-work evidence")
        if not isinstance(candidate.get("evidenceAll"), list) or not candidate["evidenceAll"]:
            raise ValueError(f"Candidate {candidate_id} has no page evidence checks")
    return candidates


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
        # Some first-party career sites return a Cloudflare/browser challenge as
        # HTTP 403 to automated checks. Preserve the response body so a curated,
        # recently human-verified candidate can use the narrow protected-page
        # fallback in candidate_to_live_job(). An ordinary 403 remains a failure.
        body = exc.read(MAX_RESPONSE_BYTES)
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        source = decode_body(body, content_type)
        lowered = source.casefold()
        if exc.code in {403, 429} and any(marker in lowered for marker in PROTECTED_PAGE_MARKERS):
            return FetchResult(
                url=exc.geturl() or url,
                ok=True,
                status_code=exc.code,
                content_type=content_type,
                text=re.sub(r"<[^>]+>", " ", source),
                source_text=source,
                content_hash=hashlib.sha256(body).hexdigest()[:16],
                error=f"HTTP {exc.code} access protection",
            )
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
            source_text=source,
            content_hash=digest,
        )
    return FetchResult(
        url=final_url,
        ok=True,
        status_code=status_code,
        content_type=content_type,
        title=parser.title,
        text=parser.text,
        source_text=source,
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


def is_access_protection_page(result: FetchResult) -> bool:
    page = f"{result.title} {result.text} {result.source_text}".casefold()
    return any(marker in page for marker in PROTECTED_PAGE_MARKERS)


def protected_fallback_is_current(candidate: dict[str, Any], today: dt.date) -> bool:
    if candidate.get("verificationMode") != "protected-official-page":
        return False
    manual_date = candidate.get("manualVerifiedAt")
    fallback_until = candidate.get("protectedFallbackUntil")
    try:
        return bool(
            manual_date
            and fallback_until
            and dt.date.fromisoformat(str(manual_date)) <= today <= dt.date.fromisoformat(str(fallback_until))
        )
    except ValueError:
        return False


def candidate_to_live_job(
    candidate: dict[str, Any], result: FetchResult, today: dt.date
) -> tuple[dict[str, Any] | None, str]:
    """Return a homepage job only when its official page still proves the opening."""
    if not result.ok:
        return None, "unreachable"

    protected_fallback = is_access_protection_page(result) and protected_fallback_is_current(candidate, today)

    # Some employer sites render the job body from a JavaScript object or keep it
    # in an iframe/comment. Curated candidates may use the downloaded first-party
    # source as evidence; automatic discovery below still relies on visible text.
    verification_text = f"{result.text} {html.unescape(result.source_text)}"
    page_text = re.sub(r"\s+", " ", verification_text).casefold()
    if not protected_fallback:
        missing = [term for term in candidate.get("evidenceAll", []) if term.casefold() not in page_text]
        if missing:
            return None, "evidence-missing"
        if any(marker.casefold() in page_text for marker in EXPIRED_MARKERS):
            return None, "closed-marker"

    deadline_raw = candidate.get("deadline")
    if deadline_raw:
        try:
            if dt.date.fromisoformat(deadline_raw) < today:
                return None, "deadline-passed"
        except ValueError:
            return None, "invalid-deadline"

    if not candidate.get("fitEvidence") or not candidate.get("officialEvidence"):
        return None, "catalog-evidence-missing"
    if not is_enterprise_record(candidate):
        return None, "non-enterprise"

    job = {key: value for key, value in candidate.items() if key != "evidenceAll"}
    job.update(
        {
            "status": "open",
            "officialStatus": "verified",
            "officialKind": "employer-job",
            "officialVerifiedAt": candidate.get("manualVerifiedAt", today.isoformat())
            if protected_fallback
            else today.isoformat(),
            "lastChecked": today.isoformat(),
            "sourceState": "official-protected" if protected_fallback else "official-verified",
            "contentHash": result.content_hash,
        }
    )
    if protected_fallback:
        job["verificationNote"] = "官网对自动访问启用保护；当前状态沿用最近一次人工浏览器核验，并继续每日检查链接与截止日。"
    elif result.title:
        job["pageTitle"] = result.title[:180]
    return job, "verified-open"


def collect_discovery_links(
    sources: list[dict[str, Any]],
    source_results: dict[str, FetchResult],
    known_urls: set[str],
) -> list[tuple[dict[str, Any], str, str]]:
    """Collect possible detail pages; no candidate is published before detail verification."""
    links: list[tuple[dict[str, Any], str, str]] = []
    seen = set(known_urls)
    for source in sources:
        if not is_enterprise_record(source):
            continue
        result = source_results.get(source.get("url", ""))
        if not result or not result.ok or not result.links:
            continue
        for anchor_text, href in result.links:
            title = re.sub(r"\s+", " ", anchor_text).strip()
            lowered = title.casefold()
            if len(title) < 4 or len(title) > 90:
                continue
            if not any(keyword.casefold() in lowered for keyword in ROLE_KEYWORDS):
                continue
            absolute_url = urllib.parse.urljoin(result.url, href)
            if not absolute_url.startswith(("http://", "https://")):
                continue
            if not is_employer_owned_link(source, absolute_url) or not looks_like_recruitment_link(absolute_url):
                continue
            canonical = canonical_url(absolute_url)
            if canonical in seen:
                continue
            seen.add(canonical)
            links.append((source, title, absolute_url))
    return links


def strict_discovered_job(
    source: dict[str, Any], title: str, url: str, result: FetchResult, today: dt.date
) -> dict[str, Any] | None:
    """Publish only enterprise detail pages that prove mechanics work and active hiring."""
    if not result.ok or not result.text:
        return None
    if not is_enterprise_record(source):
        return None
    page_text = re.sub(r"\s+", " ", result.text)
    lowered = page_text.casefold()
    if "2027" not in f"{title} {page_text}":
        return None
    if any(marker.casefold() in lowered for marker in EXPIRED_MARKERS):
        return None
    if not contains_mechanics_evidence(f"{title} {page_text}"):
        return None
    if not any(marker.casefold() in lowered for marker in (*OPEN_MARKERS, "招聘", "招满为止", "长期")):
        return None

    exact_terms = [term for term in MECHANICS_EVIDENCE_TERMS if term.casefold() in page_text.casefold()]
    mechanics_text = "、".join(exact_terms[:4]) if exact_terms else "结构/仿真类力学工作"
    title_lowered = title.casefold()
    return {
        "id": discovered_id(source.get("company", "待确认单位"), title, url),
        "company": source.get("company", "待确认单位"),
        "employerType": source.get("employerType", "私营企业"),
        "role": title,
        "city": source.get("city", "待确认"),
        "salaryMin": 0,
        "salaryMax": 0,
        "salaryText": "官网未披露",
        "match": role_match(title),
        "status": "open",
        "priority": 2,
        "keywords": [keyword for keyword in ROLE_KEYWORDS if keyword.casefold() in title_lowered][:3],
        "reputation": "自动发现并通过官网岗位详情严格核验；团队环境与压力需在面试中确认。",
        "url": url,
        "lastChecked": today.isoformat(),
        "discoveredAt": today.isoformat(),
        "sourceState": "official-verified",
        "officialStatus": "verified",
        "officialKind": "employer-job",
        "officialVerifiedAt": today.isoformat(),
        "deadlineStatus": "not-published",
        "deadlineTracking": "detail",
        "fitEvidence": f"官网岗位职责或要求明确出现：{mechanics_text}",
        "officialEvidence": "企业官网详情页同时存在具体岗位标题、力学相关工作内容和有效招聘/投递信号",
        "contentHash": result.content_hash,
        "pageTitle": result.title[:180] if result.title else title,
    }


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

    candidates = load_candidates()
    watch_sources = [] if args.skip_discovery else [source for source in load_watch_sources() if is_enterprise_record(source)]
    candidate_urls = [candidate.get("url", "") for candidate in candidates]
    urls = list(candidate_urls)
    urls.extend(source.get("url", "") for source in watch_sources)
    results = fetch_many(urls, timeout=args.timeout, workers=args.workers)

    unique_official_urls = {url for url in candidate_urls if url}
    reachable_probe = sum(bool(results.get(url) and results[url].ok) for url in unique_official_urls)
    minimum_reachable = max(1, (len(unique_official_urls) + MIN_REACHABLE_DIVISOR - 1) // MIN_REACHABLE_DIVISOR)
    if unique_official_urls and reachable_probe < minimum_reachable:
        print(
            f"Connectivity guard: only {reachable_probe}/{len(unique_official_urls)} official pages reachable; "
            f"need at least {minimum_reachable}. Dataset was preserved for a later retry.",
            file=sys.stderr,
        )
        return 2

    old_jobs = data.get("jobs", [])
    live_jobs: list[dict[str, Any]] = []
    exclusion_reasons: dict[str, int] = {}
    reachable = 0
    unreachable = 0
    for candidate in candidates:
        candidate_url = candidate.get("url", "")
        result = results.get(candidate_url, FetchResult(url=candidate_url, ok=False, error="missing result"))
        reachable += int(result.ok)
        unreachable += int(not result.ok)
        job, reason = candidate_to_live_job(candidate, result, today)
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
        if job:
            live_jobs.append(job)

    discovered_links = collect_discovery_links(
        watch_sources,
        results,
        {canonical_url(candidate.get("url", "")) for candidate in candidates},
    )
    detail_results = fetch_many(
        [url for _, _, url in discovered_links], timeout=args.timeout, workers=args.workers
    )
    new_jobs: list[dict[str, Any]] = []
    known_pairs = {(job.get("company", "").casefold(), job.get("role", "").casefold()) for job in live_jobs}
    for source, title, url in discovered_links:
        discovered = strict_discovered_job(
            source,
            title,
            url,
            detail_results.get(url, FetchResult(url=url, ok=False, error="missing result")),
            today,
        )
        if not discovered:
            continue
        pair = (discovered.get("company", "").casefold(), discovered.get("role", "").casefold())
        if pair in known_pairs:
            continue
        live_jobs.append(discovered)
        new_jobs.append(discovered)
        known_pairs.add(pair)

    old_by_id = {job.get("id"): job for job in old_jobs}
    new_by_id = {job.get("id"): job for job in live_jobs}
    changed_jobs = sum(old_by_id.get(job_id) != new_by_id.get(job_id) for job_id in old_by_id.keys() | new_by_id.keys())
    data["jobs"] = live_jobs

    bump_version(data.setdefault("meta", {}), now)
    data["meta"]["lastRun"] = {
        "reachable": reachable,
        "unreachable": unreachable,
        "officialUnavailable": 0,
        "changedJobs": changed_jobs,
        "discoveredJobs": len(new_jobs),
        "verifiedOpenJobs": len(live_jobs),
        "excludedCandidates": len(candidates) - (len(live_jobs) - len(new_jobs)),
        "exclusionReasons": {key: value for key, value in exclusion_reasons.items() if key != "verified-open"},
    }
    data["meta"]["homepagePolicy"] = (
        "只展示企业官方招聘系统能够确认的当前在招具体岗位；职责须明确属于结构、强度、振动、"
        "有限元、CAE、CFD、热流体或多物理场等力学方向。高校课题组、PI实验室和博士后项目组一律排除。"
    )
    data["meta"]["officialLinkPolicy"] = (
        "只允许企业自有域名、企业自有招聘子域名，或能够明确归属于该企业的官方招聘系统；"
        "不使用高校转载页、综合招聘平台或聚合站作为跳转入口。"
    )
    data["meta"]["officialLinksVerifiedAt"] = today.isoformat()
    data["meta"]["candidateCount"] = len(candidates)
    data["meta"]["monitoredSourceCount"] = len(
        [source for source in load_watch_sources() if is_enterprise_record(source)]
    )
    data["meta"]["officialLinkCount"] = len(data["jobs"])
    data["meta"]["officialUnavailableCount"] = 0
    data["meta"]["officialDeadlineCount"] = sum(job.get("deadlineStatus") == "dated" for job in data["jobs"])

    if args.dry_run:
        print(
            f"Dry run: {len(data['jobs'])} verified-open jobs, {reachable} candidate checks reachable, "
            f"{unreachable} unreachable, {len(candidates) - (len(live_jobs) - len(new_jobs))} excluded, "
            f"{changed_jobs} changed, {len(new_jobs)} strictly discovered"
        )
        return 0

    write_dataset(data)
    print(
        f"Updated {len(data['jobs'])} verified-open jobs: {reachable} candidate checks reachable, "
        f"{unreachable} unreachable, {len(candidates) - (len(live_jobs) - len(new_jobs))} excluded, "
        f"{changed_jobs} changed, {len(new_jobs)} strictly discovered"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
