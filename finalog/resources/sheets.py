#written with Claude Code


import os
import re
from datetime import datetime, date
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from dotenv import load_dotenv

load_dotenv()

# Env vars:
#   GOOGLE_SHEETS_CREDS  - path to service account JSON key file
#   GOOGLE_SHEET_ID      - spreadsheet ID from the URL

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Columns: A=#, B=Date, C=Item, D=Amount, E=Notes
# Row 1 is the header; data starts at row 2.


def _sheet_name() -> str:
    return os.getenv("SHEET_NAME", "Spring 2026")


def _column_range() -> str:
    """Return column range like 'A:E'."""
    return os.getenv("COLUMN_RANGE", "A:E")


def _col_start() -> str:
    return _column_range().split(":")[0]


def _col_end() -> str:
    return _column_range().split(":")[1]

# Year to assume for sheet dates that lack a year
_DEFAULT_YEAR = 2026


def _get_service():
    creds_path = os.getenv("GOOGLE_SHEETS_CREDS")
    if not creds_path:
        raise RuntimeError("GOOGLE_SHEETS_CREDS env var not set (path to service account JSON)")
    creds = Credentials.from_service_account_file(os.path.join(os.getcwd(), creds_path), scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _sheet_id():
    sid = os.getenv("GOOGLE_SHEET_ID")
    if not sid:
        raise RuntimeError("GOOGLE_SHEET_ID env var not set")
    return sid


def _get_sheet_gid() -> int:
    """Get the numeric sheet ID for the named tab (needed for insertDimension)."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=_sheet_id()).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == _sheet_name():
            return sheet["properties"]["sheetId"]
    raise RuntimeError(f"Sheet tab '{_sheet_name()}' not found")


# ── Date parsing ──────────────────────────────────────────────────────────────

# Matches sheet dates like "Friday, Jan 2" or "Saturday, Jan 31"
_SHEET_DATE_RE = re.compile(r"(?:\w+,\s+)?(\w{3})\s+(\d{1,2})")

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_sheet_date(date_str: str, prev: date | None = None) -> date | None:
    """Parse a sheet date cell into a date object.

    Empty / '~' / '??' → inherits previous date. Returns None if no date
    can be determined at all.
    """
    s = date_str.strip()
    if not s or s in ("~", "??", ""):
        return prev

    m = _SHEET_DATE_RE.match(s)
    if m:
        mon = _MONTH_MAP.get(m.group(1))
        day = int(m.group(2))
        if mon:
            # If month goes backward (e.g. Dec→Jan), bump the year
            year = _DEFAULT_YEAR
            if prev and mon < prev.month:
                year = prev.year + 1
            return date(year, mon, day)
    return prev


def _parse_iso_date(date_str: str) -> date | None:
    """Parse an ISO date (YYYY-MM-DD) from Gemini output."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_sheet_date(d: date) -> str:
    """Format a date into the sheet style: 'Wednesday, Jan 14'."""
    return d.strftime("%A, %b %-d")


# ── Read ──────────────────────────────────────────────────────────────────────

def read_transactions(range_str: str = None) -> list[list[str]]:
    """Read existing rows from the sheet.

    Returns:
        list of rows, each row is [#, date, item, amount, notes?]
    """
    service = _get_service()
    if range_str is None:
        range_str = f"'{_sheet_name()}'!{_col_start()}2:{_col_end()}"

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_sheet_id(), range=range_str)
        .execute()
    )
    return result.get("values", [])


def _read_data_rows() -> list[list[str]]:
    """Read all rows and filter to only rows that have actual data (not just a row number)."""
    all_rows = read_transactions()
    data = []
    for row in all_rows:
        # A row with data has at least 3 columns (# + date + item) or a non-empty item
        if len(row) >= 3 and any(cell.strip() for cell in row[1:]):
            data.append(row)
        elif len(row) >= 2 and row[1].strip():
            data.append(row)
    return data


# ── Insert logic ──────────────────────────────────────────────────────────────

