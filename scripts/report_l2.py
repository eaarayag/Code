# report_l2.py
# Reads CSV files from the local weekly_report folder and generates a
# consolidated report CSV with owner, test_case, status, and model.
# Usage: python report_l2.py

import csv
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))       # Directory where this script lives
ROOT_DIR = os.path.dirname(SCRIPT_DIR)                        # Project root (parent of scripts/)
OWNERSHIP_FILE = os.path.join(SCRIPT_DIR, "ownership.txt")    # Maps test prefixes to owners
WEEKLY_REPORT_DIR = os.path.join(ROOT_DIR, "weekly_report")   # Folder with per-model regression CSVs
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")                # Folder for generated reports
PARSE_SCRIPT = os.path.join(SCRIPT_DIR, "parse_l2_regression.py")  # Parser script path
os.makedirs(REPORTS_DIR, exist_ok=True)
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")          # Timestamp for output filename
OUTPUT_REPORT = os.path.join(REPORTS_DIR, f"general_report_{TIMESTAMP}.csv")  # Final consolidated report
STACK_HISTORY_FILE = os.path.join(REPORTS_DIR, "stack_status_history.csv")      # Historical stack-level percentages
GITHUB_PAGES_INDEX = "https://eaarayag.github.io/Code/reports/index.html"     # Report history on GitHub Pages
GITHUB_PAGES_BASE = "https://eaarayag.github.io/Code/reports/"               # Base URL for individual reports


def load_ownership(filepath):
    """Load ownership.txt and return a list of (owner, prefix) tuples, sorted longest prefix first.
    Also parses test-type-level overrides (lines with '@' prefix) into a separate structure."""
    ownership = []
    test_type_overrides = []  # List of (owner, test_type, excluded_owners)
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            owner = parts[0].strip()
            target = parts[1].strip() if len(parts) > 1 else ''
            if target.startswith('@'):
                # Test-type-level override: Owner,@test_type[,exclude:OwnerName]
                test_type = target[1:]  # Strip '@'
                excluded_owners = set()
                for extra in parts[2:]:
                    extra = extra.strip()
                    if extra.startswith('exclude:'):
                        excluded_owners.add(extra[len('exclude:'):].strip())
                test_type_overrides.append((owner, test_type, excluded_owners))
            else:
                # Partition-level ownership
                ownership.append((owner, target))
    # Sort by prefix length descending so longer prefixes match first
    ownership.sort(key=lambda x: len(x[1]), reverse=True)
    return ownership, test_type_overrides


def find_owner(test_name, ownership):
    """Find the owner for a test_name by matching the longest ownership prefix."""
    for owner, prefix in ownership:
        if test_name.startswith(prefix):
            return owner
    return "UNKNOWN"


def split_test_name(test_name, ownership):
    """Split test_name into (partition, test_type) using ownership prefixes."""
    for _, prefix in ownership:
        # If test_name starts with "prefix_", split at that boundary
        if test_name.startswith(prefix + '_'):
            return prefix, test_name[len(prefix) + 1:]
        # If test_name matches the prefix exactly, test_type is empty
        if test_name == prefix:
            return prefix, ''
    # No matching prefix found; treat entire name as partition
    return test_name, ''


def extract_model_from_filename(filename):
    """Extract model name from CSV filename, e.g. 'nio_mc-a0-26ww14a' from 'nio_mc-a0-26ww14a_regression_results.csv'."""
    basename = os.path.basename(filename)
    # Remove the '_regression_results.csv' suffix
    if basename.endswith("_regression_results.csv"):
        return basename[:-len("_regression_results.csv")]
    return basename


def list_available_models(category):
    """List available CSV files in weekly_report/ matching a category prefix (e.g. 'nio_mc')."""
    pattern = os.path.join(WEEKLY_REPORT_DIR, f"{category}*_regression_results.csv")
    files = sorted(glob.glob(pattern))
    return [extract_model_from_filename(f) for f in files]


