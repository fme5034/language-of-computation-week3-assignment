# Book Ingestion Rules & Data Specification (Final)

## 1. Problem Treatment: Repair, Reject, or Warn?

### A. Automatic Deterministic Repairs
- **Leading Zero Padding (ISBN-10):** If an ISBN-10 has 7, 8, or 9 digits, pad with leading zeros to 10 characters. Validate Mod-11 checksum.
- **Stripped-978 Repair:** If a padded 10-digit value fails Mod-11, but `978` + the 10 characters forms a valid Mod-10 ISBN-13, recalculate the correct Mod-11 check digit for ISBN-10 and repair it.
- **Scientific Notation (ISBN-13):** Reconstruct the canonical 13-digit ISBN using the row's valid ISBN-10 (prefix `978` + Mod-10 check digit).
- **Formatting Artifacts:** Strip whitespace and hyphens. Uppercase lowercase 'x' to 'X' in check-digit position.
- **`.0` Trailing Values:** Strip trailing `.0`. If 10 digits → treat as ISBN-10 column inversion (repair). If 11+ digits → reject as `INVALID_LENGTH`.
- **Column Inversion:** If an ISBN-13 is stored in the `isbn` column, move it to `isbn13`.
- **Missing Counterpart:** Derive missing ISBN-13 from valid ISBN-10, or derive missing ISBN-10 from valid 978-prefix ISBN-13.
- **One Column Bad, Other Good:** Load using the valid column. Derive the counterpart. Log discarded corrupt value as `WARNING_DISCARDED_COLUMN`.

### B. Rejections & Standard Reason Codes
- `MISSING_ISBN` — Both ISBN columns are empty or null.
- `BAD_CHECKSUM` — Value fails checksum validation and is not repairable.
- `CORRUPT_CHARACTERS` — Non-digit characters present outside the final check-digit position.
- `INVALID_LENGTH` — Length does not equal 10 or 13 after cleanup.
- `NON_BOOK_IDENTIFIER` — Barcode, Nook ID (2940...), or music ISMN (9790...) with no valid ISBN-10 to recover from.
- `UNRECOVERABLE_ISBN13` — Scientific notation ISBN-13 with no valid ISBN-10 to rebuild from.
- `ISBN_COLLISION_DIFFERENT_TITLE` — Incoming ISBN matches an existing book with a different normalized title.
- `MALFORMED_ROW` — Row has an incorrect column count.
- `CORRUPT_YEAR` — Non-empty `original_publication_year` cannot be parsed into an integer in range [-4000, 2100].
- `MISSING_REQUIRED_FIELD` — The `title` or `authors` column is empty. The log message names the specific empty field.

### C. Warnings
- `WARNING_EDITION_MISMATCH` — Both ISBN-10 and ISBN-13 pass checksums but refer to different editions.
- `WARNING_ISBN13_INCONSISTENT` — Rebuilt ISBN-13 differs from stored scientific-notation float when rounded to same precision.
- `WARNING_DISCARDED_COLUMN` — One ISBN column was corrupt and discarded in favor of the valid column.
- `WARNING_METADATA_MISMATCH` — Matching ISBN and title found, but author or publication year differs.

---

## 2. Identity, Deduplication & Updates

- **Primary Key:** Canonical **ISBN-13** is the unique identifier.
- **Year Handling:** Empty publication years are stored as `NULL`.
- **Title Normalization:** Strip leading/trailing whitespace, collapse internal spaces, and lowercase before comparing titles.
- **Exact Duplicates in Same Batch:** If the same ISBN appears with identical title, skip as `rows_skipped_duplicate`.
- **Re-ingestion / Existing Records:**
  - If ISBN exists in `books.db` with identical title/author/year: skip as `rows_skipped_already_exists`.
  - If ISBN exists and incoming row fills a `NULL` field: update the record, count as `rows_updated`, and log `WARNING_METADATA_MISMATCH` if metadata strings differed.

---

## 3. Database Schema (`books.db`)

SQLite table `books`:
```sql
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

---

## 7. Edge Case Resolutions

- **Secondary ISBN-13 storage:** When both columns hold valid, differing ISBN-13s, store the `isbn13`-column value as canonical, put the inverted one in `secondary_isbn13`, and log `WARNING_EDITION_MISMATCH`.
- **isbn10 on edition-mismatch rows:** The `isbn10` column stores the canonical ISBN-10 derived from the `isbn13` (NULL when ISBN-13 starts with 979). The input ISBN-10 that disagreed goes in `secondary_isbn10`.
- **Updates allowed on existing records:** Only `isbn10`, `publication_year`, and `secondary_isbn10` may be filled on update. `raw_isbn`, `raw_isbn13`, and `warning` are provenance fields and are preserved from the original load. `warning` may ONLY be appended to (never overwritten).
- **Duplicate detection precedence:** Within-batch duplicate checks execute before database existence checks. A row that duplicates an earlier line in the same CSV counts as `rows_skipped_duplicate` even if its match already exists in `books.db`.
- **Combined reason codes on non-book identifiers in scientific notation:** List ALL applicable codes, comma-separated (e.g., `"NON_BOOK_IDENTIFIER,UNRECOVERABLE_ISBN13"`).
- **Two ISBN-10s in one row:** The `isbn` column's value is canonical. The `isbn13`-column value (which turned out to also be an ISBN-10) is stored in `secondary_isbn10` and logged as `WARNING_DISCARDED_COLUMN`.