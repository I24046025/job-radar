"""Collect US job postings from the last 24h, from LinkedIn's public job search and from
employers' own career sites (data/companies.json), and flag H-1B sponsorship signals.

Output: data/candidates.json (raw material for the daily review step).
Usage: python3 scripts/fetch_jobs.py [linkedin] [employers]   (default: both)
"""
import html
import json
import random
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from company_jobs import collect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "candidates.json"

SEARCHES = {
    "data": [
        "data analyst",
        "business intelligence analyst",
        "data quality analyst",
        "reporting analyst SQL",
        "analytics analyst python",
    ],
    "supply_chain": [
        "supply chain analyst",
        "procurement analyst",
        "demand planning analyst",
        "inventory analyst",
        "logistics analyst",
    ],
    "aml": [
        "AML analyst",
        "KYC analyst",
        "sanctions analyst",
        "transaction monitoring analyst",
        "financial crimes analyst",
        "compliance data analyst",
    ],
}
PAGES = 4  # 10 results per page

SKIP_TITLE = re.compile(
    r"\b(director|vice president|vp|head of|chief|intern|internship|co-op|"
    r"principal|staff|lead|manager|clearance|ts/sci|secret|part[- ]time|"
    r"paid training|teacher|nurse)\b",
    re.I,
)

NO_SPONSOR = re.compile(
    r"\b(not|unable to|cannot|can't|won't|no)\s+(\w+\s+){0,4}(visa\s+|immigration\s+)?sponsor"
    r"|without\s+(the\s+need\s+for\s+)?(\w+\s+){0,3}sponsorship"
    r"|sponsorship\s+(is\s+)?not\s+(available|offered|provided)"
    r"|not\s+(be\s+)?eligible\s+for\s+(\w+\s+){0,2}sponsorship"
    r"|not\s+require\s+(\w+\s+){0,3}sponsorship"
    r"|u\.?s\.?\s+citizens?\s+only|must\s+be\s+a\s+u\.?s\.?\s+citizen|u\.?s\.?\s+citizenship\s+(is\s+)?required"
    r"|green\s+card\s+holders?\s+only|gc\s*/\s*usc\s+only"
    r"|\bitar\b|u\.?s\.?\s+persons?\b|export\s+control\s+(laws|regulations).{0,80}(citizen|person)"
    r"|security\s+clearance|active\s+clearance",
    re.I,
)
YES_SPONSOR = re.compile(
    r"(will|can|able to|happy to|willing to|open to)\s+(provide\s+|offer\s+)?(visa\s+|h-?1b\s+)?sponsor"
    r"|h-?1b\s+(transfer|sponsorship)\s+(available|accepted|welcome|considered)"
    r"|sponsorship\s+(is\s+)?(available|provided|offered)"
    r"|visa\s+sponsorship\s+available",
    re.I,
)

UA = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
]


def get(url, tries=3):
    # curl rather than urllib: it uses the system trust store, which matters on
    # networks that re-sign TLS traffic
    for i in range(tries):
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "20", "-A", random.choice(UA), "-w", "\n%{http_code}", url],
            capture_output=True, text=True,
        )
        body, _, status = r.stdout.rpartition("\n")
        if status == "200":
            return body
        print(f"  retry {i + 1} {url[:80]}: HTTP {status} {r.stderr.strip()}", file=sys.stderr)
        time.sleep(5 * (i + 1))  # 429s and timeouts: back off and retry
    return ""


