"""Rebuild data/index.json (newest first) and drop daily files older than 60 days."""
import json
from datetime import date, timedelta
from pathlib import Path

JOBS = Path(__file__).resolve().parent.parent / "data" / "jobs"
cutoff = (date.today() - timedelta(days=60)).isoformat()
dates = []
for f in sorted(JOBS.glob("*.json"), reverse=True):
    if f.stem < cutoff:
        f.unlink()
    else:
        dates.append(f.stem)
(JOBS.parent / "index.json").write_text(json.dumps(dates))
print(f"{len(dates)} days in index")
