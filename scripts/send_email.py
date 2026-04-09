# send_email.py
# Send an email using settings from config/email_config.ini.
# Usage:
#   python send_email.py --subject "Subject" --body "Body text"
#   python send_email.py --subject "Subject" --body-file path/to/file.txt
#   python send_email.py --subject "Subject" --body "Body" --to extra@intel.com --cc someone@intel.com
#   python send_email.py --subject "Subject" --body "Body" --attach report.csv

import argparse
import configparser
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(ROOT_DIR, "config", "email_config.ini")


def load_config(config_path=CONFIG_FILE):
    """Load email configuration from INI file."""
    if not os.path.isfile(config_path):
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")
    return config


def parse_address_list(raw):
    """Split a comma-separated address string into a list, filtering blanks."""
    if not raw:
        return []
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def send_email(subject, body, to_extra=None, cc_extra=None, attachments=None, config_path=CONFIG_FILE, dry_run=False):
    """
    Send an email using the settings in email_config.ini.

    Args:
        subject:      Email subject line.
        body:         Plain-text email body.
        to_extra:     Additional To addresses (list of strings).
        cc_extra:     Additional CC addresses (list of strings).
        attachments:  List of file paths to attach.
        config_path:  Path to the INI config file.
        dry_run:      If True, print email details without sending.
    """
    config = load_config(config_path)

    smtp_server = config.get("smtp", "server")
    smtp_port = config.getint("smtp", "port")
    sender_address = config.get("sender", "address")
    display_name = config.get("sender", "display_name")

    to_addrs = parse_address_list(config.get("recipients", "to", fallback=""))
    cc_addrs = parse_address_list(config.get("recipients", "cc", fallback=""))

    if to_extra:
        to_addrs.extend(to_extra)
    if cc_extra:
        cc_addrs.extend(cc_extra)

    if not to_addrs:
        print("Error: No recipients specified (check config or --to flag).")
        sys.exit(1)

    msg = MIMEMultipart()
    msg["From"] = f"{display_name} <{sender_address}>"
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject

    # Detect HTML content and send accordingly
    if body.strip().startswith('<!DOCTYPE') or body.strip().startswith('<html'):
        msg.attach(MIMEText(body, "html"))
    else:
        import html
        html_body = (
            '<html><body>'
            '<pre style="font-family: Consolas, Courier New, monospace; font-size: 10pt;">'
            + html.escape(body)
            + '</pre></body></html>'
        )
        msg.attach(MIMEText(html_body, "html"))

    # Attach files
    for filepath in (attachments or []):
        if not os.path.isfile(filepath):
            print(f"Warning: Attachment not found, skipping: {filepath}")
            continue
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(filepath)}")
        msg.attach(part)

    all_recipients = to_addrs + cc_addrs

    if dry_run:
        print("=== DRY RUN — email not sent ===")
        print(f"  SMTP:    {smtp_server}:{smtp_port}")
        print(f"  From:    {msg['From']}")
        print(f"  To:      {msg['To']}")
        if cc_addrs:
            print(f"  Cc:      {msg['Cc']}")
        print(f"  Subject: {subject}")
        print(f"  Attach:  {[os.path.basename(a) for a in (attachments or []) if os.path.isfile(a)] or 'none'}")
        print(f"  Body:")
        print(f"    {body[:500]}{'...' if len(body) > 500 else ''}")
        print("================================")
        return

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.sendmail(sender_address, all_recipients, msg.as_string())
        print(f"Email sent to: {', '.join(all_recipients)}")
    except Exception as e:
        print(f"Error sending email: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Send an email using config/email_config.ini")
    parser.add_argument("--subject", required=True, help="Email subject line")
    body_group = parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Email body text")
    body_group.add_argument("--body-file", help="Path to a file whose contents become the email body")
    parser.add_argument("--to", nargs="*", default=[], help="Additional To recipients")
    parser.add_argument("--cc", nargs="*", default=[], help="Additional CC recipients")
    parser.add_argument("--attach", nargs="*", default=[], help="File paths to attach")
    parser.add_argument("--config", default=CONFIG_FILE, help="Path to email config INI file")
    parser.add_argument("--dry-run", action="store_true", help="Print email details without sending")

    args = parser.parse_args()

    if args.body_file:
        if not os.path.isfile(args.body_file):
            print(f"Error: Body file not found: {args.body_file}")
            sys.exit(1)
        with open(args.body_file, "r", encoding="utf-8") as f:
            body = f.read()
    else:
        body = args.body

    send_email(
        subject=args.subject,
        body=body,
        to_extra=args.to,
        cc_extra=args.cc,
        attachments=args.attach,
        config_path=args.config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
