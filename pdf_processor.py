"""
Handles PDF splitting and encryption.

- Splits a multi-page PDF into individual single-page PDFs
- Encrypts each PDF with the employee's ID as the password
- Restricts permissions to print-only (no editing, copying, form filling, etc.)
"""

import os
import pikepdf

# pikepdf permission flags — we ONLY allow printing.
PRINT_ONLY_PERMISSIONS = pikepdf.Permissions(
    print_lowres=True,
    print_highres=True,
    # Everything else is denied by default (False)
    accessibility=False,
    extract=False,
    modify_annotation=False,
    modify_assembly=False,
    modify_form=False,
    modify_other=False,
)


def split_and_encrypt(
    source_pdf_path: str,
    output_dir: str,
    payslips: list,
) -> list[dict]:
    """
    Split the source PDF into individual pages and encrypt each one.

    Args:
        source_pdf_path: Path to the uploaded multi-page PDF.
        output_dir: Directory to write encrypted single-page PDFs.
        payslips: List of EmployeePayslip objects from the parser.

    Returns:
        List of dicts with keys: filename, path, payslip (the EmployeePayslip).
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []

    with pikepdf.open(source_pdf_path) as source:
        for payslip in payslips:
            page_idx = payslip.page_number

            if page_idx >= len(source.pages):
                continue

            # Create a new single-page PDF
            dest = pikepdf.new()
            dest.pages.append(source.pages[page_idx])

            # Build the output filename
            safe_name = _sanitize_filename(payslip.filename)
            output_filename = f"{safe_name}.pdf"
            output_path = os.path.join(output_dir, output_filename)

            # Encrypt: employee ID is the user password (required to open).
            # Owner password is a different strong password so users can't
            # remove restrictions.
            owner_password = _generate_owner_password(payslip.employee_id or "owner")
            user_password = payslip.employee_id or ""

            dest.save(
                output_path,
                encryption=pikepdf.Encryption(
                    owner=owner_password,
                    user=user_password,
                    allow=PRINT_ONLY_PERMISSIONS,
                    aes=True,
                    R=6,  # AES-256 encryption
                ),
            )
            dest.close()

            results.append(
                {
                    "filename": output_filename,
                    "path": output_path,
                    "payslip": payslip,
                }
            )

    return results


def _sanitize_filename(name: str) -> str:
    """Remove characters that are problematic in filenames."""
    # Keep Hebrew chars, alphanumerics, spaces, hyphens, underscores
    sanitized = ""
    for ch in name:
        if ch.isalnum() or ch in (" ", "-", "_", ".") or "\u0590" <= ch <= "\u05FF":
            sanitized += ch
        else:
            sanitized += "_"
    return sanitized.strip()


def _generate_owner_password(seed: str) -> str:
    """
    Generate a strong owner password derived from the seed.
    The owner password prevents users from changing PDF permissions.
    """
    import hashlib

    return hashlib.sha256(f"payslip-owner-{seed}-secret".encode()).hexdigest()[:32]