def _find_insert_index(data_rows: list[list[str]], target: date) -> int:
    """Find the index in data_rows where a transaction with `target` date should go.

    Returns the index of the first row whose date is strictly after `target`,
    so the new row lands at the end of the target date's group.
    """
    parsed_date = None
    for i, row in enumerate(data_rows):
        date_cell = row[1] if len(row) > 1 else ""
        parsed_date = _parse_sheet_date(date_cell, parsed_date)
        if parsed_date and parsed_date > target:
            return i
    # After all rows → append at end
    return len(data_rows)


def insert_transactions(transactions: list[dict]) -> int:
    """Insert transactions into the correct date-sorted position, or append if newest.

    Args:
        transactions: list of dicts with keys: date, company, amount
                      (from the Gemini extract schema, dates in ISO format)

    Returns:
        number of rows inserted
    """
    if not transactions:
        return 0

    service = _get_service()
    spreadsheet_id = _sheet_id()
    sheet_gid = _get_sheet_gid()
    data_rows = _read_data_rows()

    # Sort new transactions by date so earlier ones are inserted first
    # (insert from bottom-up to keep indices stable)
    parsed_txns = []
    for txn in transactions:
        d = _parse_iso_date(txn.get("date", ""))
        parsed_txns.append((d, txn))

    # Sort: transactions with dates first, then None-dates at the end
    parsed_txns.sort(key=lambda x: (x[0] is None, x[0] or date.max))

    # Build list of (sheet_row_index, new_row_values) — index into the data rows
    inserts = []  # (insert_position_in_data, [A, B, C, D])
    for d, txn in parsed_txns:
        if d is not None:
            idx = _find_insert_index(data_rows, d)
        else:
            idx = len(data_rows)  # no date → append

        company = txn.get("company") or ""
        amount = txn.get("amount") or ""
        date_str = _format_sheet_date(d) if d else ""
        row_vals = ["", date_str, company, amount]  # A col renumbered later

        # Insert into our local copy so subsequent inserts see it
        data_rows.insert(idx, row_vals)
        inserts.append(idx)

    # Renumber column A
    for i, row in enumerate(data_rows):
        row[0] = str(i + 2)  # row 2 is first data row

    # Now figure out which are mid-sheet inserts vs appends.
    # Easiest: rewrite all data rows (A2:E) in one shot. For sheets with <1000 rows
    # this is fast and avoids complex insert/shift logic.
    # Pad rows to 5 columns
    padded = []
    for row in data_rows:
        r = list(row) + [""] * (5 - len(row))
        padded.append(r[:5])

    # Clear existing data and write fresh
    end_row = len(padded) + 1  # +1 for header
    clear_range = f"'{_sheet_name()}'!{_col_start()}2:{_col_end()}{end_row}"
    write_range = f"'{_sheet_name()}'!{_col_start()}2:{_col_end()}{end_row}"

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=clear_range,
        body={},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=write_range,
        valueInputOption="USER_ENTERED",
        body={"values": padded},
    ).execute()

    return len(inserts)


# ── Compat wrapper ────────────────────────────────────────────────────────────

def append_transactions(transactions: list[dict]) -> int:
    """Alias — routes through insert_transactions which handles both
    mid-sheet insertion and end-of-sheet appending."""
    return insert_transactions(transactions)


# ── CSV output ────────────────────────────────────────────────────────────────

import csv

def insert_transactions_csv(transactions: list[dict], csv_path: str) -> int:
    """Append transactions to a CSV file.

    Creates the file with a header row if it doesn't exist.

    Args:
        transactions: list of dicts with keys: date, company, amount
        csv_path: path to the output CSV file

    Returns:
        number of rows written
    """
    if not transactions:
        return 0

    file_exists = os.path.isfile(csv_path)
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Date", "Company", "Amount"])
        for txn in transactions:
            d = _parse_iso_date(txn.get("date", ""))
            date_str = _format_sheet_date(d) if d else ""
            writer.writerow([
                date_str,
                txn.get("company") or "",
                txn.get("amount") or "",
            ])

    return len(transactions)


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Existing rows ===")
    for row in _read_data_rows():
        print(row)

    print(f"\nTotal data rows: {len(_read_data_rows())}")
