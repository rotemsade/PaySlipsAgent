"""
Flask web application for the PaySlip Agent.

Provides a UI to:
1. Upload a multi-page payslip PDF
2. AI-extract employee data using Claude Vision API
3. Preview each payslip page as an image
4. Edit/correct extracted data before processing
5. Split, encrypt, and email each payslip
6. View processing history from SQLite database
"""

import argparse
import io
import json
import logging
import os
import shutil
import uuid

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from config import Config
from payslip_parser import HEBREW_MONTHS, EmployeePayslip, parse_payslips
from pdf_processor import split_and_encrypt
from email_sender import send_all_payslips
from vision_extractor import extract_with_vision, generate_all_previews, get_cached_preview
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)

# In-memory store for upload sessions (maps session_id -> data).
_sessions: dict[str, dict] = {}


def _use_vision() -> bool:
    """Check if Claude Vision API is configured."""
    return bool(Config.ANTHROPIC_API_KEY)


# ---------- Pages ----------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Handle PDF upload and parse payslips using AI or regex."""
    if "pdf_file" not in request.files:
        flash("לא נבחר קובץ", "error")
        return redirect(url_for("index"))

    file = request.files["pdf_file"]
    if file.filename == "":
        flash("לא נבחר קובץ", "error")
        return redirect(url_for("index"))

    if not file.filename.lower().endswith(".pdf"):
        flash("יש להעלות קובץ PDF בלבד", "error")
        return redirect(url_for("index"))

    # Save uploaded file
    session_id = str(uuid.uuid4())
    session_dir = os.path.join(Config.UPLOAD_FOLDER, session_id)
    os.makedirs(session_dir, exist_ok=True)

    filename = secure_filename(file.filename) or "payslips.pdf"
    pdf_path = os.path.join(session_dir, filename)
    file.save(pdf_path)

    # Extract employee data
    try:
        if _use_vision():
            logger.info("Using Claude Vision API for extraction")
            extracted = extract_with_vision(pdf_path)
            payslips = []
            for data in extracted:
                # Try to fill in email from DB if we have the employee_id
                email = data.get("email")
                if not email and data.get("employee_id"):
                    existing = db.get_employee_by_tz(data["employee_id"])
                    if existing and existing.get("email"):
                        email = existing["email"]

                ps = EmployeePayslip(
                    page_number=data["page_number"],
                    name=data.get("name"),
                    employee_id=data.get("employee_id"),
                    email=email,
                    month=data.get("month"),
                    year=data.get("year"),
                    raw_text=data.get("raw_text", ""),
                )
                payslips.append(ps)
        else:
            logger.info("No API key configured, using regex extraction")
            payslips = parse_payslips(pdf_path)
            # Try to fill emails from DB
            for ps in payslips:
                if not ps.email and ps.employee_id:
                    existing = db.get_employee_by_tz(ps.employee_id)
                    if existing and existing.get("email"):
                        ps.email = existing["email"]

    except Exception as e:
        logger.error(f"Extraction error: {e}", exc_info=True)
        flash(f"שגיאה בקריאת הקובץ: {e}", "error")
        shutil.rmtree(session_dir, ignore_errors=True)
        return redirect(url_for("index"))

    if not payslips:
        flash("לא נמצאו תלושי שכר בקובץ", "error")
        shutil.rmtree(session_dir, ignore_errors=True)
        return redirect(url_for("index"))

    # Pre-generate all preview thumbnails (once, to avoid concurrent pdfplumber access)
    preview_dir = os.path.join(session_dir, "previews")
    try:
        generate_all_previews(pdf_path, preview_dir)
    except Exception as e:
        logger.warning(f"Preview generation failed: {e}")

    # Store session data
    _sessions[session_id] = {
        "pdf_path": pdf_path,
        "original_filename": file.filename,
        "session_dir": session_dir,
        "preview_dir": preview_dir,
        "payslips": payslips,
    }

    return redirect(url_for("preview", session_id=session_id))


