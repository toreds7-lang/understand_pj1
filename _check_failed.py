import json

ERROR_PREFIX = "[summary error:"
path = r"d:\2026_Agent\1_Understanding_fast\paper_read_project_pdf\data\survey_physical_ai\paper.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)
pages = data.get("pages", [])
total = len(pages)
good = sum(1 for p in pages if (p.get("summary") or "").strip() and not (p.get("summary") or "").startswith(ERROR_PREFIX))
failed = sum(1 for p in pages if (p.get("summary") or "").startswith(ERROR_PREFIX))
empty = total - good - failed
print(f"Total pages: {total}")
print(f"Good summaries: {good}")
print(f"Failed (error): {failed}")
print(f"Empty (not done): {empty}")
