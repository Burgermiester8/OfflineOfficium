"""
The indexes at the end of the book: the psalms and canticles by number, the
hymns by their first line, and the feasts in alphabetical order, each with
the page where this book (or this volume) first prints it.

The part's offices are made empty (placeholders(), where the part stands in
the book) and filled in once the book's other offices are final (fill(),
from dobreviary.finish): the first line of each psalm and hymn printed then
gets a marker, as the first copy of a repeated text does, and its page is
known the way a reference's is -- in the same volume from the marker's own
label, from an earlier one by its bookmark, from a later one when the volume
is set again.
"""

import itertools
import re

import dopdf

KINDS = (("psalmi", "Index Psalmorum et Canticorum", "Index of Psalms and Canticles"),
         ("hymni", "Index Hymnorum", "Index of Hymns"),
         ("festa", "Index Festorum", "Index of Feasts"))
_PSALM = re.compile(r"^\s*(?:Psalmus|Psalm|Psaume|Salmo)\s+(\d+)\b")
# A canticle's title with its name ("Canticum Isaiæ [3]"): the Psalter's own
# pattern, which never takes a verse number ("[15]") for a name.
from dopsalter import _TITLE_OF_CANTICLE as _CANTICLE  # noqa: E402
_HYMN = re.compile(r"^\s*(?:Hymnus|Alius hymnus|Hymn|Another hymn)\b")
_VERSE = re.compile(r"^\s*(?:\d+:\d+[a-z]?|\d+)\s+")
_SEASON_DAY = re.compile(r"^(Dominica|Feria|Sabbato|Die |Hebdomada|Week|Sunday|Monday|Tuesday|"
                         r"Wednesday|Thursday|Friday|Saturday|In Octava|Infra Octavam|Vigilia)", re.I)
# Words a title starts with that it is not found by: "S. Titi" under T.
_SORT_SKIP = re.compile(r"^(?:(?:S|Ss|SS|St|Sts|B|Bb)\.\s*|Sancti\s+|Sanctæ\s+|Sanctorum\s+|"
                        r"Beati\s+|Beatæ\s+|The\s+|In\s+|Festum\s+|Commemoratio\s+)+", re.I)


def placeholders(part, office_cls, english):
    """The index's offices, empty until fill()."""
    return [office_cls(part=part, key="index-" + kind, title=en if english else la,
                       title2=la if english else en, table=("index", {"kind": kind, "rows": []}))
            for kind, la, en in KINDS]


def _plain(line):
    """A line's words, without its heading run in or a verse number."""
    text = "".join(r.text for r in line.runs if r.size != "heading")
    text = re.sub(r"^\s*(?:Hymnus|Hymn)\b\.?\s*", "", text)
    return re.sub(r"\s+", " ", _VERSE.sub("", text)).strip(" *†.,;:")


def _is_label(line):
    """A line that names no text but says what follows: "Hymnus" alone, or
    "Hymnus. Dominica:" (the hymn of Sunday comes on the next line)."""
    text = _plain(line)
    return not text or (text.endswith(":") and len(text.split()) <= 4) or \
        (len(text.split()) <= 4 and line.text.rstrip().endswith(":"))


def _incipit(text, words=6):
    """The opening words: to the asterisk (a psalm's half verse), or six."""
    text = text.replace(chr(0xAD), "").replace("‡", "")  # a soft hyphen; the Coverdale's mark
    text = text.split("*")[0].split("†")[0].strip(" ,;:")
    w = text.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def _next_text(col, j):
    """The first line after j with words of its own (not a rubric, an antiphon, a reference)."""
    for line in col[j + 1:]:
        if line.is_blank or line.is_heading or line.is_rubric or getattr(line, "ref", None):
            continue
        t = _plain(line)
        if t and not re.match(r"^(?:Ant\.|℣|℟|V\.|R\.)", t):
            return t
        if t.startswith("Ant."):
            continue
        return ""
    return ""


