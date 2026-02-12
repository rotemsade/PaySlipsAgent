"""
Flask web application for the PaySlip Agent.

Provides a UI to:
1. Upload a multi-page payslip PDF
2. Preview extracted employee data (name, ID, email, period)
3. Edit/correct extracted data before processing
4. Split, encrypt, and email each payslip
"""

import os
import uuid
import shutil
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
)
from werkzeug.utils import secure_filename
from config import Config
from payslip_parser import parse_payslips, HEBREW_MONTHS
from pdf_processor import split_and_encrypt
from email_sender import send_all_payslips

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)

# In-memory store for parsed sessions (maps session_id -> data).
# In production, use Redis or a database.
_sessions: dict[str, dict] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Handle PDF upload and parse payslips."""
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

    # Parse payslips
    try:
        payslips = parse_payslips(pdf_path)
    except Exception as e:
        flash(f"שגיאה בקריאת הקובץ: {e}", "error")
        shutil.rmtree(session_dir, ignore_errors=True)
        return redirect(url_for("index"))

    if not payslips:
        flash("לא נמצאו תלושי שכר בקובץ", "error")
        shutil.rmtree(session_dir, ignore_errors=True)
        return redirect(url_for("index"))

    # Store session data
    _sessions[session_id] = {
        "pdf_path": pdf_path,
        "session_dir": session_dir,
        "payslips": payslips,
    }

    return redirect(url_for("preview", session_id=session_id))


@app.route("/preview/<session_id>")
def preview(session_id: str):
    """Show extracted data for review before processing."""
    sess = _sessions.get(session_id)
    if not sess:
        flash("Session expired or not found", "error")
        return redirect(url_for("index"))

    payslips_data = []
    for ps in sess["payslips"]:
        hebrew_month = HEBREW_MONTHS.get(ps.month, "") if ps.month else ""
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
            }
        )

    return render_template(
        "preview.html",
        session_id=session_id,
        payslips=payslips_data,
        hebrew_months=HEBREW_MONTHS,
    )


@app.route("/process", methods=["POST"])
def process():
    """Apply user edits, split/encrypt PDFs, and send emails."""
    session_id = request.form.get("session_id", "")
    sess = _sessions.get(session_id)
    if not sess:
        flash("Session expired or not found", "error")
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

    # Split and encrypt
    output_dir = os.path.join(Config.OUTPUT_FOLDER, session_id)
    try:
        processed = split_and_encrypt(sess["pdf_path"], output_dir, payslips)
    except Exception as e:
        flash(f"שגיאה בעיבוד הקבצים: {e}", "error")
        return redirect(url_for("preview", session_id=session_id))

    # Send emails
    send_results = send_all_payslips(processed)

    # Cleanup session
    _sessions.pop(session_id, None)

    return render_template("results.html", results=send_results)


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
        import json

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

    # Parse
    try:
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

    # Split & encrypt
    output_dir = os.path.join(Config.OUTPUT_FOLDER, session_id)
    try:
        processed = split_and_encrypt(pdf_path, output_dir, payslips)
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"error": f"Failed to process PDF: {e}"}), 500

    # Send emails
    send_results = send_all_payslips(processed)

    # Cleanup
    shutil.rmtree(session_dir, ignore_errors=True)
    shutil.rmtree(output_dir, ignore_errors=True)

    return jsonify(
        {
            "total": len(send_results),
            "sent": sum(1 for r in send_results if r["success"]),
            "failed": sum(1 for r in send_results if not r["success"]),
            "details": send_results,
        }
    )


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    app.run(debug=True, host="0.0.0.0", port=port)
