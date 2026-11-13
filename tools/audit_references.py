"""Audit every cross-reference in a breviary PDF from its printed pages alone.

    python tools/audit_references.py "Breviarium Monasticum (Monastic - 1963).pdf"

Exit status 0 when every reference checks out, 1 otherwise.

For each "… pag. N" (Latin column) and "… p. N" (English column):
  1. page N exists and comes before the reference;
  2. ALL the quoted words (the text after its red label or heading, up to the
     "…") are printed on page N, in the same column, as text -- references on
     that page are blanked out first, so a reference never vouches for another;
  3. for "usque ad / as far as X…", all of X is printed on page N or the four
     pages after it (the run may go on over the page);
  4. the Latin and English references on each page give the same pages (or
     the next: where one language's text begins a row lower than the other's,
     a page can end between them; and a reference's row may break at the
     page's end, one language's on this page and the other's on the next);
  5. the number printed in page N's running head is N, at its outer corner
     as the page prints: the right on a right-hand page (odd in the PDF), the
     left on a left-hand one -- never by the binding;
  6. the heading a reference names ("Resp. IV, pag. 56") is the last red
     heading of its kind before the quoted words on page N (or, for part of a
     long section, on up to three pages before it, where the section began):
     the nearest responsory heading above the text is "Responsorium IV".
Red (rubric-coloured) spans are read as ⟨…⟩: labels, run-in headings and the
page numbers themselves; the grey "AI" mark and word joiners are left out.

A book of one language (only "pag." or only "p." in it; --one-language says
so) is read as one text: a page set in two columns, its left column and then
its right; a page across its width (one column, the Calendar), line by line.
Its words are looked for anywhere on page N, and check 4 has nothing to pair.
"""
import collections
import os
import re
import sys
import unicodedata

try:
    import pymupdf as fitz  # PyMuPDF 1.24 and later
except ImportError:
    import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dobreviary import HEAD_SHORT, HOUR_SHORT  # the abbreviations a reference uses

sys.stdout.reconfigure(encoding="utf-8")
PDF = sys.argv[1]
d = fitz.open(PDF)
W = d[0].rect.width
MID = W / 2

label_to_index = {}
for i in range(len(d)):
    lab = d[i].get_label()
    if lab and lab.isdigit():
        label_to_index[int(lab)] = i


def one_language():
    """Only one language's references in the book: "pag." (Latin) or "p."."""
    if "--one-language" in sys.argv:
        return True
    pag = p = 0
    for i in label_to_index.values():
        t = d[i].get_text()
        pag += len(re.findall(r"\bpag\.\s*\d", t))
        p += len(re.findall(r"(?<![^\W\d_])p\.\s*\d", t))
    return not (pag and p)


ONE = one_language()


def mirrored():
    """Two languages with the first on the outside of each page (the book's
    "first language on the outside"): the Latin references ("pag.") are at
    one side on the left-hand pages and at the other on the right-hand ones.
    The first language's column is then the left one of a left-hand page."""
    if ONE:
        return False
    side = {0: [0, 0], 1: [0, 0]}  # the page number's parity -> "pag." at the left, at the right
    for lab, i in label_to_index.items():
        for w in d[i].get_text("words"):
            if w[4].startswith("pag."):
                side[lab % 2][0 if (w[0] + w[2]) / 2 < d[i].rect.width / 2 else 1] += 1
    if not sum(side[0]) or not sum(side[1]):
        return False
    return (side[0][0] > side[0][1]) != (side[1][0] > side[1][1])


MIRRORED = mirrored()
index_to_label = {i: lab for lab, i in label_to_index.items()}

# the rubric colour: that of the ℟ signs; and the size of the book's text
colours, sizes = collections.Counter(), collections.Counter()
for i in list(label_to_index.values())[20:40]:
    for b in d[i].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if "℟" in s["text"]:
                    colours[s["color"]] += 1
                sizes[round(s["size"], 1)] += len(s["text"].strip())
RUB = colours.most_common(1)[0][0]
BODY = sizes.most_common(1)[0][0] if sizes else 10
GREY_AI = 0x6E6E6E


