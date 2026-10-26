# Daily update

Run from the repo root. The candidate's profile and preferences are in the prompt that started this run.

1. `python3 scripts/fetch_jobs.py` — writes `data/candidates.json` (LinkedIn postings from the past 24h, with
   description text and a regex `sponsor_signal`: yes / unstated / no). Takes ~10 minutes.
   If it returns 0 postings (blocked network, LinkedIn changed its markup), fall back to WebSearch/WebFetch for
   postings from the past 24 hours and build the same fields by hand, and say so in the day's `summary`.

2. Review every candidate and keep only real matches:
   - Drop `sponsor_signal: "no"`, and anything requiring US citizenship, a green card, or a security clearance.
   - Keep `sponsor: "yes"` when the description says it sponsors or accepts H-1B transfer.
   - Keep `sponsor: "likely"` when the description is silent but the employer is a well-known, regular H-1B
     sponsor (large banks, Big 4, big tech, large manufacturers/retailers, etc. — use your knowledge; check
     h1bgrader.com or myvisajobs.com with WebFetch when unsure). Drop small/unknown employers with no signal.
   - Drop staffing-agency spam, "paid training" bootcamps, commission-only roles, and roles clearly too junior
     (pure entry level with no analytics) or too senior (10+ years, people-management).
   - Dedupe the same role at the same company.

3. Write `data/jobs/YYYY-MM-DD.json` (today's date in Pacific/Honolulu):

   ```json
   {
     "date": "YYYY-MM-DD",
     "generated_at": "<ISO 8601 UTC>",
     "summary": "繁體中文 2–4 行：今天幾個、最推薦哪 3 個、為什麼",
     "jobs": [{
       "title": "", "company": "", "location": "", "url": "", "posted_ago": "", "salary": "",
       "category": "data | supply_chain | aml",
       "sponsor": "yes | likely",
       "sponsor_note": "繁中一句：根據什麼判斷（引用 JD 原句或公司過往 H-1B 紀錄）",
       "fit": 1-5,
       "why": "繁中一兩句：對照履歷為什麼適合／要注意什麼",
       "resume": "DataAnalyst | SupplyChain | AML"
     }]
   }
   ```
   Sort by `fit` descending. Aim for quality over quantity (typically 15–40 jobs). Never include the candidate's
   name, contact details, or employer names from the profile anywhere in the repo — this repo is public.

4. `python3 scripts/update_index.py`, then commit (`jobs: YYYY-MM-DD`) and push to `main`.
   Do not commit `data/candidates.json`.
