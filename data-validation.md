# ISBN data validation: books.csv and books-batch2.csv

Diagnosis only. Neither CSV has been modified and nothing has been fixed.

- **books.csv**: 10,000 data rows (the goodbooks-10k file, as attached)
- **books-batch2.csv**: 31 data rows

"Line" numbers are file line numbers (line 1 is the header). Every non-empty value in the `isbn` and `isbn13` columns was checked, row by row.

## How values were checked

- **ISBN-10**: exactly 10 characters, nine digits followed by a digit or an uppercase `X`. Checksum: multiply each character by a weight from 10 down to 1 (`X` = 10); the sum must be divisible by 11.
- **ISBN-13**: exactly 13 digits, starting with the book prefixes `978` or `979` (but not `9790`, which is reserved for printed music). Checksum: digits weighted alternately 1 and 3 must sum to a multiple of 10.
- Values that failed were tested against likely causes. For example, does the value become valid if leading zeros are restored, hyphens or spaces removed, or a lowercase `x` uppercased?
- Where a row has both columns, the ISBN-13 was compared with the ISBN-13 computed from that row's ISBN-10.

## Summary

| | books.csv `isbn` | books.csv `isbn13` | batch2 `isbn` | batch2 `isbn13` |
|---|---:|---:|---:|---:|
| Valid exactly as stored | 2,690 | **0** | 12 | 9 |
| Would be valid but stored in a damaged format | 6,587 | — | 11 | — |
| Unverifiable (scientific notation) | — | 9,410 | — | 14 |
| Invalid | 23 | 5 | 3 | 0 |
| Empty | 700 | 585 | 5 | 8 |
| **Total rows** | 10,000 | 10,000 | 31 | 31 |

The biggest problems are format damage rather than wrong numbers. Most ISBN-10s in books.csv lost their leading zeros, and **every** ISBN-13 in books.csv is stored in scientific notation, which rounds away the check digit. Both point to the columns having been treated as numbers instead of text at some point.

---

## books.csv: `isbn` column (ISBN-10)

| Status | Rows |
|---|---:|
| Valid as stored | 2,690 |
| Leading zeros dropped (valid once restored) | 6,587 |
| Invalid checksum (incl. 14 that also lost leading zeros) | 23 |
| Empty | 700 |

### Valid as stored: 2,690 rows

Example: line 10, *Angels & Demons*, `1416524797`. Weighted sum 1×10 + 4×9 + 1×8 + 6×7 + 5×6 + 2×5 + 4×4 + 7×3 + 9×2 + 7×1 = 198 = 11 × 18, so the value is valid.

This includes all 814 values ending in `X`. Every value ending in `X` kept its leading zeros (618 values start with `0`), which supports the cause in the next section.

### Leading zeros dropped: 6,587 rows

- **What's wrong:** the value is shorter than 10 characters, and padding it on the left with zeros produces a valid ISBN-10 every time.
  - 5,563 rows lost one zero
  - 913 rows lost two zeros
  - 111 rows lost three zeros
- **Examples:**
  - line 2, *The Hunger Games*: `439023483` (should read `0439023483`)
  - line 5, *To Kill a Mockingbird*: `61120081` (should read `0061120081`)
  - line 70, *Insurgent*: `7442912` (should read `0007442912`)
- **Why it's recoverable:** an ISBN-10 always has exactly 10 characters, and converting to an integer only deletes zeros from the front. So the number missing is 10 minus the current length, and there is only one way to restore them. The checksum confirms it: a random 10-character value passes about 1 time in 11, yet all 6,587 padded values pass.
- **Probable cause:** the column was read as an integer upstream, in a spreadsheet or a data library, and the leading zeros were discarded. Values ending in `X` couldn't be parsed as numbers, stayed text, and so kept their zeros.

### Invalid checksum: 23 rows

This count covers 9 values with 10 characters plus 14 that also lost leading zeros and still fail after padding.

**Cause A: ISBN-13 check digit copied onto the ISBN-10 (20 of the 23).**

- **What's wrong:** the first nine digits are right, but the last character is the check digit of the 978-prefixed ISBN-13, not the ISBN-10's own check digit.
- **Evidence:** in all 20 cases, `978` followed by the stored value is a valid ISBN-13. If these were random wrong digits, that would happen about 1 time in 10, i.e. around 2 of 23 rows.
- **Example:** line 2779, *Two-Way Street*, `1416913184`.
  - As an ISBN-10 the weighted sum is 190, and 190 mod 11 = 3, so it fails. The correct check digit is 1, giving `1416913181`.
  - `9781416913184` is a valid ISBN-13 and matches the row's own `isbn13` (`9.78141691318e+12`).
- **Probable cause:** someone converted ISBN-13s to ISBN-10s by just removing `978`, without recalculating the check digit (the two systems use different checksum formulas).