def text_block(page):
    """(middle of the text block, foot of the running head) from the rule under
    the head, which spans the text's width: with margins of their own (a wider
    inside one) neither is the page's middle or a fixed height."""
    rules = [(r.x0, r.x1, r.y1) for dr in page.get_drawings() for it in dr["items"]
             for r in ([it[1].rect if hasattr(it[1], "rect") else None] if it[0] == "re" else
                       [fitz.Rect(it[1], it[2])] if it[0] == "l" else [])
             if r is not None and r.height < 2 and r.width > W / 3 and r.y1 < page.rect.height / 4]
    if not rules:
        return MID, 52
    x0, x1, y = min(rules, key=lambda r: r[2])
    return (x0 + x1) / 2, y + 1


def reading_order(col, width):
    """A column's lines ({key: (y, x, text)}, key in the PDF's own order) in
    reading order: from the top down -- but a hymn set in two columns of
    stanzas (dobreviary BookWriter.hymn), whose second column begins near the
    middle of the language's column, is read column by column. Its first
    column's lines are those just before the second's in the PDF's order (the
    order Typst wrote them), from the second's top down."""
    if not col:
        return []
    left = min(x for _y, x, _t in col.values())
    second = {k for k, (_y, x, _t) in col.items() if x > left + 0.4 * width}
    keys = sorted(col)
    order = {k: (col[k][0], 0, col[k][1]) for k in keys}
    i = 0
    while i < len(keys):
        if keys[i] not in second:
            i += 1
            continue
        j = i
        while j < len(keys) and keys[j] in second:
            j += 1
        top = min(col[k][0] for k in keys[i:j])
        first, h = [], i - 1
        while h >= 0 and keys[h] not in second and col[keys[h]][0] >= top - 2:
            first.append(keys[h])
            h -= 1
        if first:
            base = min(col[k][0] for k in first)
            by_y = lambda ks: sorted(ks, key=lambda k: (col[k][0], col[k][1]))
            for n, k in enumerate(by_y(first) + by_y(keys[i:j])):
                order[k] = (base, 1, n)
        i = j
    return [col[k][2].strip() for k in sorted(col, key=lambda k: order[k])]


head_x = {}  # page index -> where the number in its head stands (its middle)
centred = {}  # page index -> its centred text in reading order (two languages)


