"""
Parses a multi-page PDF of payslips and extracts employee data from each page.

Expects Hebrew payslips with fields like:
  - Employee name (שם עובד / שם מלא)
  - Employee ID / Teudat Zehut (ת.ז / תעודת זהות / מספר זהות)
  - Month and year of the payslip (חודש / תקופה)
  - Email address (דוא"ל / אימייל / דואר אלקטרוני)

Each page is assumed to be one employee's payslip.
"""

import re
import pdfplumber

# Hebrew month names mapping (1-indexed)
HEBREW_MONTHS = {
    1: "ינואר",
    2: "פברואר",
    3: "מרץ",
    4: "אפריל",
    5: "מאי",
    6: "יוני",
    7: "יולי",
    8: "אוגוסט",
    9: "ספטמבר",
    10: "אוקטובר",
    11: "נובמבר",
    12: "דצמבר",
}

# Regex patterns for extracting fields from Hebrew payslips
# These patterns are designed to be flexible and match common payslip formats.
# Adjust as needed for specific payslip layouts.

ID_PATTERNS = [
    # ת.ז: 123456789 or ת.ז 123456789
    r'ת\.?\s*ז\.?\s*[:\-]?\s*(\d{5,9})',
    # תעודת זהות: 123456789
    r'תעודת\s+זהות\s*[:\-]?\s*(\d{5,9})',
    # מספר זהות: 123456789
    r'מספר\s+זהות\s*[:\-]?\s*(\d{5,9})',
    # מס זהות: 123456789
    r'מס\.?\s*זהות\s*[:\-]?\s*(\d{5,9})',
    # ID / id followed by digits
    r'(?:ID|id)\s*[:\-]?\s*(\d{5,9})',
]

NAME_PATTERNS = [
    # שם עובד: ישראל ישראלי
    r'שם\s+(?:ה)?עובד(?:ת)?\s*[:\-]?\s*([\u0590-\u05FF\s\-]+)',
    # שם מלא: ישראל ישראלי
    r'שם\s+מלא\s*[:\-]?\s*([\u0590-\u05FF\s\-]+)',
    # שם: ישראל ישראלי
    r'(?<!\S)שם\s*[:\-]\s*([\u0590-\u05FF\s\-]+)',
    # עובד: ישראל ישראלי
    r'(?<!\S)עובד(?:ת)?\s*[:\-]\s*([\u0590-\u05FF\s\-]+)',
    # לכבוד ישראל ישראלי
    r'לכבוד\s+([\u0590-\u05FF\s\-]+)',
]

EMAIL_PATTERNS = [
    # Standard email regex
    r'([\w\.\+\-]+@[\w\-]+(?:\.[\w\-]+)+)',
]

# Patterns for month/year - supports formats like:
# חודש: 01/2024, תקופה: ינואר 2024, חודש 1 שנה 2024, etc.
PERIOD_PATTERNS = [
    # MM/YYYY or MM-YYYY or MM.YYYY
    r'(?:חודש|תקופה|לחודש|חודש\s+שכר)\s*[:\-]?\s*(\d{1,2})\s*[/\-\.]\s*(\d{4})',
    # Hebrew month name followed by year
    r'(?:חודש|תקופה|לחודש)\s*[:\-]?\s*([\u0590-\u05FF]+)\s+(\d{4})',
    # Standalone MM/YYYY near top of page
    r'(\d{1,2})\s*/\s*(\d{4})',
]


def _extract_field(text: str, patterns: list[str]) -> str | None:
    """Try each pattern in order and return the first match."""
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _parse_month_year(text: str) -> tuple[int | None, int | None]:
    """Extract month (as int) and year from the payslip text."""
    # Reverse mapping: Hebrew month name -> number
    hebrew_to_num = {name: num for num, name in HEBREW_MONTHS.items()}

    for pattern in PERIOD_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue

        g1, g2 = match.group(1).strip(), match.group(2).strip()

        # If first group is a digit, it's the month number
        if g1.isdigit():
            month = int(g1)
            year = int(g2)
            if 1 <= month <= 12 and 1900 <= year <= 2100:
                return month, year
        else:
            # First group is a Hebrew month name
            month_num = hebrew_to_num.get(g1)
            if month_num and g2.isdigit():
                year = int(g2)
                if 1900 <= year <= 2100:
                    return month_num, year

    return None, None


def _format_filename(name: str, month: int | None, year: int | None) -> str:
    """
    Format filename as: name surname - month(HEB) full_year
    Example: ישראל ישראלי - ינואר 2024
    """
    if month and year:
        hebrew_month = HEBREW_MONTHS.get(month, str(month))
        return f"{name} - {hebrew_month} {year}"
    elif year:
        return f"{name} - {year}"
    else:
        return name


class EmployeePayslip:
    """Holds parsed data for a single employee payslip."""

    def __init__(
        self,
        page_number: int,
        name: str | None = None,
        employee_id: str | None = None,
        email: str | None = None,
        month: int | None = None,
        year: int | None = None,
        raw_text: str = "",
    ):
        self.page_number = page_number
        self.name = name
        self.employee_id = employee_id
        self.email = email
        self.month = month
        self.year = year
        self.raw_text = raw_text

    @property
    def filename(self) -> str:
        display_name = self.name or f"employee_page_{self.page_number + 1}"
        return _format_filename(display_name, self.month, self.year)

    @property
    def is_valid(self) -> bool:
        return bool(self.name and self.employee_id)

    def __repr__(self) -> str:
        return (
            f"EmployeePayslip(page={self.page_number}, name={self.name!r}, "
            f"id={self.employee_id!r}, email={self.email!r}, "
            f"month={self.month}, year={self.year})"
        )


def parse_payslips(pdf_path: str) -> list[EmployeePayslip]:
    """
    Parse a multi-page PDF and extract employee data from each page.
    Each page is treated as a separate employee's payslip.

    Returns a list of EmployeePayslip objects.
    """
    payslips = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""

            name = _extract_field(text, NAME_PATTERNS)
            employee_id = _extract_field(text, ID_PATTERNS)
            email = _extract_field(text, EMAIL_PATTERNS)
            month, year = _parse_month_year(text)

            payslip = EmployeePayslip(
                page_number=page_num,
                name=name,
                employee_id=employee_id,
                email=email,
                month=month,
                year=year,
                raw_text=text,
            )
            payslips.append(payslip)

    return payslips
