# report_l2_scan.py
# Reads CSV files from the local weekly_report folder and generates a
# consolidated report CSV with owner, test_case, status, and model.
# Usage: python report_l2_scan.py

import csv
import glob
import json
import os
import re
import subprocess
import sys
import struct
import zlib
from datetime import datetime

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))       # Directory where this script lives
ROOT_DIR = os.path.dirname(SCRIPT_DIR)                        # Project root (parent of scripts/)
OWNERSHIP_FILE = os.path.join(SCRIPT_DIR, "scan_ownership.txt")  # Maps test prefixes to owners (SCAN pipeline)
SOC_OWNERSHIP_PREFIXES = set()  # Populated from scan_ownership.txt ',soc'-tagged lines; lets get_partition_type classify SOC partitions
WEEKLY_REPORT_DIR = os.path.join(ROOT_DIR, "weekly_report")   # Folder with per-model regression CSVs
REPORTS_DIR = os.path.join(ROOT_DIR, "scan_reports")            # Folder for generated SCAN reports
PARSE_SCRIPT = os.path.join(SCRIPT_DIR, "parse_l2_regression.py")  # Parser script path
os.makedirs(REPORTS_DIR, exist_ok=True)
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")          # Timestamp for output filename
OUTPUT_REPORT = os.path.join(REPORTS_DIR, f"scan_general_report_{TIMESTAMP}.csv")  # Final consolidated report
STACK_HISTORY_FILE = os.path.join(REPORTS_DIR, "scan_stack_status_history.csv")      # Historical percentages for partition-level plots (par*)
STACK_LEVEL_HISTORY_FILE = os.path.join(REPORTS_DIR, "scan_stack_level_status_history.csv")  # Historical percentages for stack-level plots
GITHUB_PAGES_INDEX = "https://eaarayag.github.io/Code/scan_reports/scan_index.html"     # Report history on GitHub Pages
GITHUB_PAGES_BASE = "https://eaarayag.github.io/Code/scan_reports/"               # Base URL for individual reports
SIH_TEST_TOKEN = "_sih_"
SIH_PVIM_ITEM = "[NWP] SIH case val"
SIH_OWNER = "Diego Matamoros"


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
                # Partition-level ownership. An optional trailing ',soc' field
                # marks a SOC-model partition so get_partition_type classifies
                # it as 'soc' (SOC names overlap stack prefixes like pard2d*).
                ownership.append((owner, target))
                if len(parts) > 2 and parts[2].strip().lower() == 'soc':
                    SOC_OWNERSHIP_PREFIXES.add(target)
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


# SOC partitions have no ownership prefixes, so split SOC test names at the
# first known DFT test-type marker instead of relying on ownership.
SOC_TEST_TYPE_MARKERS = ('atspeed', 'stuckat', 'ijtag', 'ssn', 'icl', 'scan_ctlr', 'scandump')


def split_soc_test_name(test_name):
    """Split a SOC test_name into (partition, test_type) at the first DFT marker."""
    m = re.search(r'_(?:' + '|'.join(SOC_TEST_TYPE_MARKERS) + r')_', test_name)
    if m:
        return test_name[:m.start()], test_name[m.start() + 1:]
    return test_name, ''


def extract_model_from_filename(filename):
    """Extract model name from CSV filename, e.g. 'nio_mc-a0-26ww14a' from 'nio_mc-a0-26ww14a_regression_results.csv'."""
    basename = os.path.basename(filename)
    # Remove the '_regression_results.csv' suffix
    if basename.endswith("_regression_results.csv"):
        return basename[:-len("_regression_results.csv")]
    return basename


def list_available_models(category):
    """List available CSV files in weekly_report/ matching a category prefix (e.g. 'nio_mc').

    Excludes stack-level CSVs (`<model>_stacklevel_regression_results.csv`) which are
    consumed only by the TAP pipeline — SCAN must not present them as selectable models.
    """
    pattern = os.path.join(WEEKLY_REPORT_DIR, f"{category}*_regression_results.csv")
    files = sorted(f for f in glob.glob(pattern) if "_stacklevel_" not in os.path.basename(f))
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
    'icl',
)


def is_excluded_test_type(test_type):
    """Return True if a test_type should be excluded from the report."""
    if test_type in EXCLUDED_TEST_TYPES:
        return True
    for token in EXCLUDED_TEST_PREFIXES:
        if test_type.startswith(token) or ('_' + token) in test_type:
            return True
    return False


# PVIM mapping: maps test_type to PVIM item description for SCAN pipeline.
SCAN_PVIM_MAPPING = {
    'stuckat_edt_bypass_low_internal_serial_scan': '[NWP] scan: edtbyp stuckat proxy',
    'stuckat_edt_edt_low_internal_serial_scan': '[NWP] scan: edten stuckat proxy',
    'atspeed_edt_edt_low_internal_serial_scan': '[NWP] scan: edten atspeed proxy',
    'stuckat_edt_bypass_low_internal_serial_chain': '[NWP] scan: edtbyp cont proxy',
    'stuckat_edt_edt_low_internal_serial_chain': '[NWP] scan: edten cont proxy',
    'stuckat_edt_bypass_low_internal_burnin_togcnt_cap_off': '[NWP] scan: Burnin',
    'ssn_continuity': '[NWP] SSN: continuity',
    'stuckat_edt_edt_low_internal_loopback': '[NWP] SSN: endpoint loopback',
}

# Suffix-based PVIM mappings (for test types matched with '*' in EXPECTED_TESTS)
SCAN_PVIM_SUFFIX_MAPPING = {
    'scan_ctlr_stuckat_edt_bypass_low_internal_scandump': '[NWP] scan: scandump',
}


def get_scan_pvim_item(test_type):
    """Look up PVIM item description for a given test_type.

    Stack-level retarget tests (e.g. `d2d1_retarget_atspeed_edt_edt_low_internal_serial_scan`)
    share the same Item value as their partition-level base test
    (`atspeed_edt_edt_low_internal_serial_scan`). To support this, after an
    exact-match miss we fall back to suffix matching against `SCAN_PVIM_MAPPING`
    so the trailing base test name resolves to the same Item description.
    """
    if test_type in SCAN_PVIM_MAPPING:
        return SCAN_PVIM_MAPPING[test_type]
    for suffix, pvim_item in SCAN_PVIM_SUFFIX_MAPPING.items():
        if test_type.endswith(suffix):
            return pvim_item
    # Fallback: stack-level retarget variants reuse the base test's Item value.
    for base_name, pvim_item in SCAN_PVIM_MAPPING.items():
        if test_type.endswith('_' + base_name):
            return pvim_item
    return ''