def columns(page):
    """([Latin lines], [English lines], number in the head) -- or, in a book of
    one language, ([its lines in reading order], [], number in the head)."""
    # Span by span: PyMuPDF sometimes joins the two columns' lines on one
    # baseline into a single line.
    cols = {0: {}, 1: {}}
    head_num = None
    # A mirrored book's right-hand pages have the first language at the right.
    swapped = MIRRORED and index_to_label.get(page.number, 0) % 2 == 1
    mid, head_foot = text_block(page)
    blocks = page.get_text("dict")["blocks"]
    # One language across the page (one column, the Calendar): text runs over
    # the middle; in two columns nothing does but a part's title.
    across = ONE and sum(1 for b in blocks for l in b.get("lines", []) for s in l["spans"]
                         if s["bbox"][0] < mid - 5 and s["bbox"][2] > mid + 5 and s["bbox"][3] > head_foot) >= 3
    # Centred (two languages): a row of text running across the middle -- a
    # heading, or the note under an office's title, which PyMuPDF gives in
    # pieces (its red page, its second language, a short last line). By its
    # ink: a column's words keep 0.29em and more from the gutter's middle (the
    # gutter is 1.1em, and a line's last comma hangs 0.26em into it; measured
    # at 8 pt in Bookman Old Style), a centred row's are upon it, or leave a
    # word's space there. A piece's own box will not do: it has the line's
    # last space, which ends at the middle itself.
    centred_spans = set()
    if not ONE:
        spans = sorted((s for b in blocks for l in b.get("lines", []) for s in l["spans"]
                        if s["text"].strip() and (s["bbox"][1] + s["bbox"][3]) / 2 > head_foot),
                       key=lambda s: (s["bbox"][1] + s["bbox"][3]) / 2)
        rows = []
        for s in spans:
            c = (s["bbox"][1] + s["bbox"][3]) / 2
            if rows and abs(c - rows[-1][0]) < 0.4 * (s["bbox"][3] - s["bbox"][1]):
                rows[-1][1].append(s)
            else:
                rows.append([c, [s]])
        words = [w for w in page.get_text("words") if w[4].strip()]
        for _c, row in rows:
            top, foot = min(s["bbox"][1] for s in row), max(s["bbox"][3] for s in row)
            ink = [(w[0], w[2]) for w in words if top <= (w[1] + w[3]) / 2 <= foot]
            size = max(s["size"] for s in row)
            ends = [x1 for _x0, x1 in ink if x1 <= mid]
            starts = [x0 for x0, _x1 in ink if x0 >= mid]
            if any(x0 < mid < x1 for x0, x1 in ink) \
                    or (ends and max(ends) > mid - 0.18 * size) \
                    or (starts and min(starts) < mid + 0.18 * size) \
                    or (ends and starts and min(starts) - max(ends) < 0.45 * size):
                centred_spans.update(id(s) for s in row)
    mids = []  # (top, foot, left, text) of the centred spans
    for bi, b in enumerate(blocks):
        for li, l in enumerate(b.get("lines", [])):
            for s in l["spans"]:
                t = s["text"].replace("⁠", "")
                if not t.strip():
                    continue
                x0, y0, x1, y1 = s["bbox"]
                # In the head by its middle: the rule under the head is drawn
                # over the foot of its letters' box (y 47 against 49).
                if (y0 + y1) / 2 < head_foot:
                    if t.strip().isdigit():
                        head_num = int(t.strip())
                        head_x[page.number] = (x0 + x1) / 2
                    continue
                if id(s) in centred_spans:
                    mids.append((y0, y1, x0, t))  # kept for its "Cetera ut in" (check 7)
                    continue
                if s["color"] == GREY_AI and t.strip() == "AI":
                    continue
                c = 0 if across or (x0 + x1) / 2 < mid else 1
                if swapped and not across:
                    c = 1 - c
                key = (bi, li)
                # The height of this column's part of the line, not of the
                # joined line: a Latin line joined to the English one beside
                # it would take the English line's height, and a Latin line
                # with a large initial before it could come out after it.
                y, x, out = cols[c].get(key, (round(y0, 1), x0, ""))
                cols[c][key] = (min(y, round(y0, 1)), min(x, x0),
                                out + ("⟨%s⟩" % t if s["color"] == RUB else t))
    # The centred text in reading order: row by row (a span level with the
    # row's), each row left to right.
    rows = []
    for top, foot, x, t in sorted(mids):
        if rows and top <= rows[-1][0] + 0.6 * (rows[-1][1] - rows[-1][0]):
            rows[-1][2].append((x, t))
        else:
            rows.append([top, foot, [(x, t)]])
    centred[page.number] = " ".join("".join(t for _x, t in sorted(r[2])) for r in rows)
    lefts = [x for _y, x, _t in cols[1 if swapped else 0].values()]  # the column at the left
    width = (mid - min(lefts)) * (2 if across else 1) if lefts else W / 2
    left = reading_order(cols[0], width)
    right = reading_order(cols[1], width)
    if ONE:  # one text: the left column, then the right
        return left + right, [], head_num
    return left, right, head_num


def letters(s):
    s = unicodedata.normalize("NFC", s.replace("⟨", "").replace("⟩", ""))
    return "".join(ch for ch in s.lower() if ch.isalpha())


def quoted(segment):
    """The quoted words: after the last red span (label, heading) in the
    segment -- or, when the reference quotes red text itself (a psalm's
    title), that text without its labels."""
    after = segment[segment.rindex("⟩") + 1:] if "⟩" in segment else segment
    if re.search(r"\w", after):
        return after.strip()
    plain = re.sub(r"[⟨⟩]", "", segment).strip()
    return re.sub(r"^(?:℟\.|℣\.|Ant\.|\d+[:.]\d*\w?)\s*", "", plain).strip()


