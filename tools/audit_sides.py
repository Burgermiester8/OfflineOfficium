"""Audit which side each language stands on in a breviary set with the first
language on the outside of each page, against the same book set without that
(`--no-mirror`, or the box unticked: the first language at the left of every
page), from the two PDFs' printed pages alone.

    python tools/audit_sides.py plain.pdf mirrored.pdf

Exit status 0 when every page checks out, 1 otherwise.

  1. Each page has the same words in both books (a number is any number: a
     page referred to may differ). Where the two break a page at another
     line, the pages until they agree again have the same words between
     them; those are counted and not compared further.
  2. On a left-hand page (an even page number) every word stands where it
     does in the plain book.
  3. On a right-hand page the words of the two columns have changed places:
     the plain book's left column is the right one, and its right the left.
     What stands as it does in the plain book there is a line set across the
     page (a heading, the note under an office's title, the Calendar): its
     words are upon the middle of the page, or leave a word's space there.
  4. No word is printed over another (a row that goes over a page turn is
     set in both orders, each word shown in one of them).

The Calendar's pages and those before page 1 are set across the page and are
only compared word for word (1).
"""
import collections
import re
import sys

try:
    import pymupdf as fitz  # PyMuPDF 1.24 and later
except ImportError:
    import fitz

sys.stdout.reconfigure(encoding="utf-8")
plain, mirrored = fitz.open(sys.argv[1]), fitz.open(sys.argv[2])
HEAD = 46  # the running head's band, in points from the top
ACROSS = ("Kalendarium", "Calendar")


def clean(text):
    """A word as compared: without soft hyphens and word joiners; a number is
    any number (a page referred to can differ between the two books, below)."""
    text = re.sub("[­⁠]", "", text)
    return "#" if re.fullmatch(r"\d+[.,;:]?", text) else text


def words_of(page):
    return [w for w in page.get_text("words") if w[4].strip() and (w[1] + w[3]) / 2 > HEAD]


def rows_of(words):
    """The words in rows: those level with one another."""
    rows = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        c = (w[1] + w[3]) / 2
        if rows and abs(c - rows[-1][0]) < 0.4 * (w[3] - w[1]):
            rows[-1][1].append(w)
        else:
            rows.append([c, [w]])
    return [r for _c, r in rows]


def across(row, mid, em):
    """A row set across the page: a word upon the middle, or a word's space there."""
    ends = [w[2] for w in row if w[2] <= mid]
    starts = [w[0] for w in row if w[0] >= mid]
    return (any(w[0] < mid < w[2] for w in row) or (ends and max(ends) > mid - 0.18 * em)
            or (starts and min(starts) < mid + 0.18 * em)
            or (ends and starts and min(starts) - max(ends) < max(0.52 * em, GUTTER / 2)))


def calendar_pages(doc):
    """The pages of the Calendar, from the book's bookmarks."""
    tops = [(title, page) for level, title, page in doc.get_toc() if level == 1]
    out = set()
    for (title, page), (_t, nxt) in zip(tops, tops[1:] + [("", len(doc) + 1)]):
        if title.startswith(ACROSS):
            out.update(range(page - 1, nxt - 1))
    return out


def overlaps(words):
    ws = sorted(words, key=lambda w: w[1])
    n = 0
    for k, u in enumerate(ws):
        for v in ws[k + 1:]:
            if v[1] > u[3] - 2:
                break
            if min(u[2], v[2]) - max(u[0], v[0]) > 1.5 and min(u[3], v[3]) - max(u[1], v[1]) > 3:
                n += 1
    return n


def pitch():
    """How far a column's words are from where the other column has them: the
    commonest distance between a word of a right-hand page (one that the
    page has only once) and the same word in the plain book."""
    found = collections.Counter()
    for i in range(min(len(plain), len(mirrored))):
        label = mirrored[i].get_label()
        if not (label and label.isdigit() and int(label) % 2):
            continue
        pw, mw = words_of(plain[i]), words_of(mirrored[i])
        once = {t for t, n in collections.Counter(w[4] for w in mw).items() if n == 1}
        there = {w[4]: w[0] for w in pw if w[4] in once}
        for w in mw:
            if w[4] in there and abs(w[0] - there[w[4]]) > 40:
                found[round(abs(w[0] - there[w[4]]))] += 1
        if sum(found.values()) > 4000:
            break
    return found.most_common(1)[0][0] if found else 0


def match(words, others, shifts):
    """Each word with the same word of the other book, level with it and one
    of `shifts` away (to within NEAR points): ([matched], [not matched]);
    the words of `others` that were matched are taken out of it."""
    got, rest = [], []
    for w in words:
        here = others.get((clean(w[4]), round((w[1] + w[3]) / 2 / 4)), [])
        hit = next((o for o in here for d in shifts if abs(w[0] - o[0] - d) <= NEAR
                    and abs(w[1] - o[1]) <= 1.5), None)
        if hit is None:
            rest.append(w)
        else:
            here.remove(hit)
            got.append(w)
    return got, rest