def apply_sih_override(row):
    """Force SIH rows to the required item/owner mapping."""
    test_type = (row.get('test_type') or '').lower()
    if SIH_TEST_TOKEN in test_type:
        row['pvim_item'] = SIH_PVIM_ITEM
        row['owner'] = SIH_OWNER


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

# Test types that are NOT expected to run at Stack Level scope.
# Stack-root regions (d2d1, memstack, uio_a_0) should not be flagged as MISSING
# for these test types because they will never exist as stack-level tests.
STACK_LEVEL_EXCLUDED_EXPECTED_TESTS = {
    'atspeed_edt_edt_low_internal_serial_scan',
    'scan_ctlr_stuckat_edt_bypass_low_internal_scandump',
    'stuckat_edt_bypass_low_internal_burnin_togcnt_cap_off',
    'stuckat_edt_bypass_low_internal_serial_chain',
    'stuckat_edt_bypass_low_internal_serial_scan',
    'stuckat_edt_edt_low_internal_loopback',
    'stuckat_edt_edt_low_internal_serial_chain',
    'stuckat_edt_edt_low_internal_serial_scan',
}

# Base test types that must exist at Stack Level scope for every non-root
# partition, in the form `<stack>_retarget_<base>`. They map to these Items:
#   stuckat_edt_edt_low_internal_serial_scan   -> [NWP] scan: edten stuckat proxy
#   stuckat_edt_edt_low_internal_serial_chain  -> [NWP] scan: edten cont proxy
#   stuckat_edt_edt_low_internal_loopback      -> [NWP] SSN: endpoint loopback
#   atspeed_edt_edt_low_internal_serial_scan   -> [NWP] scan: edten atspeed proxy
STACK_LEVEL_EXPECTED_BASE_TESTS = [
    'stuckat_edt_edt_low_internal_serial_scan',
    'stuckat_edt_edt_low_internal_serial_chain',
    'stuckat_edt_edt_low_internal_loopback',
    'atspeed_edt_edt_low_internal_serial_scan',
]

# Map partition model-type to the stack root used in `<stack>_retarget_*` tests.
STACK_ROOT_BY_TYPE = {
    'd2d': 'd2d1',
    'mc': 'memstack',
    'uio': 'uio_a_0',
    'uioe': 'uio_1',
}

# Partitions classified under the new `uioestack` bucket. These partitions
# live inside the existing `nio_uio` model CSVs but are tracked in their own
# table/history bucket.
EXPLICIT_UIOE_PARTITIONS = {
    'parmiofblpvnpipeac_uio_1',
    'parmiofblptx_uio_1',
    'parmiopcie6ttidecee_uio_1',
    'parmioasf_uio_1',
    'parmioula_uio_1',
    'parmiomisc_uio_1',
    'parmiofblprxfcrarbmux_uio_1',
}

# Which partition-types belong to each model category. A single model can host
# more than one partition-type bucket (e.g. `nio_uio` hosts both `uio` and the
# new `uioe` partitions).
PARTITION_TYPES_FOR_MODEL = {
    'mc': ('mc',),
    'uio': ('uio', 'uioe'),
    'd2d': ('d2d',),
    'soc': ('soc',),
}

# Ordered list of stack buckets and their display labels used for trend
# charts, history CSVs, and per-model detail tables.
STACK_BUCKETS = ('mc', 'uio', 'd2d', 'uioe')
STACK_LABELS = {
    'mc': 'MC Stack',
    'uio': 'UIO Stack',
    'd2d': 'D2D Stack',
    'uioe': 'UIOe Stack',
}
STACK_CHART_LABELS = {
    'mc': 'MEMSTACK',
    'uio': 'UIO',
    'd2d': 'D2D',
    'uioe': 'UIOe Stack',
}

# SOC is a standalone model bucket (its own model category + trend section),
# kept apart from the stack buckets. It has a Partition Level and a SOC Level
# scope (the latter has no data yet). `HISTORY_BUCKETS` is the full set tracked
# in the history CSVs and metrics.
SOC_BUCKET = 'soc'
SOC_LABEL = 'SOC'
HISTORY_BUCKETS = STACK_BUCKETS + (SOC_BUCKET,)

def get_effective_owner(test_type, owner, test_type_overrides):
    """Return effective owner based on test-type-level overrides from ownership.txt.
    If the test_type matches an override and the current owner is not excluded, return the override owner."""
    for override_owner, override_test_type, excluded_owners in test_type_overrides:
        if test_type == override_test_type or test_type.endswith(override_test_type):
            if owner in excluded_owners:
                return owner
            return override_owner
    return owner


def get_scope(partition, test_type=''):
    """Return validation scope label based on the stack identifier.

    A row is classified as Stack Level when either:
      1. The region/partition itself is a stack root (e.g. `d2d1`, `memstack`,
         `uio_a_0`, `uio_1`), or
      2. The test name carries the cross-stack retarget identifier
         `<stack>_retarget` (e.g. `d2d1_retarget_*`, `memstack_retarget_*`,
         `uio_a_0_retarget_*`, `uio_1_retarget_*`).
    Everything else is classified as Partition Level.
    """
    stack_root_names = ('d2d1', 'memstack', 'uio_a_0', 'uio_1')
    retarget_roots = ('d2d1', 'memstack', 'uio_a_0', 'uio_1')
    region = (partition or '').strip().lower()
    if region in stack_root_names:
        return 'Stack Level'
    name = (test_type or '').lower()
    for stack in retarget_roots:
        if f'{stack}_retarget' in name:
            return 'Stack Level'
    return 'Partition Level'


def get_partition_type(partition):
    """Determine model type (mc/uio/d2d/uioe/soc) for a partition based on its naming prefix."""
    # SOC partitions (tagged ',soc' in scan_ownership.txt) are classified first
    # because their names overlap stack prefixes (pard2d*, parmio*).
    if partition in SOC_OWNERSHIP_PREFIXES:
        return 'soc'
    if partition.startswith('memstack'):
        return 'mc'
    if partition.startswith('d2d1'):
        return 'd2d'
    if partition.startswith('uio_a_0'):
        return 'uio'
    # `uio_1` is the stack root for the new UIOe Stack bucket — must be
    # classified as `uioe` (not plain `uio`) so its Stack Level rows land in
    # the UIOe Stack column of the trend charts.
    if partition == 'uio_1':
        return 'uioe'
    # The new `uioestack` partitions live in `nio_uio` CSVs but form their own
    # bucket. Match them explicitly BEFORE the generic `parmio*` rule so they
    # are not misclassified as plain `uio`.
    if partition in EXPLICIT_UIOE_PARTITIONS:
        return 'uioe'
    if partition.startswith('pard2d'):
        return 'd2d'
    elif partition.startswith('parmc') or partition.startswith('parmem'):
        return 'mc'
    elif partition.startswith('parmio'):
        return 'uio'
    # Explicit overrides for partitions whose prefix does not fit the rules
    # above. Keep this list in sync with scan_ownership.txt entries so every
    # partition is classified for completeness checks.
    explicit_mc_partitions = {'pardfi', 'phy_cluster'}
    if partition in explicit_mc_partitions:
        return 'mc'
    return None