# "…", then what the reference names ("Resp. IV, "), then the page
REF = re.compile(r"…([^…]{0,90}?)\b(pag|p)\.\s*(\d+)\s*⟩?")


def named(between):
    """The heading a reference names, written out in each language it may be
    ("Resp. IV" -> Responsorium IV, Responsory IV); [] if it names none."""
    # the red run just before the page ("Resp. IV, "), which a line break may
    # split, without an antiphon's red psalms ("Ps. 1, 2") before it
    tail = re.search(r"((?:⟨[^⟨⟩]*⟩?\s*)+)$", between)
    s = re.sub(r"[⟨⟩⁠]", "", tail.group(1)) if tail else ""
    s = s.split("…")[-1]  # not a red "usque ad" line (a rubric) before it
    s = re.sub(r"^\s*Ps\.\s[\d,\s()-]+", "", s)
    s = re.sub(r"\s+", " ", re.sub(r"\bAI\b", "", s)).strip().rstrip(",").strip()
    for hours in HOUR_SHORT.values():
        for h in hours.values():
            if s.endswith(h):
                s = s[:-len(h)].strip()
    if not s:
        return []
    out = [full + s[len(short):] for pairs in HEAD_SHORT.values() for full, short in pairs
           if s == short or s.startswith(short + " ")]
    return out or [s]
UNTIL = re.compile(r"…\s*(usque\s+ad|as\s+far\s+as)\s+")  # a line may break inside it

pages = {i: columns(d[i]) for i in label_to_index.values()}
refs = []  # (page label, column, [(quoted, explicit or None)], target, raw, heading named)
blank = {}
red = {}  # (page index, column) -> the letters of its red text, references left out
for lab, i in sorted(label_to_index.items()):
    for c in (0, 1):
        lines = pages[i][c]
        # a word hyphenated at a line's end ("us­" / "que ad", "Ves­⟩" / "⟨pers")
        # is joined again
        text = re.sub("­⟩?\\s*\n\\s*⟨?", "", "\n".join(lines))
        cut = text
        spans = []
        for m in REF.finditer(text):
            end_ell = m.start()  # the "…" before the page
            # the reference's row begins at the start of this line or the one before
            line_start = text.rfind("\n", 0, end_ell) + 1
            prev_start = text.rfind("\n", 0, max(0, line_start - 1)) + 1 if line_start else 0
            # A partial reference ("… usque ad …") may wrap over three lines:
            # look for "usque ad" from two lines back; else the quoted words
            # start on this line, or (when they are all on the line before) on that one.
            far_start = text.rfind("\n", 0, max(0, prev_start - 1)) + 1 if prev_start else 0
            seg = text[far_start:end_ell]
            us = [u for u in UNTIL.finditer(seg) if "…" not in seg[u.end():]]
            if us:
                u = us[-1]
                head = seg[:u.start()]
                head_line = head.rfind("\n") + 1 if quoted(head[head.rfind("\n") + 1:]) else 0
                cands = [(quoted(head[head_line:]), quoted(seg[u.end():]))]
                start = far_start + head_line
                first = text[start:end_ell]
            else:
                here = text[line_start:end_ell]
                start = line_start if quoted(here) else prev_start
                cands = [(quoted(text[start:end_ell]), None)]
                first = text[start:end_ell]
            # Blanked from its quoted words on: a heading run into the same
            # line (a first copy's, perhaps) stays readable.
            q0 = cands[0][0]
            k = first.find(q0) if q0 else -1
            start += k if k > 0 else 0
            refs.append((lab, c, cands, int(m.group(3)), text[prev_start:m.end()].replace("\n", " "),
                         named(m.group(1))))
            spans.append((start, m.end()))
        for a, b in sorted(spans, reverse=True):
            cut = cut[:a] + " " + cut[b:]
        blank[(i, c)] = letters(cut)
        # the page's text as letters, with where each red run begins. A red
        # heading a narrow column carries onto the next line at a space
        # before or inside its parenthesis ("Commemoration of the Octave (at"
        # / "First Vespers).", "Lesson III" / "(alternative).", US Trade) is
        # one run, as one broken at a hyphen already is. Only there: an hour
        # over a label ("In I Vesperis" / "Antiphonæ."), or a red initial
        # before a reference and a heading, are two. So is a heading whose
        # words a stretched line gives as pieces, each a line of its own to
        # PyMuPDF but ending in its space ("Lesson ", "III ", "(alternative)."),
        # never past a stop ("Lesson I." "Isa 1:1-3").
        flat, reds, gap, last = "", [], "", ""
        for part in re.split(r"(⟨[^⟩]*⟩)", cut):
            if part.startswith("⟨"):
                carried = "\n" in gap and not gap.strip() \
                    and (last.count("(") > last.count(")") or part[1:].lstrip().startswith("("))
                spread = not gap.strip() and re.search(r"[^\W\d_]$", last.rstrip()) \
                    and (last[-1:].isspace() or (gap and "\n" not in gap))
                if reds and (carried or spread):
                    reds[-1] = (reds[-1][0], reds[-1][1] + letters(part))
                else:
                    reds.append((len(flat), letters(part)))
                last = part.strip("⟨⟩")
            else:
                gap = part
            flat += letters(part)
        red[(i, c)] = (flat, reds)

