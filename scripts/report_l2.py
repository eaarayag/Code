# report_l2.py
# Reads CSV files from the local weekly_report folder and generates a
# consolidated report CSV with owner, test_case, status, and model.
# Usage: python report_l2.py

import csv
import glob
import os
import sys
from datetime import datetime

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))       # Directory where this script lives
ROOT_DIR = os.path.dirname(SCRIPT_DIR)                        # Project root (parent of scripts/)
OWNERSHIP_FILE = os.path.join(SCRIPT_DIR, "ownership.txt")    # Maps test prefixes to owners
WEEKLY_REPORT_DIR = os.path.join(ROOT_DIR, "weekly_report")   # Folder with per-model regression CSVs
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")                # Folder for generated reports
os.makedirs(REPORTS_DIR, exist_ok=True)
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")          # Timestamp for output filename
OUTPUT_REPORT = os.path.join(REPORTS_DIR, f"general_report_{TIMESTAMP}.csv")  # Final consolidated report


def load_ownership(filepath):
    """Load ownership.txt and return a list of (owner, prefix) tuples, sorted longest prefix first."""
    ownership = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Parse each line as "owner,prefix" format
            owner, prefix = line.split(',', 1)
            ownership.append((owner.strip(), prefix.strip()))
    # Sort by prefix length descending so longer prefixes match first
    ownership.sort(key=lambda x: len(x[1]), reverse=True)
    return ownership


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


def prompt_model_selection(category):
    """Show available models for a category and let the user pick one (or skip)."""
    models = list_available_models(category)
    if not models:
        print(f"  No models found for '{category}'. Skipping.")
        return None

    print(f"\n  Available {category.upper()} models:")
    for i, model in enumerate(models, 1):
        print(f"    {i}. {model}")
    print(f"    0. Skip {category.upper()}")

    while True:
        choice = input(f"  Select {category.upper()} model [1-{len(models)}, 0 to skip]: ").strip()
        if choice == '0':
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        print(f"  Invalid choice. Enter 0-{len(models)}.")


# Expected test cases for every partition. '*' prefix means suffix match.
EXPECTED_TESTS = [
    'atspeed_edt_bypass_low_internal_serial_scan',
    'atspeed_edt_edt_low_internal_serial_scan',
    'ijtag_basic_tap_tests_rw_access',
    '*scan_ctlr_stuckat_edt_bypass_low_internal_scandump',
    'stuckat_edt_bypass_low_internal_burnin_togcnt_cap_off',
    'stuckat_edt_bypass_low_internal_serial_chain',
    'stuckat_edt_bypass_low_internal_serial_scan',
    'stuckat_edt_edt_low_internal_serial_chain',
    'stuckat_edt_edt_low_internal_serial_scan',
]


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


def check_test_completeness(all_rows, ownership, selected_models):
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
                    missing_rows.append({
                        'owner': owner,
                        'partition': partition,
                        'test_type': suffix,
                        'status': 'MISSING',
                        'model': model,
                    })
            else:
                # Exact match
                if expected not in test_types:
                    missing_rows.append({
                        'owner': owner,
                        'partition': partition,
                        'test_type': expected,
                        'status': 'MISSING',
                        'model': model,
                    })

    return missing_rows