def fill(offices, mark, label):
    """Fill the index offices among `offices` (the book or volume, final):
    mark(hour, index, j, ids, col) gives a printed line a marker (dobreviary._mark)
    and returns its label; label(office) gives an office's heading one."""
    wanted = {o.table[1]["kind"]: o for o in offices
              if o.table is not None and o.table[0] == "index"}
    if not wanted:
        return
    ids = itertools.count(100000)  # "ref100000" on: apart from the references (_mark)
    body = [o for o in offices if o.rows is None and o.table is None and not o.include
            and not o.part.key.startswith("custom")]
    psalter = [o for o in body if o.part.key == "psalterium"]
    if "psalmi" in wanted:
        seen, rows = {}, []
        # The Psalter's copy first, where the book has a Psalter: the page a
        # reader looks a psalm up for.
        for o in psalter + [o for o in body if o.part.key != "psalterium"]:
            for hour in o.hours:
                for si, section in enumerate(hour.sections):
                    col = section.columns[0]
                    for j, line in enumerate(col):
                        if not (line.is_heading or line.is_rubric) or getattr(line, "ref", None):
                            continue
                        text = line.text.strip()
                        m = _PSALM.match(text)
                        key = ("ps", int(m.group(1))) if m else None
                        if not m:
                            c = _CANTICLE.match(text)
                            key = ("cant", c.group("name").strip()) if c else None
                        if key is None or key in seen:
                            continue
                        first = _next_text(col, j)
                        if not first:
                            continue
                        second = ""
                        if len(section.columns) > 1:
                            k = next((k for k, l2 in enumerate(section.columns[1])
                                      if (l2.is_heading or l2.is_rubric)
                                      and (_PSALM.match(l2.text) or _CANTICLE.match(l2.text))), None)
                            second = _next_text(section.columns[1], k) if k is not None else ""
                        ident = mark(hour, si, j, ids)
                        section = hour.sections[si]  # mark() may have copied it
                        col = section.columns[0]
                        seen[key] = True
                        name = str(key[1]) if key[0] == "ps" else "Cant. " + key[1]
                        rows.append((key, name, _incipit(first), _incipit(second), ident))
        rows.sort(key=lambda r: (r[0][0] != "ps", r[0][1] if r[0][0] == "ps" else 0))
        wanted["psalmi"].table[1]["rows"] = [r[1:] for r in rows]
    if "hymni" in wanted:
        seen, rows = set(), []
        for o in body:
            for hour in o.hours:
                for si, section in enumerate(hour.sections):
                    col = section.columns[0]
                    for j, line in enumerate(col):
                        if line.is_blank or not _HYMN.match(line.text.strip()):
                            continue
                        b = j + 1 if (line.is_heading or line.is_rubric) and _is_label(line) else j
                        while b < len(col) and (col[b].is_blank or _is_label(col[b])):
                            b += 1
                        if b >= len(col) or getattr(col[b], "ref", None) or getattr(line, "ref", None):
                            break  # a hymn printed before: its reference
                        first = _plain(col[b])
                        key = re.sub(r"\W+", "", first.lower())[:40]
                        if not first or key in seen:
                            break
                        second = ""
                        if len(section.columns) > 1:
                            c2 = section.columns[1]
                            k = next((k for k, l2 in enumerate(c2) if _HYMN.match(l2.text.strip())), None)
                            if k is not None:
                                k2 = k + 1 if (c2[k].is_heading or c2[k].is_rubric) and _is_label(c2[k]) else k
                                while k2 < len(c2) and (c2[k2].is_blank or _is_label(c2[k2])):
                                    k2 += 1
                                second = _plain(c2[k2]) if k2 < len(c2) else ""
                        seen.add(key)
                        ident = mark(hour, si, b, ids)
                        rows.append((first.rstrip(",;:"), second.rstrip(",;:"), ident))
                        break
        rows.sort(key=lambda r: sort_key(r[0]))
        wanted["hymni"].table[1]["rows"] = rows
    if "festa" in wanted:
        rows, seen = [], set()
        for o in body:
            if o.untitled or o.part.key not in ("sancti", "tempora"):
                continue
            if o.part.key == "tempora" and (_SEASON_DAY.match(o.title or "") or re.match(r"^\d", o.key)):
                continue  # the days of the season: found by the season
            if (o.file, o.title) in seen:
                continue
            seen.add((o.file, o.title))
            rows.append((o.title, o.title2 if o.title2 != o.title else "", o.left, label(o)))
        rows.sort(key=lambda r: sort_key(r[0]))
        wanted["festa"].table[1]["rows"] = rows


def sort_key(text):
    t = _SORT_SKIP.sub("", text or "")
    t = t.replace("Æ", "Ae").replace("æ", "ae").replace("Œ", "Oe").replace("œ", "oe")
    t = re.sub(r"[áàâä]", "a", t)
    t = re.sub(r"[éèêë]", "e", t)
    t = re.sub(r"[íìîï]", "i", t)
    t = re.sub(r"[óòôö]", "o", t)
    t = re.sub(r"[úùûü]", "u", t)
    t = re.sub(r"[ýÿ]", "y", t)
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def markup(office, writer):
    """The Typst for one index after its heading: a row an entry, the page last."""
    q = dopdf.q
    kind, rows = office.table[1]["kind"], office.table[1]["rows"]
    two = writer.meta.lang2 != writer.meta.lang1
    cells = []
    for r in rows:
        if kind == "psalmi":
            name, first, second, ident = r
            row = ["text(fill: rub, %s)" % q(name), q(first)]
            if two:
                row.append('text(style: "italic", %s)' % q(second))
        elif kind == "hymni":
            first, second, ident = r
            row = [q(first)]
            if two:
                row.append('text(style: "italic", %s)' % q(second))
        else:
            t1, t2, day, ident = r
            row = [q(t1) if not day else "[#%s #text(size: 0.8em, fill: luma(90), %s)]" % (q(t1), q(day))]
            if two:
                row.append('text(style: "italic", %s)' % q(t2))
        row.append(writer.page_ref(ident, 0, bare=True) if ident else "[]")
        cells.append(", ".join(row))
    if kind == "psalmi":
        cols = "(auto, 1fr, 1fr, 2.2em)" if two else "(auto, 1fr, 2.2em)"
    else:
        cols = "(1fr, 1fr, 2.2em)" if two else "(1fr, 2.2em)"
    last = cols.count(",")
    out = ["#[", "#set par(justify: false)",
           "#table(columns: %s, stroke: (x, y) => if y > 0 { (top: 0.3pt + luma(215)) }," % cols,
           "  inset: (x: 0.3em, y: 0.25em),",
           "  align: (x, y) => if x == %d or (x == 0 and %s) { right + top } else { left + top },"
           % (last, "true" if kind == "psalmi" else "false"),
           "  %s)" % ",\n  ".join(cells) if cells else "  [])", "]"]
    return "\n".join(out)