def get_model_type(model_name):
    """Extract model type (mc/uio/d2d/soc) from model name like 'nio_mc-a0-26ww14a'."""
    if model_name.startswith('nio_mc'):
        return 'mc'
    elif model_name.startswith('nio_uio'):
        return 'uio'
    elif model_name.startswith('nio_d2d'):
        return 'd2d'
    elif model_name.startswith('nio_soc'):
        return 'soc'
    return None


def bucket_for_row(row):
    """Return the trend/history bucket for a report row.

    SOC partition names are arbitrary and not prefix-classifiable, so SOC rows
    are bucketed by their model. Everything else buckets by partition prefix.
    """
    if get_model_type(row.get('model', '')) == 'soc':
        return SOC_BUCKET
    return get_partition_type(row.get('partition', ''))


def _status_priority(status):
    """Priority for collapsing duplicate rows of the same test.
    FAIL is highest priority, then PASS, then MISSING/other."""
    order = {
        'FAIL': 3,
        'PASS': 2,
        'MISSING': 1,
    }
    return order.get((status or '').strip().upper(), 0)


def dedupe_and_normalize_rows(rows):
    """Collapse duplicate rows by (model, partition, test_type).

    If multiple statuses are present for the same key, keep the one with the
    highest priority so regressions are not hidden.
    """
    deduped = {}
    for row in rows:
        key = (row.get('model', ''), row.get('partition', ''), row.get('test_type', ''))
        prev = deduped.get(key)
        if prev is None:
            deduped[key] = dict(row)
            continue

        prev_pri = _status_priority(prev.get('status'))
        curr_pri = _status_priority(row.get('status'))
        if curr_pri > prev_pri:
            chosen = dict(row)
            # Preserve fields that may be missing in the promoted row.
            if not chosen.get('owner'):
                chosen['owner'] = prev.get('owner', '')
            if not chosen.get('pvim_item'):
                chosen['pvim_item'] = prev.get('pvim_item', '')
            deduped[key] = chosen
            continue

        # Keep previous status, but backfill missing non-key fields if needed.
        if not prev.get('owner') and row.get('owner'):
            prev['owner'] = row['owner']
        if not prev.get('pvim_item') and row.get('pvim_item'):
            prev['pvim_item'] = row['pvim_item']

    return list(deduped.values())


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


def _compute_stack_metrics(rows, include_row=None):
    """Compute PASS/FAIL/MISSING counts and percentages per stack bucket.

    Rows are bucketed by partition-type (mc/uio/d2d/uioe) so that partitions
    in the new `uioestack` bucket (which physically live inside `nio_uio`
    model CSVs) are separated from the regular `uio` bucket.
    A predicate `include_row(row)` can filter which rows are counted.
    """
    metrics = {}
    for r in rows:
        if include_row and not include_row(r):
            continue
        stack = bucket_for_row(r)
        if stack not in HISTORY_BUCKETS:
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


def _write_stack_history_csv(file_path, entries):
    """Write stack history entries into a CSV file."""
    with open(file_path, 'w', newline='', encoding='utf-8') as f:
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


def rebuild_stack_history_csv():
    """Rebuild partition-level and stack-level history CSV files from all general reports."""
    try:
        result = subprocess.run(
            ['git', 'ls-files', '--', 'scan_reports/scan_general_report_*.csv'],
            capture_output=True, text=True, cwd=ROOT_DIR
        )
        tracked_names = {os.path.basename(f.strip()) for f in result.stdout.splitlines() if f.strip()}
        report_paths = sorted(
            [p for p in glob.glob(os.path.join(REPORTS_DIR, 'scan_general_report_*.csv'))
             if os.path.basename(p) in tracked_names]
        )
    except Exception:
        report_paths = sorted(glob.glob(os.path.join(REPORTS_DIR, 'scan_general_report_*.csv')))

    partition_entries = []
    stack_entries = []

    def make_entry(report_name, ts_human, ts_compact, stack, m):
        return {
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
        }

    for report_path in report_paths:
        report_name = os.path.basename(report_path)
        ts_compact = _extract_timestamp_from_report_name(report_name)
        ts_human = _format_report_timestamp_for_history(ts_compact)
        rows = _load_general_report_rows(report_path)
        if not rows:
            continue

        # Partition-level trends: rows whose scope is Partition Level.
        metrics = _compute_stack_metrics(
            rows,
            include_row=lambda r: get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Partition Level',
        )
        for stack in HISTORY_BUCKETS:
            m = metrics.get(stack)
            if not m:
                continue
            partition_entries.append(make_entry(report_name, ts_human, ts_compact, stack, m))

        # Stack-level trends: rows whose scope is Stack Level.
        stack_metrics = _compute_stack_metrics(
            rows,
            include_row=lambda r: get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Stack Level',
        )
        for stack in HISTORY_BUCKETS:
            m = stack_metrics.get(stack)
            if not m:
                continue
            stack_entries.append(make_entry(report_name, ts_human, ts_compact, stack, m))

    partition_entries.sort(key=lambda e: (e['timestamp_key'], e['stack']))
    stack_entries.sort(key=lambda e: (e['timestamp_key'], e['stack']))

    _write_stack_history_csv(STACK_HISTORY_FILE, partition_entries)
    _write_stack_history_csv(STACK_LEVEL_HISTORY_FILE, stack_entries)

    print(f"Stack history file updated: {STACK_HISTORY_FILE}")
    print(f"Stack-level history file updated: {STACK_LEVEL_HISTORY_FILE}")