def heading_is_last(target, c, quoted_letters, name):
    """Whether the last red heading of the named kind ("responsorium...")
    before the quoted words -- on page `target`, else on up to three pages
    before it, where a long section began -- is the one named."""
    head = letters(name)
    numbered = re.search(r"\s[IVXLC]+\.?$", name) is not None  # "Responsorium IV"
    kind = re.match(r"[^\W\d_]+?(?=[ivxlc]*$)|[^\W\d_]+", head).group(0)[:8] if numbered else head[:8]

    def is_named(last):
        # "Responsorium I" is not "Responsorium IV": what follows a numbered
        # name must not carry on the numeral
        return last.startswith(head) and not (numbered and re.match(r"[ivxlc]", last[len(head):]))

    def stem(x):
        return re.sub(r"[ivxlc]+$", "", x)

    def last_of_kind(runs):
        """The heading over the text, as named or not (True / False), or None
        when there is none of this kind. Passed over: a shorter red label that
        only begins the name ("Absolutio." inside "Absolutio et Benedictiones"),
        and a subtitle under the heading ("Commemoratio Octavæ Nativitatis"
        under "Commemoratio Octavæ (in I Vesperis)"); a heading that differs only
        in its number ("Responsorium II" for "Responsorium I") is a rival."""
        seen = 0
        for r in reversed(runs):
            if r != head and head.startswith(r):
                continue
            if is_named(r):
                return True
            if (numbered and stem(r) == stem(head)) or seen:
                return False
            seen += 1  # one subtitle at most
        return False if seen else None

    flat, reds = red[(label_to_index[target], c)]
    # any place on the page the quoted words stand (a responsory may repeat
    # its lesson's words)
    spots = [m.start() for m in re.finditer(re.escape(quoted_letters), flat)] if quoted_letters else []
    for spot in spots or [len(flat)]:
        found = last_of_kind([r for p, r in reds if p <= spot and r.startswith(kind)])
        if found is not None:
            if found:
                return True
            continue
        for t in range(target - 1, target - 4, -1):  # a section begun on an earlier page
            if t not in label_to_index:
                break
            found = last_of_kind([r for p, r in red[(label_to_index[t], c)][1] if r.startswith(kind)])
            if found is not None:
                if found:
                    return True
                break
    return False


