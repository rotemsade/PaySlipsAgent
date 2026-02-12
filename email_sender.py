"""
Sends encrypted payslip PDFs to employees via email.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from config import Config


# Hebrew email body template
EMAIL_SUBJECT = "תלוש שכר - {period}"
EMAIL_BODY_HTML = """\
<div dir="rtl" style="font-family: Arial, sans-serif; font-size: 14px;">
<p>שלום {name},</p>

<p>מצורף תלוש השכר שלך עבור <strong>{period}</strong>.</p>

<p>הקובץ מוצפן. הסיסמה לפתיחה היא <strong>מספר תעודת הזהות שלך</strong>.</p>

<p>בברכה,<br>{sender_name}</p>
</div>
"""


def send_payslip_email(
    recipient_email: str,
    employee_name: str,
    period: str,
    pdf_path: str,
    pdf_filename: str,
) -> dict:
    """
    Send an encrypted payslip PDF to an employee.

    Args:
        recipient_email: Employee's email address.
        employee_name: Employee's display name (Hebrew).
        period: Human-readable period string, e.g. "ינואר 2024".
        pdf_path: Path to the encrypted PDF file on disk.
        pdf_filename: Filename to use for the attachment.

    Returns:
        dict with keys: success (bool), error (str or None).
    """
    msg = MIMEMultipart()
    msg["From"] = f"{Config.SENDER_NAME} <{Config.SENDER_EMAIL}>"
    msg["To"] = recipient_email
    msg["Subject"] = EMAIL_SUBJECT.format(period=period)

    body = EMAIL_BODY_HTML.format(
        name=employee_name,
        period=period,
        sender_name=Config.SENDER_NAME,
    )
    msg.attach(MIMEText(body, "html", "utf-8"))

    # Attach the encrypted PDF
    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{pdf_filename}"',
        )
        msg.attach(part)

    try:
        with smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
            server.send_message(msg)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_all_payslips(processed_files: list[dict]) -> list[dict]:
    """
    Send emails for all processed payslip files.

    Args:
        processed_files: List of dicts from pdf_processor.split_and_encrypt().

    Returns:
        List of result dicts with: employee_name, email, filename, success, error.
    """
    from payslip_parser import HEBREW_MONTHS

    results = []

    for item in processed_files:
        payslip = item["payslip"]

        if not payslip.email:
            results.append(
                {
                    "employee_name": payslip.name or "Unknown",
                    "email": None,
                    "filename": item["filename"],
                    "success": False,
                    "error": "No email address found in payslip",
                }
            )
            continue

        # Build a human-readable period string
        if payslip.month and payslip.year:
            period = f"{HEBREW_MONTHS.get(payslip.month, str(payslip.month))} {payslip.year}"
        elif payslip.year:
            period = str(payslip.year)
        else:
            period = "לא ידוע"

        result = send_payslip_email(
            recipient_email=payslip.email,
            employee_name=payslip.name or "עובד/ת",
            period=period,
            pdf_path=item["path"],
            pdf_filename=item["filename"],
        )

        results.append(
            {
                "employee_name": payslip.name or "Unknown",
                "email": payslip.email,
                "filename": item["filename"],
                "success": result["success"],
                "error": result["error"],
            }
        )

    return results
