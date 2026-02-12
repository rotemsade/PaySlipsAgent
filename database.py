"""
SQLite database for tracking employees, payslip processing, and email delivery.

Tables:
  - employees: cached employee details (name, tz, email)
  - payslip_batches: each uploaded PDF (original filename, upload date)
  - payslip_records: individual payslip per employee per batch
"""

import sqlite3
import os
from datetime import datetime
from config import Config

DB_PATH = os.path.join(os.path.dirname(__file__), "payslips.db")


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT UNIQUE NOT NULL,  -- Teudat Zehut
            name TEXT NOT NULL,
            email TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS payslip_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT,
            total_pages INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'processing'  -- processing, completed, failed
        );

        CREATE TABLE IF NOT EXISTS payslip_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES payslip_batches(id),
            employee_db_id INTEGER REFERENCES employees(id),
            employee_id TEXT,          -- ת.ז
            employee_name TEXT,
            employee_email TEXT,
            month INTEGER,
            year INTEGER,
            page_number INTEGER,
            output_filename TEXT,
            encrypted_path TEXT,
            email_sent INTEGER NOT NULL DEFAULT 0,     -- 0=not sent, 1=sent
            email_sent_at TEXT,
            email_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_records_batch ON payslip_records(batch_id);
        CREATE INDEX IF NOT EXISTS idx_records_employee ON payslip_records(employee_id);
        CREATE INDEX IF NOT EXISTS idx_records_period ON payslip_records(year, month);
        """
    )
    conn.commit()
    conn.close()


# ---------- Employee operations ----------


def upsert_employee(employee_id: str, name: str, email: str | None = None) -> int:
    """Insert or update an employee. Returns the database row id."""
    conn = get_db()
    cursor = conn.execute(
        "SELECT id, name, email FROM employees WHERE employee_id = ?",
        (employee_id,),
    )
    row = cursor.fetchone()

    if row:
        # Update if name or email changed
        if row["name"] != name or (email and row["email"] != email):
            conn.execute(
                "UPDATE employees SET name = ?, email = COALESCE(?, email), updated_at = datetime('now') WHERE employee_id = ?",
                (name, email, employee_id),
            )
            conn.commit()
        db_id = row["id"]
    else:
        cursor = conn.execute(
            "INSERT INTO employees (employee_id, name, email) VALUES (?, ?, ?)",
            (employee_id, name, email),
        )
        conn.commit()
        db_id = cursor.lastrowid

    conn.close()
    return db_id


def get_employee_by_tz(employee_id: str) -> dict | None:
    """Look up an employee by their Teudat Zehut."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_employee_by_name(name: str) -> dict | None:
    """Look up an employee by their full name (exact match)."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM employees WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_employee_by_email(email: str) -> dict | None:
    """Look up an employee by their email address."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM employees WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_employee(db_id: int, name: str, employee_id: str, email: str | None) -> bool:
    """Update an employee record by its database row id. Returns True if updated."""
    conn = get_db()
    conn.execute(
        """UPDATE employees
        SET name = ?, employee_id = ?, email = ?, updated_at = datetime('now')
        WHERE id = ?""",
        (name, employee_id, email or None, db_id),
    )
    conn.commit()
    changed = conn.total_changes > 0
    conn.close()
    return changed


def get_all_employees() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Batch operations ----------


def create_batch(original_filename: str, total_pages: int) -> int:
    """Create a new batch record. Returns the batch id."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO payslip_batches (original_filename, total_pages) VALUES (?, ?)",
        (original_filename, total_pages),
    )
    conn.commit()
    batch_id = cursor.lastrowid
    conn.close()
    return batch_id


def update_batch_status(batch_id: int, status: str):
    conn = get_db()
    conn.execute(
        "UPDATE payslip_batches SET status = ? WHERE id = ?",
        (status, batch_id),
    )
    conn.commit()
    conn.close()


def get_all_batches() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM payslip_batches ORDER BY processed_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Payslip record operations ----------


def create_payslip_record(
    batch_id: int,
    employee_db_id: int | None,
    employee_id: str | None,
    employee_name: str | None,
    employee_email: str | None,
    month: int | None,
    year: int | None,
    page_number: int,
    output_filename: str | None = None,
    encrypted_path: str | None = None,
) -> int:
    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO payslip_records
        (batch_id, employee_db_id, employee_id, employee_name, employee_email,
         month, year, page_number, output_filename, encrypted_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_id,
            employee_db_id,
            employee_id,
            employee_name,
            employee_email,
            month,
            year,
            page_number,
            output_filename,
            encrypted_path,
        ),
    )
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return record_id


def update_record_email_status(
    record_id: int, sent: bool, error: str | None = None
):
    conn = get_db()
    conn.execute(
        """UPDATE payslip_records
        SET email_sent = ?, email_sent_at = ?, email_error = ?
        WHERE id = ?""",
        (
            1 if sent else 0,
            datetime.now().isoformat() if sent else None,
            error,
            record_id,
        ),
    )
    conn.commit()
    conn.close()


def update_record_file_info(
    record_id: int, output_filename: str, encrypted_path: str
):
    conn = get_db()
    conn.execute(
        "UPDATE payslip_records SET output_filename = ?, encrypted_path = ? WHERE id = ?",
        (output_filename, encrypted_path, record_id),
    )
    conn.commit()
    conn.close()


def get_records_for_batch(batch_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM payslip_records WHERE batch_id = ? ORDER BY page_number",
        (batch_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_history(limit: int = 100) -> list[dict]:
    """Get recent payslip records with batch info for the history page."""
    conn = get_db()
    rows = conn.execute(
        """SELECT r.*, b.original_filename, b.processed_at as batch_date
        FROM payslip_records r
        JOIN payslip_batches b ON r.batch_id = b.id
        ORDER BY r.created_at DESC
        LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_already_processed(employee_id: str, month: int, year: int) -> bool:
    """Check if a payslip was already successfully sent for this employee/period."""
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM payslip_records
        WHERE employee_id = ? AND month = ? AND year = ? AND email_sent = 1
        LIMIT 1""",
        (employee_id, month, year),
    ).fetchone()
    conn.close()
    return row is not None


# Initialize on import
init_db()
