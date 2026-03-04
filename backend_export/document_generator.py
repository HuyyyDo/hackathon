from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
import json
import os
import smtplib
import ssl
import subprocess
import xml.etree.ElementTree as ET


OUTPUT_DIR = Path(__file__).resolve().parent / "generated"


@dataclass
class EmailResult:
    sent: bool
    detail: str


def _escape_ps_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def _send_via_outlook_desktop(
    target_email: str,
    subject: str,
    body: str,
    attachments: list[str] | None = None,
) -> EmailResult:
    attachments = attachments or []

    lines = [
        "$ErrorActionPreference='Stop'",
        "$outlook = New-Object -ComObject Outlook.Application",
        "$mail = $null",
        "for ($i = 0; $i -lt 5 -and -not $mail; $i++) {",
        "  try { $mail = $outlook.CreateItem(0) } catch { Start-Sleep -Milliseconds 700 }",
        "}",
        "if (-not $mail) { throw 'Unable to create Outlook mail item after retries.' }",
        f"$mail.To = '{_escape_ps_single_quoted(target_email)}'",
        f"$mail.Subject = '{_escape_ps_single_quoted(subject)}'",
        f"$mail.Body = '{_escape_ps_single_quoted(body)}'",
    ]

    for attachment_path in attachments:
        lines.append(f"$mail.Attachments.Add('{_escape_ps_single_quoted(str(attachment_path))}') | Out-Null")

    lines.extend(
        [
            "$sent = $false",
            "for ($i = 0; $i -lt 5 -and -not $sent; $i++) {",
            "  try { $mail.Send(); $sent = $true } catch { Start-Sleep -Milliseconds 700 }",
            "}",
            "if (-not $sent) { throw 'Unable to send Outlook email after retries.' }",
            "Write-Output 'OUTLOOK_SEND_OK'",
        ]
    )

    script = "; ".join(lines)

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Sta", "-Command", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except Exception as exc:
        return EmailResult(False, f"Outlook desktop send failed: {exc}")

    if result.returncode == 0 and "OUTLOOK_SEND_OK" in (result.stdout or ""):
        return EmailResult(True, f"Email sent to {target_email} via Outlook desktop")

    detail = (result.stderr or result.stdout or "Unknown Outlook desktop error").strip()
    return EmailResult(False, f"Outlook desktop send failed: {detail}")


def _resolve_smtp_host(configured_host: str | None, smtp_user: str | None) -> str | None:
    if configured_host:
        return configured_host
    if not smtp_user or "@" not in smtp_user:
        return None

    domain = smtp_user.split("@", 1)[1].strip().lower()
    if domain == "gmail.com":
        return "smtp.gmail.com"
    if domain in {"outlook.com", "hotmail.com", "live.com", "office365.com"}:
        return "smtp.office365.com"
    if domain == "effectiveai.net":
        return "smtp.hostedemail.com"
    return None