def generate_general_report_for_models(selected_models):
    """Read CSV files for the selected models and produce a consolidated general_report.csv."""
    ownership = load_ownership(OWNERSHIP_FILE)

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
                all_rows.append({
                    'owner': owner,
                    'partition': partition,
                    'test_type': test_type,
                    'status': row['test_status'],
                    'model': model,
                })

    if not all_rows:
        print("No test entries found for selected models.")
        return

    # Check for missing expected tests and add MISSING rows (checks ALL ownership partitions)
    missing_rows = check_test_completeness(all_rows, ownership, selected_models)
    if missing_rows:
        print(f"\nFound {len(missing_rows)} missing test(s) across partitions.")
        all_rows.extend(missing_rows)

    all_rows.sort(key=lambda r: (r['owner'], r['model'], r['partition'], r['test_type']))

    with open(OUTPUT_REPORT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['partition', 'test_type', 'status', 'owner', 'model'])
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nGeneral report generated: {OUTPUT_REPORT}")
    print(f"Total test entries: {len(all_rows)} ({len(missing_rows)} MISSING)")

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
    h.append('<table width="780" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
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
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;{MONO}border-bottom:1px solid #eee;">{html_mod.escape(r["partition"])}</td>')
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;{MONO}border-bottom:1px solid #eee;">{html_mod.escape(r["test_type"])}</td>')
            h.append(f'<td align="center" bgcolor="{status_bg(st)}" style="padding:6px 10px;font-size:12px;font-weight:bold;color:{status_fg(st)};{FONT}border-bottom:1px solid #eee;">{st}</td>')
            h.append(f'<td style="padding:6px 10px;font-size:12px;color:#555;{FONT}border-bottom:1px solid #eee;">{html_mod.escape(r["owner"])}</td>')
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
    h.append(f'<tr><td style="font-size:14px;color:#444;{FONT}padding:3px 0;">'
             f'<b>Report:</b> {html_mod.escape(os.path.basename(report_path))}</td>')
    h.append(f'<td align="right" style="font-size:13px;color:#888;{FONT}padding:3px 0;">{html_mod.escape(report_date)}</td></tr>')
    if prev_report_path:
        h.append(f'<tr><td style="font-size:14px;color:#444;{FONT}padding:3px 0;">'
                 f'<b>Compared to:</b> {html_mod.escape(os.path.basename(prev_report_path))}</td>')
        h.append(f'<td align="right" style="font-size:13px;color:#888;{FONT}padding:3px 0;">{html_mod.escape(prev_date)}</td></tr>')
    h.append(f'<tr><td colspan="2" style="font-size:12px;color:#999;{FONT}padding:8px 0 0;">'
             f'Generated: {html_mod.escape(generated)}</td></tr>')
    h.append('</table></td></tr>')

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
    if owner_changes:
        h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;"><tr><td style="{FONT}">')
        h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;text-transform:uppercase;{FONT}">STATUS CHANGES</span> ')
        h.append(f'<span style="font-size:13px;color:#888;{FONT}">({total_changes} vs previous report)</span>')
        h.append('</td></tr></table>')

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

    elif prev_data:
        h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
                 f'<td style="font-size:14px;color:#555;{FONT}">No status changes compared to previous report.</td>'
                 f'</tr></table>')
    else:
        h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
                 f'<td style="font-size:14px;color:#555;{FONT}">No previous report available for comparison.</td>'
                 f'</tr></table>')
    h.append('</td></tr>')

    # ── Footer ──
    h.append('<tr><td bgcolor="#f8f8f8" style="padding:16px 32px;border-top:1px solid #e0e0e0;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:11px;color:#999;{FONT}mso-line-height-rule:exactly;line-height:18px;">')
    h.append('This is an automated report generated by AI.<br>')
    h.append('Thanks for reading.<br>')
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


def main():
    # Step 1: Let user select one model per category
    categories = ['nio_mc', 'nio_uio', 'nio_d2d']
    print("\n--- Model Selection ---")
    selected = []
    for cat in categories:
        model = prompt_model_selection(cat)
        if model:
            selected.append(model)

    if not selected:
        print("\nNo models selected. Exiting.")
        sys.exit(0)

    print(f"\nSelected models: {', '.join(selected)}")

    # Step 3: Generate consolidated general report for selected models only
    generate_general_report_for_models(selected)

    # Step 4: Generate executive summary per owner
    if os.path.isfile(OUTPUT_REPORT):
        generate_executive_summary(OUTPUT_REPORT)

    # Step 5: Regenerate index page with all reports
    generate_index_html()


if __name__ == "__main__":
    main()
