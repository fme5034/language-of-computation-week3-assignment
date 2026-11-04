#!/usr/bin/env python3
"""Load a books CSV file into books.db, following rules.md.

Usage:
    python ingest.py <csv_file>

books.db and ingestion-log.json are written to the current working directory.
Python 3 standard library only. No AI, no network access: the same input file
always produces the same database changes and the same log contents (apart
from the run timestamp).
"""

import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB_FILE = "books.db"
LOG_FILE = "ingestion-log.json"

# Columns the rules depend on. If any is missing from the header, the run
# halts with FATAL_MISSING_COLUMNS and books.db is not touched.
REQUIRED_COLUMNS = ["isbn", "isbn13", "title", "authors", "original_publication_year"]

# Schema exactly as given in rules.md, Section 3.
SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    isbn13 TEXT PRIMARY KEY,
    isbn10 TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    publication_year INTEGER,
    secondary_isbn10 TEXT,
    secondary_isbn13 TEXT,
    raw_isbn TEXT,
    raw_isbn13 TEXT,
    warning TEXT
);
"""

# Fields an incoming row may fill on an existing record (rules.md, Section 7).
UPDATABLE_FIELDS = ["isbn10", "publication_year", "secondary_isbn10"]

# Codes that mark a row as needing a manual catalog lookup.
MANUAL_REVIEW_WARNINGS = {
    "WARNING_EDITION_MISMATCH",
    "WARNING_ISBN13_INCONSISTENT",
    "WARNING_METADATA_MISMATCH",
}
MANUAL_REVIEW_REJECTIONS = {"ISBN_COLLISION_DIFFERENT_TITLE"}
# NON_BOOK_IDENTIFIER rejections are flagged only when the row has a title.

SCI_RE = re.compile(r"^(\d)(?:\.(\d+))?[eE]\+?(\d+)$")
YEAR_RE = re.compile(r"^-?\d+(?:\.0+)?$")


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------

def isbn10_check_digit(first9):
    """Mod-11 check digit for nine ISBN-10 digits ('X' stands for 10)."""
    total = sum((10 - i) * int(d) for i, d in enumerate(first9))
    check = (11 - total % 11) % 11
    return "X" if check == 10 else str(check)


def isbn10_valid(value):
    if not re.fullmatch(r"\d{9}[\dX]", value):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value))
    return total % 11 == 0


def isbn13_check_digit(first12):
    """Mod-10 check digit for twelve ISBN-13 digits (weights 1 and 3)."""
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def isbn13_valid(value):
    return bool(re.fullmatch(r"\d{13}", value)) and isbn13_check_digit(value[:12]) == value[12]


def isbn10_to_isbn13(isbn10):
    body = "978" + isbn10[:9]
    return body + isbn13_check_digit(body)


def isbn13_to_isbn10(isbn13):
    """Only 978-prefix ISBN-13s have an ISBN-10 counterpart; 979 gives None."""
    if not isbn13.startswith("978"):
        return None
    body = isbn13[3:12]
    return body + isbn10_check_digit(body)


def is_book_prefix(isbn13_digits):
    """978 and 979 are book prefixes, except 9790 (music ISMN)."""
    if isbn13_digits.startswith("9790"):
        return False
    return isbn13_digits.startswith(("978", "979"))


# --------------------------------------------------------------------------
# Cleanup and per-column analysis
# --------------------------------------------------------------------------

def repair(column, repair_type, original, repaired):
    return {"column": column, "repair_type": repair_type,
            "original": original, "repaired": repaired}


def cleanup(raw, column, repairs):
    """Formatting repairs: strip whitespace and hyphens, strip a trailing
    '.0', uppercase a lowercase 'x' in the check-digit (last) position.
    Returns (cleaned_value, dot_zero_was_stripped)."""
    value = raw
    stripped = re.sub(r"[\s\-]", "", value)
    if stripped != value and stripped != "":
        repairs.append(repair(column, "FORMATTING_STRIPPED", value, stripped))
    value = stripped

    dot_zero = False
    if re.fullmatch(r"\d+\.0", value):
        new = value[:-2]
        repairs.append(repair(column, "TRAILING_DOT_ZERO_STRIPPED", value, new))
        value = new
        dot_zero = True

    if value.endswith("x"):
        new = value[:-1] + "X"
        repairs.append(repair(column, "LOWERCASE_X_UPPERCASED", value, new))
        value = new
    return value, dot_zero


def shape_codes(value):
    """CORRUPT_CHARACTERS and/or INVALID_LENGTH for a cleaned value.
    'X' is allowed only as the last character of a 10-character value."""
    codes = []
    if value:
        body_bad = any(not c.isdigit() for c in value[:-1])
        last = value[-1]
        last_bad = not (last.isdigit() or (last == "X" and len(value) == 10))
        if body_bad or last_bad:
            codes.append("CORRUPT_CHARACTERS")
    if len(value) not in (10, 13):
        codes.append("INVALID_LENGTH")
    return codes


def analyse_as_isbn10(value, column, repairs):
    """Leading-zero padding, Mod-11 check, stripped-978 repair.
    Returns (valid_isbn10 or None, codes)."""
    if re.fullmatch(r"\d{7,9}", value):
        padded = value.zfill(10)
        repairs.append(repair(column, "LEADING_ZEROS_PADDED", value, padded))
        value = padded
    codes = shape_codes(value)
    if codes:
        return None, codes
    if len(value) != 10:
        return None, ["INVALID_LENGTH"]
    if isbn10_valid(value):
        return value, []
    if value.isdigit() and isbn13_valid("978" + value):
        fixed = value[:9] + isbn10_check_digit(value[:9])
        repairs.append(repair(column, "STRIPPED_978_CHECK_DIGIT", value, fixed))
        return fixed, []
    return None, ["BAD_CHECKSUM"]


def analyse_as_isbn13(value):
    """Prefix and Mod-10 check for a 13-digit value. Returns (valid or None, codes)."""
    if not is_book_prefix(value):
        return None, ["NON_BOOK_IDENTIFIER"]
    if not isbn13_valid(value):
        return None, ["BAD_CHECKSUM"]
    return value, []


def new_result(raw):
    return {"raw": raw, "empty": False, "isbn10": None, "isbn13": None,
            "sci": None, "codes": []}


def analyse_isbn_column(raw, repairs):
    """The `isbn` column: an ISBN-10, or an ISBN-13 stored in the wrong column."""
    res = new_result(raw)
    value, _ = cleanup(raw, "isbn", repairs)
    if value == "":
        res["empty"] = True
        return res
    if re.fullmatch(r"\d{13}", value):
        repairs.append(repair("isbn", "COLUMN_INVERSION", value, "isbn13 column"))
        res["isbn13"], res["codes"] = analyse_as_isbn13(value)
        return res
    res["isbn10"], res["codes"] = analyse_as_isbn10(value, "isbn", repairs)
    return res


def parse_scientific(text):
    """Return (mantissa_digits, exponent, integer_digits) or None.
    integer_digits is None when the value is not a whole number."""
    m = SCI_RE.fullmatch(text)
    if not m:
        return None
    mantissa = m.group(1) + (m.group(2) or "")
    exponent = int(m.group(3))
    total_digits = exponent + 1
    if len(mantissa) > total_digits:
        return mantissa, exponent, None
    return mantissa, exponent, mantissa.ljust(total_digits, "0")


def analyse_isbn13_column(raw, repairs):
    """The `isbn13` column: an ISBN-13, a scientific-notation float, or an
    ISBN-10 stored in the wrong column."""
    res = new_result(raw)
    sci = parse_scientific(raw.strip())
    if sci is not None:
        mantissa, exponent, digits = sci
        res["sci"] = {"mantissa": mantissa, "exponent": exponent, "digits": digits,
                      "is_book": bool(digits) and len(digits) == 13 and is_book_prefix(digits)}
        return res

    value, dot_zero = cleanup(raw, "isbn13", repairs)
    if value == "":
        res["empty"] = True
        return res
    if re.fullmatch(r"\d{13}", value):
        res["isbn13"], res["codes"] = analyse_as_isbn13(value)
        return res
    looks_like_isbn10 = (dot_zero and value.isdigit() and len(value) <= 10) or len(value) == 10
    if looks_like_isbn10:
        repairs.append(repair("isbn13", "COLUMN_INVERSION", value, "isbn column"))
        res["isbn10"], res["codes"] = analyse_as_isbn10(value, "isbn13", repairs)
        return res
    res["codes"] = shape_codes(value) or ["INVALID_LENGTH"]
    return res


def sci_consistent(rebuilt, sci):
    """WARNING_ISBN13_INCONSISTENT test: round the rebuilt ISBN-13 to the
    stored float's precision and compare. Writers drop trailing zeros
    (9.7815651256e+12 was written at 12 significant digits), so every
    precision from the number of digits shown up to 13 is tried."""
    if sci["digits"] is None or len(sci["digits"]) != 13:
        return False
    stored = int(sci["digits"])
    as_float = float(int(rebuilt))  # exact: 13-digit integers fit in a float
    for precision in range(len(sci["mantissa"]), 14):
        if int(float(f"{as_float:.{precision - 1}e}")) == stored:
            return True
    return False


# --------------------------------------------------------------------------
# Row analysis
# --------------------------------------------------------------------------

def unique(seq):
    out = []
    for item in seq:
        if item not in out:
            out.append(item)
    return out


def analyse_row(row):
    """Apply the repair / reject / warn rules to one CSV row.

    Returns a dict with either 'reject_codes' (non-empty) or a 'record'
    ready for the database, plus 'warnings', 'warning_details',
    'repairs' and 'details'."""
    raw_isbn = row["isbn"]
    raw_isbn13 = row["isbn13"]
    repairs_a = []  # repairs made while reading the isbn column
    repairs_b = []  # repairs made while reading the isbn13 column
    repairs = []    # repairs made while combining the two
    warnings = []
    warning_details = []
    details = []

    a = analyse_isbn_column(raw_isbn, repairs_a)
    b = analyse_isbn13_column(raw_isbn13, repairs_b)

    isbn10 = None
    canonical13 = None
    secondary10 = None
    secondary13 = None
    isbn_codes = []

    def discard(column, raw_value, reason):
        warnings.append("WARNING_DISCARDED_COLUMN")
        warning_details.append(f"{column} value {raw_value!r} discarded ({reason})")

    if a["empty"] and b["empty"]:
        isbn_codes.append("MISSING_ISBN")
        details.append("isbn and isbn13 are both empty")
    else:
        # Valid ISBN-10: the isbn column wins; an ISBN-10 found in the
        # isbn13 column is canonical only when the isbn column has none.
        valid10 = a["isbn10"] or b["isbn10"]
        if a["isbn10"] and b["isbn10"] and a["isbn10"] != b["isbn10"]:
            secondary10 = b["isbn10"]
            discard("isbn13", raw_isbn13, "a second, different ISBN-10")

        # Valid ISBN-13: the isbn13 column wins over an inverted one.
        valid13 = b["isbn13"] or a["isbn13"]
        if a["isbn13"] and b["isbn13"] and a["isbn13"] != b["isbn13"]:
            secondary13 = a["isbn13"]
            warnings.append("WARNING_EDITION_MISMATCH")
            warning_details.append(
                f"isbn column ISBN-13 {a['isbn13']} differs from isbn13 column {b['isbn13']}")

        if valid13 and valid10:
            canonical13 = valid13
            derived = isbn10_to_isbn13(valid10)
            if derived == valid13:
                isbn10 = valid10
            else:
                isbn10 = isbn13_to_isbn10(valid13)
                secondary10 = valid10
                warnings.append("WARNING_EDITION_MISMATCH")
                warning_details.append(
                    f"ISBN-10 {valid10} implies {derived}, but ISBN-13 is {valid13}")
        elif valid13:
            canonical13 = valid13
            isbn10 = isbn13_to_isbn10(valid13)
            if isbn10:
                repairs.append(repair("isbn", "COUNTERPART_DERIVED", "", isbn10))
            if b["sci"] is not None:
                # Only reachable when the ISBN-13 came from the isbn column.
                discard("isbn13", raw_isbn13, "scientific notation; isbn column holds the ISBN-13")
        elif valid10:
            isbn10 = valid10
            canonical13 = isbn10_to_isbn13(valid10)
            if b["sci"] is not None:
                repairs.append(repair("isbn13", "SCIENTIFIC_NOTATION_REBUILT",
                                      raw_isbn13, canonical13))
                if not b["sci"]["is_book"]:
                    discard("isbn13", raw_isbn13, "not a book identifier")
                elif not sci_consistent(canonical13, b["sci"]):
                    warnings.append("WARNING_ISBN13_INCONSISTENT")
                    warning_details.append(
                        f"rebuilt ISBN-13 {canonical13} does not round to stored {raw_isbn13}")
            else:
                repairs.append(repair("isbn13", "COUNTERPART_DERIVED", "", canonical13))

        if canonical13 is None:
            # Neither column produced a usable ISBN: reject with every code.
            for label, res in (("isbn", a), ("isbn13", b)):
                if res["sci"] is not None:
                    codes = []
                    if not res["sci"]["is_book"]:
                        codes.append("NON_BOOK_IDENTIFIER")
                    codes.append("UNRECOVERABLE_ISBN13")
                    isbn_codes.extend(codes)
                    details.append(f"{label}: {','.join(codes)} ({res['raw']!r})")
                elif res["codes"]:
                    isbn_codes.extend(res["codes"])
                    details.append(f"{label}: {','.join(res['codes'])} ({res['raw']!r})")
        else:
            # A non-empty column that failed is discarded in favour of the good one.
            for label, res in (("isbn", a), ("isbn13", b)):
                if res["codes"] and not res["empty"]:
                    discard(label, res["raw"], ",".join(res["codes"]))

    # Year
    year_codes = []
    year_text = row["original_publication_year"].strip()
    year = None
    if year_text:
        if YEAR_RE.fullmatch(year_text):
            year = int(year_text.split(".")[0])
            if not -4000 <= year <= 2100:
                year = None
                year_codes.append("CORRUPT_YEAR")
        else:
            year_codes.append("CORRUPT_YEAR")
        if year_codes:
            details.append(f"original_publication_year: CORRUPT_YEAR ({row['original_publication_year']!r})")

    # Required fields
    field_codes = []
    for field in ("title", "authors"):
        if row[field].strip() == "":
            field_codes.append("MISSING_REQUIRED_FIELD")
            details.append(f"{field} is empty")

    # Keep a column's own repairs only if that column's value was used.
    used_a = a["isbn10"] is not None or a["isbn13"] is not None
    used_b = b["isbn10"] is not None or b["isbn13"] is not None
    all_repairs = (repairs_a if used_a else []) + (repairs_b if used_b else []) + repairs

    reject_codes = unique(isbn_codes + year_codes + field_codes)
    result = {"reject_codes": reject_codes, "details": details, "repairs": all_repairs,
              "warnings": unique(warnings), "warning_details": warning_details}
    if not reject_codes:
        result["record"] = {
            "isbn13": canonical13,
            "isbn10": isbn10,
            "title": row["title"],
            "authors": row["authors"],
            "publication_year": year,
            "secondary_isbn10": secondary10,
            "secondary_isbn13": secondary13,
            "raw_isbn": raw_isbn if raw_isbn != "" else None,
            "raw_isbn13": raw_isbn13 if raw_isbn13 != "" else None,
        }
    return result


# --------------------------------------------------------------------------
# Comparison helpers
# --------------------------------------------------------------------------

def normalize(text):
    """Strip, collapse internal whitespace, lowercase."""
    return " ".join((text or "").split()).lower()


def metadata_differs(existing_authors, existing_year, authors, year):
    """Author differs (normalized), or both years present and different."""
    if normalize(existing_authors) != normalize(authors):
        return True
    return existing_year is not None and year is not None and existing_year != year


def append_warning(current, code):
    codes = [c for c in (current or "").split(",") if c]
    if code not in codes:
        codes.append(code)
    return ",".join(codes)


# --------------------------------------------------------------------------
# Log handling
# --------------------------------------------------------------------------

def load_log():
    """Existing runs, or [] when the log does not exist yet. Exits without
    touching anything if the log exists but cannot be read, so earlier runs
    are never overwritten."""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        sys.exit(f"ERROR: {LOG_FILE} exists but cannot be read ({exc}). "
                 "Nothing was changed. Fix or move the file and run again.")
    if not isinstance(data, list):
        sys.exit(f"ERROR: {LOG_FILE} is not a JSON list of runs. "
                 "Nothing was changed. Fix or move the file and run again.")
    return data


def write_log(runs):
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, LOG_FILE)


def new_run(csv_path):
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file": os.path.basename(csv_path),
        "status": "SUCCESS",
        "error": None,
        "counts": {
            "rows_read": 0,
            "rows_loaded": 0,
            "rows_updated": 0,
            "rows_skipped_duplicate": 0,
            "rows_skipped_already_exists": 0,
            "rows_rejected": 0,
        },
        "not_loaded": [],
        "warnings": [],
        "repairs": [],
        "needs_manual_review": [],
    }


# --------------------------------------------------------------------------
# Main ingestion
# --------------------------------------------------------------------------

def row_ref(line, raw_isbn, raw_isbn13, title):
    return {"line": line, "raw_isbn": raw_isbn, "raw_isbn13": raw_isbn13, "title": title}


def ingest(csv_path, run):
    counts = run["counts"]

    def not_loaded(ref, status, reason, details):
        entry = dict(ref)
        entry.update({"status": status, "reason": reason, "details": details})
        run["not_loaded"].append(entry)

    def flag_review(ref, codes):
        entry = dict(ref)
        entry["codes"] = codes
        run["needs_manual_review"].append(entry)

    def log_warnings(ref, codes, details, row_status, isbn13):
        if not codes:
            return
        entry = dict(ref)
        entry.update({"isbn13": isbn13, "codes": codes, "details": details,
                      "row_status": row_status})
        run["warnings"].append(entry)
        review = [c for c in codes if c in MANUAL_REVIEW_WARNINGS]
        if review:
            flag_review(ref, review)

    def log_repairs(line, title, repairs):
        for r in repairs:
            entry = {"line": line, "title": title}
            entry.update(r)
            run["repairs"].append(entry)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise FatalError("FATAL_MISSING_COLUMNS: " + ", ".join(missing))
        index = {name: header.index(name) for name in header}

        # Only now is books.db opened, so a fatal header leaves it untouched.
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute(SCHEMA)
            seen = {}  # canonical ISBN-13 -> (line, normalized title, authors, year)
            prev_line_end = reader.line_num

            for fields in reader:
                line = prev_line_end + 1
                prev_line_end = reader.line_num
                counts["rows_read"] += 1

                def get(name):
                    i = index[name]
                    return fields[i] if i < len(fields) else ""

                ref = row_ref(line, get("isbn"), get("isbn13"), get("title"))

                if len(fields) != len(header):
                    counts["rows_rejected"] += 1
                    not_loaded(ref, "rejected", "MALFORMED_ROW",
                               [f"expected {len(header)} columns, found {len(fields)}"])
                    continue

                row = {name: fields[i] for name, i in index.items()}
                result = analyse_row(row)

                if result["reject_codes"]:
                    counts["rows_rejected"] += 1
                    reason = ",".join(result["reject_codes"])
                    not_loaded(ref, "rejected", reason, result["details"])
                    review = [c for c in result["reject_codes"] if c in MANUAL_REVIEW_REJECTIONS]
                    if "NON_BOOK_IDENTIFIER" in result["reject_codes"] and row["title"].strip():
                        review.append("NON_BOOK_IDENTIFIER")
                    if review:
                        flag_review(ref, review)
                    continue

                rec = result["record"]
                key = rec["isbn13"]
                ntitle = normalize(rec["title"])
                warnings = list(result["warnings"])
                warning_details = list(result["warning_details"])

                # 1. Duplicates within this file (checked before the database).
                if key in seen:
                    first_line, first_title, first_authors, first_year = seen[key]
                    if first_title != ntitle:
                        counts["rows_rejected"] += 1
                        detail = [f"ISBN-13 {key} already used on line {first_line} by a different title"]
                        not_loaded(ref, "rejected", "ISBN_COLLISION_DIFFERENT_TITLE", detail)
                        flag_review(ref, ["ISBN_COLLISION_DIFFERENT_TITLE"])
                        continue
                    counts["rows_skipped_duplicate"] += 1
                    not_loaded(ref, "duplicate_skipped", "duplicate_skipped",
                               [f"same ISBN-13 {key} and title as line {first_line}"])
                    if metadata_differs(first_authors, first_year, rec["authors"], rec["publication_year"]):
                        warnings.append("WARNING_METADATA_MISMATCH")
                        warning_details.append(f"author or year differs from line {first_line}")
                    log_warnings(ref, unique(warnings), warning_details, "duplicate_skipped", key)
                    continue

                # 2. Records already in the database.
                existing = conn.execute(
                    "SELECT title, authors, publication_year, isbn10, secondary_isbn10, warning "
                    "FROM books WHERE isbn13 = ?", (key,)).fetchone()

                if existing is None:
                    conn.execute(
                        "INSERT INTO books (isbn13, isbn10, title, authors, publication_year, "
                        "secondary_isbn10, secondary_isbn13, raw_isbn, raw_isbn13, warning) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, rec["isbn10"], rec["title"], rec["authors"], rec["publication_year"],
                         rec["secondary_isbn10"], rec["secondary_isbn13"], rec["raw_isbn"],
                         rec["raw_isbn13"], ",".join(unique(warnings)) or None))
                    counts["rows_loaded"] += 1
                    seen[key] = (line, ntitle, rec["authors"], rec["publication_year"])
                    log_repairs(line, rec["title"], result["repairs"])
                    log_warnings(ref, unique(warnings), warning_details, "loaded", key)
                    continue

                ex_title, ex_authors, ex_year, ex_isbn10, ex_sec10, ex_warning = existing
                if normalize(ex_title) != ntitle:
                    counts["rows_rejected"] += 1
                    detail = [f"ISBN-13 {key} already in books.db with title {ex_title!r}"]
                    not_loaded(ref, "rejected", "ISBN_COLLISION_DIFFERENT_TITLE", detail)
                    flag_review(ref, ["ISBN_COLLISION_DIFFERENT_TITLE"])
                    continue

                seen[key] = (line, ntitle, rec["authors"], rec["publication_year"])
                differs = metadata_differs(ex_authors, ex_year, rec["authors"], rec["publication_year"])
                current = {"isbn10": ex_isbn10, "publication_year": ex_year,
                           "secondary_isbn10": ex_sec10}
                fills = {f: rec[f] for f in UPDATABLE_FIELDS
                         if current[f] is None and rec[f] is not None}
                new_warning = ex_warning
                if differs:
                    warnings.append("WARNING_METADATA_MISMATCH")
                    warning_details.append("author or year differs from the record in books.db")
                    new_warning = append_warning(ex_warning, "WARNING_METADATA_MISMATCH")

                if fills:
                    assignments = ", ".join(f"{f} = ?" for f in fills)
                    conn.execute(f"UPDATE books SET {assignments}, warning = ? WHERE isbn13 = ?",
                                 list(fills.values()) + [new_warning, key])
                    counts["rows_updated"] += 1
                    log_repairs(line, rec["title"], result["repairs"])
                    log_warnings(ref, unique(warnings), warning_details, "updated", key)
                else:
                    if new_warning != ex_warning:
                        conn.execute("UPDATE books SET warning = ? WHERE isbn13 = ?",
                                     (new_warning, key))
                    counts["rows_skipped_already_exists"] += 1
                    not_loaded(ref, "skipped_already_exists", "skipped_already_exists",
                               [f"ISBN-13 {key} already in books.db"])
                    log_warnings(ref, unique(warnings), warning_details,
                                 "skipped_already_exists", key)

            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class FatalError(Exception):
    pass


def main(argv):
    if len(argv) != 2:
        print("Usage: python ingest.py <csv_file>", file=sys.stderr)
        return 2
    csv_path = argv[1]
    runs = load_log()  # read first: an unreadable log stops the run untouched
    run = new_run(csv_path)
    exit_code = 0
    try:
        ingest(csv_path, run)
    except Exception as exc:  # FatalError, file errors, anything unexpected
        run["status"] = "FAILED"
        run["error"] = str(exc) if isinstance(exc, FatalError) else f"{type(exc).__name__}: {exc}"
        exit_code = 1
    runs.append(run)
    write_log(runs)

    c = run["counts"]
    print(f"{run['status']}: {run['file']} at {run['timestamp']}")
    if run["error"]:
        print(f"  error: {run['error']} (books.db was not changed)")
    print(f"  read {c['rows_read']}, loaded {c['rows_loaded']}, updated {c['rows_updated']}, "
          f"duplicates skipped {c['rows_skipped_duplicate']}, "
          f"already existing {c['rows_skipped_already_exists']}, rejected {c['rows_rejected']}")
    print(f"  warnings {len(run['warnings'])}, needs manual review {len(run['needs_manual_review'])}")
    print(f"  log: {LOG_FILE}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
