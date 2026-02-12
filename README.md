# PaySlip Agent

A web application that processes multi-page payslip PDFs (Hebrew), splits them into individual employee payslips, encrypts each with the employee's ID, and emails them.

## Features

- **PDF Parsing**: Extracts employee data (name, ID, email, pay period) from Hebrew payslips
- **PDF Splitting**: Separates a multi-page PDF into individual payslips
- **Encryption**: Each payslip is encrypted with the employee's Teudat Zehut (ID) as the password
- **Print-Only Permissions**: Encrypted PDFs only allow printing — no editing, copying, or form filling
- **Hebrew File Naming**: Files are named `שם משפחה פרטי - חודש(עברית) שנה.pdf`
- **Email Delivery**: Sends each encrypted payslip to the employee's email
- **Web UI**: Upload, preview/edit extracted data, then process and send
- **REST API**: Programmatic endpoint for integration with n8n, Make, or other automation tools

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your SMTP credentials and settings
```

#### SMTP Configuration (Gmail example)

For Gmail, create an [App Password](https://myaccount.google.com/apppasswords):

```
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SENDER_EMAIL=your-email@gmail.com
SENDER_NAME=מחלקת משאבי אנוש
```

### 3. Run the application

```bash
python app.py
```

Open http://localhost:5000 in your browser.

## Usage

### Web UI

1. Open the app in your browser
2. Upload a multi-page payslip PDF
3. Review the extracted data — correct any errors
4. Click "הצפן ושלח תלושים" to encrypt and email all payslips

### REST API

Send a POST request to `/api/upload` with the PDF file:

```bash
curl -X POST http://localhost:5000/api/upload \
  -F "pdf_file=@payslips.pdf"
```

Optionally override extracted data with a JSON `overrides` field (keyed by 1-based page number):

```bash
curl -X POST http://localhost:5000/api/upload \
  -F "pdf_file=@payslips.pdf" \
  -F 'overrides={"1": {"name": "ישראל ישראלי", "employee_id": "123456789", "email": "israel@example.com", "month": 1, "year": 2024}}'
```

Response:

```json
{
  "total": 5,
  "sent": 4,
  "failed": 1,
  "details": [
    {
      "employee_name": "ישראל ישראלי",
      "email": "israel@example.com",
      "filename": "ישראל ישראלי - ינואר 2024.pdf",
      "success": true,
      "error": null
    }
  ]
}
```

### Integration with n8n / Make

Use the `/api/upload` endpoint as an HTTP Request node. This allows you to trigger payslip processing from any automation workflow.

## Project Structure

```
PaySlipsAgent/
├── app.py               # Flask web application & API
├── config.py            # Configuration from environment variables
├── payslip_parser.py    # PDF text extraction and field parsing
├── pdf_processor.py     # PDF splitting and encryption
├── email_sender.py      # SMTP email delivery
├── templates/
│   ├── base.html        # Base layout (RTL Hebrew)
│   ├── index.html       # Upload page
│   ├── preview.html     # Data review/edit page
│   └── results.html     # Send results page
├── requirements.txt
├── .env.example
└── .gitignore
```

## Payslip Field Detection

The parser looks for these Hebrew field patterns:

| Field | Patterns matched |
|-------|-----------------|
| Name | `שם עובד`, `שם מלא`, `עובד`, `לכבוד` |
| ID | `ת.ז`, `תעודת זהות`, `מספר זהות` |
| Email | Standard email regex |
| Period | `חודש`, `תקופה` + `MM/YYYY` or Hebrew month name |

If automatic extraction fails, users can correct data in the preview screen before processing.