**Cause B: no identifiable pattern (3 rows).** These look like a typo or a wrong source value.

- Line 917, *Reading Lolita in Tehran*: `812971060`. The check digit should be `X`; an `X` may have become `0`.
- Line 4323, *One Fifth Avenue*: `1400139027`. The check digit should be `3`.
- Line 8553, *Rejoice*: `84386874`. Its `isbn13` (`9.78084238688e+12`) contains different digits (`084238688`), so this ISBN-10 also disagrees with the row's other ISBN.

### Empty: 700 rows

- **Example:** line 107, *Bossypants*.
- **Breakdown:** 565 of these rows have no `isbn13` either; 135 do have one.
- **Probable cause:** the edition Goodreads chose as the record had no ISBN-10 listed, e.g. an ebook or an edition that only has an ISBN-13.

---

## books.csv: `isbn13` column

| Status | Rows |
|---|---:|
| Valid as stored | **0** |
| Scientific notation (cannot be verified as stored) | 9,410 |
| Other malformed values | 5 |
| Empty | 585 |

### Scientific notation: 9,410 rows (every non-empty value but 5)

- **What's wrong:** the values are written as floating-point numbers in scientific notation, such as `9.78043902348e+12`. No value in this column can be checksum-verified, and none is a usable ISBN-13 as stored.
- **Example:** line 2, *The Hunger Games*, `9.78043902348e+12`. The real value is `9780439023481`.
- **What was lost, exactly:** the float itself lost nothing. A 64-bit float stores every integer up to 2⁵³ (about 9.007 × 10¹⁵) exactly, so `9780439023481` survives as a float. The damage happened when it was written out as text with **12 significant digits**. No stored value has more than 12; 8,419 have exactly 12, and 1,000 have fewer because trailing zeros were dropped. Writing 12 of 13 digits rounds away the 13th digit, which is the check digit. Rounding can also change the 12th digit:
  - *The Hunger Games*: `978043902348|1` → the 1 rounds down → `9.78043902348e+12`, first 12 digits intact.
  - *Pride and Prejudice* (line 11): `978067978326|8` → the 8 rounds up → `9.78067978327e+12`, so the stored 12th digit (7) is wrong; it was a 6.
- **Probable cause:** the column was converted to a floating-point number (for example, by a data library treating it as numeric because some cells are blank), then written back out with 12-significant-digit formatting.

The digits that survive can still be compared with the row's ISBN-10. Rounding the ISBN-13 rebuilt from the ISBN-10 to 12 significant digits should reproduce the stored value exactly.

| What the surviving digits show | Rows |
|---|---:|
| Matches the ISBN-13 rebuilt from the row's ISBN-10 | 9,238 |
| 978/979 prefix, but the row has no usable ISBN-10 to compare | 131 |
| 978/979 prefix, but **disagrees** with the row's ISBN-10 | 13 |
| Barnes & Noble Nook product IDs (prefix `2940`), not ISBNs | 22 |
| 12-digit UPC barcodes, not ISBNs | 3 |
| Non-ISBN EAN prefix `622` (Egypt barcode range) | 2 |
| `1.2300000055e+12`, apparently a placeholder | 1 |

#### Recovering the 131 with no usable ISBN-10

These are not all lost. An ISBN-13's check digit is determined by its first 12 digits, so the question is only whether rounding bumped the 12th digit. For each stored value I tried the shown 12 digits and their neighbours one above and one below, computed each one's check digit, and kept the candidates that round back to the stored value.

Line 2, as an example (the row has an ISBN-10, but it shows the method):

- **Prefix `978043902348`:** check digit 1, and `9780439023481` rounds to `…348`, matching the stored value.
- **Prefix `978043902347`:** check digit 4, and `9780439023474` rounds to `…347`, which doesn't match.

| Result for the 131 rows | Rows |
|---|---:|
| Exactly one candidate: recoverable from the data alone | 45 |
| Two candidates: needs an outside lookup to choose | 84 |
| No candidate | 2 |

#### Disagreements with the ISBN-10: 13 rows

Lines 623, 1028, 1107, 4352, 4502, 5184, 5999, 7143, 7839, 7857, 8619, 8870 and 9039.

- **Example:** line 623, *Nights in Rodanthe*. The `isbn` is `446612707` (a Warner Books prefix), but the `isbn13` begins `978055381395` (a Bantam prefix). The two columns probably describe different editions of the book.
- **Borderline case:** line 5999, *Wanted*, ISBN-10 `098870742X`. The rebuilt ISBN-13, `9780988707429`, rounds at 12 digits to `9.78098870743e+12`, but the stored value is `9.7809887074e+12`. It only matches if you assume an 11-digit rounding, which no other value in the file uses, so it is counted as a disagreement.
- **Not an edition at all:** line 1107, *The Great Divorce*, has an ISBN-13 starting `9790`. That prefix is reserved for printed music (ISMN), so this is probably a mistyped prefix or a non-book identifier rather than another edition.

