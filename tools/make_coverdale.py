#!/usr/bin/env python3
"""
Make the English-Coverdale language: the Psalter of the Book of Common Prayer
(Coverdale) in place of the English psalms, everything else from English.

The engine reads each psalm from Psalterium/Psalmorum/PsalmN.txt, numbered as
the Latin numbers it (the Vulgate's psalms and verses), one verse a line:

    109:1a The Lord said to my Lord: * Sit thou at my right hand:

A language named "English-Coverdale" that has only these files takes the
rest from English (the part before the dash). This script writes them into
data-additions/English-Coverdale, from

    sources/coverdale/psalter.txt     the pointed Prayer Book psalter, as given
    sources/coverdale/canticles.txt   the Magnificat and Benedictus, annotated

and datafixes.py puts data-additions/ into the data. The psalter file is the
Prayer Book's thirty days; read from it are the psalms and verses alone (not
the day and Morning/Evening Prayer headings, the Glorias and Amens), its parts
(Psalm 119's twenty-two, I. and II. of others) joined again, a part's first
verse numbered as the one after the last, and a verse broken over two lines
joined.

Numbering. The Prayer Book numbers psalms as the Hebrew does (Latin 109 is its
110; Latin 9 is its 9 and 10, Latin 113 its 114 and 115, its 116 is Latin 114
and 115, its 147 Latin 146 and 147) and divides verses as it will. So each
psalm is lined up against the project's English (the Douay, which follows the
Latin line for line) by the words they share, in order, one verse to one line
where it can and else one to two, two to one: each Coverdale verse takes the
number of the Latin line it stands beside. Where it spans two of the Latin's
verses it takes the first's; where two share one Latin line, the second takes
the number printed inside that line ("(9)") or else the letter b. Where the
Latin breaks a psalm into parts (118(1-16), 9(22-39) ...), every break falls
between two Coverdale verses, which this script checks.

The invitatory (Psalterium/Invitatorium.txt) and Psalm 94 take the Prayer
Book's Venite in the Latin's divisions and marks.

    python tools/make_coverdale.py          # write the files, report
    python tools/make_coverdale.py --show 4 # how Latin psalm 4 was lined up
"""

import argparse
import glob
import math
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HORAS = os.path.join(HERE, "repo", "web", "www", "horas")
SOURCE = os.path.join(HERE, "sources", "coverdale", "psalter.txt")
CANTICLES = os.path.join(HERE, "sources", "coverdale", "canticles.txt")
LANGUAGE = "English-Coverdale"
OUT = os.path.join(HERE, "data-additions", LANGUAGE, "Psalterium")

# Corrections to the source: (Prayer Book psalm, verse, text, replacement).
SOURCE_FIXES = [
    # three verses lacked their *; the accents mark the cadence before it
    (4, 1, "tróuble; have mercy", "tróuble; * have mercy"),
    (5, 3, "thée, • and", "thée, * and"),
    (11, 1, "sóul, that she", "sóul, * that she"),
    # the engine prints any (...) in a psalm as a red rubric
    (7, 4, "* (yea,", "* yea,"),
    (7, 4, "énemy;)", "énemy;"),
    (49, 8, "(For it", "For it"),
    (49, 8, "forever;)", "forever;"),
    # stray letters among the accents
    (105, 19, "LŐRD", "LÓRD"),
    (119, 13, "have İ been", "have Í been"),
]

ROMAN_PART = re.compile(r"^[IVX]+\.\s+[A-Z][a-z]")


