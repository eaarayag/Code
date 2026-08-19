# Standard library imports for CSV writing, regex parsing, CLI args, and path handling
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- Remote SSH Configuration ---
REMOTE_HOST = "sccc06381314.zsc24.intel.com"
REMOTE_USER = "mnavarro"
REMOTE_WORK_DIR = "/nfs/site/disks/nwp_dft_fe_009/mnavarro/REPORTS/test_tap"

# --- Regression Storage Paths (searched in order) ---
BASE_PATHS = [
    "/nfs/site/disks/nwp_vmgr_testresults_006/NWP_DFT_Regressions",
    "/nfs/site/disks/nwp_vmgr_testresults_016/NWP_DFT_Regressions",
]

# --- Stack-level TAP report paths (relative to <base>/<model>/) ---
# nio_d2d intentionally excluded — no stacklevel rpt for d2d.
STACKLEVEL_SUBPATHS = {
    'nio_uio': 'uio/uio/uio_a_0_dft_stacklevel_L2.list.latest/uio_a_0_dft_stacklevel_L2.rpt',
    'nio_mc':  'memstack/memstack/L2_stacklevel_regression.list.latest/L2_stacklevel_regression.rpt',
}

# --- TAP stacklevel filter: only keep tests whose test_name contains one of these substrings (case-insensitive) ---
TAP_STACKLEVEL_KEYWORDS = ('rw', 'reset', 'continuity')

# --- Local Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WEEKLY_REPORT_DIR = os.path.join(ROOT_DIR, "weekly_report")

def parse_l2_regression_report(file_path, output_csv='regression_results.csv', filter_keywords=None):
    """
    Parse L2_regression.rpt file and extract test information to CSV
    
    Args:
        file_path (str): Path to the L2_regression.rpt file
        output_csv (str): Output CSV file name
        filter_keywords (tuple|list|None): Optional case-insensitive substrings; if provided,
            only tests whose test_name contains at least one keyword are kept.
    """
    
    test_results = []  # List to hold parsed test result dictionaries
    lowered_keywords = tuple(k.lower() for k in filter_keywords) if filter_keywords else None
    
    try:
        # Read the entire report file into memory
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        # Split the report into individual test sections using the
        # "T E S T    R E P O R T   F I L E" header as a delimiter
        test_sections = re.split(r'T E S T    R E P O R T   F I L E\s*#+', content)
        
        # Iterate over each test section and extract relevant fields
        for section in test_sections:
            # Skip empty sections (e.g., text before the first delimiter)
            if not section.strip():
                continue
                
            # Extract test name from "TEST NAME: <name>" line
            test_name_match = re.search(r'TEST NAME:\s*(.+)', section)
            if not test_name_match:
                continue  # Skip sections without a test name
                
            test_name = test_name_match.group(1).strip()
            
            # Extract test status from "TEST STATUS: <status>" line
            test_status_match = re.search(r'TEST STATUS:\s*(.+)', section)
            test_status = test_status_match.group(1).strip() if test_status_match else 'UNKNOWN'
            
            # Extract test result from "TEST RESULT: ... PASS/FAIL: PASS" line
            test_result_match = re.search(r'TEST RESULT:\s*(.+)', section)
            if test_result_match:
                result_line = test_result_match.group(1).strip()
                # The result line may contain "PASS/FAIL: PASS" — extract the final verdict
                final_result_match = re.search(r'PASS/FAIL:\s*(\w+)', result_line)
                test_result = final_result_match.group(1).strip() if final_result_match else result_line
            else:
                test_result = 'UNKNOWN'
            
            # Strip the ".v" Verilog file extension from test names if present
            if test_name.endswith('.v'):
                test_name = test_name[:-2]
            
            # Filter out test cases with these suffixes
            excluded_suffixes = (
                'atspeed_edt_bypass_low_internal_loopback',
                'atspeed_edt_bypass_low_internal_serial_chain',
                'atspeed_edt_edt_low_internal_loopback',
                'atspeed_edt_edt_low_internal_serial_chain',
                'stuckat_edt_bypass_low_internal_loopback',
                'on_chip_compare',
            )
            if any(test_name.endswith(s) for s in excluded_suffixes):
                continue

            # Optional keyword filter (case-insensitive substring match on test_name)
            if lowered_keywords is not None:
                lowered_name = test_name.lower()
                if not any(k in lowered_name for k in lowered_keywords):
                    continue

            # Store the parsed result as a dictionary
            test_results.append({
                'test_name': test_name,
                'test_status': test_status,
                'test_result': test_result
            })
        
        # Write all parsed results to a CSV file
        if test_results:
            with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['test_name', 'test_status', 'test_result']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for result in test_results:
                    writer.writerow(result)
            
            print(f"Successfully parsed {len(test_results)} tests and saved to {output_csv}")
            
            # Display a formatted summary table to the console
            print("\nTest Results Summary:")
            print("-" * 80)
            print(f"{'Test Name':<50} {'Status':<10} {'Result':<10}")
            print("-" * 80)
            
            # Count PASS, FAIL, and other (unknown/unexpected) results
            pass_count = sum(1 for r in test_results if r['test_result'] == 'PASS')
            fail_count = sum(1 for r in test_results if r['test_result'] == 'FAIL')
            other_count = len(test_results) - pass_count - fail_count
            
            for result in test_results:
                # Truncate long test names to fit the 50-char column width
                display_name = result['test_name'][:47] + "..." if len(result['test_name']) > 50 else result['test_name']
                print(f"{display_name:<50} {result['test_status']:<10} {result['test_result']:<10}")
            
            print("-" * 80)
            print(f"Summary: {pass_count} PASSED, {fail_count} FAILED, {other_count} OTHER")
            print(f"Total Tests: {len(test_results)}")
                        
        else:
            print("No test results found. Please check the file format.")
                        
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
    except Exception as e:
        print(f"Error parsing file: {str(e)}")


