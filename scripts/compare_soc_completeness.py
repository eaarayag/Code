"""Ad-hoc comparison of completeness across nio-a0-rtl-26ww41* SOC versions.

Buckets per the L2 pipeline definitions:
  PASS / FAIL  - finished tests (result present in the regression CSV)
  ON QUEUE     - launched in this version but no result yet (QUEUED)
  NOT LAUNCHED - in the expected plan but never launched by this version

The "expected plan" is the union of launched tags across all compared versions.
"""
import csv
import glob
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WEEKLY = os.path.join(ROOT_DIR, "weekly_report")


def normalize_tag(name):
    s = (name or "").strip().lower()
    return re.sub(r"\.\d+$", "", s)


pattern = os.path.join(WEEKLY, "nio-a0-rtl-26ww41*_regression_results.csv")
versions = sorted(glob.glob(pattern))

data = {}
for csv_path in versions:
    base = os.path.basename(csv_path).replace("_regression_results.csv", "")
    week = re.sub(r"^nio-a0-rtl-", "", base)

    json_path = os.path.join(WEEKLY, base + "_launched_tests.json")
    launched = set()
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            d = json.load(f)
        launched = {normalize_tag(t) for t in d.get("launched", []) or []}

    results = {}
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = normalize_tag(r.get("test_name") or "")
            if name:
                results[name] = (r.get("test_status") or "").strip().upper()

    data[week] = {"launched": launched, "results": results}

# Expected plan = union of all launched tags across versions.
expected = set()
for v in data.values():
    expected |= v["launched"]

rows = []
for week, v in data.items():
    launched = v["launched"]
    results = v["results"]
    pass_cnt = sum(1 for s in results.values() if s == "PASS")
    fail_cnt = sum(1 for s in results.values() if s == "FAIL")
    finished = set(results) & launched
    on_queue = len(launched - finished)                 # launched, no result yet
    not_launched = len(expected - launched)             # expected but never launched
    completeness = (len(finished) / len(expected) * 100) if expected else 0.0
    rows.append({
        "week": week, "launched": len(launched), "pass": pass_cnt, "fail": fail_cnt,
        "on_queue": on_queue, "not_launched": not_launched, "completeness": completeness,
    })

hdr = (f"{'Version':<16}{'Launched':>9}{'PASS':>6}{'FAIL':>6}"
       f"{'OnQueue':>9}{'NotLaunched':>13}{'Complete%':>11}")
print(f"Expected plan (union of launched tags): {len(expected)}\n")
print(hdr)
print("-" * len(hdr))
for r in rows:
    print(f"{r['week']:<16}{r['launched']:>9}{r['pass']:>6}{r['fail']:>6}"
          f"{r['on_queue']:>9}{r['not_launched']:>13}{r['completeness']:>10.1f}%")