def plain(s):
    """Lower case without accents (to recognise the Gloria however it is pointed)."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def parse(path=SOURCE):
    """{Prayer Book psalm: [[verse, text]]}"""
    psalms, cur, first_next = {}, None, False
    with open(path, encoding="utf-8") as fh:
        raw_lines = fh.read().splitlines()
    for raw in raw_lines:
        s = " ".join(raw.split())
        p = plain(s)
        if not s or re.match(r"^the [a-z-]+ day$", p) or re.match(r"^(morning|evening) prayer\.?$", p):
            continue
        if re.match(r"^amen\.?$", p):
            first_next = True  # what follows begins a psalm or a part
            continue
        m = re.match(r"^Psalm (\d+)\.?(\s|$)", s)
        if m:
            cur = int(m.group(1))
            psalms[cur] = []
            first_next = True
            continue
        if ROMAN_PART.match(s):
            first_next = True
            continue
        if re.match(r"^(\d+\s+)?(glory be to the father|as it was in the beginning)", p):
            continue
        m = re.match(r"^(\d+)\s+(.*)$", s)
        if m:
            psalms[cur].append([int(m.group(1)), m.group(2)])
            first_next = False
        elif first_next:
            psalms[cur].append([psalms[cur][-1][0] + 1 if psalms[cur] else 1, s])
            first_next = False
        else:
            psalms[cur][-1][1] += " " + s  # a verse broken over two lines
    for h, n, old, new in SOURCE_FIXES:
        verse = psalms[h][n - 1]
        if old not in verse[1]:
            raise SystemExit("source fix not found: psalm %d:%d %r" % (h, n, old))
        verse[1] = verse[1].replace(old, new)
    for verses in psalms.values():
        for v in verses:
            v[1] = tidy(v[1])
    return psalms


def tidy(t):
    t = re.sub(r"\s*†\s*([,;:.])", r"\1 †", t)  # "temple†," -> "temple, †"
    t = re.sub(r"\s*†\s*", " † ", t)
    t = re.sub(r"\s*\*\s*", " * ", t)
    t = re.sub(r"(\w)\s+([,;:])", r"\1\2", t)  # "LÓRD ;"
    return " ".join(t.split())


def vulgate_parts():
    """[(Latin psalm, [(Prayer Book psalm, first verse, last verse)])]"""
    out = []
    for v in range(1, 151):
        if v <= 8 or v >= 148:
            src = [(v, 1, 999)]
        elif v == 9:
            src = [(9, 1, 999), (10, 1, 999)]
        elif v <= 112:
            src = [(v + 1, 1, 999)]
        elif v == 113:
            src = [(114, 1, 999), (115, 1, 999)]
        elif v == 114:
            src = [(116, 1, 9)]
        elif v == 115:
            src = [(116, 10, 999)]
        elif v <= 145:
            src = [(v + 1, 1, 999)]
        elif v == 146:
            src = [(147, 1, 11)]
        else:
            src = [(147, 12, 999)]
        out.append((v, src))
    return out


def psalm_lines(lang, v):
    """[(verse, letter, text)] of the project's file for Latin psalm v."""
    out = []
    with open(os.path.join(HORAS, lang, "Psalterium", "Psalmorum", "Psalm%d.txt" % v),
              encoding="utf-8-sig") as fh:
        for l in fh.read().splitlines():
            m = re.match(r"^\d+:(\d+)([a-z]?)\s+(.*)$", l)
            if m:
                out.append((int(m.group(1)), m.group(2), m.group(3)))
    return out


# -- lining up ---------------------------------------------------------------

STOP = set("""the and of to in that he his him thou thy thee me my mine i is be for a an unto
shall hath have them they will with all not are it as but which o upon from their who ye our us
we you your so there this at by was were been also yea even then when let lord god doth did do art
hast shalt wilt thine its into out up down or nor no on what whom how why because than these
those such any every over before after under""".split())

# Words the two translations use for one Latin word, by their first five letters.
SYNONYMS = """just right | wicke ungod | iniqu wicke | justi right | natio heath genti |
enemi foes adver | tribu troub affli | confe thank | exult rejoi glad | salva healt |
merci kindn | mount hills hill | hear heark | cried cry call | sanct holy | glory worsh |
fathe forea | wrath anger displ indig | snare net | poor needy | despi scorn | prais laud |
waters flood | habit dwell | taber tent | proud scorn | power might | refug defen |
protec defen | delive rid | kings princ | truth faith"""
SYN = {}
for _group in SYNONYMS.split("|"):
    _ws = _group.split()
    for _w in _ws:
        SYN.setdefault(_w, _ws[0])


def words(t):
    ws = re.findall(r"[a-z]+", plain(t))
    return {SYN.get(w[:5], w[:5]) for w in ws if w not in STOP and len(w) > 2}


MERGE = 1.6  # what a verse more on either side must earn, in shared words


def align(cov, lines):
    """Groups ([Coverdale indices], [Latin line indices]), in order, covering both."""
    n, m = len(cov), len(lines)
    cw = [words(t) for t in cov]
    lw = [words(t) for _n, _c, t in lines]
    NEG = float("-inf")
    best = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if best[i][j] == NEG:
                continue
            for di, dj in ((1, 1), (1, 2), (2, 1), (1, 3), (3, 1), (2, 2)):
                i2, j2 = i + di, j + dj
                if i2 > n or j2 > m:
                    continue
                shared = len(set().union(*cw[i:i2]) & set().union(*lw[j:j2]))
                cost = MERGE * (di - 1 + dj - 1)
                if di == 1 and dj > 1 and len({lines[k][0] for k in range(j, j2)}) == 1:
                    cost = 0.1  # the Latin's verse in lines a, b: one Coverdale verse
                score = best[i][j] + shared - cost
                if score > best[i2][j2]:
                    best[i2][j2] = score
                    back[i2][j2] = (i, j)
    groups, i, j = [], n, m
    while (i, j) != (0, 0):
        pi, pj = back[i][j]
        groups.append((list(range(pi, i)), list(range(pj, j))))
        i, j = pi, pj
    groups.reverse()
    return groups


