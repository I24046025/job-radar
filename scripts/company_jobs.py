"""Pull last-24h postings straight from employers' own career sites.

Employers live in data/companies.json, one entry per company:
  {"name": "Capital One", "ats": "workday", "tenant": "capitalone", "wd": "wd12", "site": "Capital_One"}
  {"name": "Stripe", "ats": "greenhouse", "board": "stripe"}
  {"name": "Plaid", "ats": "lever", "board": "plaid"}
  {"name": "Ramp", "ats": "ashby", "board": "ramp"}
  {"name": "Visa", "ats": "workday", "tenant": "visa", "wd": "wd5", "site": "Visa"}
  {"name": "Amazon", "ats": "amazon"}
  {"name": "JPMorgan Chase", "ats": "oracle", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"}

Check one entry before adding it:
  python3 scripts/company_jobs.py probe '<json entry>'
"""
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

RELEVANT = re.compile(
    r"data analy|business intelligence|\bbi\b|analytics|reporting analyst|data quality|data governance|"
    r"insights analyst|business analyst|operations analyst|"
    r"supply chain|procurement|sourcing|demand plan|supply plan|inventory|logistics|planning analyst|"
    r"\baml\b|anti[- ]money|\bkyc\b|know your customer|sanctions|financial crime|\bbsa\b|"
    r"transaction monitoring|fraud|compliance analyst|compliance specialist|risk analyst",
    re.I,
)

US_STATES = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
US_HINT = re.compile(
    r"(?i:united states|\busa?\b|\bremote\b|\d+ locations|multiple locations)|"
    r"(?:^|, ?)(?:" + "|".join(US_STATES) + r")\b"
)
NON_US = re.compile(
    r"canada|mexico|brazil|argentina|colombia|united kingdom|\buk\b|london|ireland|germany|france|"
    r"netherlands|spain|poland|india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|china|"
    r"shanghai|hong kong|singapore|japan|tokyo|korea|taiwan|philippines|manila|malaysia|australia|"
    r"sydney|israel|costa rica|toronto|vancouver",
    re.I,
)


def curl(url, data=None, tries=3):
    cmd = ["curl", "-sS", "--max-time", "25", "-A", UA, "-H", "Accept: application/json", "-w", "\n%{http_code}"]
    if data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(data)]
    for i in range(tries):
        r = subprocess.run(cmd + [url], capture_output=True, text=True)
        body, _, status = r.stdout.rpartition("\n")
        if status == "200":
            return json.loads(body)
        if status in ("400", "401", "403", "404", "410", "422"):
            raise RuntimeError(f"HTTP {status}")
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"HTTP {status} {r.stderr.strip()}")


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def recent(ts, hours=26):
    """ts: ISO string or epoch ms. A little slack past 24h so nothing falls between daily runs."""
    if ts is None or ts == "":
        return False
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts / 1000, timezone.utc)
    else:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(hours=hours)


def job(c, title, location, url, posted, description=""):
    return {"title": title.strip(), "company": c["name"], "location": location or "", "url": url,
            "posted_ago": posted, "description": description, "source": c["ats"]}


# --- one fetcher per ATS; each yields recent postings (all titles) -------------------------------

