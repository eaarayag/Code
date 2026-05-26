#!/usr/bin/env python3
"""Send the test_status_tracker.html to Emmanuel with an executive summary body.

Reads the embedded CSV in reports/test_status_tracker.html, computes a
high-level executive summary grouped by Model classification (UIO/D2D/MC/SOC),
and sends an HTML email with the tracker attached. Recipient is Emmanuel only
(overrides config/email_config.ini).
"""

import argparse
import configparser
import csv
import io
import os
import re
import smtplib
import sys
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "email_config.ini")
TRACKER_PATH = os.path.join(ROOT_DIR, "reports", "test_status_tracker.html")

EMMANUEL_EMAIL = "emmanuel.a.araya.gamboa@intel.com"
MARIO_EMAIL = "mario.alpizar.castro@intel.com"
MAURICIO_EMAIL = "mauricio.segura.zuniga@intel.com"
RECIPIENTS = [EMMANUEL_EMAIL, MARIO_EMAIL, MAURICIO_EMAIL]

MODEL_OVERRIDES_SOC = {"parscfmemmisc", "pargpiod2d"}
MODEL_OVERRIDES_UIO = {"parmiofblprxfcrarbmux", "parmiofblptx"}


def classify_model(partition: str) -> str:
    p = (partition or "").lower()
    if p in MODEL_OVERRIDES_SOC:
        return "SOC"
    if p in MODEL_OVERRIDES_UIO:
        return "UIO"
    if "uio" in p:
        return "UIO"
    if "d2d" in p:
        return "D2D"
    if "mc" in p or "mem" in p:
        return "MC"
    return "SOC"