def labels(groups, lines):
    """The Latin verse number ("12", "3a") for each Coverdale verse, in order."""
    out = []
    for ci, li in groups:
        first = lines[li[0]]
        whole = all(lines[k][0] == first[0] for k in li) and \
            len(li) == sum(1 for l in lines if l[0] == first[0])
        base = "%d%s" % (first[0], "" if whole and len(li) > 1 else first[1])
        if len(ci) == 1:
            out.append(base)
        elif len(li) == len(ci):
            out += ["%d%s" % (lines[k][0], lines[k][1]) for k in li]
        else:
            inner = re.findall(r"\((\d+[a-z]?)\)", " ".join(lines[k][2] for k in li))
            if len(inner) >= len(ci) - 1:
                out += [base] + inner[:len(ci) - 1]
            elif not first[1]:
                out += ["%d%s" % (first[0], "abc"[k]) for k in range(len(ci))]
            else:
                out += [None] * len(ci)  # see LABELS
    return out


# Numbers the lining up cannot give: (Prayer Book psalm, verse) -> Latin verse.
LABELS = {
    # The two divide 111:5-7 at different places. Coverdale's 6 begins at the
    # Latin's verse 6, printed inside its line 5 ("(6) quia in ætérnum...").
    (112, 6): "6",
    (112, 7): "7a",
}


def coverdale_by_latin(psalms):
    """{Latin psalm: [(Prayer Book psalm, verse, text)]}"""
    return {v: [(h, n, t) for h, a, b in src for n, t in psalms[h] if a <= n <= b]
            for v, src in vulgate_parts()}


def latin_breaks():
    """{Latin psalm: {first verse of each part}} from every "N(a-b)" in the Latin data."""
    breaks = {}
    for f in glob.glob(os.path.join(HORAS, "Latin", "**", "*.txt"), recursive=True):
        with open(f, encoding="utf-8-sig") as fh:
            text = fh.read()
        for m in re.finditer(r"(?<![\d:])(\d{1,3})\((\d+)[a-z]?-(\d+)[a-z]?\)", text):
            v = int(m.group(1))
            if v <= 150:
                breaks.setdefault(v, set()).update({int(m.group(2)), int(m.group(3)) + 1})
    return breaks


# -- the Venite --------------------------------------------------------------


def _halves(t):
    a, _, b = t.partition(" * ")
    return a, b


def _unmarked(t):
    return " ".join(t.replace(" * ", " ").replace(" † ", " ").split())


def _cap(t):
    return t[:1].upper() + t[1:]


def _low(t):
    return t if t[:2].isupper() else t[:1].lower() + t[1:]


def venite(psalms):
    """Psalm94.txt, Psalm94C.txt and Invitatorium.txt from the Prayer Book's 95."""
    c = {n: t for n, t in psalms[95]}
    h7, h8 = _halves(c[7]), _halves(c[8])
    v7 = "%s * %s:" % (_cap(h7[1].rstrip(".")) + ".", h8[0])
    v8 = "%s * %s" % (_cap(h8[1]), _low(_unmarked(c[9])))
    v6 = "%s * %s" % (_unmarked(c[6]).rstrip(",.") + ".", h7[0])
    ps94 = ["94:1 " + c[1], "94:2 " + c[2], "$ant", "94:3 " + c[3], "94:4 " + c[4], "$ant",
            "94:5 " + c[5], "$ant", "94:7 " + v7, "94:8 " + v8, "$ant",
            "94:9 " + c[10], "94:10 " + c[11], "$ant"]
    ps94c = ["94:%d %s" % (n, c[n]) for n in range(1, 6)] + \
            ["94:6 " + v6, "94:7 " + v7, "94:8 " + v8, "94:9 " + c[10], "94:10 " + c[11]]
    a1, b1 = _halves(c[1])
    a10, b10 = _halves(c[10])
    b10a, _, b10b = _unmarked(b10).partition(", for ")
    a8, b8 = _halves(c[8])
    invit = [
        "$ant", "$ant",
        "v. %s + %s * %s" % (a1, b1, _unmarked(c[2])),
        "$ant",
        "v. %s = %s" % (_unmarked(c[3]), _unmarked(c[4])),
        "$ant2",
        "v. %s /:(genuflect):/ %s %s" % (_unmarked(c[5]), _cap(_unmarked(c[6])).rstrip(",.") + ".",
                                       _unmarked(c[7])),
        "$ant",
        # the engine capitalises the word after ^ when it begins there
        "v. %s, ^ %s %s" % (a8.rstrip(",;:"), b8, _low(_unmarked(c[9]))),
        "$ant2",
        "v. %s _ %s, _ for %s %s" % (a10, b10a, b10b, _low(_unmarked(c[11]))),
        "$ant", "&Gloria", "$ant2", "$ant",
    ]
    return ps94, ps94c, invit


