#!/usr/bin/env python3
"""Send L2 regression report email using config/email_config_scan.ini."""

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
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "email_config_scan.ini")


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    return config


def build_message(config, subject, body_html, attachments, to_override=None, cc_override=None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{config['sender']['display_name']} <{config['sender']['address']}>"
    msg["To"] = to_override if to_override is not None else config["recipients"]["to"]
    cc_value = cc_override if cc_override is not None else config["recipients"].get("cc", "")
    if cc_value:
        msg["Cc"] = cc_value

    msg.attach(MIMEText(body_html, "html"))

    for filepath in attachments:
        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filename}")
        msg.attach(part)

    return msg


def get_all_recipients(msg):
    recipients = [addr.strip() for addr in (msg["To"] or "").split(",") if addr.strip()]
    if msg.get("Cc"):
        recipients += [addr.strip() for addr in msg["Cc"].split(",") if addr.strip()]
    return recipients


def send(msg, config, dry_run=False):
    all_recipients = get_all_recipients(msg)
    print(f"\nFrom:    {msg['From']}")
    print(f"To:      {msg['To']}")
    if msg.get("Cc"):
        print(f"Cc:      {msg['Cc']}")
    print(f"Subject: {msg['Subject']}")
    print(f"Recipients: {', '.join(all_recipients)}")

    if dry_run:
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
        smtp.sendmail(config["sender"]["address"], all_recipients, msg.as_string())
    print("Email sent successfully.")


def main():
    parser = argparse.ArgumentParser(description="Send L2 regression report email")
    parser.add_argument("--subject", required=True, help="Email subject line")
    parser.add_argument("--body-file", required=True, help="Path to HTML file for email body")
    parser.add_argument("--attach", nargs="*", default=[], help="Files to attach")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--to", default=None,
                        help="Override the recipients 'To' list from the config (comma-separated).")
    parser.add_argument("--cc", default=None,
                        help="Override the recipients 'Cc' list from the config (comma-separated). "
                             "Pass an empty string to clear Cc.")
    args = parser.parse_args()

    body_path = os.path.abspath(args.body_file)
    if not os.path.isfile(body_path):
        print(f"Error: body file not found: {body_path}", file=sys.stderr)
        sys.exit(1)

    for att in args.attach:
        if not os.path.isfile(os.path.abspath(att)):
            print(f"Error: attachment not found: {att}", file=sys.stderr)
            sys.exit(1)

    with open(body_path, encoding="utf-8") as f:
        body_html = f.read()

    config = load_config()
    msg = build_message(
        config, args.subject, body_html,
        [os.path.abspath(a) for a in args.attach],
        to_override=args.to,
        cc_override=args.cc,
    )
    send(msg, config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