@app.route("/preview/<session_id>")
def preview(session_id: str):
    """Show extracted data with page previews for review."""
    sess = _sessions.get(session_id)
    if not sess:
        flash("Session פג תוקף או לא נמצא", "error")
        return redirect(url_for("index"))

    payslips_data = []
    for ps in sess["payslips"]:
        hebrew_month = HEBREW_MONTHS.get(ps.month, "") if ps.month else ""

        # Check if this was already sent
        already_sent = False
        if ps.employee_id and ps.month and ps.year:
            already_sent = db.is_already_processed(ps.employee_id, ps.month, ps.year)

        payslips_data.append(
            {
                "page": ps.page_number + 1,
                "name": ps.name or "",
                "employee_id": ps.employee_id or "",
                "email": ps.email or "",
                "month": ps.month or "",
                "year": ps.year or "",
                "hebrew_month": hebrew_month,
                "filename": ps.filename,
                "is_valid": ps.is_valid,
                "already_sent": already_sent,
            }
        )

    extraction_method = "Claude Vision AI" if _use_vision() else "Regex"

    return render_template(
        "preview.html",
        session_id=session_id,
        payslips=payslips_data,
        hebrew_months=HEBREW_MONTHS,
        extraction_method=extraction_method,
    )


@app.route("/page_preview/<session_id>/<int:page_number>")
def page_preview(session_id: str, page_number: int):
    """Serve a cached PNG preview image of a specific payslip page."""
    sess = _sessions.get(session_id)
    if not sess:
        return "Session not found", 404

    preview_dir = sess.get("preview_dir", "")
    image_bytes = get_cached_preview(preview_dir, page_number)
    if image_bytes:
        return Response(image_bytes, mimetype="image/png")

    return "Preview unavailable", 500


@app.route("/process", methods=["POST"])
def process():
    """Apply user edits, split/encrypt PDFs, send emails, and record in DB."""
    session_id = request.form.get("session_id", "")
    sess = _sessions.get(session_id)
    if not sess:
        flash("Session פג תוקף או לא נמצא", "error")
        return redirect(url_for("index"))

    payslips = sess["payslips"]

    # Apply user corrections from the form
    for i, ps in enumerate(payslips):
        ps.name = request.form.get(f"name_{i}", ps.name)
        ps.employee_id = request.form.get(f"employee_id_{i}", ps.employee_id)
        ps.email = request.form.get(f"email_{i}", ps.email)

        month_val = request.form.get(f"month_{i}", "")
        if month_val and month_val.isdigit():
            ps.month = int(month_val)

        year_val = request.form.get(f"year_{i}", "")
        if year_val and year_val.isdigit():
            ps.year = int(year_val)

    # Validate: every payslip must have at least name and ID
    errors = []
    for ps in payslips:
        if not ps.name:
            errors.append(f"עמוד {ps.page_number + 1}: חסר שם עובד")
        if not ps.employee_id:
            errors.append(f"עמוד {ps.page_number + 1}: חסר מספר ת.ז")

    if errors:
        flash(" | ".join(errors), "error")
        return redirect(url_for("preview", session_id=session_id))

    # Create batch in DB
    batch_id = db.create_batch(
        original_filename=sess.get("original_filename", "unknown.pdf"),
        total_pages=len(payslips),
    )

    # Upsert employees and create records
    record_ids = []
    for ps in payslips:
        emp_db_id = db.upsert_employee(ps.employee_id, ps.name, ps.email)
        record_id = db.create_payslip_record(
            batch_id=batch_id,
            employee_db_id=emp_db_id,
            employee_id=ps.employee_id,
            employee_name=ps.name,
            employee_email=ps.email,
            month=ps.month,
            year=ps.year,
            page_number=ps.page_number,
        )
        record_ids.append(record_id)

    # Split and encrypt
    output_dir = os.path.join(Config.OUTPUT_FOLDER, str(batch_id))
    try:
        processed = split_and_encrypt(sess["pdf_path"], output_dir, payslips)
    except Exception as e:
        db.update_batch_status(batch_id, "failed")
        flash(f"שגיאה בעיבוד הקבצים: {e}", "error")
        return redirect(url_for("preview", session_id=session_id))

    # Update DB records with file info
    for item, record_id in zip(processed, record_ids):
        db.update_record_file_info(record_id, item["filename"], item["path"])

    # Send emails
    send_results = send_all_payslips(processed)

    # Update DB with email results
    for result, record_id in zip(send_results, record_ids):
        db.update_record_email_status(
            record_id,
            sent=result["success"],
            error=result.get("error"),
        )

    db.update_batch_status(batch_id, "completed")

    # Cleanup session
    _sessions.pop(session_id, None)

    return render_template("results.html", results=send_results, batch_id=batch_id)