def extract_csv(html_path: str) -> str:
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const csvData = `([^`]+)`", html)
    if not m:
        raise RuntimeError("Could not locate csvData in tracker HTML")
    return m.group(1)


def parse_rows(csv_text: str):
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    rows = []
    for r in reader:
        r["model_class"] = classify_model(r["partition"])
        rows.append(r)
    return rows


def build_summary(rows):
    """Return dict of stats."""
    total = len(rows)
    by_status = defaultdict(int)
    by_model = defaultdict(lambda: defaultdict(int))
    partitions_by_model = defaultdict(set)
    for r in rows:
        s = (r.get("status") or "").upper().strip() or "UNKNOWN"
        m = r["model_class"]
        by_status[s] += 1
        by_model[m][s] += 1
        by_model[m]["TOTAL"] += 1
        partitions_by_model[m].add(r["partition"])
    return {
        "total": total,
        "by_status": dict(by_status),
        "by_model": {k: dict(v) for k, v in by_model.items()},
        "partition_counts": {k: len(v) for k, v in partitions_by_model.items()},
    }


def status_color(s: str) -> str:
    return {
        "PASS": "#28a745",
        "FAIL": "#dc3545",
        "BLOCKED": "#dc3545",
        "MISSING": "#ffc107",
        "OPEN": "#ffc107",
    }.get(s, "#6c757d")


def render_html(summary, generated_at):
    total = summary["total"]
    by_status = summary["by_status"]
    by_model = summary["by_model"]
    part_counts = summary["partition_counts"]

    pass_count = by_status.get("PASS", 0)
    fail_count = by_status.get("FAIL", 0)
    blocked_count = by_status.get("BLOCKED", 0)
    missing_count = by_status.get("MISSING", 0)
    open_count = by_status.get("OPEN", 0)
    pass_rate = (pass_count / total * 100) if total else 0

    status_order = ["PASS", "FAIL", "BLOCKED", "OPEN"]
    model_order = ["SOC", "D2D", "MC", "UIO"]

    status_chips = "".join(
        f'<span style="display:inline-block;background:{status_color(s)};color:#fff;'
        f'padding:4px 10px;border-radius:12px;margin:2px;font-size:12px;font-weight:600;">'
        f"{s}: {by_status.get(s, 0)}</span>"
        for s in status_order
        if by_status.get(s, 0)
    )

    cell_border = "border:1px solid #495057;"
    model_rows = ""
    for m in model_order:
        if m not in by_model:
            continue
        d = by_model[m]
        tot = d.get("TOTAL", 0)
        cells = "".join(
            f'<td style="{cell_border}padding:6px 10px;text-align:center;color:{status_color(s)};font-weight:600;">'
            f"{d.get(s, 0)}</td>"
            for s in status_order
        )
        model_rows += (
            f'<tr><td style="{cell_border}padding:6px 10px;font-weight:600;">{m}</td>'
            f'<td style="{cell_border}padding:6px 10px;text-align:center;">{part_counts.get(m, 0)}</td>'
            f"{cells}"
            f'<td style="{cell_border}padding:6px 10px;text-align:center;font-weight:600;">{tot}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Segoe UI,Arial,sans-serif;color:#212529;max-width:820px;margin:0 auto;padding:20px;">
  <h2 style="color:#0d6efd;border-bottom:3px solid #0d6efd;padding-bottom:8px;margin-bottom:8px;">
    NIO DFT L2 Status — Executive Summary
  </h2>
  <p style="color:#6c757d;margin:0 0 18px 0;font-size:13px;">Generated {generated_at}</p>

  <h3 style="color:#212529;margin-bottom:8px;">Status Distribution by Model</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;border:2px solid #495057;margin-bottom:18px;">
    <thead style="background:#0d6efd;color:#fff;">
      <tr>
        <th style="{cell_border}padding:8px 10px;text-align:left;">Model</th>
        <th style="{cell_border}padding:8px 10px;">Partitions</th>
        <th style="{cell_border}padding:8px 10px;">PASS</th>
        <th style="{cell_border}padding:8px 10px;">FAIL</th>
        <th style="{cell_border}padding:8px 10px;">BLOCKED</th>
        <th style="{cell_border}padding:8px 10px;">OPEN</th>
        <th style="{cell_border}padding:8px 10px;">Total</th>
      </tr>
    </thead>
    <tbody>{model_rows}</tbody>
  </table>

  <h3 style="color:#212529;margin-bottom:8px;">High-Level Status</h3>
  <ul style="font-size:14px;line-height:1.6;color:#212529;">
    <li><b>{pass_count}</b> test cases <span style="color:#28a745;font-weight:600;">passing</span> out of <b>{total}</b> total.</li>
    <li><b>{blocked_count}</b> <span style="color:#dc3545;font-weight:600;">BLOCKED</span> — most are NWP tests waiting on a model fix tracked by HSDES 14027646772 and a few partition-specific HSDES.</li>
    <li><b>{open_count}</b> <span style="color:#ffc107;font-weight:600;">OPEN (WIP)</span> — passing on the local model but not yet enabled in the regression flow.</li>
    <li><b>{fail_count}</b> <span style="color:#dc3545;font-weight:600;">FAIL</span> — under active debug.</li>
  </ul>

  <h3 style="color:#212529;margin-bottom:8px;">Notes</h3>
  <ul style="font-size:14px;line-height:1.6;color:#212529;">
    <li>Full per-test detail is attached as <code>test_status_tracker.html</code> and <code>test_status_tracker.csv</code> (raw data).</li>
    <li><b>Scope:</b> This report covers only the partitions owned by <b>Mario Alpizar</b>, <b>Emmanuel Araya</b>, and <b>Mauricio Segura</b>. Additional partitions owned by the <b>WIPRO team</b> are <u>not</u> included.</li>
  </ul>

  <h3 style="color:#212529;margin-bottom:8px;">HSDES by Model</h3>
  <table style="border-collapse:collapse;width:100%;font-size:13px;border:2px solid #495057;margin-bottom:18px;">
    <thead style="background:#0d6efd;color:#fff;">
      <tr>
        <th style="{cell_border}padding:8px 10px;text-align:left;">Model</th>
        <th style="{cell_border}padding:8px 10px;text-align:left;">HSDES</th>
        <th style="{cell_border}padding:8px 10px;text-align:left;">Partition(s)</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="{cell_border}padding:6px 10px;font-weight:600;" rowspan="1">SOC</td>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/14027646772">14027646772</a></td>
        <td style="{cell_border}padding:6px 10px;">parmioinf3e, parmioinf3w, parmioinf1w, parmioinf1e, pargpiod2d, parscfhsf, parscfmvf, parseinf1, parocs, paridfttop, parrclk, paroobip, pars3m</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;font-weight:600;" rowspan="2">D2D</td>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/22022076230">22022076230</a></td>
        <td style="{cell_border}padding:6px 10px;">pard2d1chnl (iJTAG)</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/14026768084">14026768084</a></td>
        <td style="{cell_border}padding:6px 10px;">pard2d1misc (iJTAG)</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;font-weight:600;" rowspan="1">MC</td>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/article/14027563987">14027563987</a></td>
        <td style="{cell_border}padding:6px 10px;">parmemdfi0</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;font-weight:600;" rowspan="4">UIO</td>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/22021933271">22021933271</a></td>
        <td style="{cell_border}padding:6px 10px;">parmiofblpvnpipeac_uio_0</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/14027742654">14027742654</a></td>
        <td style="{cell_border}padding:6px 10px;">parmioiommu_uio_0</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/14027016207">14027016207</a></td>
        <td style="{cell_border}padding:6px 10px;">parmioasf_uio_0 (iJTAG)</td>
      </tr>
      <tr>
        <td style="{cell_border}padding:6px 10px;"><a href="https://hsdes.intel.com/appstore/article-one/#/14027224680">14027224680</a></td>
        <td style="{cell_border}padding:6px 10px;">parmiocxltx_uio_0</td>
      </tr>
    </tbody>
  </table>

  <p style="font-size:12px;color:#6c757d;margin-top:24px;border-top:1px solid #dee2e6;padding-top:10px;">
    This is an automated message from the L2 Regression Agent.
  </p>
</body>
</html>
"""
    return html


def build_message(config, subject, body_html, attachment_paths, to_addr):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{config['sender']['display_name']} <{config['sender']['address']}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(body_html, "html"))

    for attachment_path in attachment_paths:
        filename = os.path.basename(attachment_path)
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filename}")
        msg.attach(part)
    return msg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--save-summary", help="Optional path to save the HTML body for inspection")
    args = parser.parse_args()

    if not os.path.isfile(TRACKER_PATH):
        print(f"Error: tracker not found: {TRACKER_PATH}", file=sys.stderr)
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)

    csv_text = extract_csv(TRACKER_PATH)
    rows = parse_rows(csv_text)
    summary = build_summary(rows)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    body_html = render_html(summary, generated_at)

    if args.save_summary:
        with open(args.save_summary, "w", encoding="utf-8") as f:
            f.write(body_html)
        print(f"Summary HTML saved to {args.save_summary}")

    # Write CSV alongside the tracker so it can be attached
    csv_path = os.path.join(ROOT_DIR, "reports", "test_status_tracker.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["partition", "model", "test_case", "status", "owner", "source"])
        for r in rows:
            writer.writerow([
                r.get("partition", ""),
                r.get("model_class", ""),
                r.get("test_type", ""),
                r.get("status", ""),
                r.get("owner", ""),
                r.get("model", ""),
            ])
    print(f"CSV written to {csv_path}")

    attachments = [TRACKER_PATH, csv_path]
    subject = f"NIO DFT L2 Test Status Tracker - {datetime.now().strftime('%Y-%m-%d')}"
    msg = build_message(config, subject, body_html, attachments, ", ".join(RECIPIENTS))

    print(f"\nFrom:    {msg['From']}")
    print(f"To:      {msg['To']}")
    print(f"Subject: {msg['Subject']}")
    print(f"Attachments: {', '.join(os.path.basename(a) for a in attachments)}")
    print(f"Total tests: {summary['total']}  |  Status: {summary['by_status']}")

    if args.dry_run:
        print("\n[DRY RUN] Email not sent.")
        return

    confirm = input("\nSend email? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted. Email not sent.")
        return

    server = config["smtp"]["server"]
    port = int(config["smtp"]["port"])
    print(f"\nConnecting to {server}:{port} ...")
    with smtplib.SMTP(server, port) as smtp:
        smtp.sendmail(config["sender"]["address"], RECIPIENTS, msg.as_string())
    print("Email sent successfully.")


if __name__ == "__main__":
    main()
