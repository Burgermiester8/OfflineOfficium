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
     a page can end between them);
  5. the number printed in page N's running head is N;
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

# the rubric colour: that of the ℟ signs
colours = collections.Counter()
for i in list(label_to_index.values())[20:40]:
    for b in d[i].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if "℟" in s["text"]:
                    colours[s["color"]] += 1
RUB = colours.most_common(1)[0][0]
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


def columns(page):
    """([Latin lines], [English lines], number in the head) -- or, in a book of
    one language, ([its lines in reading order], [], number in the head)."""
    # Span by span: PyMuPDF sometimes joins the two columns' lines on one
    # baseline into a single line.
    cols = {0: {}, 1: {}}
    head_num = None
    mid, head_foot = text_block(page)
    blocks = page.get_text("dict")["blocks"]
    # One language across the page (one column, the Calendar): text runs over
    # the middle; in two columns nothing does but a part's title.
    across = ONE and sum(1 for b in blocks for l in b.get("lines", []) for s in l["spans"]
                         if s["bbox"][0] < mid - 5 and s["bbox"][2] > mid + 5 and s["bbox"][3] > head_foot) >= 3
    for bi, b in enumerate(blocks):
        for li, l in enumerate(b.get("lines", [])):
            for s in l["spans"]:
                t = s["text"].replace("⁠", "")
                if not t.strip():
                    continue
                x0, y0, x1, y1 = s["bbox"]
                if y1 < head_foot:
                    if t.strip().isdigit():
                        head_num = int(t.strip())
                    continue
                if x0 < mid - 5 and x1 > mid + 5 and not ONE:
                    continue  # centred headings (hours, offices)
                if s["color"] == GREY_AI and t.strip() == "AI":
                    continue
                c = 0 if across or (x0 + x1) / 2 < mid else 1
                key = (bi, li)
                # The height of this column's part of the line, not of the
                # joined line: a Latin line joined to the English one beside
                # it would take the English line's height, and a Latin line
                # with a large initial before it could come out after it.
                y, x, out = cols[c].get(key, (round(y0, 1), x0, ""))
                cols[c][key] = (min(y, round(y0, 1)), min(x, x0),
                                out + ("⟨%s⟩" % t if s["color"] == RUB else t))
    lefts = [x for _y, x, _t in cols[0].values()]
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
        # the page's text as letters, with where each red run begins
        flat, reds = "", []
        for part in re.split(r"(⟨[^⟩]*⟩)", cut):
            if part.startswith("⟨"):
                reds.append((len(flat), letters(part)))
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

by_page = collections.defaultdict(lambda: ([], []))
for lab, c, cands, target, raw, _heading in refs:
    by_page[lab][c].append(target)
next_page = 0
for lab, (la, en) in sorted(by_page.items()):
    if ONE:
        break  # one language: nothing to pair
    if len(la) != len(en) or any(b - a not in (0, 1) for a, b in zip(la, en)):
        problems["Latin and English differ"].append(("p.%d" % lab, "Latin %s / English %s" % (la, en)))
    next_page += sum(1 for a, b in zip(la, en) if b == a + 1) if len(la) == len(en) else 0

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
print("PROBLEMS: %d" % sum(len(v) for v in problems.values()))
for kind, items in problems.items():
    print("  %s: %d" % (kind, len(items)))
    for where, raw in items[:400]:
        print("     %-14s %s" % (where, raw[:170]))
sys.exit(1 if problems else 0)