problems = collections.defaultdict(list)
ok = 0
named_ok = [0]
for lab, c, cands, target, raw, heading in refs:
    where = "p.%d %s" % (lab, "" if ONE else "Latin" if c == 0 else "English")
    if target not in label_to_index:
        problems["page does not exist"].append((where, raw))
        continue
    if target > lab:
        problems["points forward"].append((where, raw))
    ti = label_to_index[target]
    head = pages[ti][2]
    if head is not None and head != target:
        problems["head number is not the page"].append((where, "page %d shows %s" % (target, head)))
    hay = blank[(ti, c)]
    hay2 = "".join(blank.get((label_to_index[t], c), "") for t in range(target, target + 5) if t in label_to_index)
    good = False
    for q, explicit in cands:
        lq = letters(q)
        if lq and lq in hay and (explicit is None or (letters(explicit) and letters(explicit) in hay2)):
            good = True
            break
    heading = [h for h in heading if letters(h)]  # a name of letters (not "[3]")
    if heading:
        if not any(heading_is_last(target, c, letters(cands[0][0]), h) for h in heading):
            good = False
            problems["named heading is not the one over that text"].append((where, raw))
            continue
        named_ok[0] += 1
    if good:
        ok += 1
    else:
        problems["quoted words not printed there"].append((where, raw))

# Each page's number at its outer corner as printed: the first page of the PDF
# is a right-hand page, so its odd pages are right-hand ones.
binding_side = [lab for lab, i in sorted(label_to_index.items())
                if i in head_x and (head_x[i] > W / 2) != (i % 2 == 0)]
if binding_side:
    problems["page number on the binding side"].append(
        ("%d pages" % len(binding_side), "p. %s" % ", ".join(map(str, binding_side[:12]))
         + (" …" if len(binding_side) > 12 else "")))

by_page = collections.defaultdict(lambda: ([], []))
for lab, c, cands, target, raw, _heading in refs:
    by_page[lab][c].append(target)


def english_column():
    """The column whose references say "p." (English): the right in a Latin
    and English book, the left in one with English first."""
    said = [" ".join(" ".join(pages[i][c]) for i in label_to_index.values()) for c in (0, 1)]
    p = [len(re.findall(r"(?<![^\W\d_])p\.\s*\d", t)) for t in said]
    return 0 if p[0] > p[1] else 1


# The English may give the page after the Latin's (its text a row lower).
LATER = 1 if ONE else english_column()


def later(a, b):
    """How many pages the English column's reference is after the other's."""
    return b - a if LATER == 1 else a - b


def paired(la, en):
    return len(la) == len(en) and all(later(a, b) in (0, 1) for a, b in zip(la, en))


# A row broken at a page's end: one language's reference ends on this page,
# the other's -- a line longer -- on the next (a Latin reference of four lines
# beside an English one of three). Given back to this page when the rest pairs.
split_rows = 0
for lab in sorted(by_page):
    if ONE or lab + 1 not in by_page:
        continue
    (la, en), (nla, nen) = by_page[lab], by_page[lab + 1]
    if not paired(la, en) and nla and paired(la + nla[:1], en):
        la.append(nla.pop(0))
        split_rows += 1
    elif not paired(la, en) and nen and paired(la, en + nen[:1]):
        en.append(nen.pop(0))
        split_rows += 1
next_page = 0
for lab, (la, en) in sorted(by_page.items()):
    if ONE:
        break  # one language: nothing to pair
    if not paired(la, en):
        problems["Latin and English differ"].append(("p.%d" % lab, "%s %s / %s %s" % (
            ("Latin", la, "English", en) if LATER == 1 else ("English", la, "Latin", en))))
    next_page += sum(1 for a, b in zip(la, en) if later(a, b) == 1) if len(la) == len(en) else 0

# "Cetera ut in: X, pag. N" / "The rest as in: X, p. N", under an office's
# title or as its only text: office X is headed on page N -- X among the
# page's headings (bold and larger than the text; a part's title, larger
# still, for an office that is a part: the Office of the Dead), or, in the
# second language, the translation under such a heading. A Common printed
# within its family (dobreviary.merge_commons) has no heading of its own: the
# page is its family's, and X a red label on that page or the FAMILY_PAGES
# after it ("Commune Evangelistarum:" before its own prayer). These may point
# forward.
# a name has no "·", "—" or "…" (a reference's), nor ":" (the next note's)
REST = re.compile(r"(Cetera ut in|The rest as in):\s*([^·—…:]{1,200}?),\s*(pag|p)\.\s*(\d+)")
FAMILY_PAGES = 25
heads = {}
reds = {}