def load_stack_history(history_file=STACK_HISTORY_FILE):
    """Load stack history rows keyed by stack from a history CSV file."""
    data = {stack: [] for stack in HISTORY_BUCKETS}
    if not os.path.isfile(history_file):
        return data

    with open(history_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stack = row.get('stack', '').strip()
            if stack not in data:
                continue
            data[stack].append(row)

    for stack in data:
        data[stack].sort(key=lambda r: _extract_timestamp_from_report_name(r.get('report_name', '')))
    return data


def _write_png_rgb(path, width, height, rgb_bytes):
    """Write an RGB PNG file from raw bytes (width * height * 3)."""
    if len(rgb_bytes) != width * height * 3:
        raise ValueError("Invalid RGB buffer size")

    def png_chunk(tag, data):
        return (
            struct.pack('!I', len(data))
            + tag
            + data
            + struct.pack('!I', zlib.crc32(tag + data) & 0xffffffff)
        )

    # Add per-row filter byte 0 (no filter)
    rows = []
    stride = width * 3
    for y in range(height):
        start = y * stride
        rows.append(b'\x00' + rgb_bytes[start:start + stride])
    compressed = zlib.compress(b''.join(rows), level=9)

    ihdr = struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', ihdr) + png_chunk(b'IDAT', compressed) + png_chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)


def _render_trend_png(history_data, output_path):
    """Render horizontal line charts (one per stack bucket) into a PNG image using matplotlib."""
    import importlib

    matplotlib = importlib.import_module('matplotlib')
    matplotlib.use('Agg')
    plt = importlib.import_module('matplotlib.pyplot')

    def build_points(rows):
        # Keep only the latest point per date to avoid duplicate daily entries.
        by_date = {}
        for r in rows:
            date_str = (r.get('report_timestamp', '') or '')[:10]
            if not date_str:
                continue
            try:
                by_date[date_str] = (
                    float(r.get('pass_pct', 0) or 0),
                    float(r.get('fail_pct', 0) or 0),
                    float(r.get('missing_pct', 0) or 0),
                )
            except ValueError:
                by_date[date_str] = (0.0, 0.0, 0.0)
        dates = sorted(by_date.keys())
        if not dates:
            return [], [], [], []
        p = [by_date[d][0] for d in dates]
        f = [by_date[d][1] for d in dates]
        m = [by_date[d][2] for d in dates]
        return dates, p, f, m

    stacks = [(k, STACK_CHART_LABELS[k]) for k in STACK_BUCKETS]
    fig, axes = plt.subplots(1, len(stacks), figsize=(5.2 * len(stacks), 3.6), dpi=160)
    if len(stacks) == 1:
        axes = [axes]

    for idx, (stack_key, title) in enumerate(stacks):
        ax = axes[idx]
        dates, pass_vals, fail_vals, miss_vals = build_points(history_data.get(stack_key, []))

        ax.set_title(title, fontsize=11, fontweight='bold')
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.grid(axis='y', color='#e5e7eb', linestyle='-', linewidth=0.8)

        if dates:
            x = list(range(len(dates)))
            ax.plot(x, pass_vals, color='#2e7d32', marker='o', markersize=3, linewidth=1.8, label='PASS %')
            ax.plot(x, fail_vals, color='#c62828', marker='o', markersize=3, linewidth=1.8, label='FAIL %')
            ax.plot(x, miss_vals, color='#e65100', marker='o', markersize=3, linewidth=1.8, label='MISSING %')

            label_step = max(1, len(dates) // 6)
            tick_idx = list(range(0, len(dates), label_step))
            if tick_idx[-1] != len(dates) - 1:
                tick_idx.append(len(dates) - 1)
            ax.set_xticks(tick_idx)
            ax.set_xticklabels([dates[i] for i in tick_idx], rotation=35, ha='right', fontsize=8)
        else:
            ax.set_xticks([])

        for spine in ax.spines.values():
            spine.set_color('#d1d5db')
        ax.tick_params(axis='y', labelsize=8)

    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc='lower center', ncol=3, frameon=False, fontsize=9, bbox_to_anchor=(0.5, -0.02))

    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(output_path, format='png', facecolor='white')
    plt.close(fig)


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
    unmapped_prefixes = []
    for owner, prefix in ownership:
        p_type = get_partition_type(prefix)
        if p_type is None:
            unmapped_prefixes.append(prefix)
            continue
        for model in selected_models:
            m_type = get_model_type(model)
            # Only pair partition with a model whose category hosts this
            # partition-type bucket (e.g. `nio_uio` hosts both `uio` and `uioe`).
            if p_type and m_type and p_type in PARTITION_TYPES_FOR_MODEL.get(m_type, ()):
                all_partition_combos.append((prefix, model, owner))
    if unmapped_prefixes:
        print(f"Warning: {len(unmapped_prefixes)} ownership prefix(es) not classified "
              f"by get_partition_type and will be skipped from MISSING checks: "
              f"{', '.join(sorted(set(unmapped_prefixes)))}")

    missing_rows = []
    for (partition, model, owner) in all_partition_combos:
        test_types = existing.get((partition, model), set())
        is_stack_level = get_scope(partition) == 'Stack Level'
        for expected in EXPECTED_TESTS:
            if expected.startswith('*'):
                # Suffix match: check if any existing test_type ends with the pattern
                suffix = expected[1:]
                if is_stack_level and suffix in STACK_LEVEL_EXCLUDED_EXPECTED_TESTS:
                    continue
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
                if is_stack_level and expected in STACK_LEVEL_EXCLUDED_EXPECTED_TESTS:
                    continue
                if expected not in test_types:
                    effective_owner = get_effective_owner(expected, owner, test_type_overrides)
                    missing_rows.append({
                        'owner': effective_owner,
                        'partition': partition,
                        'test_type': expected,
                        'status': 'MISSING',
                        'model': model,
                    })

    # Stack Level expected retarget tests: every non-root partition should
    # carry `<stack>_retarget_<base>` for each base test in
    # STACK_LEVEL_EXPECTED_BASE_TESTS. Missing ones surface as Stack Level
    # MISSING rows (scope is derived from the test name by get_scope).
    stack_root_partitions = set(STACK_ROOT_BY_TYPE.values())
    for (partition, model, owner) in all_partition_combos:
        if partition in stack_root_partitions:
            continue  # stack roots themselves are not expected to retarget
        stack_root = STACK_ROOT_BY_TYPE.get(get_partition_type(partition))
        if not stack_root:
            continue
        test_types = existing.get((partition, model), set())
        for base in STACK_LEVEL_EXPECTED_BASE_TESTS:
            expected_tt = f'{stack_root}_retarget_{base}'
            if expected_tt in test_types:
                continue
            effective_owner = get_effective_owner(expected_tt, owner, test_type_overrides)
            missing_rows.append({
                'owner': effective_owner,
                'partition': partition,
                'test_type': expected_tt,
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
            is_soc = get_model_type(model) == 'soc'
            for row in reader:
                test_name = row['test_name']
                owner = find_owner(test_name, ownership)
                # SOC partitions have no ownership entries yet — keep them
                # (owner UNKNOWN) so the new model is never silently dropped.
                if owner == "UNKNOWN" and not is_soc:
                    continue
                if is_soc:
                    partition, test_type = split_soc_test_name(test_name)
                else:
                    partition, test_type = split_test_name(test_name, ownership)
                # Exclude TAP-only test types (ijtag/icl) from SCAN for all models, SOC included.
                if is_excluded_test_type(test_type):
                    continue
                # Override owner if test_type is owned at the test-type level
                effective_owner = get_effective_owner(test_type, owner, test_type_overrides)
                all_rows.append({
                    'owner': effective_owner,
                    'partition': partition,
                    'test_type': test_type,
                    'pvim_item': get_scan_pvim_item(test_type),
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

    # Consolidate repeated rows from source CSVs to keep one canonical entry per test.
    before_dedupe = len(all_rows)
    all_rows = dedupe_and_normalize_rows(all_rows)
    deduped_count = before_dedupe - len(all_rows)
    if deduped_count > 0:
        print(f"Collapsed {deduped_count} duplicate test row(s) in consolidated report.")

    # Populate pvim_item for missing rows that were added by check_test_completeness
    for r in all_rows:
        if 'pvim_item' not in r:
            r['pvim_item'] = get_scan_pvim_item(r['test_type'])
        # Populate validation scope so downstream consumers can group rows.
        r['scope'] = get_scope(r.get('partition', ''), r.get('test_type', ''))
        apply_sih_override(r)

    all_rows.sort(key=lambda r: (r['model'], r['partition'], r['test_type'], r['owner']))

    with open(OUTPUT_REPORT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['scope', 'partition', 'test_type', 'pvim_item', 'status', 'owner', 'model'])
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
    html_path = os.path.join(REPORTS_DIR, f"scan_general_report_{TIMESTAMP}.html")

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

    def compute_summary(rows):
        total = len(rows)
        total_pass = sum(1 for r in rows if r['status'] == 'PASS')
        total_fail = sum(1 for r in rows if r['status'] == 'FAIL')
        total_missing = sum(1 for r in rows if r['status'] == 'MISSING')
        pass_rate = (total_pass / total * 100) if total else 0.0
        return {
            'total': total,
            'pass': total_pass,
            'fail': total_fail,
            'missing': total_missing,
            'pass_rate': pass_rate,
        }

    partition_rows = [r for r in all_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Partition Level']
    stack_rows = [r for r in all_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Stack Level']
    soc_rows = [r for r in all_rows if get_model_type(r.get('model', '')) == 'soc']
    soc_partition_rows = [r for r in soc_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) != 'Stack Level']
    soc_level_rows = [r for r in soc_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Stack Level']
    partition_summary = compute_summary(partition_rows)
    stack_summary = compute_summary(stack_rows)
    soc_partition_summary = compute_summary(soc_partition_rows)
    soc_level_summary = compute_summary(soc_level_rows)

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
    h.append('<table width="1100" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
             'style="border:1px solid #dddddd;">')

    # ── Header banner ──
    h.append('<tr><td bgcolor="#0071c5" style="padding:24px 32px;">')
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="color:#ffffff;font-size:22px;font-weight:bold;{FONT}">SCAN GENERAL REPORT</span><br>')
    h.append(f'<span style="color:#b3d9f2;font-size:14px;{FONT}">SCAN L2 Regression &mdash; {datetime.now().strftime("%Y-%m-%d %H:%M")}</span>')
    h.append(f'</td><td align="right" valign="top" style="{FONT}">')
    h.append(f'<a href="{GITHUB_PAGES_INDEX}" style="color:#ffffff;font-size:13px;text-decoration:underline;{FONT}">Report History</a>')
    h.append('</td></tr></table>')
    h.append('</td></tr>')

    def append_summary_cards(title, summary):
        h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tr><td style="{FONT}">')
        h.append(f'<span style="font-size:14px;font-weight:bold;color:#333;{FONT}">{title}</span>')
        h.append('</td></tr></table>')
        h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')
        stats = [
            (str(summary['total']), 'TOTAL', '#f0f7ff', '#0071c5'),
            (str(summary['pass']), 'PASS', '#e8f5e9', '#2e7d32'),
            (str(summary['fail']), 'FAIL', '#ffebee', '#c62828'),
            (str(summary['missing']), 'MISSING', '#fff3e0', '#e65100'),
        ]
        for i, (val, label, bg, fg) in enumerate(stats):
            if i > 0:
                h.append('<td width="8"></td>')
            h.append(f'<td width="25%" align="center" bgcolor="{bg}" style="padding:14px 8px;">')
            h.append(f'<span style="font-size:28px;font-weight:bold;color:{fg};{FONT}">{val}</span><br>')
            h.append(f'<span style="font-size:11px;color:#666;text-transform:uppercase;{FONT}">{label}</span>')
            h.append('</td>')
        h.append('</tr></table>')
        h.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;margin-bottom:14px;">')
        h.append(f'<tr><td align="center" style="font-size:14px;color:#555;{FONT}">')
        h.append(f'Pass rate: <b style="color:#0071c5;font-size:20px;">{summary["pass_rate"]:.1f}%</b>')
        h.append('</td></tr></table>')

    # ── Partition and stack-level summary cards ──
    h.append('<tr><td style="padding:24px 32px 16px;">')
    append_summary_cards('SOC PARTITION LEVEL STATUS', soc_partition_summary)
    append_summary_cards('SOC LEVEL STATUS', soc_level_summary)
    append_summary_cards('PARTITION LEVEL STATUS', partition_summary)
    append_summary_cards('STACK LEVEL STATUS', stack_summary)
    h.append('</td></tr>')

    # ── Historical stack trends (horizontal, 3-across) ──
    stack_history = load_stack_history(STACK_HISTORY_FILE)
    stack_level_history = load_stack_history(STACK_LEVEL_HISTORY_FILE)

    def render_stack_trend_svg(history_data, stack_key, title):
        rows = history_data.get(stack_key, [])
        if not rows:
            return (
                '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'<tr><td align="center" style="padding:0 0 4px;font-size:12px;font-weight:bold;color:#333;{FONT}">{title}</td></tr>'
                '<tr><td align="center" style="padding:18px 6px;font-size:12px;color:#999;">'
                'No historical data available'
                '</td></tr></table>'
            )

        points = []
        seen_dates = set()
        for r in rows:
            try:
                p = float(r.get('pass_pct', 0) or 0)
                f_ = float(r.get('fail_pct', 0) or 0)
                m = float(r.get('missing_pct', 0) or 0)
            except ValueError:
                p, f_, m = 0.0, 0.0, 0.0
            date_str = r.get('report_timestamp', '')[:10]  # YYYY-MM-DD
            # Skip duplicate dates — keep only the last entry per date
            if date_str in seen_dates:
                # Replace previous entry for same date with this one (later run)
                points = [pt for pt in points if pt[0] != date_str]
            seen_dates.add(date_str)
            # Convert date to work-week label (ww##p#) with day of week
            try:
                from datetime import date as _date
                parts = date_str.split('-')
                d = _date(int(parts[0]), int(parts[1]), int(parts[2]))
                ww = d.isocalendar()[1]
                dow = d.isocalendar()[2]  # 1=Monday ... 7=Sunday
                ww_label = f"ww{ww:02d}p{dow}"
            except (ValueError, IndexError):
                ww_label = date_str
            points.append((date_str, p, f_, m, ww_label))

        width, height = 330, 200
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
        svg.append('<style>polyline { transition: stroke-width 0.15s; } polyline:hover { stroke-width: 4; }</style>')

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

        # Data point circles with hover tooltips + larger invisible hover targets
        for i, pt in enumerate(points):
            cx = x_at(i)
            date_label = f"{pt[4]} ({pt[0]})"
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[1]):.1f}" r="2.5" fill="#2e7d32"/>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[1]):.1f}" r="10" fill="transparent" stroke="none"><title>{date_label} — PASS: {pt[1]:.1f}%</title></circle>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[2]):.1f}" r="2.5" fill="#c62828"/>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[2]):.1f}" r="10" fill="transparent" stroke="none"><title>{date_label} — FAIL: {pt[2]:.1f}%</title></circle>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[3]):.1f}" r="2" fill="#e65100"/>')
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_at(pt[3]):.1f}" r="10" fill="transparent" stroke="none"><title>{date_label} — MISSING: {pt[3]:.1f}%</title></circle>')

        # X-axis labels — all points, rotated 45 degrees, work-week format
        label_y = top + plot_h + 10
        for i, pt in enumerate(points):
            cx = x_at(i)
            svg.append(f'<text x="{cx:.1f}" y="{label_y}" text-anchor="end" font-size="8" fill="#666" font-family="Arial,sans-serif" transform="rotate(-50 {cx:.1f} {label_y})">{pt[4]}</text>')

        svg.append('</svg>')
        svg.append('</td></tr>')
        # Legend
        svg.append(f'<tr><td align="center" style="padding:2px 0 4px;font-size:10px;color:#555;{FONT}">')
        svg.append(f'<span style="color:#2e7d32;font-weight:bold;">&#9679; PASS {latest[1]:.1f}%</span> &nbsp;')
        svg.append(f'<span style="color:#c62828;font-weight:bold;">&#9679; FAIL {latest[2]:.1f}%</span> &nbsp;')
        svg.append(f'<span style="color:#e65100;font-weight:bold;">&#9679; MISS {latest[3]:.1f}%</span>')
        svg.append('</td></tr></table>')
        return ''.join(svg)

    # ── SOC historical trends (standalone — shown first) ──
    # Left: SOC Partition Level (has data). Right: SOC Level (no data yet).
    h.append('<tr><td style="padding:8px 20px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">SOC HISTORICAL TRENDS</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')
    h.append('<td width="50%" valign="top" bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:8px 6px;">')
    h.append(render_stack_trend_svg(stack_history, SOC_BUCKET, 'SOC Partition Level'))
    h.append('</td>')
    h.append('<td width="10"></td>')
    h.append('<td width="50%" valign="top" bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:8px 6px;">')
    h.append(render_stack_trend_svg(stack_level_history, SOC_BUCKET, 'SOC Level'))
    h.append('</td>')
    h.append('</tr></table>')
    h.append('</td></tr>')

    h.append('<tr><td style="padding:0 20px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">PARTITION LEVEL HISTORICAL TRENDS</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')

    # Partition-level trends: one column per bucket in STACK_BUCKETS
    # (mc/uio/d2d/uioe). Column widths adapt so the row always sums to 100%.
    partition_stacks = [(k, STACK_LABELS[k]) for k in STACK_BUCKETS]
    partition_col_width = f"{int(100 / max(1, len(partition_stacks)))}%"
    for i, (stack_key, stack_title) in enumerate(partition_stacks):
        if i > 0:
            h.append('<td width="10"></td>')
        h.append(f'<td width="{partition_col_width}" valign="top" bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:8px 6px;">')
        h.append(render_stack_trend_svg(stack_history, stack_key, stack_title))
        h.append('</td>')

    h.append('</tr></table>')
    h.append('</td></tr>')

    # ── Stack-level historical trends for memstack/d2d1/uio_a_0/uio_1 ──
    h.append('<tr><td style="padding:0 20px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">STACK LEVEL HISTORICAL TRENDS</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')

    stack_level_stacks = [(k, STACK_LABELS[k]) for k in STACK_BUCKETS]
    stack_level_col_width = f"{int(100 / max(1, len(stack_level_stacks)))}%"
    for i, (stack_key, stack_title) in enumerate(stack_level_stacks):
        if i > 0:
            h.append('<td width="10"></td>')
        h.append(f'<td width="{stack_level_col_width}" valign="top" bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:8px 6px;">')
        h.append(render_stack_trend_svg(stack_level_history, stack_key, stack_title))
        h.append('</td>')

    h.append('</tr></table>')
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
    # Rows for partitions in the `uioestack` bucket are pulled into their own
    # sub-table so they are not visually mixed with the main UIO stack rows.
    # SOC model tables are shown first.
    ordered_models = (
        [m for m in models_seen if get_model_type(m) == 'soc']
        + [m for m in models_seen if get_model_type(m) != 'soc']
    )
    for model in ordered_models:
        model_rows = rows_by_model[model]
        main_rows = [r for r in model_rows if get_partition_type(r.get('partition', '')) != 'uioe']
        uioe_rows = [r for r in model_rows if get_partition_type(r.get('partition', '')) == 'uioe']

        # Sort rows so Stack Level appears before Partition Level, then by
        # region/test for stable, predictable ordering within each group.
        def _scope_sort_key(row):
            scope_val = row.get('scope') or get_scope(row.get('partition', ''), row.get('test_type', ''))
            scope_rank = 0 if scope_val == 'Stack Level' else 1
            return (scope_rank, row.get('partition', ''), row.get('test_type', ''))

        for sub_label, sub_rows in (('', main_rows), (STACK_LABELS['uioe'], uioe_rows)):
            if not sub_rows:
                continue
            sub_rows = sorted(sub_rows, key=_scope_sort_key)
            m_pass = sum(1 for r in sub_rows if r['status'] == 'PASS')
            m_fail = sum(1 for r in sub_rows if r['status'] == 'FAIL')
            m_miss = sum(1 for r in sub_rows if r['status'] == 'MISSING')

            heading = html_mod.escape(model)
            if sub_label:
                heading = f"{heading} &mdash; {html_mod.escape(sub_label)}"

            h.append('<tr><td style="padding:16px 32px 8px;">')
            h.append(f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
            h.append(f'<span style="font-size:15px;font-weight:bold;color:#333;{FONT}">{heading}</span> ')
            h.append(f'<span style="font-size:12px;color:#888;{FONT}">')
            h.append(f'&mdash; {len(sub_rows)} tests: ')
            h.append(f'<span style="color:#2e7d32;">{m_pass} pass</span>, ')
            h.append(f'<span style="color:#c62828;">{m_fail} fail</span>, ')
            h.append(f'<span style="color:#e65100;">{m_miss} missing</span>')
            h.append('</span></td></tr></table>')
            h.append('</td></tr>')

            h.append('<tr><td style="padding:0 32px 16px;">')
            h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e0e0e0;">')
            # Table header
            h.append(f'<tr bgcolor="#f0f0f0">')
            for col in ['Scope', 'Region name', 'Item', 'Test', 'Status', 'Owner']:
                h.append(f'<td style="padding:7px 10px;font-size:12px;font-weight:bold;color:#555;border-bottom:2px solid #d0d0d0;{FONT}">{col}</td>')
            h.append('</tr>')
            # Table rows
            for i, r in enumerate(sub_rows):
                bg = '#ffffff' if i % 2 == 0 else '#fafafa'
                st = r['status']
                pvim = html_mod.escape(r.get('pvim_item', ''))
                scope_val = html_mod.escape(r.get('scope') or get_scope(r.get('partition', ''), r.get('test_type', '')))
                h.append(f'<tr bgcolor="{bg}">')
                h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;{FONT}border-bottom:1px solid #eee;">{scope_val}</td>')
                h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;{MONO}border-bottom:1px solid #eee;">{html_mod.escape(r["partition"])}</td>')
                h.append(f'<td style="padding:6px 10px;font-size:12px;color:#333;white-space:nowrap;{MONO}border-bottom:1px solid #eee;">{pvim}</td>')
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
    """Find the most recent general_report CSV from a different day than the current one."""
    import re
    current_name = os.path.basename(current_report_path)
    m = re.search(r'(\d{8})_\d{6}', current_name)
    current_date = m.group(1) if m else None
    pattern = os.path.join(REPORTS_DIR, "scan_general_report_*.csv")
    reports = sorted(glob.glob(pattern))
    # Filter out the current report and all reports from the same day
    previous = [
        r for r in reports
        if os.path.basename(r) != current_name
        and (current_date is None or not re.search(r'(\d{8})_\d{6}', os.path.basename(r)) or re.search(r'(\d{8})_\d{6}', os.path.basename(r)).group(1) != current_date)
    ]
    return previous[-1] if previous else None


def _report_date_str(report_path):
    """Extract date from report filename like scan_general_report_20260407_151132.csv -> '2026-04-07 15:11:32'."""
    basename = os.path.basename(report_path)
    import re
    m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', basename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
    return "unknown"


def load_report_as_dict(report_path):
    """Load a general report CSV and return a dict keyed by (model_type, partition, test_type) -> row.

    Uses model *category* (mc/uio/d2d) instead of the full model name so that
    week-over-week model rotations (e.g. ww26 -> ww27) still align entries for
    status-change detection."""
    result = {}
    with open(report_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (get_model_type(row.get('model', '')), row['partition'], row['test_type'])
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


def _new_border_color(status):
    """Left-border color for a newly-appeared test row (no prior baseline)."""
    return {
        'PASS': '#4caf50',
        'FAIL': '#e53935',
        'MISSING': '#fb8c00',
    }.get(status, '#9e9e9e')


def _new_bg_color(status):
    """Background color for a newly-appeared test row."""
    return {
        'PASS': '#e8f5e9',
        'FAIL': '#ffebee',
        'MISSING': '#fff3e0',
    }.get(status, '#f5f5f5')


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

    # Compute summary stats

    def compute_summary(all_rows):
        total = len(all_rows)
        total_pass = sum(1 for r in all_rows if r['status'] == 'PASS')
        total_fail = sum(1 for r in all_rows if r['status'] == 'FAIL')
        total_missing = sum(1 for r in all_rows if r['status'] == 'MISSING')
        pass_rate = (total_pass / total * 100) if total else 0.0
        return {
            'total': total,
            'pass': total_pass,
            'fail': total_fail,
            'missing': total_missing,
            'pass_rate': pass_rate,
        }

    partition_rows = [r for r in rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Partition Level']
    stack_rows = [r for r in rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Stack Level']
    soc_rows = [r for r in rows if get_model_type(r.get('model', '')) == 'soc']
    soc_partition_rows = [r for r in soc_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) != 'Stack Level']
    soc_level_rows = [r for r in soc_rows if get_scope(r.get('partition', ''), r.get('test_type', '')) == 'Stack Level']
    partition_summary = compute_summary(partition_rows)
    stack_summary = compute_summary(stack_rows)
    soc_partition_summary = compute_summary(soc_partition_rows)
    soc_level_summary = compute_summary(soc_level_rows)

    report_date = _report_date_str(report_path)
    prev_date = _report_date_str(prev_report_path) if prev_report_path else None
    generated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Collect status changes
    owner_changes = {}
    owner_new_rows = {}
    if prev_data:
        current_data = {}
        for row in rows:
            key = (get_model_type(row.get('model', '')), row['partition'], row['test_type'])
            current_data[key] = row
        for key, cur_row in current_data.items():
            prev_row = prev_data.get(key)
            if prev_row is None:
                # No baseline entry — this is a newly-tracked test.
                owner = cur_row.get('owner', 'UNKNOWN')
                owner_new_rows.setdefault(owner, []).append(cur_row)
                continue
            if prev_row['status'] != cur_row['status']:
                owner = cur_row.get('owner', 'UNKNOWN')
                if owner not in owner_changes:
                    owner_changes[owner] = []
                owner_changes[owner].append((cur_row, prev_row['status']))

    total_changes = sum(len(v) for v in owner_changes.values())
    total_new = sum(len(v) for v in owner_new_rows.values())

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
    h.append('<table width="760" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
             'style="border:1px solid #dddddd;">')

    # ── Header banner ──
    h.append('<tr><td bgcolor="#0071c5" style="padding:24px 32px;">')
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="{FONT}">')
    h.append(f'<span style="color:#ffffff;font-size:22px;font-weight:bold;{FONT}">SCAN EXECUTIVE SUMMARY</span><br>')
    h.append(f'<span style="color:#b3d9f2;font-size:14px;{FONT}">SCAN L2 Regression Report</span>')
    h.append(f'</td><td align="right" valign="top" style="{FONT}">')
    h.append(f'<a href="{GITHUB_PAGES_INDEX}" style="color:#ffffff;font-size:13px;text-decoration:underline;{FONT}">Report History</a>')
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
        elif 'nio_soc' in model:
            cat_label, cat_color = 'SOC', '#b5651d'
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

    def append_overall_summary_cards(title, summary):
        h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tr><td style="{FONT}">')
        h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;text-transform:uppercase;{FONT}">{title}</span>')
        h.append('</td></tr></table>')
        h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;"><tr>')
        stats = [
            (str(summary['total']), 'TOTAL', '#f0f7ff', '#0071c5'),
            (str(summary['pass']), 'PASS', '#e8f5e9', '#2e7d32'),
            (str(summary['fail']), 'FAIL', '#ffebee', '#c62828'),
            (str(summary['missing']), 'MISSING', '#fff3e0', '#e65100'),
        ]
        for i, (val, label, bg, fg) in enumerate(stats):
            if i > 0:
                h.append('<td width="8"></td>')
            h.append(f'<td width="25%" align="center" bgcolor="{bg}" style="padding:14px 8px;">')
            h.append(f'<span style="font-size:26px;font-weight:bold;color:{fg};{FONT}">{val}</span><br>')
            h.append(f'<span style="font-size:11px;color:#666;text-transform:uppercase;{FONT}">{label}</span>')
            h.append('</td>')
        h.append('</tr></table>')
        h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;margin-bottom:14px;">')
        h.append(f'<tr><td align="center" style="font-size:14px;color:#555;{FONT}">')
        h.append(f'Pass rate: <b style="color:#0071c5;font-size:20px;">{summary["pass_rate"]:.1f}%</b>')
        h.append('</td></tr></table>')

    # ── Overall status sections ──
    h.append('<tr><td style="padding:24px 32px 16px;">')
    append_overall_summary_cards('OVERALL PARTITION LEVEL STATUS', partition_summary)
    append_overall_summary_cards('OVERALL STACK LEVEL STATUS', stack_summary)
    append_overall_summary_cards('OVERALL SOC PARTITION LEVEL STATUS', soc_partition_summary)
    append_overall_summary_cards('OVERALL SOC LEVEL STATUS', soc_level_summary)
    h.append('</td></tr>')

    # ── Historical trends link ──
    h.append('<tr><td style="padding:8px 20px 16px;">')
    h.append(f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;"><tr><td style="{FONT}">')
    h.append(f'<span style="font-size:16px;font-weight:bold;color:#333;{FONT}">HISTORICAL TRENDS</span>')
    h.append('</td></tr></table>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>')
    h.append('<td bgcolor="#fbfbfb" style="border:1px solid #e6e6e6;padding:12px;">')
    h.append(f'<span style="font-size:13px;color:#555;{FONT}">View partition and stack trend charts on GitHub Pages: </span>')
    h.append(f'<a href="{report_html_url}" style="color:#0071c5;text-decoration:none;font-size:13px;{FONT}">Open latest general report trends</a>')
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
    lines.append("  OVERALL PARTITION LEVEL STATUS")
    lines.append("=" * 60)
    lines.append(
        f"  Total: {partition_summary['total']} tests | "
        f"{partition_summary['pass']} PASS | {partition_summary['fail']} FAIL | "
        f"{partition_summary['missing']} MISSING | {partition_summary['pass_rate']:.1f}% pass rate"
    )
    lines.append("")
    lines.append("=" * 60)
    lines.append("  OVERALL STACK LEVEL STATUS")
    lines.append("=" * 60)
    lines.append(
        f"  Total: {stack_summary['total']} tests | "
        f"{stack_summary['pass']} PASS | {stack_summary['fail']} FAIL | "
        f"{stack_summary['missing']} MISSING | {stack_summary['pass_rate']:.1f}% pass rate"
    )
    lines.append("")
    lines.append("=" * 60)
    lines.append("  OVERALL SOC PARTITION LEVEL STATUS")
    lines.append("=" * 60)
    lines.append(
        f"  Total: {soc_partition_summary['total']} tests | "
        f"{soc_partition_summary['pass']} PASS | {soc_partition_summary['fail']} FAIL | "
        f"{soc_partition_summary['missing']} MISSING | {soc_partition_summary['pass_rate']:.1f}% pass rate"
    )
    lines.append("")
    lines.append("=" * 60)
    lines.append("  OVERALL SOC LEVEL STATUS")
    lines.append("=" * 60)
    lines.append(
        f"  Total: {soc_level_summary['total']} tests | "
        f"{soc_level_summary['pass']} PASS | {soc_level_summary['fail']} FAIL | "
        f"{soc_level_summary['missing']} MISSING | {soc_level_summary['pass_rate']:.1f}% pass rate"
    )
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
    summary_file = os.path.join(REPORTS_DIR, f"scan_executive_summary_{TIMESTAMP}.html")
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(html_text)
    print(f"\nExecutive summary saved to: {summary_file}")


def generate_index_html():
    """Generate scan_index.html in scan_reports/ that links to all SCAN report HTML files."""
    import html as html_mod
    import re

    FONT = "font-family:Arial,Helvetica,sans-serif;"

    # Discover only git-tracked general report HTML files
    try:
        result = subprocess.run(
            ['git', 'ls-files', '--', 'scan_reports/scan_general_report_*.html'],
            capture_output=True, text=True, cwd=ROOT_DIR
        )
        tracked_names = {os.path.basename(f.strip()) for f in result.stdout.splitlines() if f.strip()}
        html_reports = sorted(
            [p for p in glob.glob(os.path.join(REPORTS_DIR, "scan_general_report_*.html"))
             if os.path.basename(p) in tracked_names],
            reverse=True
        )
    except Exception:
        html_reports = sorted(glob.glob(os.path.join(REPORTS_DIR, "scan_general_report_*.html")), reverse=True)

    if not html_reports:
        print("No committed HTML general reports found. Skipping index generation.")
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
            ww_raw = basename  # e.g. scan_general_report_20260409_144523.html
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
        summary_name = basename.replace('scan_general_report_', 'scan_executive_summary_')
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

    index_path = os.path.join(REPORTS_DIR, "scan_index.html")
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(h))
    print(f"Index page saved to: {index_path}")


def git_commit_and_push():
    """Stage reports/ and weekly_report/, commit, and push to GitHub."""
    import subprocess

    print("\n--- Git Commit & Push ---")
    try:
        subprocess.run(["git", "add", "scan_reports/", "weekly_report/"], cwd=ROOT_DIR, check=True)
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
    categories = ['nio_mc', 'nio_uio', 'nio_d2d', 'nio_soc']
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

    # Step 5: Commit and push reports to GitHub (skippable via SKIP_GIT_PUSH env var)
    if os.environ.get('SKIP_GIT_PUSH', '').strip().lower() in ('1', 'true', 'yes'):
        print("\n--- Git Commit & Push --- SKIPPED (SKIP_GIT_PUSH env var set)")
    else:
        git_commit_and_push()


if __name__ == "__main__":
    main()