def parse_stacklevel_report(file_path, output_csv, filter_keywords=None):
    """
    Parse a stack-level rpt (e.g. `uio_a_0_dft_stacklevel_L2.rpt`) where every
    section's `TEST NAME` is the generic `dft_svf_test`. The real test
    identifier lives inside the `+SVF_FILE=<path>.svf` argument of the CMD-LINE
    (fallback: `DIR TAG`).

    Args:
        file_path (str): Path to the stacklevel rpt file.
        output_csv (str): Output CSV file name.
        filter_keywords (tuple|list|None): Optional case-insensitive substrings;
            only tests whose derived name contains at least one keyword are kept.
    """
    test_results = []
    lowered_keywords = tuple(k.lower() for k in filter_keywords) if filter_keywords else None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    # Split sections using the standard test-report banner as delimiter
    test_sections = re.split(r'T E S T    R E P O R T   F I L E\s*#+', content)

    svf_re = re.compile(r'\+SVF_FILE=(\S+\.svf)')
    dir_tag_re = re.compile(r'^\s*DIR TAG:\s*(.+)$', re.MULTILINE)
    status_re = re.compile(r'^\s*TEST STATUS:\s*(.+)$', re.MULTILINE)
    pass_fail_re = re.compile(r'PASS/FAIL:\s*(\w+)')

    for section in test_sections:
        if not section.strip():
            continue

        # Extract the effective test name: prefer SVF_FILE basename, fall back to DIR TAG.
        svf_match = svf_re.search(section)
        if svf_match:
            effective_name = os.path.basename(svf_match.group(1))
            if effective_name.endswith('.svf'):
                effective_name = effective_name[:-4]
        else:
            dt_match = dir_tag_re.search(section)
            if not dt_match:
                continue
            effective_name = dt_match.group(1).strip()

        # Optional keyword filter (case-insensitive substring match)
        if lowered_keywords is not None:
            lowered_name = effective_name.lower()
            if not any(k in lowered_name for k in lowered_keywords):
                continue

        # Test status (PASS/FAIL/etc.)
        status_match = status_re.search(section)
        test_status = status_match.group(1).strip() if status_match else 'UNKNOWN'

        # Test result: prefer PASS/FAIL: verdict if present, else mirror test_status
        pf_match = pass_fail_re.search(section)
        test_result = pf_match.group(1).strip() if pf_match else test_status

        test_results.append({
            'test_name': effective_name,
            'test_status': test_status,
            'test_result': test_result,
        })

    if not test_results:
        print(f"No stack-level test results found in {file_path}.")
        return

    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['test_name', 'test_status', 'test_result'])
        writer.writeheader()
        writer.writerows(test_results)

    pass_count = sum(1 for r in test_results if r['test_status'] == 'PASS')
    fail_count = sum(1 for r in test_results if r['test_status'] == 'FAIL')
    other_count = len(test_results) - pass_count - fail_count
    print(f"Stack-level: parsed {len(test_results)} tests "
          f"({pass_count} PASS, {fail_count} FAIL, {other_count} OTHER) -> {output_csv}")