def send_direct_email(
    target_email: str,
    subject: str,
    body: str,
    attachments: list[str] | None = None,
) -> EmailResult:
    smtp_host = _resolve_smtp_host(os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"))
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "noreply@example.com")

    if not smtp_host or not smtp_user or not smtp_password:
        return EmailResult(False, "SMTP credentials not configured; direct email was not sent.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = target_email
    msg.set_content(body)

    for attachment_path in attachments or []:
        file_path = Path(attachment_path)
        if not file_path.exists() or not file_path.is_file():
            continue

        suffix = file_path.suffix.lower()
        if suffix in {".htm", ".html"}:
            maintype, subtype = "text", "html"
        elif suffix == ".xml":
            maintype, subtype = "application", "xml"
        else:
            maintype, subtype = "application", "octet-stream"

        msg.add_attachment(
            file_path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=file_path.name,
        )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return EmailResult(
            False,
            "SMTP authentication failed. Check SMTP_HOST, SMTP_USER/SMTP_PASSWORD, and provider SMTP auth settings.",
        )
    except Exception as exc:
        return EmailResult(False, f"SMTP send failed: {exc}")

    return EmailResult(True, f"Email sent to {target_email}")


def _timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _build_printable_html(form_data: dict, report_id: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>Teddy Bear Tracking Form - {report_id}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #111; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .meta {{ color: #555; margin-bottom: 18px; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 680px; }}
    th, td {{ border: 1px solid #ddd; text-align: left; padding: 10px; }}
    th {{ background: #f6f6f6; width: 220px; }}
  </style>
</head>
<body>
  <h1>Teddy Bear Tracking Form</h1>
  <div class=\"meta\">Report ID: {report_id}<br/>Generated: {datetime.now().isoformat(timespec='seconds')}</div>
  <table>
        <tr><th>Name</th><td>{form_data.get('name', 'N/A')}</td></tr>
    <tr><th>Recipient Age</th><td>{form_data['age']}</td></tr>
    <tr><th>Gender</th><td>{form_data['gender']}</td></tr>
    <tr><th>Recipient Type</th><td>{form_data['recipient_type']}</td></tr>
  </table>
</body>
</html>
"""


def create_teddy_bear_docs(form_data: dict) -> tuple[str, str]:
    out_dir = _ensure_output_dir()
    report_id = f"TB_{_timestamp_id()}"

    printable_path = out_dir / f"{report_id}_printable.html"
    xml_path = out_dir / f"{report_id}.xml"

    printable_path.write_text(_build_printable_html(form_data, report_id), encoding="utf-8")

    root = ET.Element("teddyBearTrackingForm")
    ET.SubElement(root, "reportId").text = report_id
    ET.SubElement(root, "generatedAt").text = datetime.now().isoformat(timespec="seconds")
    ET.SubElement(root, "name").text = str(form_data.get("name", ""))
    ET.SubElement(root, "age").text = str(form_data["age"])
    ET.SubElement(root, "gender").text = str(form_data["gender"])
    ET.SubElement(root, "recipientType").text = str(form_data["recipient_type"])

    tree = ET.ElementTree(root)
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    return str(printable_path), str(xml_path)


def _build_occurrence_html(form_data: dict, report_id: str) -> str:
        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Occurrence Report Form - {report_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 32px; color: #111; }}
        h1 {{ margin: 0 0 8px; font-size: 24px; }}
        .meta {{ color: #555; margin-bottom: 18px; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 900px; }}
        th, td {{ border: 1px solid #ddd; text-align: left; padding: 10px; vertical-align: top; }}
        th {{ background: #f6f6f6; width: 260px; }}
    </style>
</head>
<body>
    <h1>EMS Occurrence Report (Form 1)</h1>
    <div class=\"meta\">Report ID: {report_id}<br/>Generated: {datetime.now().isoformat(timespec='seconds')}</div>
    <table>
        <tr><th>Date</th><td>{form_data.get('date', 'N/A')}</td></tr>
        <tr><th>Time</th><td>{form_data.get('time', 'N/A')}</td></tr>
        <tr><th>Classification</th><td>{form_data.get('classification', 'N/A')}</td></tr>
        <tr><th>Occurrence Type</th><td>{form_data.get('occurrence_type', 'N/A')}</td></tr>
        <tr><th>Brief Description</th><td>{form_data.get('brief_description', 'N/A')}</td></tr>
        <tr><th>Requested By</th><td>{form_data.get('requested_by', 'N/A')}</td></tr>
        <tr><th>Report Creator</th><td>{form_data.get('report_creator', 'N/A')}</td></tr>
        <tr><th>Call Number</th><td>{form_data.get('call_number', 'N/A') or 'N/A'}</td></tr>
        <tr><th>Occurrence Reference</th><td>{form_data.get('occurrence_reference', 'N/A') or 'N/A'}</td></tr>
    </table>
</body>
</html>
"""


def create_occurrence_docs(form_data: dict) -> tuple[str, str]:
        out_dir = _ensure_output_dir()
        report_id = f"OCC_{_timestamp_id()}"

        printable_path = out_dir / f"{report_id}_printable.html"
        xml_path = out_dir / f"{report_id}.xml"

        printable_path.write_text(_build_occurrence_html(form_data, report_id), encoding="utf-8")

        root = ET.Element("occurrenceReport")
        ET.SubElement(root, "reportId").text = report_id
        ET.SubElement(root, "generatedAt").text = datetime.now().isoformat(timespec="seconds")

        for key, value in form_data.items():
                ET.SubElement(root, key).text = str(value)

        tree = ET.ElementTree(root)
        tree.write(xml_path, encoding="utf-8", xml_declaration=True)

        return str(printable_path), str(xml_path)


def _build_shift_report_html(form_data: dict, report_id: str) -> str:
        rows = "".join(
                [
                        f"<tr><th>{key.replace('_', ' ').title()}</th><td>{value}</td></tr>"
                        for key, value in form_data.items()
                ]
        )
        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Shift Report - {report_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 32px; color: #111; }}
        h1 {{ margin: 0 0 8px; font-size: 24px; }}
        .meta {{ color: #555; margin-bottom: 18px; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 900px; }}
        th, td {{ border: 1px solid #ddd; text-align: left; padding: 10px; vertical-align: top; }}
        th {{ background: #f6f6f6; width: 260px; }}
    </style>
</head>
<body>
    <h1>Paramedic Shift Report (Form 3)</h1>
    <div class=\"meta\">Report ID: {report_id}<br/>Generated: {datetime.now().isoformat(timespec='seconds')}</div>
    <table>{rows}</table>
</body>
</html>
"""


def create_shift_report_docs(form_data: dict) -> tuple[str, str]:
        out_dir = _ensure_output_dir()
        report_id = f"SHIFT_{_timestamp_id()}"

        printable_path = out_dir / f"{report_id}_printable.html"
        xml_path = out_dir / f"{report_id}.xml"

        printable_path.write_text(_build_shift_report_html(form_data, report_id), encoding="utf-8")

        root = ET.Element("shiftReport")
        ET.SubElement(root, "reportId").text = report_id
        ET.SubElement(root, "generatedAt").text = datetime.now().isoformat(timespec="seconds")

        for key, value in form_data.items():
                ET.SubElement(root, key).text = str(value)

        tree = ET.ElementTree(root)
        tree.write(xml_path, encoding="utf-8", xml_declaration=True)

        return str(printable_path), str(xml_path)


def _build_status_report_html(status_data: dict, report_id: str) -> str:
        rows_html = ""
        for row in status_data.get("rows", []):
                rows_html += (
                        "<tr>"
                        f"<td>{row.get('item_type', '')}</td>"
                        f"<td>{row.get('description', '')}</td>"
                        f"<td>{row.get('status', '')}</td>"
                        f"<td>{row.get('issues', '')}</td>"
                        f"<td>{row.get('notes', '')}</td>"
                        "</tr>"
                )

        summary = status_data.get("summary", {})
        summary_html = ""
        if summary:
                summary_html = (
                        "<div class=\"meta\">"
                        f"BAD items: {summary.get('bad_items', 0)}"
                        f" | BAD issues total: {summary.get('bad_issue_total', 0)}"
                        "</div>"
                )

        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Form 4 Status Report - {report_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 32px; color: #111; }}
        h1 {{ margin: 0 0 8px; font-size: 24px; }}
        .meta {{ color: #555; margin: 8px 0 18px; }}
        table {{ border-collapse: collapse; width: 100%; max-width: 980px; }}
        th, td {{ border: 1px solid #ddd; text-align: left; padding: 10px; vertical-align: top; }}
        th {{ background: #f6f6f6; }}
    </style>
</head>
<body>
    <h1>Paramedic Status Report (Form 4)</h1>
    <div class=\"meta\">Report ID: {report_id}<br/>Generated: {datetime.now().isoformat(timespec='seconds')}</div>
    {summary_html}
    <table>
        <thead>
            <tr><th>Item Type</th><th>Description</th><th>Status</th><th>Issues</th><th>Notes</th></tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</body>
</html>
"""


def create_status_report_docs(status_data: dict) -> tuple[str, str]:
        out_dir = _ensure_output_dir()
        report_id = f"STATUS_{_timestamp_id()}"

        printable_path = out_dir / f"{report_id}_printable.html"
        xml_path = out_dir / f"{report_id}.xml"

        printable_path.write_text(_build_status_report_html(status_data, report_id), encoding="utf-8")

        root = ET.Element("statusReport")
        ET.SubElement(root, "reportId").text = report_id
        ET.SubElement(root, "generatedAt").text = datetime.now().isoformat(timespec="seconds")
        ET.SubElement(root, "payloadJson").text = json.dumps(status_data, ensure_ascii=False)

        tree = ET.ElementTree(root)
        tree.write(xml_path, encoding="utf-8", xml_declaration=True)

        return str(printable_path), str(xml_path)


def email_target_address(printable_path: str, xml_path: str, target_email: str | None = None) -> EmailResult:
    smtp_host = _resolve_smtp_host(os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"))
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "noreply@example.com")
    recipient = target_email or os.getenv("TARGET_EMAIL")

    if not recipient:
        return EmailResult(False, "TARGET_EMAIL not set; skipped email send.")

    if not smtp_host or not smtp_user or not smtp_password:
        return EmailResult(False, "SMTP credentials not configured; artifacts generated locally.")

    msg = EmailMessage()
    msg["Subject"] = "Paramedic AI Form Submission"
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg.set_content("Attached are the print-ready form and XML payload for this submission.")

    printable_file = Path(printable_path)
    xml_file = Path(xml_path)

    msg.add_attachment(
        printable_file.read_bytes(),
        maintype="text",
        subtype="html",
        filename=printable_file.name,
    )
    msg.add_attachment(
        xml_file.read_bytes(),
        maintype="application",
        subtype="xml",
        filename=xml_file.name,
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return EmailResult(
            False,
            "SMTP authentication failed. Check SMTP_HOST, SMTP_USER/SMTP_PASSWORD, and provider SMTP auth settings.",
        )
    except Exception as exc:
        return EmailResult(False, f"SMTP send failed: {exc}")

    return EmailResult(True, f"Email sent to {recipient}")