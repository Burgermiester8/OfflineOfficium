# Changelog

What changed in each release, newest first. The README has the details and
the measurements.

## 2026.11.13

**New in the Breviary Builder**

- **Latin on the outside of each page.** The first language stands at the
  outer edge of both pages and the second by the binding. A box in the Page
  options turns it off.
- **A rule between the two languages**, in the rubric colour. Its own box.
- **The Common of the Saints by families.** 16 Commons instead of 50 in the
  Monastic book (17 instead of 55 in 1960), with nothing printed twice.
- **Every saint points to its Common, with the page.** St Luke and Sts Simon
  and Jude had none.
- **The Office of the Dead**, as a part of its own that can be ticked,
  unticked and moved.
- **A table of invitatories in the Ordinary:** each weekday's, each season's
  and each Common's.
- **Matins responsories:** in full after each lesson, or the repeated ones by
  their first words and page, whatever *Print a repeated text once* says.
- **Christ the King** is in the Proper of the Season.

**Fixed**

- The line under the running head no longer touches the text at a tight
  spacing.
- Monastic and Cistercian feasts printed a leftover Roman fourth or ninth
  lesson, the same text as the lessons after it (115 offices).
- Coverdale psalter: verses in five psalms (13, 48, 51, 100, 138) were beside
  the wrong Latin line; three verses of Psalm 13 were missing; two Latin
  lines with no Coverdale now have the Douay's.
- Canticles: *wails* for *walls* (Isaias 62:6), two verses with the wrong
  chapter number, and half a verse the canticle does not have.
- A long title in the Contents overprinted the line under it.
- The Office of the Dead's running head showed the hour of the part before.
- The window's estimate of the time was far too short.

**For checking a book**

- `tools/audit_sides.py`: which side each language is on, page by page.
- `tools/audit_psalms.py`: every skipped or repeated verse number in the
  psalms. No Latin verse is missing.

A book with the first language outside takes two to three times as long to
make as one without.

## 2026.10.24

- Breviaries print as a bound book should: page 1 is a right-hand page, the
  page numbers on the outer edge, the wide margin on the binding side.
- Every *The rest as in* note gives the page to turn to.
- Each feast of the Proper of the Saints shows its date, above its title and
  in the running head.
- A **Book** choice: the whole breviary, Matins alone, a Diurnal, or both as
  two books.

## 2026.10.11

- The Calendar shows each day's liturgical colour, and the headings of the
  Propers are printed in the day's colour (a box turns this off).
- Three new book sizes: Crown Quarto, Executive and US Trade.
- The texts of all fifteen versions checked and corrected: three lessons of
  St Augustine restored, garbled passages settled from printed breviaries.

## 2026.10.07.2

- Windows stay above the taskbar, and the PDF and breviary windows scroll on
  small screens.

## 2026.10.07

- The first release: the Office, the Mass, PDFs and printable breviaries,
  all offline. The app can update itself (*Check for updates*).
