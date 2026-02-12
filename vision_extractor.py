"""
Extracts employee data from payslip PDF pages using Claude Vision API.

Sends each page as a high-resolution image to Claude, which reads the Hebrew
payslip and returns structured JSON with: name, employee_id, email, month, year.

Also handles preview image generation — previews are generated once during upload
and cached to disk to avoid concurrent pdfplumber access (which causes heap corruption).
"""

import base64
import io
import json
import logging
import os

import anthropic
import pdfplumber
from PIL import Image

from config import Config

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are an expert at reading Israeli payslips (תלושי שכר) in Hebrew.

Carefully examine this payslip image and extract the following employee details.

Return ONLY a valid JSON object (no markdown fences, no explanation):
{
  "name": "שם פרטי ושם משפחה של העובד/ת",
  "employee_id": "מספר תעודת זהות — ספרות בלבד",
  "email": "כתובת דוא\"ל אם מופיעה, אחרת null",
  "month": "חודש השכר כמספר 1-12",
  "year": "שנת השכר כמספר בן 4 ספרות"
}

Detailed instructions:
1. NAME (שם עובד/ת): Look for fields labeled שם עובד, שם משפחה, שם פרטי, שם מלא, or לכבוד.
   - The name typically appears near the top of the payslip.
   - Return FIRST NAME then LAST NAME (שם פרטי + שם משפחה), in Hebrew.
   - Be precise — read each letter carefully. Hebrew letters that look similar:
     ב/כ, ג/נ, ד/ר, ה/ח/ת, ו/ז, ט/מ, ע/צ, פ/ף, כ/ך, מ/ם, נ/ן, פ/ף, צ/ץ
   - If first name and last name are in separate fields, combine them.

2. EMPLOYEE ID (ת.ז): Look for ת.ז, ת"ז, תעודת זהות, מספר זהות, מס' זהות.
   - This is a 5-9 digit Israeli ID number.
   - Return digits only, no dashes or spaces.

3. EMAIL: Look for דוא"ל, אימייל, דואר אלקטרוני, מייל, email, or an @ symbol.
   - Return null if no email is visible on the payslip.

4. MONTH & YEAR: Look for חודש, חודש שכר, תקופה, תקופת שכר, לחודש.
   - Common formats: "01/2024", "ינואר 2024", "חודש 1 שנת 2024"
   - Return month as integer (1=January/ינואר, 12=December/דצמבר).
   - Return year as 4-digit integer.

Return null for any field you truly cannot find. Be accurate — do not guess.
"""


def _page_to_image_bytes(page, resolution: int = 300) -> bytes:
    """Convert a pdfplumber page to PNG image bytes at the given resolution."""
    img = page.to_image(resolution=resolution)
    buf = io.BytesIO()
    img.original.save(buf, format="PNG")
    return buf.getvalue()


def _build_prompt(known_names: list[str] | None = None) -> str:
    """Build the extraction prompt, optionally including known employee names."""
    prompt = EXTRACTION_PROMPT
    if known_names:
        names_list = ", ".join(known_names)
        prompt += (
            "\n\nIMPORTANT — Known employees in this company:\n"
            f"{names_list}\n"
            "If the name you read on the payslip closely matches one of these "
            "known names, prefer the known spelling. Only use a different name "
            "if you are confident the payslip shows a genuinely different person."
        )
    return prompt


def apply_corrections(
    extracted: list[dict],
    corrections: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    """Apply known corrections to extracted data in-place.

    *corrections* is expected as ``{field: {extracted_val: corrected_val}}``.
    """
    if not corrections:
        return extracted
    for entry in extracted:
        for field, mapping in corrections.items():
            val = entry.get(field)
            if val and val in mapping:
                logger.info(
                    "Auto-correcting %s: %r -> %r", field, val, mapping[val]
                )
                entry[field] = mapping[val]
    return extracted


def extract_with_vision(
    pdf_path: str,
    known_names: list[str] | None = None,
    corrections: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    """
    Extract employee data from each page of a PDF using Claude Vision API.

    *known_names*: list of employee names from the DB to include in the prompt
    for better accuracy.
    *corrections*: ``{field: {extracted: corrected}}`` dict of known mistakes.

    Returns a list of dicts, one per page, with keys:
    name, employee_id, email, month, year, page_number, raw_text
    """
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    prompt = _build_prompt(known_names)
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            raw_text = page.extract_text() or ""

            # Convert page to high-res image for vision API
            image_bytes = _page_to_image_bytes(page, resolution=300)
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
                                    "text": prompt,
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

    # Apply known corrections to the raw extraction results
    apply_corrections(results, corrections)

    return results


def generate_all_previews(pdf_path: str, output_dir: str, max_width: int = 400) -> int:
    """
    Pre-generate PNG preview thumbnails for ALL pages in the PDF.
    Saves them as 0.png, 1.png, etc. in output_dir.

    This is called ONCE during upload to avoid concurrent pdfplumber access.

    Returns the number of pages processed.
    """
    os.makedirs(output_dir, exist_ok=True)

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            img = page.to_image(resolution=150)
            pil_img = img.original

            # Resize to thumbnail
            ratio = max_width / pil_img.width
            new_size = (max_width, int(pil_img.height * ratio))
            pil_img = pil_img.resize(new_size, Image.LANCZOS)

            out_path = os.path.join(output_dir, f"{page_num}.png")
            pil_img.save(out_path, format="PNG")

        return len(pdf.pages)


def get_cached_preview(preview_dir: str, page_number: int) -> bytes | None:
    """Read a cached preview PNG from disk. Returns None if not found."""
    path = os.path.join(preview_dir, f"{page_number}.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None