#### Non-ISBN identifiers: 28 rows

- **Example:** line 1166, *Wait for You*, `2.94001623192e+12`, a Nook ID typical of self-published ebooks.
- **Probable cause:** the source listed the product's barcode or store ID in the ISBN-13 field.
- **Recoverability:** 25 of these rows have no ISBN-10. The other 3 are 12-digit UPCs that do have a valid ISBN-10 (e.g. line 2895, *Sailor Moon*, `189221301X`).

### Other malformed values: 5 rows

All five end in `.0`, so they are float-formatted numbers too.

| Line | Title | Value | What it appears to be |
|---|---|---|---|
| 959 | *The Complete Anne of Green Gables Boxed Set* | `76783609419.0` | 12-digit UPC with its leading 0 dropped |
| 1444 | *The Secret of the Nagas* | `97893806587.0` | ISBN-13 cut to 11 digits |
| 6143 | *One Tuesday Morning* | `25986247522.0` | 12-digit UPC with its leading 0 dropped |
| 6438 | *Unveiled: Tamar* | `31809014745.0` | 12-digit UPC with its leading 0 dropped |
| 8175 | *Washington's Crossing* | `195170342.0` | the ISBN-10 (`0195170342`) placed in the ISBN-13 column |

**Probable cause:** the same numeric conversion as above, combined with non-ISBN or misplaced source values.

### Empty: 585 rows

- **Breakdown:** 565 rows have no ISBN-10 either; 20 have an ISBN-10 but no ISBN-13.
- **Example:** line 107, *Bossypants*.

---

## books-batch2.csv: `isbn` column (ISBN-10)

| Status | Rows | Lines |
|---|---:|---|
| Valid as stored | 12 | 4, 5, 7, 9, 14, 15, 16, 19, 21, 23, 24, 28 |
| Leading zeros dropped | 7 | 6, 8, 12, 13, 17, 29, 32 |
| Hyphens or spaces in the value | 2 | 30, 31 |
| Lowercase `x` check digit | 1 | 27 |
| Invalid character | 1 | 3 |
| Invalid checksum | 1 | 11 |
| ISBN-13 in the ISBN-10 column | 1 | 18 |
| Extra digit | 1 | 20 |
| Empty | 5 | 2, 10, 22, 25, 26 |

Three of the "valid" values have other problems, covered under [Cross-row and cross-file problems](#cross-row-and-cross-file-problems): lines 14–15 are a duplicated row, line 24 reuses another book's ISBN, and line 16 disagrees with its own ISBN-13.

### Leading zeros dropped: 7 rows

- **What's wrong:** same as in books.csv. Six values lost one zero and one lost two.
- **Example:** line 6, *The Long Walk Home*, `441773028` (should read `0441773028`).
- **Probable cause:** the same numeric conversion. Six of the seven rows are copied directly from books.csv; the new row (line 6) went through the same process.

### Hyphens or spaces in the value: 2 rows

- **What's wrong:** each is a valid ISBN-10 once the separators are removed, but separators aren't allowed in a stored value.
  - Line 30, *Salt and Ember*: `0-450-19827-8`, with printed-style hyphens.
  - Line 31, *A Field Guide to Quiet Places*: ` 162 001 4750 `, with spaces between digit groups plus leading and trailing spaces.
- **Probable cause:** typed or pasted from a copyright page or a website, where ISBNs are shown with separators.

### Lowercase `x` check digit: 1 row

- **What's wrong:** ISBN-10 check digits must use an uppercase `X` (meaning 10). With `X`, this value is valid.
- **Example:** line 27, *Winter Orchard*, `031200009x`.
- **Probable cause:** manual entry.

### Invalid character: 1 row

- **What's wrong:** contains the letter `A`, which can't appear in an ISBN.
- **Example:** line 3, *The Glass Aviary*, `04390A3483`.
- **Notable:** the only digit that would make it valid is `2`. That gives `0439023483`, which is *The Hunger Games'* ISBN, so even the "repaired" value would point to the wrong book.
- **Probable cause:** a corrupted copy of another row's ISBN (a typo or OCR error) rather than this book's own number.

### Invalid checksum: 1 row

- **What's wrong:** 10 digits, but the checksum fails. A check digit of `6` would make it valid, but the error could just as well be in another digit. `978` + the value isn't a valid ISBN-13, so it isn't the stripped-978 pattern.
- **Example:** line 11, *Small Hours*, `1854433017`.
- **Probable cause:** a typing error.

### ISBN-13 in the ISBN-10 column: 1 row

- **What's wrong:** `9781982147600` is a valid ISBN-13, but it is in the `isbn` column while `isbn13` is empty.
- **Example:** line 18, *An Atlas of Small Kindnesses*.
- **Probable cause:** entered in the wrong column.

### Extra digit: 1 row

- **What's wrong:** 11 digits. Removing the final `7` leaves `1528746309`, the ISBN on lines 14–15 (*The Lantern Keeper's Daughter*).
- **Example:** line 20, *Borrowed Light*, `15287463097`.
- **Probable cause:** a copy-paste of another row's ISBN with an extra keystroke. This book's real ISBN is unknown.

