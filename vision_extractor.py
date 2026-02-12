"""
Extracts employee data from payslip PDF pages using Claude Vision API.

Sends each page as an image to Claude, which reads the Hebrew payslip and returns
structured JSON with: name, employee_id, email, month, year.

Falls back to regex-based extraction if the API key is not configured.
"""

import base64
import io
import json
import logging

import anthropic
import pdfplumber
from PIL import Image

from config import Config

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are analyzing a Hebrew payslip (תלוש שכר). Extract the following fields from this payslip image.

Return ONLY a JSON object with these keys (no markdown, no explanation):
{
  "name": "Employee full name in Hebrew (שם פרטי + שם משפחה)",
  "employee_id": "Teudat Zehut number (ת.ז) — digits only, 5-9 digits",
  "email": "Employee email address if visible, otherwise null",
  "month": "Pay period month as integer 1-12",
  "year": "Pay period year as 4-digit integer"
}

Rules:
- For name: look for שם עובד, שם מלא, or the employee name field. Return the full name in Hebrew.
- For employee_id: look for ת.ז, תעודת זהות, מספר זהות. Return digits only.
- For email: look for דוא"ל, אימייל, דואר אלקטרוני, or any email pattern. Return null if not found.
- For month/year: look for חודש שכר, תקופה, or date fields indicating the pay period.
- Return null for any field you cannot find.
- Return ONLY the JSON object, nothing else.
"""


def _page_to_image_bytes(page) -> bytes:
    """Convert a pdfplumber page to a PNG image bytes."""
    img = page.to_image(resolution=200)
    buf = io.BytesIO()
    img.original.save(buf, format="PNG")
    return buf.getvalue()


def extract_with_vision(pdf_path: str) -> list[dict]:
    """
    Extract employee data from each page of a PDF using Claude Vision API.

    Returns a list of dicts, one per page, with keys:
    name, employee_id, email, month, year, page_number, raw_text
    """
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            raw_text = page.extract_text() or ""

            # Convert page to image for vision API
            image_bytes = _page_to_image_bytes(page)
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

            try:
                response = client.messages.create(
                    model=Config.ANTHROPIC_MODEL,
                    max_tokens=500,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": image_b64,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": EXTRACTION_PROMPT,
                                },
                            ],
                        }
                    ],
                )

                # Parse the JSON response
                response_text = response.content[0].text.strip()
                # Handle potential markdown wrapping
                if response_text.startswith("```"):
                    response_text = response_text.split("\n", 1)[1]
                    response_text = response_text.rsplit("```", 1)[0].strip()

                data = json.loads(response_text)

                results.append(
                    {
                        "page_number": page_num,
                        "name": data.get("name"),
                        "employee_id": str(data["employee_id"]) if data.get("employee_id") else None,
                        "email": data.get("email"),
                        "month": int(data["month"]) if data.get("month") else None,
                        "year": int(data["year"]) if data.get("year") else None,
                        "raw_text": raw_text,
                    }
                )
                logger.info(f"Page {page_num + 1}: extracted {data.get('name')}")

            except Exception as e:
                logger.error(f"Page {page_num + 1}: Claude API error: {e}")
                results.append(
                    {
                        "page_number": page_num,
                        "name": None,
                        "employee_id": None,
                        "email": None,
                        "month": None,
                        "year": None,
                        "raw_text": raw_text,
                    }
                )

    return results


def generate_page_preview(pdf_path: str, page_number: int, max_width: int = 400) -> bytes:
    """
    Generate a PNG preview image of a specific PDF page.

    Args:
        pdf_path: Path to the PDF file.
        page_number: 0-based page index.
        max_width: Maximum width in pixels for the thumbnail.

    Returns:
        PNG image bytes.
    """
    with pdfplumber.open(pdf_path) as pdf:
        if page_number >= len(pdf.pages):
            raise ValueError(f"Page {page_number} does not exist")

        page = pdf.pages[page_number]
        img = page.to_image(resolution=150)
        pil_img = img.original

        # Resize to thumbnail
        ratio = max_width / pil_img.width
        new_size = (max_width, int(pil_img.height * ratio))
        pil_img = pil_img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return buf.getvalue()