def build_report_path(model_name):
    """
    Build the full path to the L2_regression.rpt file from the model name.

    Args:
        model_name (str): Full model name, e.g. 'nio_mc-a0-26ww14a', 'nio_uio-a0-26ww13a', 'nio_d2d-a0-26ww13a'

    Returns:
        str: Full path to the L2_regression.rpt file, or None on error
    """
    # SOC models: canonical 'nio_soc-a0-<week>' -> on-disk 'nio-a0-0p5_refresh-<week>/imh/'
    if model_name.startswith('nio_soc'):
        m = re.match(r'^nio_soc-a0-(.+)$', model_name)
        if not m:
            print(f"Error: Could not parse SOC model name '{model_name}'.")
            return None
        dir_name = f"nio-a0-0p5_refresh-{m.group(1)}"
        for base_path in BASE_PATHS:
            model_dir = f"{base_path}/{dir_name}"
            if os.path.isdir(model_dir):
                return f"{model_dir}/imh/L2_regression.list.latest/L2_regression.rpt"
        print(f"Error: SOC model directory '{dir_name}' not found in any base path.")
        _print_available_models()
        return None

    # Determine subfolder based on model type in the name
    subfolder_map = {
        'nio_mc':  'memstack',
        'nio_uio': 'uio',
        'nio_d2d': 'd2d',
    }

    subfolder = None
    for prefix, folder in subfolder_map.items():
        if model_name.startswith(prefix):
            subfolder = folder
            break

    if subfolder is None:
        print(f"Error: Could not determine subfolder for model '{model_name}'.")
        print(f"Expected model name starting with: nio_soc, {', '.join(subfolder_map.keys())}")
        _print_available_models()
        return None

    # Search across all base paths for the model directory
    for base_path in BASE_PATHS:
        model_dir = f"{base_path}/{model_name}"
        if os.path.isdir(model_dir):
            return f"{base_path}/{model_name}/{subfolder}/L2_regression.list.latest/L2_regression.rpt"

    print(f"Error: Model directory '{model_name}' does not exist in any known path.")
    _print_available_models()
    return None


def build_stacklevel_report_path(model_name):
    """
    Build the full path to the stack-level TAP rpt for a model (uio or mc only).

    Returns:
        str | None: Full path if the model type is supported (uio/mc) and the
        model directory is found under any BASE_PATHS; otherwise None.
    """
    # Determine which stacklevel subpath applies to this model
    subpath = None
    for prefix, sub in STACKLEVEL_SUBPATHS.items():
        if model_name.startswith(prefix):
            subpath = sub
            break

    if subpath is None:
        # nio_d2d (and any unknown prefix) has no stacklevel rpt
        return None

    for base_path in BASE_PATHS:
        model_dir = f"{base_path}/{model_name}"
        if os.path.isdir(model_dir):
            return f"{base_path}/{model_name}/{subpath}"

    return None


def _print_available_models():
    """List available model directories under all base paths."""
    print("\nAvailable models:")
    for base_path in BASE_PATHS:
        try:
            entries = sorted(os.listdir(base_path))
            dirs = [e for e in entries if os.path.isdir(os.path.join(base_path, e))]
            if dirs:
                print(f"  [{base_path}]")
                for d in dirs:
                    print(f"    {d}")
        except OSError as e:
            print(f"  [{base_path}] Could not list: {e}")


def discover_models(base_paths=None):
    """
    Auto-discover all valid model directories under all base paths.

    Args:
        base_paths: List of paths to search. Defaults to BASE_PATHS.

    Returns:
        list: Sorted list of unique model directory names matching known prefixes.
    """
    if base_paths is None:
        base_paths = BASE_PATHS
    subfolder_prefixes = ('nio_mc', 'nio_uio', 'nio_d2d')
    soc_pattern = re.compile(r'^nio-a0-0p5_refresh-(.+)$')
    all_models = set()
    for base_path in base_paths:
        try:
            entries = os.listdir(base_path)
            for e in entries:
                full = os.path.join(base_path, e)
                if not os.path.isdir(full):
                    continue
                if any(e.startswith(p) for p in subfolder_prefixes):
                    all_models.add(e)
                else:
                    m = soc_pattern.match(e)
                    if m:
                        all_models.add(f"nio_soc-a0-{m.group(1)}")
        except OSError as e:
            print(f"Warning: Could not list models in {base_path}: {e}")
    return sorted(all_models)


def list_remote_models():
    """Print available model names to stdout (one per line). No parsing."""
    models = discover_models()
    for m in models:
        print(m)