# A line's last comma hangs 0.26 em into the margin; in the few words set one
# by one where a row goes over a page turn it does not, and the line is
# justified to the margin itself: 2.1 pt at 8 pt.
NEAR = 3.0
PITCH = pitch()
if not PITCH:
    print("No right-hand page has a word in the other column: the second book is not set with "
          "the first language on the outside.")
    sys.exit(1)


def gutter():
    """The space between the two columns: the commonest gap at the middle of
    a row of the plain book (to half a point)."""
    found = collections.Counter()
    for i in range(min(len(plain), 400)):
        label = plain[i].get_label()
        if not (label and label.isdigit()):
            continue
        mid = plain[i].rect.width / 2
        for row in rows_of(words_of(plain[i])):
            ends = [w[2] for w in row if w[2] <= mid]
            starts = [w[0] for w in row if w[0] >= mid]
            if ends and starts and len(ends) + len(starts) == len(row):
                found[round(2 * (min(starts) - max(ends))) / 2] += 1
        if sum(found.values()) > 5000:
            break
    return found.most_common(1)[0][0] if found else 8.0


# A row across the page leaves a word's space at the middle, a row of the two
# columns their gutter (less a comma hanging into it): half the gutter parts them.
GUTTER = gutter()
problems = collections.defaultdict(list)
if len(plain) != len(mirrored):
    problems["the books have different numbers of pages"].append(
        ("", "%d and %d" % (len(plain), len(mirrored))))
skip = calendar_pages(mirrored)
checked = exchanged = 0
differ = []  # (page index, its name) of the pages whose words are not the plain book's
for i in range(min(len(plain), len(mirrored))):
    page = mirrored[i]
    label = page.get_label()
    where = "p.%s" % label if label else "page %d of the file" % (i + 1)
    pw, mw = words_of(plain[i]), words_of(page)
    if collections.Counter(clean(w[4]) for w in pw) != collections.Counter(clean(w[4]) for w in mw):
        differ.append((i, where))
        continue
    over = overlaps(mw)
    if over:
        problems["words printed over one another"].append((where, "%d" % over))
    if not (label and label.isdigit()) or i in skip:
        continue
    checked += 1
    mid = page.rect.width / 2
    heights = collections.Counter(round(w[3] - w[1], 1) for w in mw).most_common(1)
    em = heights[0][0] / 1.36 if heights else 8.0  # a word's box is its line: about 1.36 em
    others = collections.defaultdict(list)
    for w in pw:
        c = (w[1] + w[3]) / 2 / 4
        for k in {round(c - 0.3), round(c), round(c + 0.3)}:
            others[(clean(w[4]), k)].append(w)
    if int(label) % 2 == 0:
        _stay, rest = match(mw, others, (0,))
        if rest:
            problems["a left-hand page not as in the plain book"].append(
                (where, " ".join(w[4] for w in rest[:8])))
        continue
    # A right-hand page: a word has changed columns (first: the same word may
    # stand in both columns, a "℟." before each language's line) ...
    moved, rest = match(mw, others, (PITCH, -PITCH))
    stay, lost = match(rest, others, (0,))
    if lost:
        problems["a right-hand page's word neither where it was nor in the other column"].append(
            (where, " ".join(w[4] for w in lost[:8])))
        continue
    # ... or stands where it did, in a row set across the page.
    stayed = {id(w) for w in stay}
    bad = next((r for r in rows_of(mw) if any(id(w) in stayed for w in r)
                and not across(r, mid, max(em, max(w[3] - w[1] for w in r) / 1.36))), None)
    if bad:
        problems["a right-hand page's column not exchanged"].append(
            (where, " ".join(w[4] for w in sorted(w for w in bad if id(w) in stayed)[:8])))
    elif moved:
        exchanged += 1

# Pages with other words than the plain book's. The two books are not set by
# the same means (the plain one a grid of rows, this one row by row), and
# where a line fits a page by a hair one of them may take it over the page:
# the pages from there to the next office that begins a page have the same
# words between them, a line or two further on. Those are counted, not
# compared; any other difference is a problem.
elsewhere, run = 0, []
for k, (i, where) in enumerate(differ + [(None, "")]):
    if run and i != run[-1][0] + 1:
        both = [collections.Counter(clean(w[4]) for j, _w in run for w in words_of(doc[j]))
                for doc in (plain, mirrored)]
        if len(run) > 1 and both[0] == both[1]:
            elsewhere += len(run)
        else:
            problems["other words than in the plain book"] += [(w, "") for _j, w in run]
        run = []
    if i is not None:
        run.append((i, where))

print("%s against %s: %d pages" % (sys.argv[2].replace("\\", "/").split("/")[-1],
                                   sys.argv[1].replace("\\", "/").split("/")[-1], len(mirrored)))
print("numbered pages checked: %d (the Calendar's %d left out); right-hand pages with their "
      "columns exchanged: %d (a column's words %d pt from the other's)" % (checked, len(skip), exchanged, PITCH))
if elsewhere:
    print("pages the two books break at another line (the same words between them; not compared): %d"
          % elsewhere)
print("PROBLEMS: %d" % sum(len(v) for v in problems.values()))
for kind, items in problems.items():
    print("  %s: %d" % (kind, len(items)))
    for where, what in items[:40]:
        print("     %-22s %s" % (where, what[:150]))
sys.exit(1 if problems else 0)