### Empty: 5 rows

- **Example:** line 2, *Bossypants*. Lines 2 and 26 match the books.csv convention for books with no ISBN-10 listed.
- Line 22, *Untitled Manuscript* by "Unknown", has no ISBN in either column and looks like a placeholder record.

## books-batch2.csv: `isbn13` column

| Status | Rows | Lines |
|---|---:|---|
| Valid 13 digits with correct checksum | 9 | 4, 10, 14, 15, 16, 25, 27, 30, 31 |
| Scientific notation (cannot be verified as stored) | 14 | 5, 6, 7, 8, 9, 12, 13, 17, 19, 21, 23, 28, 29, 32 |
| Empty | 8 | 2, 3, 11, 18, 20, 22, 24, 26 |

- **Valid:**
  - Line 10, *Notes from the Night Ferry*, `9798351220413`, is a valid 979-prefix ISBN-13 with no ISBN-10. That is expected, because 979 ISBNs have no ISBN-10 equivalent.
  - Line 16 is valid on its own but conflicts with its ISBN-10 (see below).
- **Scientific notation:** same problem as books.csv. All 14 values match the ISBN-13 rebuilt from their row's ISBN-10 when rounded to 12 digits, but the check digits themselves are lost.
  - Example: line 5, *The Five People You Meet in Heaven*, `9.78140130858e+12`.
- **Empty:** for lines 3, 11, 18, 20 and 24, the missing ISBN-13 sits alongside a broken or questionable ISBN-10, so these rows have no trustworthy ISBN at all.

---

## Cross-row and cross-file problems

These values pass a checksum but are still wrong.

| Problem | Lines (batch2) | Detail | Probable cause |
|---|---|---|---|
| Duplicate row | 14, 15 | *The Lantern Keeper's Daughter* (book_id 10001) appears twice, identical in every column | Row pasted or appended twice |
| Same ISBN on two different books | 21, 24 | `1416524797` belongs to *Angels & Demons* (book_id 9, also in books.csv); line 24, *Paper Moons*, reuses it | Copy-paste from a nearby row |
| ISBN-10 and ISBN-13 disagree | 16 | *Tides of Marrow Bay*: ISBN-10 `1555550126` implies ISBN-13 `9781555550127`, but the stored value is `9781555550134`. Both checksums pass, so one of them belongs to a different book | Values entered separately, at least one from the wrong source |
| ISBNs lost compared with books.csv | 26 | *Harry Potter and the Deathly Hallows* (book_id 25) has both columns empty; books.csv has `545010225` and `9.78054501022e+12` | Data dropped during export or editing |
| Formatting differs from books.csv | 4 | *Goblet of Fire* (book_id 24) is stored correctly as text (`0439139600`, `9780439139601`), unlike its numeric form in books.csv | Re-entered by hand, which happened to be correct |

Batch2 overlaps books.csv by book_id:

- 17 batch2 rows reuse book_ids from books.csv. 15 of them have identical ISBN values; the other 2 are lines 4 and 26 above.
- The other 14 book_ids (10001–10014, on 15 rows because of the duplicate) are new. They all share identical rating and count figures (4.01 average, 1,200 ratings), which suggests placeholder records. That is outside the ISBN check but worth knowing before merging.

## What can and can't be recovered from the files alone

- **Recoverable:**
  - The 6,587 + 7 ISBN-10s with dropped zeros, and the batch2 hyphen, space and lowercase-`x` cases.
  - The ISBN-13s of the 9,238 + 14 rows whose surviving digits match a valid ISBN-10, since the ISBN-13 can be rebuilt from the ISBN-10.
  - The 20 books.csv ISBN-10s with a copied ISBN-13 check digit, by recalculating the check digit.
  - 45 of the 131 scientific-notation ISBN-13s with no usable ISBN-10, using the rounding test above.
- **Needs an outside lookup or a decision:**
  - The other 86 of those 131 (84 with two candidates, 2 with none).
  - The 3 books.csv ISBN-10s with unexplained bad checksums.
  - The 13 rows whose two ISBN columns disagree.
  - The 28 rows holding barcodes or store IDs, unless an ISBN-10 is present.
  - The batch2 rows with a typo, a corrupted or borrowed ISBN, or an extra digit (lines 3, 11, 16, 20, 24), and line 26's lost values unless taken from books.csv.