@app.route("/history")
def history():
    """Show processing history from the database."""
    records = db.get_history(limit=200)
    batches = db.get_all_batches()
    return render_template("history.html", records=records, batches=batches)


@app.route("/employees")
def employees():
    """Show the employee directory."""
    emps = db.get_all_employees()
    return render_template("employees.html", employees=emps)


@app.route("/employees/update/<int:employee_db_id>", methods=["POST"])
def update_employee(employee_db_id: int):
    """Update an employee record (name, employee_id, email)."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip() or None

    if not name or not employee_id:
        return jsonify({"success": False, "error": "שם ות.ז הם שדות חובה"}), 400

    try:
        db.update_employee(employee_db_id, name, employee_id, email)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Failed to update employee {employee_db_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/retry/<int:record_id>", methods=["POST"])
def retry_email(record_id: int):
    """Retry sending a failed email for a specific record."""
    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM payslip_records WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()

    if not row:
        flash("רשומה לא נמצאה", "error")
        return redirect(url_for("history"))

    row = dict(row)
    if not row.get("encrypted_path") or not os.path.exists(row["encrypted_path"]):
        flash("קובץ מוצפן לא נמצא. יש לעבד מחדש.", "error")
        return redirect(url_for("history"))

    if not row.get("employee_email"):
        flash("כתובת אימייל חסרה", "error")
        return redirect(url_for("history"))

    # Build period string
    if row["month"] and row["year"]:
        period = f"{HEBREW_MONTHS.get(row['month'], str(row['month']))} {row['year']}"
    else:
        period = "לא ידוע"

    from email_sender import send_payslip_email

    result = send_payslip_email(
        recipient_email=row["employee_email"],
        employee_name=row["employee_name"] or "עובד/ת",
        period=period,
        pdf_path=row["encrypted_path"],
        pdf_filename=row["output_filename"],
    )

    db.update_record_email_status(record_id, sent=result["success"], error=result.get("error"))

    if result["success"]:
        flash(f"נשלח בהצלחה ל-{row['employee_email']}", "success")
    else:
        flash(f"שליחה נכשלה: {result['error']}", "error")

    return redirect(url_for("history"))


# ---------- AJAX API for preview page ----------


@app.route("/api/employee_lookup", methods=["POST"])
def employee_lookup():
    """
    Look up stored employee data by one field (name, employee_id, or email).
    Returns the full employee record if found, so the UI can auto-fill other fields.
    """
    data = request.get_json(silent=True) or {}
    field = data.get("field")  # "name", "employee_id", or "email"
    value = (data.get("value") or "").strip()

    if not field or not value:
        return jsonify({"found": False})

    emp = None
    if field == "employee_id":
        emp = db.get_employee_by_tz(value)
    elif field == "name":
        emp = db.get_employee_by_name(value)
    elif field == "email":
        emp = db.get_employee_by_email(value)

    if emp:
        return jsonify({
            "found": True,
            "name": emp.get("name", ""),
            "employee_id": emp.get("employee_id", ""),
            "email": emp.get("email", ""),
        })

    return jsonify({"found": False})


# ---------- REST API ----------


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """REST API endpoint for programmatic use (e.g., from n8n / Make)."""
    if "pdf_file" not in request.files:
        return jsonify({"error": "No pdf_file in request"}), 400

    file = request.files["pdf_file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a PDF"}), 400

    # Optional: override employee data via JSON
    overrides_raw = request.form.get("overrides", "")
    overrides = {}
    if overrides_raw:
        try:
            overrides = json.loads(overrides_raw)
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON in overrides"}), 400

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(Config.UPLOAD_FOLDER, session_id)
    os.makedirs(session_dir, exist_ok=True)

    filename = secure_filename(file.filename) or "payslips.pdf"
    pdf_path = os.path.join(session_dir, filename)
    file.save(pdf_path)

    # Parse using Vision or regex
    try:
        if _use_vision():
            extracted = extract_with_vision(pdf_path)
            payslips = []
            for data in extracted:
                ps = EmployeePayslip(
                    page_number=data["page_number"],
                    name=data.get("name"),
                    employee_id=data.get("employee_id"),
                    email=data.get("email"),
                    month=data.get("month"),
                    year=data.get("year"),
                    raw_text=data.get("raw_text", ""),
                )
                payslips.append(ps)
        else:
            payslips = parse_payslips(pdf_path)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"error": f"Failed to parse PDF: {e}"}), 500

    # Apply overrides (keyed by page number, 1-based)
    for ps in payslips:
        page_key = str(ps.page_number + 1)
        if page_key in overrides:
            ov = overrides[page_key]
            ps.name = ov.get("name", ps.name)
            ps.employee_id = ov.get("employee_id", ps.employee_id)
            ps.email = ov.get("email", ps.email)
            if "month" in ov:
                ps.month = int(ov["month"])
            if "year" in ov:
                ps.year = int(ov["year"])

    # Create batch
    batch_id = db.create_batch(
        original_filename=file.filename or "api_upload.pdf",
        total_pages=len(payslips),
    )

    # Upsert employees
    record_ids = []
    for ps in payslips:
        emp_db_id = None
        if ps.employee_id and ps.name:
            emp_db_id = db.upsert_employee(ps.employee_id, ps.name, ps.email)
        record_id = db.create_payslip_record(
            batch_id=batch_id,
            employee_db_id=emp_db_id,
            employee_id=ps.employee_id,
            employee_name=ps.name,
            employee_email=ps.email,
            month=ps.month,
            year=ps.year,
            page_number=ps.page_number,
        )
        record_ids.append(record_id)

    # Split & encrypt
    output_dir = os.path.join(Config.OUTPUT_FOLDER, str(batch_id))
    try:
        processed = split_and_encrypt(pdf_path, output_dir, payslips)
    except Exception as e:
        db.update_batch_status(batch_id, "failed")
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"error": f"Failed to process PDF: {e}"}), 500

    for item, record_id in zip(processed, record_ids):
        db.update_record_file_info(record_id, item["filename"], item["path"])

    # Send emails
    send_results = send_all_payslips(processed)

    for result, record_id in zip(send_results, record_ids):
        db.update_record_email_status(
            record_id, sent=result["success"], error=result.get("error")
        )

    db.update_batch_status(batch_id, "completed")

    # Cleanup upload dir (keep output for retries)
    shutil.rmtree(session_dir, ignore_errors=True)

    return jsonify(
        {
            "batch_id": batch_id,
            "total": len(send_results),
            "sent": sum(1 for r in send_results if r["success"]),
            "failed": sum(1 for r in send_results if not r["success"]),
            "details": send_results,
        }
    )


# ---------- Entry point ----------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PaySlip Agent Web Server")
    parser.add_argument(
        "-p", "--port", type=int, default=8080, help="Port to run the server on (default: 8080)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--no-debug", action="store_true", help="Disable debug mode"
    )
    args = parser.parse_args()

    app.run(debug=not args.no_debug, host=args.host, port=args.port)
