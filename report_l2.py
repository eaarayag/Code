# report_l2.py
# Downloads all CSV files from the remote host to the local weekly_report folder,
# then generates a consolidated report CSV with owner, test_case, status, and model.
# Usage: python report_l2.py
# Requires: SSH/SCP access to sccc06381314.zsc24.intel.com as eaarayag

import csv
import glob
import os
import subprocess
import sys
from datetime import datetime

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))       # Directory where this script lives
OWNERSHIP_FILE = os.path.join(SCRIPT_DIR, "ownership.txt")    # Maps test prefixes to owners
WEEKLY_REPORT_DIR = os.path.join(SCRIPT_DIR, "weekly_report") # Folder with per-model regression CSVs
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")          # Timestamp for output filename
OUTPUT_REPORT = os.path.join(SCRIPT_DIR, f"general_report_{TIMESTAMP}.csv")  # Final consolidated report


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


def clear_weekly_report():
    """Delete all files in the weekly_report folder before downloading new ones."""
    if not os.path.isdir(WEEKLY_REPORT_DIR):
        os.makedirs(WEEKLY_REPORT_DIR, exist_ok=True)
        return
    removed = 0
    for f in os.listdir(WEEKLY_REPORT_DIR):
        filepath = os.path.join(WEEKLY_REPORT_DIR, f)
        if os.path.isfile(filepath):
            os.remove(filepath)
            removed += 1
    print(f"Cleared {removed} file(s) from weekly_report/.")


def download_csv_files():
    """Download all CSV files from the remote host to the local weekly_report folder."""
    # Clear old files before downloading
    clear_weekly_report()

    host = "sccc06381314.zsc24.intel.com"
    user = "eaarayag"
    # Remote directory containing the CSV regression results
    remote_path = "/nfs/site/disks/nwp_dft_fe_002/eaarayag/scripts/*.csv"
    # Local destination folder for downloaded CSV files
    local_path = r"C:\Users\eaarayag\OneDrive - Intel Corporation\Documents\L2_Regressions\weekly_report"

    # Build the SCP command to copy all CSV files from remote to local
    scp_command = [
        "scp",
        f"{user}@{host}:{remote_path}",
        local_path,
    ]

    print(f"Downloading CSV files from {user}@{host}:{remote_path}")
    print(f"Destination: {local_path}\n")

    # Execute the SCP command
    result = subprocess.run(scp_command)

    # Report success or failure
    if result.returncode == 0:
        print("\nCSV files downloaded successfully.")
    else:
        print(f"\nSCP failed with exit code {result.returncode}.")
        sys.exit(result.returncode)


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


def generate_executive_summary(report_path):
    """Read the general report CSV and print/save an executive summary grouped by owner."""
    # Load all rows from the report
    rows = []
    with open(report_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("No data in report for executive summary.")
        return

    # Group rows by owner
    owners = {}
    for row in rows:
        owner = row['owner']
        if owner not in owners:
            owners[owner] = []
        owners[owner].append(row)

    # Build summary text
    lines = []
    lines.append("=" * 60)
    lines.append("         EXECUTIVE SUMMARY - L2 REGRESSION")
    lines.append("=" * 60)
    lines.append(f"Report: {os.path.basename(report_path)}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Overall stats
    total = len(rows)
    total_pass = sum(1 for r in rows if r['status'] == 'PASS')
    total_fail = sum(1 for r in rows if r['status'] == 'FAIL')
    total_missing = sum(1 for r in rows if r['status'] == 'MISSING')
    pass_rate = (total_pass / total * 100) if total else 0
    lines.append("--- Overall ---")
    lines.append(f"  Total: {total} tests | {total_pass} PASS | {total_fail} FAIL | {total_missing} MISSING | {pass_rate:.1f}% pass rate")
    lines.append("")

    # Per-owner breakdown
    for owner in sorted(owners.keys()):
        owner_rows = owners[owner]
        o_total = len(owner_rows)
        o_pass = sum(1 for r in owner_rows if r['status'] == 'PASS')
        o_fail = sum(1 for r in owner_rows if r['status'] == 'FAIL')
        o_missing = sum(1 for r in owner_rows if r['status'] == 'MISSING')
        o_rate = (o_pass / o_total * 100) if o_total else 0

        lines.append("=" * 60)
        lines.append(f"  OWNER: {owner}")
        lines.append("=" * 60)
        lines.append(f"  Total: {o_total} tests | {o_pass} PASS | {o_fail} FAIL | {o_missing} MISSING | {o_rate:.1f}% pass rate")

        # List failing tests grouped by model
        failing = [r for r in owner_rows if r['status'] == 'FAIL']
        if failing:
            lines.append(f"")
            lines.append(f"  Failing tests ({len(failing)}):")
            failing.sort(key=lambda r: (r['model'], r['partition'], r['test_type']))
            for r in failing:
                lines.append(f"    [{r['model']}] {r['partition']} / {r['test_type']}")

        # List missing tests grouped by model
        missing = [r for r in owner_rows if r['status'] == 'MISSING']
        if missing:
            lines.append(f"")
            lines.append(f"  Missing tests ({len(missing)}):")
            missing.sort(key=lambda r: (r['model'], r['partition'], r['test_type']))
            for r in missing:
                lines.append(f"    [{r['model']}] {r['partition']} / {r['test_type']}")

        if not failing and not missing:
            lines.append(f"  All tests PASSING and complete.")

        lines.append("")

    lines.append("=" * 60)

    summary_text = "\n".join(lines)

    # Print to console
    print("\n" + summary_text)

    # Save to file
    summary_file = os.path.join(SCRIPT_DIR, f"executive_summary_{TIMESTAMP}.txt")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(summary_text)
    print(f"\nExecutive summary saved to: {summary_file}")


def main():
    # Step 1: Download CSV files from remote host
    download_csv_files()

    # Step 2: Let user select one model per category
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


if __name__ == "__main__":
    main()
