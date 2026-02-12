# PaySlip Agent

A web application that processes multi-page payslip PDFs (Hebrew), splits them into individual employee payslips, encrypts each with the employee's ID, and emails them.

## Features

- **AI-Powered Extraction**: Uses Claude Vision API to read Hebrew payslips — handles any layout, scanned or digital
- **Regex Fallback**: Works without an API key using regex patterns (less accurate)
- **PDF Splitting**: Separates a multi-page PDF into individual payslips
- **Encryption**: Each payslip is AES-256 encrypted with the employee's Teudat Zehut (ID) as the password
- **Print-Only Permissions**: Encrypted PDFs only allow printing — no editing, copying, or form filling
- **Hebrew File Naming**: Files named `שם משפחה פרטי - חודש(עברית) שנה.pdf`
- **Page Previews**: Thumbnail preview of each payslip page on the review screen (click to zoom)
- **SQLite Database**: Tracks employees, processing batches, and email delivery status
- **Duplicate Detection**: Warns if a payslip was already sent for the same employee/period
- **Retry Failed Emails**: Retry button on the history page for failed deliveries
- **Employee Directory**: Auto-populated from processed payslips; emails auto-fill on subsequent runs
- **Email Delivery**: Sends each encrypted payslip via SMTP
- **Web UI**: Upload, preview/edit, process, and view history
- **REST API**: Programmatic endpoint for n8n, Make, or other automation tools
- **CLI Arguments**: `--port`, `--host`, `--no-debug` flags

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
# Edit .env with your credentials
```

#### Required: SMTP (Gmail example)

For Gmail, create an [App Password](https://myaccount.google.com/apppasswords):

```
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SENDER_EMAIL=your-email@gmail.com
SENDER_NAME=מחלקת משאבי אנוש
```

#### Recommended: Claude Vision API

Get an API key from [console.anthropic.com](https://console.anthropic.com/):

```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929
```

Without an API key, the app falls back to regex-based extraction (less reliable for varied payslip formats).

### 3. Run the application

```bash
python app.py                  # Default: http://localhost:8080
python app.py --port 9000      # Custom port
python app.py --no-debug       # Production mode
```

## Usage

### Web UI

1. **Upload** — Drag or select a multi-page payslip PDF
2. **Preview** — Review AI-extracted data with page thumbnails; correct any errors
3. **Send** — Encrypt and email all payslips
4. **History** — View all past processing; retry failed emails
5. **Employees** — Browse auto-populated employee directory

### REST API

```bash
curl -X POST http://localhost:8080/api/upload \
  -F "pdf_file=@payslips.pdf"
```

With data overrides (keyed by 1-based page number):

```bash
curl -X POST http://localhost:8080/api/upload \
  -F "pdf_file=@payslips.pdf" \
  -F 'overrides={"1": {"name": "ישראל ישראלי", "employee_id": "123456789", "email": "israel@example.com", "month": 1, "year": 2024}}'
```

### Integration with n8n / Make

Use the `/api/upload` endpoint as an HTTP Request node to trigger payslip processing from any automation workflow.

## Project Structure

```
PaySlipsAgent/
├── app.py                # Flask web application, routes & API
├── config.py             # Environment variable configuration
├── vision_extractor.py   # Claude Vision API extraction + page preview generation
├── payslip_parser.py     # Regex-based fallback extraction
├── pdf_processor.py      # PDF splitting and AES-256 encryption
├── email_sender.py       # SMTP email delivery
├── database.py           # SQLite DB: employees, batches, records
├── templates/
│   ├── base.html         # Base layout with nav (RTL Hebrew)
│   ├── index.html        # Upload page with drag-and-drop
│   ├── preview.html      # Data review with page thumbnails
│   ├── results.html      # Send results summary
│   ├── history.html      # Processing history + retry
│   └── employees.html    # Employee directory
├── requirements.txt
├── .env.example
└── .gitignore
```

## Database

SQLite database (`payslips.db`) is created automatically on first run with three tables:

| Table | Purpose |
|-------|---------|
| `employees` | Employee directory (name, tz, email) — auto-populated |
| `payslip_batches` | Each uploaded PDF file processing run |
| `payslip_records` | Individual payslip per employee: file path, email status, errors |
