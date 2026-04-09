# L2 Regressions Workspace

This workspace manages the NIO DFT L2 regression pipeline.

## Project Structure

- `scripts/` — Python scripts (parse, report, email)
- `config/` — Configuration files (email settings)
- `weekly_report/` — Downloaded per-model regression CSVs
- `reports/` — Generated reports and executive summaries

## Conventions

- All scripts live in `scripts/` and should be run from that directory.
- Scripts use `SCRIPT_DIR` / `ROOT_DIR` for path resolution — do not hardcode absolute Windows paths.
- Remote host: `sccc06381314.zsc24.intel.com`, user: `eaarayag`
- Three model categories: `nio_mc` (memstack), `nio_uio` (uio), `nio_d2d` (d2d)