def load_report_timestamps():
    """Load report_timestamps.json from weekly_report/ if available."""
    timestamps_file = os.path.join(WEEKLY_REPORT_DIR, "report_timestamps.json")
    if os.path.isfile(timestamps_file):
        try:
            with open(timestamps_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def fetch_remote_models():
    """Call parse_l2_regression.py --list-models and return all available model names.
    Falls back to None (local CSVs will be used) if the remote call fails."""
    try:
        result = subprocess.run(
            [sys.executable, PARSE_SCRIPT, "--list-models"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            models = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            if models:
                return models
    except Exception as e:
        print(f"Warning: Remote model fetch failed ({e}). Falling back to local CSVs.")
    return None


def parse_selected_models(selected_models):
    """Call parse_l2_regression.py --models=... to parse only the selected models."""
    models_str = ",".join(selected_models)
    subprocess.run(
        [sys.executable, PARSE_SCRIPT, f"--models={models_str}"],
        check=True
    )


def prompt_model_selection(category, remote_models=None, timestamps=None):
    """Show available models for a category and let the user pick one (or skip)."""
    if remote_models is not None:
        models = sorted([m for m in remote_models if m.startswith(category)])
    else:
        models = list_available_models(category)
    if not models:
        print(f"  No models found for '{category}'. Skipping.")
        return None

    if timestamps is None:
        timestamps = {}

    print(f"\n  Available {category.upper()} models:")
    for i, model in enumerate(models, 1):
        ts = timestamps.get(model, "")
        if ts:
            print(f"    {i}. {model}  (last modified: {ts})")
        else:
            print(f"    {i}. {model}")
    print(f"    0. Skip {category.upper()}")

    while True:
        choice = input(f"  Select {category.upper()} model [1-{len(models)}, 0 to skip]: ").strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        print(f"  Invalid choice. Enter 0-{len(models)}.")


# Test types to exclude from reporting entirely (exact match against test_type after partition split).
EXCLUDED_TEST_TYPES = {
    'atspeed_edt_bypass_low_internal_serial_scan',
}

# Prefixes used to exclude any test_type that starts with one of these tokens
# (also matches when the token appears after a partition-like prefix, e.g. "uio_1_ijtag_...").
EXCLUDED_TEST_PREFIXES = (
    'ijtag',
)


def is_excluded_test_type(test_type):
    """Return True if a test_type should be excluded from the report."""
    if test_type in EXCLUDED_TEST_TYPES:
        return True
    for token in EXCLUDED_TEST_PREFIXES:
        if test_type.startswith(token) or ('_' + token) in test_type:
            return True
    return False


# Expected test cases for every partition. '*' prefix means suffix match.
EXPECTED_TESTS = [
    'atspeed_edt_edt_low_internal_serial_scan',
    '*scan_ctlr_stuckat_edt_bypass_low_internal_scandump',
    'ssn_continuity',
    'stuckat_edt_bypass_low_internal_burnin_togcnt_cap_off',
    'stuckat_edt_bypass_low_internal_serial_chain',
    'stuckat_edt_bypass_low_internal_serial_scan',
    'stuckat_edt_edt_low_internal_loopback',
    'stuckat_edt_edt_low_internal_serial_chain',
    'stuckat_edt_edt_low_internal_serial_scan',
]

def get_effective_owner(test_type, owner, test_type_overrides):
    """Return effective owner based on test-type-level overrides from ownership.txt.
    If the test_type matches an override and the current owner is not excluded, return the override owner."""
    for override_owner, override_test_type, excluded_owners in test_type_overrides:
        if test_type == override_test_type or test_type.endswith(override_test_type):
            if owner in excluded_owners:
                return owner
            return override_owner
    return owner


def get_partition_type(partition):
    """Determine model type (mc/uio/d2d) for a partition based on its naming prefix."""
    if partition.startswith('pard2d'):
        return 'd2d'
    elif partition.startswith('parmc') or partition.startswith('parmem'):
        return 'mc'
    elif partition.startswith('parmio'):
        return 'uio'
    return None


def get_model_type(model_name):
    """Extract model type (mc/uio/d2d) from model name like 'nio_mc-a0-26ww14a'."""
    if model_name.startswith('nio_mc'):
        return 'mc'
    elif model_name.startswith('nio_uio'):
        return 'uio'
    elif model_name.startswith('nio_d2d'):
        return 'd2d'
    return None


def _extract_timestamp_from_report_name(report_name):
    """Extract report timestamp string from a general_report filename."""
    import re
    m = re.search(r'(\d{8}_\d{6})', report_name)
    return m.group(1) if m else ''


def _format_report_timestamp_for_history(ts):
    """Convert 'YYYYMMDD_HHMMSS' to 'YYYY-MM-DD HH:MM:SS'."""
    if len(ts) == 15 and '_' in ts:
        date_part, time_part = ts.split('_', 1)
        return (
            f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]} "
            f"{time_part[0:2]}:{time_part[2:4]}:{time_part[4:6]}"
        )
    return ''


def _compute_stack_metrics(rows):
    """Compute PASS/FAIL/MISSING counts and percentages per stack (mc/uio/d2d)."""
    metrics = {}
    for r in rows:
        stack = get_model_type(r['model'])
        if stack not in ('mc', 'uio', 'd2d'):
            continue
        if stack not in metrics:
            metrics[stack] = {'total': 0, 'pass': 0, 'fail': 0, 'missing': 0}
        metrics[stack]['total'] += 1
        st = r['status']
        if st == 'PASS':
            metrics[stack]['pass'] += 1
        elif st == 'FAIL':
            metrics[stack]['fail'] += 1
        else:
            metrics[stack]['missing'] += 1

    out = {}
    for stack, s in metrics.items():
        total = s['total']
        out[stack] = {
            'total': total,
            'pass': s['pass'],
            'fail': s['fail'],
            'missing': s['missing'],
            'pass_pct': (s['pass'] / total * 100) if total else 0.0,
            'fail_pct': (s['fail'] / total * 100) if total else 0.0,
            'missing_pct': (s['missing'] / total * 100) if total else 0.0,
        }
    return out


def _load_general_report_rows(report_path):
    """Load rows from a general_report CSV file."""
    rows = []
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except OSError:
        return []
    return rows


def rebuild_stack_history_csv():
    """Rebuild stack_status_history.csv from all historical general_report CSV files."""
    pattern = os.path.join(REPORTS_DIR, 'general_report_*.csv')
    report_paths = sorted(glob.glob(pattern))

    entries = []
    for report_path in report_paths:
        report_name = os.path.basename(report_path)
        ts_compact = _extract_timestamp_from_report_name(report_name)
        ts_human = _format_report_timestamp_for_history(ts_compact)
        rows = _load_general_report_rows(report_path)
        if not rows:
            continue
        metrics = _compute_stack_metrics(rows)
        for stack in ('mc', 'uio', 'd2d'):
            m = metrics.get(stack)
            if not m:
                continue
            entries.append({
                'report_name': report_name,
                'report_timestamp': ts_human,
                'timestamp_key': ts_compact,
                'stack': stack,
                'total': m['total'],
                'pass': m['pass'],
                'fail': m['fail'],
                'missing': m['missing'],
                'pass_pct': f"{m['pass_pct']:.2f}",
                'fail_pct': f"{m['fail_pct']:.2f}",
                'missing_pct': f"{m['missing_pct']:.2f}",
            })

    entries.sort(key=lambda e: (e['timestamp_key'], e['stack']))

    with open(STACK_HISTORY_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                'report_name', 'report_timestamp', 'stack',
                'total', 'pass', 'fail', 'missing',
                'pass_pct', 'fail_pct', 'missing_pct',
            ]
        )
        writer.writeheader()
        for e in entries:
            writer.writerow({
                'report_name': e['report_name'],
                'report_timestamp': e['report_timestamp'],
                'stack': e['stack'],
                'total': e['total'],
                'pass': e['pass'],
                'fail': e['fail'],
                'missing': e['missing'],
                'pass_pct': e['pass_pct'],
                'fail_pct': e['fail_pct'],
                'missing_pct': e['missing_pct'],
            })

    print(f"Stack history file updated: {STACK_HISTORY_FILE}")


