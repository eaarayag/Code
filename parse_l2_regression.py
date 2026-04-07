# Standard library imports for CSV writing, regex parsing, CLI args, and path handling
import csv
import os
import re
import sys
from pathlib import Path

def parse_l2_regression_report(file_path, output_csv='regression_results.csv'):
    """
    Parse L2_regression.rpt file and extract test information to CSV
    
    Args:
        file_path (str): Path to the L2_regression.rpt file
        output_csv (str): Output CSV file name
    """
    
    test_results = []  # List to hold parsed test result dictionaries
    
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
                'icl_verify_dft',
                'ijtag_basic_tap_tests_continuity',
                'ijtag_basic_tap_tests_reset',
                'ssn_continuity',
                'stuckat_edt_bypass_low_internal_loopback',
                'stuckat_edt_edt_low_internal_loopback',
                'on_chip_compare',
            )
            if any(test_name.endswith(s) for s in excluded_suffixes):
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

def build_report_path(model_name):
    """
    Build the full path to the L2_regression.rpt file from the model name.

    Args:
        model_name (str): Full model name, e.g. 'nio_mc-a0-26ww14a', 'nio_uio-a0-26ww13a', 'nio_d2d-a0-26ww13a'

    Returns:
        str: Full path to the L2_regression.rpt file, or None on error
    """
    base_path = "/nfs/site/disks/nwp_vmgr_testresults_006/NWP_DFT_Regressions"

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
        print(f"Expected model name starting with: {', '.join(subfolder_map.keys())}")
        _print_available_models(base_path)
        return None

    # Verify the model directory exists in base_path
    model_dir = f"{base_path}/{model_name}"
    if not os.path.isdir(model_dir):
        print(f"Error: Model directory '{model_name}' does not exist.")
        _print_available_models(base_path)
        return None

    return f"{base_path}/{model_name}/{subfolder}/L2_regression.list.latest/L2_regression.rpt"


def _print_available_models(base_path):
    """List available model directories under base_path."""
    try:
        entries = sorted(os.listdir(base_path))
        dirs = [e for e in entries if os.path.isdir(os.path.join(base_path, e))]
        if dirs:
            print("\nAvailable models:")
            for d in dirs:
                print(f"  {d}")
        else:
            print(f"\nNo model directories found in {base_path}")
    except OSError as e:
        print(f"\nCould not list available models: {e}")


def discover_models(base_path):
    """
    Auto-discover all valid model directories under base_path.

    Returns:
        list: Sorted list of model directory names matching known prefixes.
    """
    subfolder_prefixes = ('nio_mc', 'nio_uio', 'nio_d2d')
    try:
        entries = sorted(os.listdir(base_path))
        models = [
            e for e in entries
            if os.path.isdir(os.path.join(base_path, e))
            and any(e.startswith(p) for p in subfolder_prefixes)
        ]
        return models
    except OSError as e:
        print(f"Error: Could not list models in {base_path}: {e}")
        return []


def main():
    base_path = "/nfs/site/disks/nwp_vmgr_testresults_006/NWP_DFT_Regressions"

    # Auto-discover all available models
    print(f"Discovering models in {base_path}...")
    model_names = discover_models(base_path)

    if not model_names:
        print("No models found matching known prefixes (nio_mc, nio_uio, nio_d2d).")
        _print_available_models(base_path)
        return

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

    for model_name, input_file in validated.items():
        print(f"\n{'='*80}")
        print(f"Processing model: {model_name}")
        print(f"{'='*80}")

        output_file = f"{model_name}_regression_results.csv"

        print(f"Report path: {input_file}")

        parse_l2_regression_report(input_file, output_file)

    print(f"\n{'='*80}")
    print(f"Done. Processed {len(validated)} model(s), skipped {len(skipped)}.")

# Entry point: only run main() when executed directly (not when imported)
if __name__ == "__main__":
    main()