def run_remote_parsing(model_filter=None):
    """Run on the remote zsc24 machine: discover models, parse reports, write CSVs."""
    # Auto-discover all available models across all base paths
    print(f"Discovering models in {len(BASE_PATHS)} path(s)...")
    for bp in BASE_PATHS:
        print(f"  {bp}")
    model_names = discover_models()

    if not model_names:
        print("No models found matching known prefixes (nio_mc, nio_uio, nio_d2d, nio_soc).")
        _print_available_models()
        return

    # Filter to only requested models if specified
    if model_filter:
        model_names = [m for m in model_names if m in model_filter]
        if not model_names:
            print(f"None of the requested models were found: {model_filter}")
            return
        print(f"Processing {len(model_names)} model(s): {', '.join(model_names)}\n")
    else:
        print(f"Found {len(model_names)} model(s): {', '.join(model_names)}\n")

    # Validate each model has a report file, skip those that don't
    validated = {}  # model_name -> input_file path
    skipped = []    # (model_name, reason)
    for model_name in model_names:
        input_file = build_report_path(model_name)
        if input_file is None:
            skipped.append((model_name, "could not determine report path"))
        elif not Path(input_file).exists():
            skipped.append((model_name, f"report file not found: {input_file}"))
        else:
            validated[model_name] = input_file

    if skipped:
        print(f"Skipping {len(skipped)} model(s) without report files:")
        for m, reason in skipped:
            print(f"  - {m}: {reason}")
        print()

    if not validated:
        print("No models with valid report files found. Nothing to process.")
        return

    # Collect last-modified timestamps for each report file
    timestamps = {}
    for model_name, input_file in validated.items():
        try:
            stat = os.stat(input_file)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            timestamps[model_name] = mtime
        except OSError:
            timestamps[model_name] = "UNKNOWN"

    # Save timestamps to JSON for use by report_l2_scan.py
    timestamps_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_timestamps.json")
    with open(timestamps_file, 'w', encoding='utf-8') as f:
        json.dump(timestamps, f, indent=2)
    print(f"Saved report timestamps to {timestamps_file}")

    for model_name, input_file in validated.items():
        print(f"\n{'='*80}")
        print(f"Processing model: {model_name}")
        print(f"{'='*80}")

        output_file = f"{model_name}_regression_results.csv"

        print(f"Report path: {input_file}")
        print(f"Last modified: {timestamps.get(model_name, 'UNKNOWN')}")

        parse_l2_regression_report(input_file, output_file)

        # --- TAP stack-level rpt (uio/mc only; d2d has none) ---
        stacklevel_path = build_stacklevel_report_path(model_name)
        if stacklevel_path is None:
            continue
        if not Path(stacklevel_path).exists():
            print(f"\n[stacklevel] Skipping — file not found: {stacklevel_path}")
            continue

        try:
            sl_mtime = datetime.fromtimestamp(os.stat(stacklevel_path).st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            sl_mtime = "UNKNOWN"
        timestamps[f"{model_name}_stacklevel"] = sl_mtime

        sl_output = f"{model_name}_stacklevel_regression_results.csv"
        print(f"\n[stacklevel] Report path: {stacklevel_path}")
        print(f"[stacklevel] Last modified: {sl_mtime}")
        print(f"[stacklevel] Keyword filter: {', '.join(TAP_STACKLEVEL_KEYWORDS)}")
        parse_stacklevel_report(stacklevel_path, sl_output, filter_keywords=TAP_STACKLEVEL_KEYWORDS)

    # Re-write timestamps JSON so it includes stacklevel entries
    with open(timestamps_file, 'w', encoding='utf-8') as f:
        json.dump(timestamps, f, indent=2)

    print(f"\n{'='*80}")
    print(f"Done. Processed {len(validated)} model(s), skipped {len(skipped)}.")


def run_list_models_from_windows():
    """SSH to zsc24 and print available model names to stdout. No parsing."""
    remote = f"{REMOTE_USER}@{REMOTE_HOST}"
    script_path = os.path.abspath(__file__)

    # Ensure remote work dir exists
    subprocess.run(["ssh", remote, f"mkdir -p {REMOTE_WORK_DIR}"], check=False)

    scp_up = subprocess.run([
        "scp", script_path,
        f"{remote}:{REMOTE_WORK_DIR}/parse_l2_regression.py",
    ])
    if scp_up.returncode != 0:
        print(f"Error: SCP upload failed (exit code {scp_up.returncode}).", file=sys.stderr)
        sys.exit(scp_up.returncode)

    ssh_cmd = subprocess.run([
        "ssh", remote,
        f"cd {REMOTE_WORK_DIR} && python3 parse_l2_regression.py --remote --list-models",
    ])
    sys.exit(ssh_cmd.returncode)


def run_from_windows(models=None):
    """Run from Windows: upload script to zsc24, execute remotely, download CSVs."""
    remote = f"{REMOTE_USER}@{REMOTE_HOST}"
    script_path = os.path.abspath(__file__)

    # Ensure local weekly_report directory exists
    os.makedirs(WEEKLY_REPORT_DIR, exist_ok=True)

    # Step 0: Ensure remote work dir exists (may not exist on first run under this user)
    print(f"Ensuring remote work dir exists: {REMOTE_WORK_DIR}")
    subprocess.run(["ssh", remote, f"mkdir -p {REMOTE_WORK_DIR}"], check=False)

    # Step 1: Upload this script to the remote working directory
    print(f"Uploading script to {remote}:{REMOTE_WORK_DIR}/")
    scp_up = subprocess.run([
        "scp", script_path,
        f"{remote}:{REMOTE_WORK_DIR}/parse_l2_regression.py",
    ])
    if scp_up.returncode != 0:
        print(f"Error: SCP upload failed (exit code {scp_up.returncode}).")
        sys.exit(scp_up.returncode)

    # Step 2: Run the script remotely — parse only requested models if specified
    print(f"\nRunning parser on {REMOTE_HOST}...")
    remote_cmd = f"cd {REMOTE_WORK_DIR} && python3 parse_l2_regression.py --remote"
    if models:
        remote_cmd += f" --models={','.join(models)}"
    ssh_cmd = subprocess.run(["ssh", remote, remote_cmd])
    if ssh_cmd.returncode != 0:
        print(f"Error: Remote parsing failed (exit code {ssh_cmd.returncode}).")
        sys.exit(ssh_cmd.returncode)

    # Step 3: Download generated CSVs to local weekly_report folder
    print(f"\nDownloading CSVs to {WEEKLY_REPORT_DIR}/")
    if models:
        # Download only the specific model CSVs
        for model in models:
            csv_name = f"{model}_regression_results.csv"
            scp_down = subprocess.run([
                "scp", f"{remote}:{REMOTE_WORK_DIR}/{csv_name}",
                WEEKLY_REPORT_DIR,
            ])
            if scp_down.returncode != 0:
                print(f"Warning: Could not download {csv_name} (exit code {scp_down.returncode}).")

            # Also try to download the stacklevel CSV (uio/mc only; d2d won't have one)
            if any(model.startswith(p) for p in STACKLEVEL_SUBPATHS.keys()):
                sl_csv_name = f"{model}_stacklevel_regression_results.csv"
                scp_sl = subprocess.run([
                    "scp", f"{remote}:{REMOTE_WORK_DIR}/{sl_csv_name}",
                    WEEKLY_REPORT_DIR,
                ])
                if scp_sl.returncode != 0:
                    print(f"Warning: Could not download {sl_csv_name} (exit code {scp_sl.returncode}).")
    else:
        scp_down = subprocess.run([
            "scp", f"{remote}:{REMOTE_WORK_DIR}/*.csv",
            WEEKLY_REPORT_DIR,
        ])
        if scp_down.returncode != 0:
            print(f"Error: SCP download failed (exit code {scp_down.returncode}).")
            sys.exit(scp_down.returncode)

    # Download report_timestamps.json
    timestamps_dest = os.path.join(WEEKLY_REPORT_DIR, "report_timestamps.json")
    scp_ts = subprocess.run([
        "scp", f"{remote}:{REMOTE_WORK_DIR}/report_timestamps.json",
        timestamps_dest,
    ])
    if scp_ts.returncode != 0:
        print(f"Warning: Could not download report_timestamps.json (exit code {scp_ts.returncode}).")

    # Count downloaded files
    csvs = [f for f in os.listdir(WEEKLY_REPORT_DIR) if f.endswith('.csv')]
    print(f"\nDone. {len(csvs)} CSV file(s) in {WEEKLY_REPORT_DIR}/")


def main():
    list_models_only = '--list-models' in sys.argv
    remote = '--remote' in sys.argv

    models_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith('--models='):
            models_arg = arg.split('=', 1)[1].split(',')
            break

    if remote:
        if list_models_only:
            list_remote_models()
        else:
            run_remote_parsing(model_filter=models_arg)
    else:
        if list_models_only:
            run_list_models_from_windows()
        else:
            run_from_windows(models=models_arg)

# Entry point: only run main() when executed directly (not when imported)
if __name__ == "__main__":
    main()