def page_headings(i):
    """The letters of page i's office headings, and of the rest of its centred
    text with any "Cetera ut in" taken out (the translations under them)."""
    if i not in heads:
        _mid, head_foot = text_block(d[i])
        spans = [s for b in d[i].get_text("dict")["blocks"] for l in b.get("lines", [])
                 for s in l["spans"] if s["text"].strip() and (s["bbox"][1] + s["bbox"][3]) / 2 > head_foot]
        body = collections.Counter(round(s["size"], 1) for s in spans).most_common(1)
        # A part's title page has its title alone (a book of one language):
        # large against the book's text, not against itself.
        size = min(body[0][0], BODY) if body else BODY
        bold = "".join(s["text"] for s in spans
                       if (s["flags"] & 16 and s["size"] > size * 1.08) or s["size"] > size * 1.5)
        under = REST.sub("", re.sub(r"\s+", " ", centred.get(i, "")))
        heads[i] = (letters(bold), letters(under))
    return heads[i]


def red_labels(i):
    """The letters of page i's red text, column by column (its labels)."""
    if i not in reds:
        found = [" ".join(re.findall(r"⟨([^⟩]*)⟩", line)) for col in pages[i][:2] for line in col]
        reds[i] = letters(" ".join(found + re.findall(r"⟨([^⟩]*)⟩", centred.get(i, ""))))
    return reds[i]


rest_notes, rest_ok, rest_within = 0, 0, 0
for lab, i in sorted(label_to_index.items()):
    texts = [centred.get(i, "")] + ["\n".join(col) for col in pages[i][:2]]
    for text in texts:
        flat = re.sub(r"\s+", " ", re.sub("­\\s*", "", text.replace("⟨", "").replace("⟩", "")))
        for m in REST.finditer(flat):
            rest_notes += 1
            name, target = m.group(2), int(m.group(4))
            where = "p.%d" % lab
            if target not in label_to_index:
                problems["office named does not exist there"].append((where, m.group(0)))
                continue
            at = label_to_index[target]
            bold, under = page_headings(at)
            if letters(name) in bold or letters(name) in under:
                rest_ok += 1
            elif bold and any(letters(name) in red_labels(j)
                              for j in range(at, min(at + FAMILY_PAGES + 1, len(d))) if j in pages):
                rest_ok += 1  # a Common within its family, which is headed there
                rest_within += 1
            else:
                problems["office named is not headed on that page"].append((where, m.group(0)))

latin = sum(1 for r in refs if r[1] == 0)
partial = sum(1 for r in refs if any(x[1] for x in r[2]))
print("%s: %d pages, body 1-%d, rubric colour #%06x" % (os.path.basename(PDF), len(d), max(label_to_index), RUB))
if ONE:
    print("references: %d (one language); %d of them to part of a section" % (len(refs), partial))
else:
    print("references: %d Latin, %d English; %d of them to part of a section" % (latin, len(refs) - latin, partial))
print("verified: %d of %d (%d of them naming a heading, found there)"
      % (ok, len(refs), named_ok[0]))
if next_page:
    print("English to the page after the Latin's (its text a row lower): %d" % next_page)
if split_rows:
    print("A reference's row broken at a page's end (Latin and English either side): %d"
          % split_rows)
if rest_notes:
    print("\"Cetera ut in\" with a page: %d, the office headed there: %d%s" % (
        rest_notes, rest_ok, " (%d of them a Common named within its family, headed there)" % rest_within
        if rest_within else ""))
print("PROBLEMS: %d" % sum(len(v) for v in problems.values()))
for kind, items in problems.items():
    print("  %s: %d" % (kind, len(items)))
    for where, raw in items[:400]:
        print("     %-14s %s" % (where, raw[:170]))
sys.exit(1 if problems else 0)