# -- the files -----------------------------------------------------------------


def canticles():
    """{file name: lines} from canticles.txt."""
    out, cur = {}, None
    with open(CANTICLES, encoding="utf-8") as fh:
        for line in fh.read().splitlines():
            m = re.match(r"^\[(Psalm\d+)\]$", line.strip())
            if m:
                cur = out.setdefault(m.group(1) + ".txt", [])
            elif cur is not None and line.strip() and not line.startswith("#"):
                cur.append(line.strip())
    return out


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = "\r\n".join(lines) + "\r\n"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(data)


def make(show=()):
    psalms = parse()
    problems = []
    for h in range(1, 151):
        nums = [n for n, _ in psalms.get(h, [])]
        if not nums or nums != list(range(1, len(nums) + 1)):
            problems.append("Prayer Book psalm %d: verses %s" % (h, nums))
    cov = coverdale_by_latin(psalms)
    breaks = latin_breaks()
    files, shapes = {}, {}
    for v in range(1, 151):
        if v == 94:
            continue
        lines = psalm_lines("English", v)
        latin = [(n, c) for n, c, _t in psalm_lines("Latin", v)]
        if [(n, c) for n, c, _t in lines] != latin:
            # the English's own numbering slipped (122:2y): number from the Latin
            lines = [(n, c, t) for (n, c), (_n, _c, t) in zip(latin, lines)]
        groups = align([t for _h, _n, t in cov[v]], lines)
        labs = labels(groups, lines)
        for k, (h, n, _t) in enumerate(cov[v]):
            labs[k] = LABELS.get((h, n), labs[k])
            if labs[k] is None:
                problems.append("Latin %d: no number for Prayer Book %d:%d (add it to LABELS)" % (v, h, n))
                labs[k] = "?"
        order = [(int(re.match(r"\d+", l).group()), l) for l in labs if l != "?"]
        if order != sorted(order):
            problems.append("Latin %d: numbers out of order: %s" % (v, labs))
        for ci, li in groups:
            key = "%d:%d" % (len(ci), len(li))
            shapes[key] = shapes.get(key, 0) + 1
        for cut in breaks.get(v, ()):
            for ci, li in groups:
                nums = [lines[k][0] for k in li]
                if min(nums) < cut <= max(nums):
                    problems.append("Latin %d: its part from verse %d begins inside a Coverdale verse"
                                    % (v, cut))
        files["Psalm%d.txt" % v] = ["%d:%s %s" % (v, lab, t) for lab, (_h, _n, t) in zip(labs, cov[v])]
        if v in show:
            for ci, li in groups:
                print("Latin %d %s" % (v, ["%d%s" % lines[k][:2] for k in li]))
                for k in ci:
                    print("    Coverdale %d:%d as %s: %s" % (cov[v][k][0], cov[v][k][1], labs[k], cov[v][k][2]))
                for k in li:
                    print("    Douay:     %s" % lines[k][2])
    ps94, ps94c, invit = venite(psalms)
    files["Psalm94.txt"], files["Psalm94C.txt"] = ps94, ps94c
    files.update(canticles())
    for name, lines in files.items():
        for line in lines:
            body = re.sub(r"^\d+:\d+[a-z]?\s+", "", line)
            if line.startswith(("$", "(")):
                continue
            if body.count("*") != 1:
                problems.append("%s: not one * in %r" % (name, line[:60]))
            if "(" in body or ")" in body:
                problems.append("%s: parentheses in %r" % (name, line[:60]))
    folder = os.path.join(OUT, "Psalmorum")
    if os.path.isdir(folder):
        for old in os.listdir(folder):
            if old not in files:
                os.remove(os.path.join(folder, old))
    for name, lines in files.items():
        write(os.path.join(folder, name), lines)
    write(os.path.join(OUT, "Invitatorium.txt"), invit)
    print("%d psalm files, Psalm94C, %d canticles and the invitatory in %s"
          % (151, len(canticles()), os.path.relpath(os.path.dirname(OUT), HERE)))
    print("Coverdale verses beside Latin lines (Coverdale:Latin): %s"
          % ", ".join("%s %d" % kv for kv in sorted(shapes.items(), key=lambda kv: -kv[1])))
    for p in problems:
        print("PROBLEM: " + p)
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, nargs="*", default=[],
                    help="print how these Latin psalms were lined up")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    return make(set(args.show))


if __name__ == "__main__":
    sys.exit(main())