def workday(c, max_pages=15):
    base = f"https://{c['tenant']}.{c['wd']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    old = 0
    for page in range(max_pages):
        d = curl(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": ""})
        posts = d.get("jobPostings", [])
        # listings come newest first, except some sites pin a few older jobs at the top;
        # stop after a run of older ones
        for p in posts:
            if p.get("postedOn") not in ("Posted Today", "Posted Yesterday"):
                old += 1
                if old >= 20:  # a full page with nothing new
                    return
                continue
            old = 0
            yield job(c, p.get("title", ""), p.get("locationsText", ""),
                      f"{base}/{c['site']}{p['externalPath']}", p["postedOn"].replace("Posted ", ""))
        if len(posts) < 20:
            return


def workday_detail(c, j):
    path = j["url"].split(f"/{c['site']}", 1)[1]
    api = f"https://{c['tenant']}.{c['wd']}.myworkdayjobs.com/wday/cxs/{c['tenant']}/{c['site']}{path}"
    info = curl(api).get("jobPostingInfo", {})
    j["description"] = strip_html(info.get("jobDescription"))
    country = (info.get("country") or {}).get("descriptor", "")
    if country:
        j["country"] = country
    if info.get("location"):
        j["location"] = info["location"]


def greenhouse(c):
    d = curl(f"https://boards-api.greenhouse.io/v1/boards/{c['board']}/jobs?content=true")
    for p in d.get("jobs", []):
        if recent(p.get("first_published") or p.get("updated_at")):
            yield job(c, p["title"], (p.get("location") or {}).get("name", ""), p["absolute_url"],
                      p.get("first_published", "")[:10], strip_html(p.get("content")))


def lever(c):
    for p in curl(f"https://api.lever.co/v0/postings/{c['board']}?mode=json"):
        if recent(p.get("createdAt")):
            cat = p.get("categories") or {}
            yield job(c, p["text"], cat.get("location", ""), p["hostedUrl"],
                      datetime.fromtimestamp(p["createdAt"] / 1000, timezone.utc).date().isoformat(),
                      p.get("descriptionPlain", "") + " " + p.get("additionalPlain", ""))


def ashby(c):
    d = curl(f"https://api.ashbyhq.com/posting-api/job-board/{c['board']}?includeCompensation=true")
    for p in d.get("jobs", []):
        if recent(p.get("publishedAt")):
            yield job(c, p["title"], p.get("location", ""), p["jobUrl"], p["publishedAt"][:10],
                      p.get("descriptionPlain", ""))


def smartrecruiters(c, max_pages=5):
    for page in range(max_pages):
        d = curl(f"https://api.smartrecruiters.com/v1/companies/{c['board']}/postings"
                 f"?limit=100&offset={page * 100}&country=us")
        posts = d.get("content", [])
        for p in posts:
            if recent(p.get("releasedDate")):
                loc = p.get("location") or {}
                yield job(c, p["name"], ", ".join(filter(None, [loc.get("city"), loc.get("region")])),
                          f"https://jobs.smartrecruiters.com/{c['board']}/{p['id']}", p["releasedDate"][:10])
        if len(posts) < 100:
            return


def amazon(c):
    for q in ("data analyst", "business intelligence", "supply chain analyst", "AML compliance", "KYC"):
        qs = urllib.parse.urlencode({"base_query": q, "loc_query": "United States", "country": "USA",
                                     "sort": "recent", "result_limit": 50})
        for p in curl(f"https://www.amazon.jobs/en/search.json?{qs}").get("jobs", []):
            posted = datetime.strptime(p["posted_date"], "%B %d, %Y").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - posted <= timedelta(days=1, hours=12):
                yield job(c, p["title"], p.get("normalized_location", ""),
                          "https://www.amazon.jobs" + p["job_path"], p["posted_date"],
                          strip_html(p.get("description", "") + " " + p.get("basic_qualifications", "")
                                     + " " + p.get("preferred_qualifications", "")))


def oracle(c, max_pages=16):
    api = f"https://{c['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    for page in range(max_pages):
        d = curl(f"{api}?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={c['site']},"
                 f"facetsList=NONE,limit=25,offset={page * 25},sortBy=POSTING_DATES_DESC")
        reqs = (d.get("items") or [{}])[0].get("requisitionList", [])
        for r in reqs:  # newest first
            if not recent(r.get("PostedDate") + "T23:59:59Z", hours=40):
                return
            j = job(c, r["Title"], r.get("PrimaryLocation", ""),
                    f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{r['Id']}",
                    r["PostedDate"])
            j["country"] = "United States" if r.get("PrimaryLocationCountry") == "US" else r.get("PrimaryLocationCountry", "")
            j["req_id"] = r["Id"]
            yield j
        if len(reqs) < 25:
            return


def oracle_detail(c, j):
    d = curl(f"https://{c['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
             f"?expand=all&onlyData=true&finder=ById;Id=%22{j.pop('req_id')}%22,siteNumber={c['site']}")
    it = (d.get("items") or [{}])[0]
    j["description"] = strip_html(" ".join(it.get(k) or "" for k in (
        "ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr")))


FETCHERS = {"workday": workday, "oracle": oracle, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "amazon": amazon}


def is_us(j):
    if j.get("country"):
        return j["country"].startswith("United States")
    return bool(US_HINT.search(j["location"])) and not NON_US.search(j["location"])


def collect(companies, skip_title, log=print):
    """Recent, relevant, US postings from every company, with descriptions filled in."""
    out = []
    for c in companies:
        try:
            found = [j for j in FETCHERS[c["ats"]](c)
                     if RELEVANT.search(j["title"]) and not skip_title.search(j["title"])]
            for j in found:
                if c["ats"] == "workday":
                    workday_detail(c, j)
                    time.sleep(0.5)
                elif c["ats"] == "oracle":
                    oracle_detail(c, j)
                elif c["ats"] == "smartrecruiters":
                    d = curl(f"https://api.smartrecruiters.com/v1/companies/{c['board']}/postings/"
                             + j["url"].rsplit("/", 1)[1])
                    sections = (d.get("jobAd") or {}).get("sections") or {}
                    j["description"] = strip_html(" ".join(s.get("text", "") for s in sections.values()))
            found = [j for j in found if is_us(j)]
            log(f"{c['name']:35} {len(found)}")
            out += found
        except Exception as e:
            log(f"{c['name']:35} ERROR {e}")
        time.sleep(0.5)
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "probe":
        c = json.loads(sys.argv[2])
        n = 0
        for j in FETCHERS[c["ats"]](c):
            n += 1
            if n <= 3:
                print(" ", j["posted_ago"], "|", j["title"], "|", j["location"])
        print(f"OK {c['name']}: {n} postings in the last day")
    else:
        print(__doc__)
