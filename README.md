# Language of Computation — Week 3 Assignment

This is my Module 3 assignment for MSAI-5523.

## Files

| File | What it is |
| --- | --- |
| `rules.md` | The ingestion rules: repairs, rejections, warnings, deduplication and database schema |
| `ingest.py` | Loads a books CSV into `books.db` following `rules.md`, and logs each run |
| `report.html` | Shows the runs recorded in `ingestion-log.json` |
| `books.csv`, `books-batch2.csv` | Input data |
| `data-validation.md` | Diagnosis of the ISBN values in the input data |

## Running ingest.py

Requires Python 3. It uses only the standard library, so there is nothing to install, and it never uses the internet.

From a terminal, in this folder:

```bash
python ingest.py books.csv
python ingest.py books-batch2.csv
```

On some systems the command is `python3` instead of `python`.

Each run:

- creates `books.db` (SQLite, table `books`) in the current folder if it doesn't exist, then loads, updates, skips or rejects every row of the file
- appends one entry to `ingestion-log.json` in the current folder, creating it on the first run and never overwriting earlier runs
- prints a one-line summary of the counts

Running the same file again doesn't create duplicates. Rows already in `books.db` are counted as skipped (`skipped_already_exists`).

If the CSV is missing a required column (`isbn`, `isbn13`, `title`, `authors` or `original_publication_year`), the run stops with `FATAL_MISSING_COLUMNS`. If anything else goes wrong mid-run, every database change from that run is rolled back. Either way, `books.db` is left unchanged and a `FAILED` entry is still written to the log.

If `ingestion-log.json` exists but isn't readable JSON, the script stops before doing anything, so earlier runs are never lost.

## Opening report.html

Double-click `report.html`, or open it in any web browser. It works offline and needs no server.

Click **Open ingestion-log.json** and choose the log file. The page then shows every run, newest first, with:

- its counts
- the rows that need a manual catalog lookup
- the rejected and skipped rows with their reasons
- the warnings

Choose the file again after a new run to see the update.

## The run log

Each entry in `ingestion-log.json` holds:

- `timestamp` (UTC, ISO 8601), `file` (base name), `status` (`SUCCESS` or `FAILED`) and `error`
- `counts`: `rows_read`, `rows_loaded`, `rows_updated`, `rows_skipped_duplicate`, `rows_skipped_already_exists` and `rows_rejected`
- `not_loaded`: every row that wasn't loaded or updated, with its line number, raw `isbn` and `isbn13`, title, status, reason code(s) and details
- `warnings`: rows that were loaded, updated or skipped with a warning code
- `repairs`: each repair applied to a row that was loaded or updated (line, title, original value, repaired value and repair type)
- `needs_manual_review`: rows that need a manual catalog lookup

Line numbers are file line numbers; the header is line 1.