def text(m):
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def search(keywords, start):
    q = urllib.parse.urlencode({
        "keywords": keywords,
        "location": "United States",
        "f_TPR": "r86400",  # posted in the past 24 hours
        "start": start,
    })
    page = get(f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{q}")
    for card in page.split("<li>")[1:]:
        url = re.search(r'href="(https://www\.linkedin\.com/jobs/view/[^"?]*)', card)
        if not url:
            continue
        job_id = re.search(r"-(\d+)$", url.group(1))
        tm = re.search(r'<time[^>]*datetime="([^"]*)"[^>]*>\s*(.*?)\s*<', card, re.S)
        yield {
            "id": job_id.group(1) if job_id else url.group(1),
            "title": text(re.search(r'base-search-card__title">(.*?)<', card, re.S)),
            "company": text(re.search(r'hidden-nested-link[^>]*>(.*?)<', card, re.S)),
            "location": text(re.search(r'job-search-card__location">(.*?)<', card, re.S)),
            "posted_date": tm.group(1) if tm else "",
            "posted_ago": tm.group(2).strip() if tm else "",
            "url": url.group(1),
        }


def describe(job_id):
    page = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
    body = re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""
    body = html.unescape(re.sub(r"\s+", " ", body)).strip()
    level = text(re.search(r"Seniority level\s*</h3>\s*<span[^>]*>(.*?)</span>", page, re.S))
    salary = text(re.search(r'salary compensation__salary">(.*?)<', page, re.S))
    return body, level, salary


def flag_sponsor(j, body):
    no = NO_SPONSOR.search(body)
    yes = YES_SPONSOR.search(body)
    j["sponsor_signal"] = "no" if no else "yes" if yes else "unstated"
    j["sponsor_evidence"] = (no or yes).group(0) if (no or yes) else ""
    # keep the tail too: work-authorization lines usually sit at the end
    j["description"] = body if len(body) <= 3000 else body[:2000] + " … " + body[-1000:]


def category_of(title):
    if re.search(r"\baml\b|anti[- ]money|\bkyc\b|know your customer|sanctions|financial crime|\bbsa\b|"
                 r"transaction monitoring|fraud|compliance", title, re.I):
        return "aml"
    if re.search(r"supply|procurement|sourcing|demand|inventory|logistics|planning", title, re.I):
        return "supply_chain"
    return "data"


def linkedin():
    jobs = {}
    for category, terms in SEARCHES.items():
        for kw in terms:
            for p in range(PAGES):
                found = 0
                for j in search(kw, p * 10):
                    found += 1
                    if SKIP_TITLE.search(j["title"]):
                        continue
                    key = (j["title"].lower(), j["company"].lower())
                    if key in {(v["title"].lower(), v["company"].lower()) for v in jobs.values()}:
                        continue  # same role re-posted in several cities
                    j["category"] = category
                    j["source"] = "linkedin"
                    jobs.setdefault(j["id"], j)
                print(f"{category:12} {kw!r} page {p}: {found}", file=sys.stderr)
                time.sleep(random.uniform(1.5, 3))
                if found < 10:
                    break

    print(f"{len(jobs)} unique LinkedIn postings; fetching descriptions", file=sys.stderr)
    for j in jobs.values():
        body, level, salary = describe(j["id"])
        flag_sponsor(j, body)
        j["seniority"] = level
        j["salary"] = salary
        time.sleep(random.uniform(1, 2))
    return list(jobs.values())


def employers():
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    jobs = collect(companies, SKIP_TITLE, log=lambda m: print(m, file=sys.stderr))
    for j in jobs:
        j["category"] = category_of(j["title"])
        flag_sponsor(j, j.get("description", ""))
    return jobs


def main():
    sources = sys.argv[1:] or ["linkedin", "employers"]
    jobs = []
    if "employers" in sources:
        jobs += employers()  # first, so a company's own posting wins over its LinkedIn copy
    if "linkedin" in sources:
        seen = {(j["title"].lower(), j["company"].lower()) for j in jobs}
        jobs += [j for j in linkedin() if (j["title"].lower(), j["company"].lower()) not in seen]

    OUT.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs": jobs,
    }, ensure_ascii=False, indent=1))
    counts = {s: sum(j["sponsor_signal"] == s for j in jobs) for s in ("yes", "unstated", "no")}
    print(f"wrote {OUT}: {len(jobs)} postings {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