def load_stack_history():
    """Load stack history rows keyed by stack from stack_status_history.csv."""
    data = {'mc': [], 'uio': [], 'd2d': []}
    if not os.path.isfile(STACK_HISTORY_FILE):
        return data

    with open(STACK_HISTORY_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stack = row.get('stack', '').strip()
            if stack not in data:
                continue
            data[stack].append(row)

    for stack in data:
        data[stack].sort(key=lambda r: _extract_timestamp_from_report_name(r.get('report_name', '')))
    return data


def check_test_completeness(all_rows, ownership, test_type_overrides, selected_models):
    """For each (partition, model), add MISSING rows for any expected test not found.
    Checks ALL partitions from ownership.txt, but only against their matching model type."""
    # Group existing test_types by (partition, model)
    existing = {}
    for row in all_rows:
        key = (row['partition'], row['model'])
        if key not in existing:
            existing[key] = set()
        existing[key].add(row['test_type'])

    # Build combos only for matching partition/model types
    all_partition_combos = []
    for owner, prefix in ownership:
        p_type = get_partition_type(prefix)
        for model in selected_models:
            m_type = get_model_type(model)
            # Only pair partition with its matching model type
            if p_type and m_type and p_type == m_type:
                all_partition_combos.append((prefix, model, owner))

    missing_rows = []
    for (partition, model, owner) in all_partition_combos:
        test_types = existing.get((partition, model), set())
        for expected in EXPECTED_TESTS:
            if expected.startswith('*'):
                # Suffix match: check if any existing test_type ends with the pattern
                suffix = expected[1:]
                if not any(tt.endswith(suffix) for tt in test_types):
                    effective_owner = get_effective_owner(suffix, owner, test_type_overrides)
                    missing_rows.append({
                        'owner': effective_owner,
                        'partition': partition,
                        'test_type': suffix,
                        'status': 'MISSING',
                        'model': model,
                    })
            else:
                # Exact match
                if expected not in test_types:
                    effective_owner = get_effective_owner(expected, owner, test_type_overrides)
                    missing_rows.append({
                        'owner': effective_owner,
                        'partition': partition,
                        'test_type': expected,
                        'status': 'MISSING',
                        'model': model,
                    })

    return missing_rows


def generate_general_report_for_models(selected_models):
    """Read CSV files for the selected models and produce a consolidated general_report.csv."""
    ownership, test_type_overrides = load_ownership(OWNERSHIP_FILE)

    all_rows = []
    for model in selected_models:
        csv_file = os.path.join(WEEKLY_REPORT_DIR, f"{model}_regression_results.csv")
        if not os.path.isfile(csv_file):
            print(f"Warning: {csv_file} not found. Skipping.")
            continue
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                test_name = row['test_name']
                owner = find_owner(test_name, ownership)
                if owner == "UNKNOWN":
                    continue
                partition, test_type = split_test_name(test_name, ownership)
                if is_excluded_test_type(test_type):
                    continue
                # Override owner if test_type is owned at the test-type level
                effective_owner = get_effective_owner(test_type, owner, test_type_overrides)
                all_rows.append({
                    'owner': effective_owner,
                    'partition': partition,
                    'test_type': test_type,
                    'status': row['test_status'],
                    'model': model,
                })

    if not all_rows:
        print("No test entries found for selected models.")
        return

    # Check for missing expected tests and add MISSING rows (checks ALL ownership partitions)
    missing_rows = check_test_completeness(all_rows, ownership, test_type_overrides, selected_models)
    if missing_rows:
        print(f"\nFound {len(missing_rows)} missing test(s) across partitions.")
        all_rows.extend(missing_rows)

    all_rows.sort(key=lambda r: (r['model'], r['partition'], r['test_type'], r['owner']))

    with open(OUTPUT_REPORT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['partition', 'test_type', 'status', 'owner', 'model'])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nGeneral report generated: {OUTPUT_REPORT}")
    print(f"Total test entries: {len(all_rows)} ({len(missing_rows)} MISSING)")

    # Rebuild stack-level historical percentages from all general reports.
    rebuild_stack_history_csv()

    # Also generate HTML version
    generate_general_report_html(all_rows)


def generate_general_report_html(all_rows):
    """Generate an Outlook-compatible HTML version of the general report."""
    import html as html_mod

    FONT = "font-family:Arial,Helvetica,sans-serif;"
    MONO = "font-family:Consolas,'Courier New',monospace;"
    html_path = os.path.join(REPORTS_DIR, f"general_report_{TIMESTAMP}.html")

    # Compute summary stats per owner
    owner_stats = {}
    for r in all_rows:
        o = r['owner']
        if o not in owner_stats:
            owner_stats[o] = {'pass': 0, 'fail': 0, 'missing': 0, 'total': 0}
        owner_stats[o]['total'] += 1
        if r['status'] == 'PASS':
            owner_stats[o]['pass'] += 1
        elif r['status'] == 'FAIL':
            owner_stats[o]['fail'] += 1
        else:
            owner_stats[o]['missing'] += 1

    # Group rows by model
    models_seen = []
    rows_by_model = {}
    for r in all_rows:
        m = r['model']
        if m not in rows_by_model:
            rows_by_model[m] = []
            models_seen.append(m)
        rows_by_model[m].append(r)

    total = len(all_rows)
    total_pass = sum(1 for r in all_rows if r['status'] == 'PASS')
    total_fail = sum(1 for r in all_rows if r['status'] == 'FAIL')
    total_missing = sum(1 for r in all_rows if r['status'] == 'MISSING')
    pass_rate = (total_pass / total * 100) if total else 0

    def status_bg(s):
        return {'PASS': '#e8f5e9', 'FAIL': '#ffebee', 'MISSING': '#fff3e0'}.get(s, '#ffffff')

    def status_fg(s):
        return {'PASS': '#2e7d32', 'FAIL': '#c62828', 'MISSING': '#e65100'}.get(s, '#333333')

    h = []
    h.append('<!DOCTYPE html>')
    h.append('<html xmlns="http://www.w3.org/1999/xhtml">')
    h.append('<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>')
    h.append(f'<body style="margin:0;padding:0;background-color:#f4f4f4;{FONT}">')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f4">')
    h.append('<tr><td align="center" style="padding:20px 0;">')
    h.append('<table width="1000" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
             'style="border:1px solid #dddddd;">')

    # ── Header banner ──
    h.append('<tr><td bgcolor="#0071c5" style="padding:24px 32px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="color:#ffffff;font-size:22px;font-weight:bold;{FONT}">GENERAL REPORT</span><br>')
    h.append(f'<span style="color:#b3d9f2;font-size:14px;{FONT}">SCAN L2 Regression &mdash; {datetime.now().strftime("%Y-%m-%d %H:%M")}</span>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # ── Overall stats cards ──
    h.append('<tr><td style="padding:24px 32px 16px;">')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')
    stats = [
        (str(total), 'TOTAL', '#f0f7ff', '#0071c5'),
        (str(total_pass), 'PASS', '#e8f5e9', '#2e7d32'),
        (str(total_fail), 'FAIL', '#ffebee', '#c62828'),
        (str(total_missing), 'MISSING', '#fff3e0', '#e65100'),
    ]
    for i, (val, label, bg, fg) in enumerate(stats):
        if i > 0:
            h.append('<td width="8"></td>')
        h.append(f'<td width="25%" align="center" bgcolor="{bg}" style="padding:14px 8px;">')
        h.append(f'<span style="font-size:28px;font-weight:bold;color:{fg};{FONT}">{val}</span><br>')
        h.append(f'<span style="font-size:11px;color:#666;text-transform:uppercase;{FONT}">{label}</span>')
        h.append('</td>')
    h.append('</tr></table>')
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">')
    h.append(f'<tr><td align="center" style="font-size:14px;color:#555;{FONT}">')
    h.append(f'Pass rate: <b style="color:#0071c5;font-size:20px;">{pass_rate:.1f}%</b>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # ── Per-owner summary row ──
    h.append('<tr><td style="padding:0 32px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">PER-OWNER SUMMARY</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0">')
    h.append(f'<tr bgcolor="#0071c5">')
    for col in ['Owner', 'Total', 'Pass', 'Fail', 'Missing', 'Pass Rate']:
        align = 'left' if col == 'Owner' else 'center'
        h.append(f'<td align="{align}" style="padding:8px 12px;font-size:12px;font-weight:bold;color:#ffffff;{FONT}">{col}</td>')
    h.append('</tr>')
    for i, owner in enumerate(sorted(owner_stats.keys())):
        s = owner_stats[owner]
        rate = (s["pass"] / s["total"] * 100) if s["total"] else 0
        bg = '#f9f9f9' if i % 2 == 0 else '#ffffff'
        h.append(f'<tr bgcolor="{bg}">')
        h.append(f'<td style="padding:8px 12px;font-size:13px;font-weight:bold;color:#333;{FONT}">{html_mod.escape(owner)}</td>')
        h.append(f'<td align="center" style="padding:8px 12px;font-size:13px;color:#333;{FONT}">{s["total"]}</td>')
        h.append(f'<td align="center" style="padding:8px 12px;font-size:13px;color:#2e7d32;font-weight:bold;{FONT}">{s["pass"]}</td>')
        h.append(f'<td align="center" style="padding:8px 12px;font-size:13px;color:#c62828;font-weight:bold;{FONT}">{s["fail"]}</td>')
        h.append(f'<td align="center" style="padding:8px 12px;font-size:13px;color:#e65100;font-weight:bold;{FONT}">{s["missing"]}</td>')
        h.append(f'<td align="center" style="padding:8px 12px;font-size:13px;color:#0071c5;font-weight:bold;{FONT}">{rate:.1f}%</td>')
        h.append('</tr>')
    h.append('</table>')
    h.append('</td></tr>')

    # ── Historical stack trends (horizontal, 3-across) ──
    stack_history = load_stack_history()

    def render_stack_trend_svg(stack_key, title):
        rows = stack_history.get(stack_key, [])
        if not rows:
            return (
                '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                '<tr><td align="center" style="padding:18px 6px;font-size:12px;color:#999;">'
                'No historical data available'
                '</td></tr></table>'
            )

        points = []
        for r in rows:
            try:
                p = float(r.get('pass_pct', 0) or 0)
                f_ = float(r.get('fail_pct', 0) or 0)
                m = float(r.get('missing_pct', 0) or 0)
            except ValueError:
                p, f_, m = 0.0, 0.0, 0.0
            label = r.get('report_timestamp', '')[:10]  # YYYY-MM-DD
            points.append((label, p, f_, m))

        width, height = 300, 200
        left, right, top, bottom = 36, 10, 14, 52
        plot_w = width - left - right
        plot_h = height - top - bottom
        n = len(points)

        def x_at(i):
            if n <= 1:
                return left + plot_w / 2
            return left + (plot_w * i / (n - 1))

        def y_at(v):
            return top + (100 - max(0.0, min(100.0, v))) * plot_h / 100

        pass_pts = ' '.join(f"{x_at(i):.1f},{y_at(pt[1]):.1f}" for i, pt in enumerate(points))
        fail_pts = ' '.join(f"{x_at(i):.1f},{y_at(pt[2]):.1f}" for i, pt in enumerate(points))
        miss_pts = ' '.join(f"{x_at(i):.1f},{y_at(pt[3]):.1f}" for i, pt in enumerate(points))

        latest = points[-1] if points else ('', 0.0, 0.0, 0.0)

        svg = []
        svg.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0">')
        svg.append(f'<tr><td align="center" style="padding:0 0 4px;font-size:12px;font-weight:bold;color:#333;{FONT}">{title}</td></tr>')
        svg.append(f'<tr><td align="center">')
        svg.append(f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">')

        # Grid and y-axis labels
        for yv in (0, 25, 50, 75, 100):
            y = y_at(yv)
            color = '#dddddd' if yv in (0, 50, 100) else '#eeeeee'
            svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{color}" stroke-width="1"/>')
            svg.append(f'<text x="{left - 5}" y="{y + 4:.1f}" text-anchor="end" font-size="9" fill="#777" font-family="Arial,sans-serif">{yv}%</text>')

        # Axes
        svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#999" stroke-width="1"/>')
        svg.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#999" stroke-width="1"/>')

        # Lines
        svg.append(f'<polyline fill="none" stroke="#2e7d32" stroke-width="2" stroke-linejoin="round" points="{pass_pts}"/>')
        svg.append(f'<polyline fill="none" stroke="#c62828" stroke-width="2" stroke-linejoin="round" points="{fail_pts}"/>')
        svg.append(f'<polyline fill="none" stroke="#e65100" stroke-width="1.5" stroke-dasharray="4,2" stroke-linejoin="round" points="{miss_pts}"/>')

        # Data point circles
        for i, pt in enumerate(points):
            cx = x_at(i)
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[1]):.1f}" r="2.5" fill="#2e7d32"/>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[2]):.1f}" r="2.5" fill="#c62828"/>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[3]):.1f}" r="2" fill="#e65100"/>')

        # X-axis labels — all points, rotated 45 degrees, YY-MM-DD format
        label_y = top + plot_h + 10
        for i, pt in enumerate(points):
            cx = x_at(i)
            short_label = pt[0][2:] if len(pt[0]) >= 10 else pt[0]  # YY-MM-DD
            svg.append(f'<text x="{cx:.1f}" y="{label_y}" text-anchor="end" font-size="8" fill="#666" font-family="Arial,sans-serif" transform="rotate(-50 {cx:.1f} {label_y})">{short_label}</text>')

        svg.append('</svg>')
        svg.append('</td></tr>')
        # Legend
        svg.append(f'<tr><td align="center" style="padding:2px 0 4px;font-size:10px;color:#555;{FONT}">')
        svg.append(f'<span style="color:#2e7d32;font-weight:bold;">&#9679; PASS {latest[1]:.1f}%</span> &nbsp;')
        svg.append(f'<span style="color:#c62828;font-weight:bold;">&#9679; FAIL {latest[2]:.1f}%</span> &nbsp;')
        svg.append(f'<span style="color:#e65100;font-weight:bold;">&#9679; MISS {latest[3]:.1f}%</span>')
        svg.append('</td></tr></table>')
        return ''.join(svg)

    h.append('<tr><td style="padding:8px 20px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">HISTORICAL STACK TRENDS</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')

    stacks = [('mc', 'MC Stack'), ('uio', 'UIO Stack'), ('d2d', 'D2D Stack')]
    for i, (stack_key, stack_title) in enumerate(stacks):
        if i > 0:
            h.append('<td width="10"></td>')
        h.append('<td width="33%" valign="top" bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:8px 6px;">')
        h.append(render_stack_trend_svg(stack_key, stack_title))
        h.append('</td>')

    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Detailed test results per model ──
    for model in models_seen:
        model_rows = rows_by_model[model]
        m_pass = sum(1 for r in model_rows if r['status'] == 'PASS')
        m_fail = sum(1 for r in model_rows if r['status'] == 'FAIL')
        m_miss = sum(1 for r in model_rows if r['status'] == 'MISSING')

        h.append('<tr><td style="padding:16px 32px 8px;">')
        h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
        h.append(f'<span style="font-size:15px;font-weight:bold;color:#333;{FONT}">{html_mod.escape(model)}</span> ')
        h.append(f'<span style="font-size:12px;color:#888;{FONT}">')
        h.append(f'&mdash; {len(model_rows)} tests: ')
        h.append(f'<span style="color:#2e7d32;">{m_pass} pass</span>, ')
        h.append(f'<span style="color:#c62828;">{m_fail} fail</span>, ')
        h.append(f'<span style="color:#e65100;">{m_miss} missing</span>')
        h.append('</span></td></tr></table>')
        h.append('</td></tr>')

        h.append('<tr><td style="padding:0 32px 16px;">')
        h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e0e0e0;">')
        # Table header
        h.append(f'<tr bgcolor="#f0f0f0">')
        for col in ['Partition', 'Test Type', 'Status', 'Owner']:
            h.append(f'<td style="padding:7px 10px;font-size:12px;font-weight:bold;color:#555;border-bottom:2px solid #d0d0d0;{FONT}">{col}</td>')
        h.append('</tr>')
        # Table rows
        for i, r in enumerate(model_rows):
            bg = '#ffffff' if i % 2 == 0 else '#fafafa'
            st = r['status']
            h.append(f'<tr bgcolor="{bg}">')
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;{MONO}border-bottom:1px solid #eee;">{html_mod.escape(r["partition"])}</td>')
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;{MONO}border-bottom:1px solid #eee;">{html_mod.escape(r["test_type"])}</td>')
            h.append(f'<td align="center" bgcolor="{status_bg(st)}" style="padding:6px 10px;font-size:12px;font-weight:bold;white-space:nowrap;color:{status_fg(st)};{FONT}border-bottom:1px solid #eee;">{st}</td>')
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#555;white-space:nowrap;{FONT}border-bottom:1px solid #eee;">{html_mod.escape(r["owner"])}</td>')
            h.append('</tr>')
        h.append('</table>')
        h.append('</td></tr>')

    # ── Footer ──
    h.append('<tr><td bgcolor="#f8f8f8" style="padding:16px 32px;border-top:1px solid #e0e0e0;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:11px;color:#999;{FONT}mso-line-height-rule:exactly;line-height:18px;">')
    h.append('This is an automated report generated by AI.<br>')
    h.append('For changes contact: <a href="mailto:emmanuel.a.araya.gamboa@intel.com" '
             'style="color:#0071c5;text-decoration:none;">Emmanuel Araya</a>')
    h.append('</span></td></tr></table>')
    h.append('</td></tr>')

    h.append('</table></td></tr></table></body></html>')

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(h))
    print(f"General report (HTML): {html_path}")


def find_previous_report(current_report_path):
    """Find the most recent general_report CSV before the current one."""
    current_name = os.path.basename(current_report_path)
    pattern = os.path.join(REPORTS_DIR, "general_report_*.csv")
    reports = sorted(glob.glob(pattern))
    # Filter out the current report and pick the latest remaining
    previous = [r for r in reports if os.path.basename(r) != current_name]
    return previous[-1] if previous else None


def _report_date_str(report_path):
    """Extract date from report filename like general_report_20260407_151132.csv -> '2026-04-07 15:11:32'."""
    basename = os.path.basename(report_path)
    import re
    m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', basename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
    return "unknown"


def load_report_as_dict(report_path):
    """Load a general report CSV and return a dict keyed by (partition, test_type) -> row."""
    result = {}
    with open(report_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['partition'], row['test_type'])
            result[key] = row
    return result


def _status_color(status):
    """Return inline CSS color for a test status."""
    return {'PASS': '#2e7d32', 'FAIL': '#c62828', 'MISSING': '#e65100'}.get(status, '#333')


def _change_border_color(new_status):
    """Return border color: green for improvements, red for regressions."""
    return '#4caf50' if new_status == 'PASS' else '#e53935'


def _change_bg_color(new_status):
    """Return background color for a status change row."""
    return '#e8f5e9' if new_status == 'PASS' else '#ffebee'


def generate_executive_summary(report_path):
    """Read the general report CSV and generate an HTML executive summary,
    highlighting changes compared to the previous report."""
    import html as html_mod

    # Load all rows from the report
    rows = []
    with open(report_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("No data in report for executive summary.")
        return

    # Load previous report for comparison
    prev_report_path = find_previous_report(report_path)
    prev_data = load_report_as_dict(prev_report_path) if prev_report_path else {}

    # Compute stats
    total = len(rows)
    total_pass = sum(1 for r in rows if r['status'] == 'PASS')
    total_fail = sum(1 for r in rows if r['status'] == 'FAIL')
    total_missing = sum(1 for r in rows if r['status'] == 'MISSING')
    pass_rate = (total_pass / total * 100) if total else 0

    report_date = _report_date_str(report_path)
    prev_date = _report_date_str(prev_report_path) if prev_report_path else None
    generated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Collect status changes
    owner_changes = {}
    if prev_data:
        current_data = {}
        for row in rows:
            key = (row['partition'], row['test_type'])
            current_data[key] = row
        for key, cur_row in current_data.items():
            prev_row = prev_data.get(key)
            if prev_row and prev_row['status'] != cur_row['status']:
                owner = cur_row.get('owner', 'UNKNOWN')
                if owner not in owner_changes:
                    owner_changes[owner] = []
                owner_changes[owner].append((cur_row, prev_row['status']))

    total_changes = sum(len(v) for v in owner_changes.values())

    # ── Build HTML (Outlook-compatible Corporate Clean) ──
    # Outlook uses Word's rendering engine, so we must:
    #   - Use tables instead of divs for layout
    #   - Use bgcolor attribute (not just CSS background-color)
    #   - Put font styles on every <td> (no CSS inheritance)
    #   - Avoid border-radius, box-shadow, letter-spacing
    #   - Use mso-line-height-rule for line heights
    FONT = "font-family:Arial,Helvetica,sans-serif;"
    MONO = "font-family:Consolas,'Courier New',monospace;"

    h = []
    h.append('<!DOCTYPE html>')
    h.append('<html xmlns="http://www.w3.org/1999/xhtml">')
    h.append('<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>')
    h.append(f'<body style="margin:0;padding:0;background-color:#f4f4f4;{FONT}">')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f4">')
    h.append('<tr><td align="center" style="padding:20px 0;">')
    h.append('<table width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
             'style="border:1px solid #dddddd;">')

    # ── Header banner ──
    h.append('<tr><td bgcolor="#0071c5" style="padding:24px 32px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="color:#ffffff;font-size:22px;font-weight:bold;{FONT}">EXECUTIVE SUMMARY</span><br>')
    h.append(f'<span style="color:#b3d9f2;font-size:14px;{FONT}">SCAN L2 Regression Report</span>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # ── Metadata ──
    h.append('<tr><td style="padding:20px 32px 16px;border-bottom:1px solid #e0e0e0;">')
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0">')
    report_html_name = os.path.basename(report_path).replace('.csv', '.html')
    report_html_url = GITHUB_PAGES_BASE + report_html_name
    h.append(f'<tr><td style="font-size:14px;color:#444;{FONT}padding:3px 0;">'
             f'<b>Report:</b> <a href="{report_html_url}" style="color:#0071c5;text-decoration:none;">{html_mod.escape(report_html_name)}</a></td>')
    h.append(f'<td align="right" style="font-size:13px;color:#888;{FONT}padding:3px 0;">{html_mod.escape(report_date)}</td></tr>')
    if prev_report_path:
        prev_html_name = os.path.basename(prev_report_path).replace('.csv', '.html')
        prev_html_url = GITHUB_PAGES_BASE + prev_html_name
        h.append(f'<tr><td style="font-size:14px;color:#444;{FONT}padding:3px 0;">'
                 f'<b>Compared to:</b> <a href="{prev_html_url}" style="color:#0071c5;text-decoration:none;">{html_mod.escape(prev_html_name)}</a></td>')
        h.append(f'<td align="right" style="font-size:13px;color:#888;{FONT}padding:3px 0;">{html_mod.escape(prev_date)}</td></tr>')
    h.append(f'<tr><td colspan="2" style="font-size:12px;color:#999;{FONT}padding:8px 0 0;">'
             f'Generated: {html_mod.escape(generated)}</td></tr>')
    h.append('</table></td></tr>')

    # ── Selected Models ──
    models_used = sorted(set(r['model'] for r in rows))
    prev_models = sorted(set(r['model'] for r in prev_data.values())) if prev_data else []
    new_models = set(models_used) - set(prev_models)
    h.append('<tr><td style="padding:16px 32px 8px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:14px;font-weight:bold;color:#333;{FONT}">SELECTED MODELS</span>')
    h.append('</td></tr></table>')
    h.append('<table cellpadding="0" cellspacing="0" border="0">')
    for model in models_used:
        # Determine category label
        if 'nio_mc' in model:
            cat_label, cat_color = 'MC', '#0071c5'
        elif 'nio_uio' in model:
            cat_label, cat_color = 'UIO', '#6a1b9a'
        elif 'nio_d2d' in model:
            cat_label, cat_color = 'D2D', '#00695c'
        else:
            cat_label, cat_color = '?', '#555'
        is_new = model in new_models
        h.append(f'<tr><td style="padding:4px 0;{FONT}">')
        h.append(f'<span style="display:inline-block;background-color:{cat_color};color:#ffffff;font-size:11px;'
                 f'font-weight:bold;padding:2px 8px;{FONT}">{cat_label}</span> ')
        h.append(f'<span style="font-size:13px;color:#333;{MONO}">{html_mod.escape(model)}</span>')
        if is_new:
            h.append(f' <span style="display:inline-block;background-color:#ff6f00;color:#ffffff;font-size:10px;'
                     f'font-weight:bold;padding:1px 6px;{FONT}">NEW</span>')
        h.append('</td></tr>')
    h.append('</table>')
    h.append('</td></tr>')

    # ── Overall stats cards ──
    h.append('<tr><td style="padding:24px 32px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;text-transform:uppercase;{FONT}">OVERALL</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;"><tr>')

    stats = [
        (str(total), 'TOTAL', '#f0f7ff', '#0071c5'),
        (str(total_pass), 'PASS', '#e8f5e9', '#2e7d32'),
        (str(total_fail), 'FAIL', '#ffebee', '#c62828'),
        (str(total_missing), 'MISSING', '#fff3e0', '#e65100'),
    ]
    for i, (val, label, bg, fg) in enumerate(stats):
        if i > 0:
            h.append('<td width="8"></td>')
        w = '25%' if i == 0 else '22%'
        h.append(f'<td width="{w}" align="center" bgcolor="{bg}" style="padding:14px 8px;">')
        h.append(f'<span style="font-size:30px;font-weight:bold;color:{fg};{FONT}">{val}</span><br>')
        h.append(f'<span style="font-size:11px;color:#666;text-transform:uppercase;{FONT}">{label}</span>')
        h.append('</td>')
    h.append('</tr></table>')

    # Pass rate
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">')
    h.append(f'<tr><td align="center" style="font-size:14px;color:#555;{FONT}">')
    h.append(f'Pass rate: <b style="color:#0071c5;font-size:20px;">{pass_rate:.1f}%</b>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # ── Status Changes ──
    h.append('<tr><td style="padding:8px 32px 24px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;"><tr>'
             f'<td bgcolor="#e3f2fd" style="padding:8px 12px;border-left:4px solid #0071c5;{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#0071c5;text-transform:uppercase;{FONT}">STATUS CHANGES</span>')
    if owner_changes:
        h.append(f' <span style="font-size:13px;color:#888;{FONT}">({total_changes} vs previous report)</span>')
    h.append('</td></tr></table>')

    if not owner_changes:
        if prev_data:
            h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
                     f'<td style="font-size:14px;color:#555;{FONT}padding:4px 0;">No changes vs previous report.</td>'
                     f'</tr></table>')
        else:
            h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
                     f'<td style="font-size:14px;color:#555;{FONT}padding:4px 0;">No previous report available for comparison.</td>'
                     f'</tr></table>')

    if owner_changes:
        for owner in sorted(owner_changes.keys()):
            changes = owner_changes[owner]
            changes.sort(key=lambda x: (x[0]['model'], x[0]['partition'], x[0]['test_type']))

            # Owner heading
            h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px;margin-top:12px;"><tr><td style="{FONT}">')
            h.append(f'<span style="font-size:14px;font-weight:bold;color:#333;{FONT}">{html_mod.escape(owner)}</span> ')
            h.append(f'<span style="font-size:13px;color:#888;{FONT}">({len(changes)} change{"s" if len(changes) != 1 else ""})</span>')
            h.append('</td></tr></table>')

            # Change rows as a table
            h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0">')
            for r, prev_status in changes:
                new_status = r['status']
                border_color = _change_border_color(new_status)
                bg = _change_bg_color(new_status)
                partition = html_mod.escape(r['partition'])
                test_type = html_mod.escape(r['test_type'])
                model = html_mod.escape(r['model'])
                h.append(f'<tr><td bgcolor="{bg}" style="padding:9px 12px;border-left:4px solid {border_color};'
                         f'font-size:13px;color:#333;{MONO}mso-line-height-rule:exactly;line-height:20px;">')
                h.append(f'[{model}] {partition} / {test_type}: '
                         f'<span style="color:{_status_color(prev_status)};{MONO}">{prev_status}</span> '
                         f'&#8594; '
                         f'<b style="color:{_status_color(new_status)};{MONO}">{new_status}</b>')
                h.append('</td></tr>')
                h.append('<tr><td style="font-size:0;line-height:0;height:4px;">&nbsp;</td></tr>')
            h.append('</table>')

    h.append('</td></tr>')

    # ── Current Failing & Missing Tests ──
    fail_rows = [r for r in rows if r['status'] == 'FAIL']
    missing_rows = [r for r in rows if r['status'] == 'MISSING']
    if fail_rows or missing_rows:
        h.append('<tr><td style="padding:8px 32px 24px;">')
        h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;"><tr>'
                 f'<td bgcolor="#fff3e0" style="padding:8px 12px;border-left:4px solid #e65100;{FONT}">')
        h.append(f'<span style="font-size:16px;font-weight:bold;color:#e65100;text-transform:uppercase;{FONT}">'
                 f'CURRENT FAILING &amp; MISSING TESTS</span> ')
        h.append(f'<span style="font-size:13px;color:#888;{FONT}">'
                 f'({len(fail_rows)} FAIL, {len(missing_rows)} MISSING)</span>')
        h.append('</td></tr></table>')

        # FAIL tests grouped by owner
        if fail_rows:
            fail_by_owner = {}
            for r in fail_rows:
                owner = r.get('owner', 'UNKNOWN')
                fail_by_owner.setdefault(owner, []).append(r)
            h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tr><td style="{FONT}">')
            h.append(f'<span style="font-size:14px;font-weight:bold;color:#c62828;{FONT}">FAIL ({len(fail_rows)})</span>')
            h.append('</td></tr></table>')
            h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0">')
            for owner in sorted(fail_by_owner.keys()):
                tests = sorted(fail_by_owner[owner], key=lambda x: (x['model'], x['partition'], x['test_type']))
                h.append(f'<tr><td style="padding:6px 0 2px;font-size:13px;font-weight:bold;color:#333;{FONT}">'
                         f'{html_mod.escape(owner)} ({len(tests)})</td></tr>')
                for r in tests:
                    partition = html_mod.escape(r['partition'])
                    test_type = html_mod.escape(r['test_type'])
                    model = html_mod.escape(r['model'])
                    h.append(f'<tr><td bgcolor="#ffebee" style="padding:6px 12px;border-left:4px solid #c62828;'
                             f'font-size:12px;color:#333;{MONO}mso-line-height-rule:exactly;line-height:18px;">')
                    h.append(f'[{model}] {partition} / {test_type}')
                    h.append('</td></tr>')
                    h.append('<tr><td style="font-size:0;line-height:0;height:3px;">&nbsp;</td></tr>')
            h.append('</table>')

        # MISSING tests grouped by owner
        if missing_rows:
            missing_by_owner = {}
            for r in missing_rows:
                owner = r.get('owner', 'UNKNOWN')
                missing_by_owner.setdefault(owner, []).append(r)
            h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;margin-bottom:8px;"><tr><td style="{FONT}">')
            h.append(f'<span style="font-size:14px;font-weight:bold;color:#e65100;{FONT}">MISSING ({len(missing_rows)})</span>')
            h.append('</td></tr></table>')
            h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0">')
            for owner in sorted(missing_by_owner.keys()):
                tests = sorted(missing_by_owner[owner], key=lambda x: (x['model'], x['partition'], x['test_type']))
                h.append(f'<tr><td style="padding:6px 0 2px;font-size:13px;font-weight:bold;color:#333;{FONT}">'
                         f'{html_mod.escape(owner)} ({len(tests)})</td></tr>')
                for r in tests:
                    partition = html_mod.escape(r['partition'])
                    test_type = html_mod.escape(r['test_type'])
                    model = html_mod.escape(r['model'])
                    h.append(f'<tr><td bgcolor="#fff3e0" style="padding:6px 12px;border-left:4px solid #e65100;'
                             f'font-size:12px;color:#333;{MONO}mso-line-height-rule:exactly;line-height:18px;">')
                    h.append(f'[{model}] {partition} / {test_type}')
                    h.append('</td></tr>')
                    h.append('<tr><td style="font-size:0;line-height:0;height:3px;">&nbsp;</td></tr>')
            h.append('</table>')

        h.append('</td></tr>')

    # ── Footer ──
    h.append('<tr><td bgcolor="#f8f8f8" style="padding:16px 32px;border-top:1px solid #e0e0e0;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:11px;color:#999;{FONT}mso-line-height-rule:exactly;line-height:18px;">')
    h.append('This is an automated report generated by AI.<br>')
    h.append('Thanks for reading.<br>')
    h.append(f'<a href="{GITHUB_PAGES_INDEX}" style="color:#0071c5;text-decoration:none;">&#128202; View all report history</a><br><br>')
    h.append('For changes contact: <a href="mailto:emmanuel.a.araya.gamboa@intel.com" '
             'style="color:#0071c5;text-decoration:none;">Emmanuel Araya</a>')
    h.append('</span>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    h.append('</table></td></tr></table></body></html>')

    html_text = "\n".join(h)

    # ── Plain-text console output ──
    lines = []
    lines.append("")
    lines.append("         EXECUTIVE SUMMARY - SCAN L2 REGRESSION")
    lines.append("")
    lines.append(f"Report: {os.path.basename(report_path)} ({report_date})")
    if prev_report_path:
        lines.append(f"Compared to: {os.path.basename(prev_report_path)} ({prev_date})")
    lines.append(f"Generated: {generated}")
    lines.append("")
    lines.append(f"  Models: {', '.join(models_used)}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("  OVERALL")
    lines.append("=" * 60)
    lines.append(f"  Total: {total} tests | {total_pass} PASS | {total_fail} FAIL | {total_missing} MISSING | {pass_rate:.1f}% pass rate")
    if owner_changes:
        lines.append(f"")
        lines.append(f"  Status changes vs previous report: {total_changes}")
        for owner in sorted(owner_changes.keys()):
            changes = owner_changes[owner]
            lines.append(f"    {owner}: {len(changes)} change(s)")
            changes.sort(key=lambda x: (x[0]['model'], x[0]['partition'], x[0]['test_type']))
            for r, prev_status in changes:
                lines.append(f"      [{r['model']}] {r['partition']} / {r['test_type']}: {prev_status} -> {r['status']}")
    elif prev_data:
        lines.append(f"")
        lines.append(f"  No status changes compared to previous report for any owner.")
    else:
        lines.append(f"")
        lines.append(f"  No previous report available for comparison.")
    lines.append("")
    lines.append("=" * 60)

    # Failing & Missing tests in console output
    if fail_rows or missing_rows:
        lines.append("")
        lines.append("  CURRENT FAILING & MISSING TESTS")
        lines.append("=" * 60)
        if fail_rows:
            lines.append(f"  FAIL ({len(fail_rows)}):")
            for r in sorted(fail_rows, key=lambda x: (x['owner'], x['model'], x['partition'], x['test_type'])):
                lines.append(f"    [{r['model']}] {r['partition']} / {r['test_type']} ({r.get('owner','UNKNOWN')})")
        if missing_rows:
            lines.append(f"  MISSING ({len(missing_rows)}):")
            for r in sorted(missing_rows, key=lambda x: (x['owner'], x['model'], x['partition'], x['test_type'])):
                lines.append(f"    [{r['model']}] {r['partition']} / {r['test_type']} ({r.get('owner','UNKNOWN')})")
        lines.append("")
        lines.append("=" * 60)

    print("\n" + "\n".join(lines))

    # Save HTML file
    summary_file = os.path.join(REPORTS_DIR, f"executive_summary_{TIMESTAMP}.html")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(html_text)
    print(f"\nExecutive summary saved to: {summary_file}")


def generate_index_html():
    """Generate an index.html in reports/ that links to all general report HTML files."""
    import html as html_mod
    import re

    FONT = "font-family:Arial,Helvetica,sans-serif;"

    # Discover all general report HTML files
    html_reports = sorted(glob.glob(os.path.join(REPORTS_DIR, "general_report_*.html")), reverse=True)

    if not html_reports:
        print("No HTML general reports found. Skipping index generation.")
        return

    # For each HTML report, try to read stats from the companion CSV
    entries = []
    for html_path in html_reports:
        basename = os.path.basename(html_path)
        csv_path = html_path.replace('.html', '.csv')

        # Extract timestamp from filename
        m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', basename)
        if m:
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            time_str = f"{m.group(4)}:{m.group(5)}:{m.group(6)}"
            ww_raw = basename  # e.g. general_report_20260409_144523.html
        else:
            date_str = "Unknown"
            time_str = ""

        # Try to get stats from CSV
        total = p = f = mi = 0
        models = set()
        if os.path.isfile(csv_path):
            try:
                with open(csv_path, 'r', encoding='utf-8') as cf:
                    reader = csv.DictReader(cf)
                    for row in reader:
                        total += 1
                        if row['status'] == 'PASS':
                            p += 1
                        elif row['status'] == 'FAIL':
                            f += 1
                        else:
                            mi += 1
                        models.add(row['model'])
            except Exception:
                pass

        rate = (p / total * 100) if total else 0

        # Find matching executive summary
        summary_name = basename.replace('general_report_', 'executive_summary_')
        summary_path = os.path.join(REPORTS_DIR, summary_name)
        has_summary = os.path.isfile(summary_path)

        entries.append({
            'basename': basename,
            'date': date_str,
            'time': time_str,
            'total': total,
            'pass': p,
            'fail': f,
            'missing': mi,
            'rate': rate,
            'models': sorted(models),
            'has_summary': has_summary,
            'summary_name': summary_name if has_summary else None,
        })

    # Build HTML
    h = []
    h.append('<!DOCTYPE html>')
    h.append('<html xmlns="http://www.w3.org/1999/xhtml">')
    h.append('<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">')
    h.append('<title>SCAN L2 Regression - Report History</title>')
    h.append('</head>')
    h.append(f'<body style="margin:0;padding:0;background-color:#f4f4f4;{FONT}">')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f4">')
    h.append('<tr><td align="center" style="padding:20px 0;">')
    h.append('<table width="780" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
             'style="border:1px solid #dddddd;">')

    # Header
    h.append('<tr><td bgcolor="#0071c5" style="padding:24px 32px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="color:#ffffff;font-size:22px;font-weight:bold;{FONT}">REPORT HISTORY</span><br>')
    h.append(f'<span style="color:#b3d9f2;font-size:14px;{FONT}">SCAN L2 Regression &mdash; {len(entries)} report(s)</span>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    # Report list
    h.append('<tr><td style="padding:24px 32px;">')

    # Table header
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0">')
    h.append(f'<tr bgcolor="#0071c5">')
    for col in ['Date', 'Total', 'Pass', 'Fail', 'Missing', 'Rate', 'Models', 'Links']:
        align = 'left' if col in ('Date', 'Models', 'Links') else 'center'
        h.append(f'<td align="{align}" style="padding:10px 10px;font-size:12px;font-weight:bold;color:#ffffff;{FONT}">{col}</td>')
    h.append('</tr>')

    for i, e in enumerate(entries):
        bg = '#f9f9f9' if i % 2 == 0 else '#ffffff'
        # Highlight latest report
        if i == 0:
            bg = '#e3f2fd'

        models_str = ', '.join(e['models']) if e['models'] else '—'
        # Truncate model names to just the short version
        short_models = [m.replace('nio_', '') for m in e['models']] if e['models'] else ['—']

        h.append(f'<tr bgcolor="{bg}">')
        # Date
        h.append(f'<td style="padding:10px;font-size:13px;font-weight:bold;color:#333;{FONT}white-space:nowrap;">')
        h.append(f'{html_mod.escape(e["date"])}<br>')
        h.append(f'<span style="font-size:11px;color:#888;font-weight:normal;">{html_mod.escape(e["time"])}</span>')
        h.append('</td>')
        # Stats
        h.append(f'<td align="center" style="padding:10px;font-size:14px;color:#333;{FONT}">{e["total"]}</td>')
        h.append(f'<td align="center" style="padding:10px;font-size:14px;color:#2e7d32;font-weight:bold;{FONT}">{e["pass"]}</td>')
        h.append(f'<td align="center" style="padding:10px;font-size:14px;color:#c62828;font-weight:bold;{FONT}">{e["fail"]}</td>')
        h.append(f'<td align="center" style="padding:10px;font-size:14px;color:#e65100;font-weight:bold;{FONT}">{e["missing"]}</td>')
        # Rate with color
        rate_color = '#2e7d32' if e['rate'] >= 80 else '#e65100' if e['rate'] >= 50 else '#c62828'
        h.append(f'<td align="center" style="padding:10px;font-size:14px;font-weight:bold;color:{rate_color};{FONT}">{e["rate"]:.1f}%</td>')
        # Models
        h.append(f'<td style="padding:10px;font-size:11px;color:#555;{FONT}">')
        h.append('<br>'.join(html_mod.escape(m) for m in short_models))
        h.append('</td>')
        # Links
        h.append(f'<td style="padding:10px;font-size:12px;{FONT}white-space:nowrap;">')
        h.append(f'<a href="{html_mod.escape(e["basename"])}" style="color:#0071c5;text-decoration:none;font-weight:bold;">Report</a>')
        if e['has_summary']:
            h.append(f' &middot; <a href="{html_mod.escape(e["summary_name"])}" style="color:#0071c5;text-decoration:none;">Summary</a>')
        h.append('</td>')
        h.append('</tr>')

    h.append('</table>')
    h.append('</td></tr>')

    # Footer
    h.append('<tr><td bgcolor="#f8f8f8" style="padding:16px 32px;border-top:1px solid #e0e0e0;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:11px;color:#999;{FONT}">')
    h.append(f'Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>')
    h.append('Generated by NIO L2 Regression Agent')
    h.append('</span></td></tr></table>')
    h.append('</td></tr>')

    h.append('</table></td></tr></table></body></html>')

    index_path = os.path.join(REPORTS_DIR, "index.html")
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(h))
    print(f"Index page saved to: {index_path}")


def git_commit_and_push():
    """Stage reports/ and weekly_report/, commit, and push to GitHub."""
    import subprocess

    print("\n--- Git Commit & Push ---")
    try:
        subprocess.run(["git", "add", "reports/", "weekly_report/"], cwd=ROOT_DIR, check=True)
        # Check if there are staged changes
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT_DIR)
        if result.returncode == 0:
            print("No new changes to commit.")
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"L2 regression reports update {timestamp}"],
            cwd=ROOT_DIR, check=True
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=ROOT_DIR, check=True)
        print("Reports pushed to GitHub successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}")
        print("You may need to push manually.")


def main():
    # Load report file timestamps (populated by parse_l2_regression.py)
    timestamps = load_report_timestamps()

    # Step 1: Let user select one model per category from local CSVs
    categories = ['nio_mc', 'nio_uio', 'nio_d2d']
    print("\n--- Model Selection ---")
    selected = []
    for cat in categories:
        model = prompt_model_selection(cat, timestamps=timestamps)
        if model:
            selected.append(model)

    if not selected:
        print("\nNo models selected. Exiting.")
        sys.exit(0)

    print(f"\nSelected models: {', '.join(selected)}")

    # Step 2: Generate consolidated general report for selected models only
    generate_general_report_for_models(selected)

    # Step 3: Generate executive summary per owner
    if os.path.isfile(OUTPUT_REPORT):
        generate_executive_summary(OUTPUT_REPORT)

    # Step 4: Regenerate index page with all reports
    generate_index_html()

    # Step 5: Commit and push reports to GitHub
    git_commit_and_push()


if __name__ == "__main__":
    main()
