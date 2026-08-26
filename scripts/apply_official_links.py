#!/usr/bin/env python3
"""Apply the human-audited, employer-owned recruitment links to the job data."""

from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "data" / "jobs.js"
JSON_MIRROR = PROJECT_ROOT / "data" / "jobs.json"
AUDIT_FILE = PROJECT_ROOT / "data" / "official_links.json"


def load_jobs() -> dict:
    raw = DATA_FILE.read_text(encoding="utf-8")
    match = re.fullmatch(r"\s*window\.SOLID_MECHANICS_JOBS\s*=\s*(\{.*\})\s*;\s*", raw, re.S)
    if not match:
        raise ValueError(f"Cannot parse {DATA_FILE}")
    return json.loads(match.group(1))


def write_jobs(data: dict) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    DATA_FILE.write_text(f"window.SOLID_MECHANICS_JOBS = {serialized.rstrip()};\n", encoding="utf-8")
    JSON_MIRROR.write_text(serialized, encoding="utf-8")


def main() -> None:
    data = load_jobs()
    audit = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    links = audit["links"]
    jobs_by_id = {job["id"]: job for job in data["jobs"]}

    missing_audit = sorted(job_id for job_id in set(jobs_by_id) - set(links) if not job_id.startswith("auto-"))
    orphan_audit = sorted(set(links) - set(jobs_by_id))
    if missing_audit or orphan_audit:
        raise ValueError(f"Audit mismatch: missing={missing_audit}, orphan={orphan_audit}")

    verified = 0
    unavailable = 0
    dated = 0
    for job_id, job in jobs_by_id.items():
        record = links[job_id]
        url = record.get("url", "").strip()
        job["url"] = url
        job["officialKind"] = record["kind"]
        job["officialVerifiedAt"] = audit["verifiedAt"]
        job["officialStatus"] = "verified" if url else "unavailable"
        job["deadlineTracking"] = "detail" if record["kind"] == "employer-job" else "portal"

        for stale_key in (
            "contentHash",
            "pageTitle",
            "lastError",
            "lastAttempt",
            "changedAt",
            "deadline",
            "deadlineSource",
            "deadlineEvidence",
        ):
            job.pop(stale_key, None)

        if url:
            verified += 1
            job["sourceState"] = "official-verified"
            job.pop("officialNote", None)
        else:
            unavailable += 1
            job["sourceState"] = "official-unavailable"
            job["officialNote"] = record.get("note", "单位自有招聘入口待开放")
            if job.get("status") not in {"closed", "monitor"}:
                job["previousStatus"] = job["status"]
                job["status"] = "monitor"

        deadline = record.get("deadline")
        if deadline:
            dated += 1
            job["deadline"] = deadline
            job["deadlineStatus"] = "dated"
            job["deadlineSource"] = "招聘单位官网"
            job["deadlineEvidence"] = record.get("deadlineEvidence", "官网明确标注")
        else:
            job["deadlineStatus"] = "not-published"

    data["meta"]["officialLinkPolicy"] = audit["policy"]
    data["meta"]["officialLinksVerifiedAt"] = audit["verifiedAt"]
    data["meta"]["officialLinkCount"] = verified
    data["meta"]["officialUnavailableCount"] = unavailable
    data["meta"]["officialDeadlineCount"] = dated
    write_jobs(data)
    print(f"Applied {verified} official links; {unavailable} unavailable; {dated} official deadlines")


if __name__ == "__main__":
    main()
