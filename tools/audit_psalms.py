"""Audit the verse numbers of the psalms and canticles: every place a number is
skipped or repeated, in the Latin, the Douay English and the Coverdale psalter,
and whether each is accounted for.

    python tools/audit_psalms.py              # the counts; exit status 1 if anything is new
    python tools/audit_psalms.py --details    # every place, the two columns side by side

A psalm file is its verses, one sung line each: "119:5 Heu mihi ... * (6) multum
incola fuit anima mea." The number is the Vulgate's. So, in the Latin:

  - a psalm whose title is its first verse or two begins at 2 or 3 (the title
    is not sung);
  - a verse sung as two lines has its number twice ("50:3a", "50:3b": the
    engine prints both as 50:3);
  - two verses sung as one line have the second number inside the line, "(6)"
    -- which the engine does not print, so the page goes from 5 to 7 -- or, in
    some lines, nowhere: those were read against the Vulgate in November 2026
    (JOINED, below) and each holds its second verse whole;
  - a canticle takes the verses its title names ("Thren. 5:1-7,15-17,19-21").

A number skipped in any other way is reported: read the lines around it.

The Douay English follows the Latin line for line, and must have the same
numbers at the head of the same lines (a few canticles join two of the Latin's
lines into one: ENGLISH_JOINS).

The Coverdale psalter (tools/make_coverdale.py) divides its verses its own
way and is numbered by the Latin beside it. Each of its numbers must be one
the Latin has, at the head of a line or inside one; where the two have a
different count of lines for a number, one column has a line with nothing
beside it, and --details prints each such place as the book sets it.
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dopdf  # the data's folder, as the builder finds it

LINE = re.compile(r"^(\d+):(\d+)([a-z]?)\s+(.*)$")
INNER = re.compile(r"\((\d+)[a-z]?\)")

# Verses with no number of their own in the Latin, at the head of a line or
# inside one, that were read and found whole in the line before ("psalm:verse").
JOINED = set("""21:19 24:2 28:8 38:11 39:9 47:8 48:10 49:2 58:11 63:5 64:9 65:2 72:22 76:16 80:10
82:7 83:7 85:4 86:2 88:17 88:38 97:7 102:15 103:18 104:20 105:22 105:34 105:36 108:16 244:2""".split())
# ... and one that is not said: on the Epiphany the antiphon is Psalm 94's
# sixth verse ("Venite, adoremus eum"), and stands in its place.
NOT_SAID = {"94:6"}
# Canticles whose English joins lines the Latin divides, or numbers a line by
# the Bible where the Latin repeats its verse (251: Isaias 62:7).
ENGLISH_JOINS = {"250", "251", "266", "267", "268"}


def read(horas, lang, name):
    """[(chapter, verse, letter, text, [verses marked inside])] and the title line."""
    path = os.path.join(horas, lang, "Psalterium", "Psalmorum", name)
    rows, title = [], ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as fh:
            for raw in fh:
                raw = raw.rstrip("\r\n")
                m = LINE.match(raw)
                if m:
                    rows.append((int(m.group(1)), int(m.group(2)), m.group(3), m.group(4),
                                 [int(x) for x in INNER.findall(m.group(4))]))
                elif raw.startswith("(") and not rows:
                    title = raw
    return rows, title


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--details", action="store_true", help="print every place, not only the counts")
    ap.add_argument("--horas", help="the data's www/horas folder (default: the app's, else repo/'s)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    horas = args.horas or os.path.join(dopdf._default_paths()[0], "www", "horas")
    folder = os.path.join(horas, "Latin", "Psalterium", "Psalmorum")
    names = sorted((f for f in os.listdir(folder) if re.fullmatch(r"Psalm\d+C?\.txt", f)),
                   key=lambda f: (int(re.sub(r"\D", "", f)), f))
    problems = collections.defaultdict(list)
    count = collections.Counter()
    details = []

    for name in names:
        ps = name[5:-4]
        number = int(re.sub(r"\D", "", ps))
        canticle = number > 150
        la, title = read(horas, "Latin", name)
        if not la:
            count["without verse numbers (the Athanasian Creed)"] += 1
            continue
        count["psalms" if not canticle else "canticles"] += 1
        count["lines"] += len(la)

        # -- the Latin's own numbers -------------------------------------
        chapters = list(dict.fromkeys(r[0] for r in la))
        for ch in chapters:
            rows = [r for r in la if r[0] == ch]
            heads = [r[1] for r in rows]
            inner = [v for r in rows for v in r[4]]
            seen = set(heads) | set(inner)
            if not canticle and min(heads) > 1:
                count["psalms that begin after verse 1 (the title is not sung)"] += 1
            count["verses sung as two lines or more (the number repeated)"] += sum(
                1 for n in collections.Counter(heads).values() if n > 1)
            count["lines holding a second verse, its number inside"] += len(inner)
            if heads != sorted(heads) and ps != "210":  # the Benedicite ends with verse 56
                problems["Latin: numbers out of order"].append("Ps %s: %s" % (ps, heads))
            selection = canticle and (len(chapters) > 1 or re.search(r"\d\s*[,;]", title))
            for v in range(min(heads), max(seen) + 1):
                if v in seen:
                    continue
                key = "%s:%d" % (ps.rstrip("C"), v)
                if key in JOINED:
                    count["lines holding a second verse, unmarked (read: whole)"] += 1
                    if args.details:
                        line = [r for r in rows if r[1] < v][-1]
                        details.append("Ps %s: verse %d is in line %d%s, unmarked: %s"
                                       % (ps, v, line[1], line[2], line[3]))
                elif key in NOT_SAID:
                    count["a verse the antiphon stands for (Psalm 94 on the Epiphany)"] += 1
                elif selection:
                    count["verses a canticle leaves out, as its title says"] += 1
                else:
                    problems["Latin: a verse number skipped and not accounted for"].append(
                        "Ps %s:%d (after: %s)" % (ps, v, ([r for r in rows if r[1] < v] or [("",) * 4])[-1][3][:90]))

        # -- the Douay English, line for line ----------------------------
        en, _t = read(horas, "English", name)
        if not en:
            problems["Douay: no file"].append(name)
        elif [(r[0], r[1]) for r in la] == [(r[0], r[1]) for r in en]:
            count["Douay: the same numbers line for line"] += 1
        elif canticle and ps in ENGLISH_JOINS and {(r[0], r[1]) for r in en} - {(r[0], r[1]) for r in la} <= {(62, 7)}:
            count["Douay: a canticle whose English joins two of the Latin's lines"] += 1
        else:
            problems["Douay: its numbers are not the Latin's"].append(
                "Ps %s: Latin %s | English %s" % (ps, ["%d:%d%s" % r[:3] for r in la][:14],
                                                   ["%d:%d%s" % r[:3] for r in en][:14]))

        # -- the Coverdale psalter ---------------------------------------
        co, _t = read(horas, "English-Coverdale", name)
        if not co or canticle:
            continue
        count["Coverdale: psalms"] += 1
        la_heads = collections.Counter(r[1] for r in la)
        co_heads = collections.Counter(r[1] for r in co)
        inner = {v for r in la for v in r[4]}
        order = [r[1] for r in co]
        if order != sorted(order):
            problems["Coverdale: numbers out of order"].append("Ps %s: %s" % (ps, order))
        for v in sorted(co_heads):
            if v not in la_heads and v not in inner and "%s:%d" % (ps.rstrip("C"), v) not in JOINED:
                problems["Coverdale: a number the Latin has not"].append("Ps %s:%d" % (ps, v))
        alone_la = sum(max(0, n - co_heads.get(v, 0)) for v, n in la_heads.items())
        alone_co = sum(max(0, n - la_heads.get(v, 0)) for v, n in co_heads.items())
        count["Coverdale: Latin lines with nothing beside them"] += alone_la
        count["Coverdale: its verses with nothing beside them"] += alone_co
        if args.details and (alone_la or alone_co):
            # The rows as the book sets them: the lines of one number side by
            # side in order, the rest of either column alone.
            for v in sorted(set(la_heads) | set(co_heads)):
                ls = [r for r in la if r[1] == v]
                cs = [r for r in co if r[1] == v]
                if len(ls) == len(cs):
                    continue
                details.append("Ps %s:%d  Latin %d line%s, Coverdale %d" % (
                    ps, v, len(ls), "" if len(ls) == 1 else "s", len(cs)))
                for k in range(max(len(ls), len(cs))):
                    details.append("      %-92s | %s" % (
                        ("%d%s %s" % (ls[k][1], ls[k][2], ls[k][3]))[:92] if k < len(ls) else "",
                        ("%d%s %s" % (cs[k][1], cs[k][2], cs[k][3]))[:110] if k < len(cs) else ""))

    print("the psalms' verse numbers, in %s" % horas)
    order = ["psalms", "canticles", "without verse numbers (the Athanasian Creed)", "lines"]
    for key in order + [k for k in count if k not in order]:
        if key in count:
            print("   %-62s %5d" % (key, count[key]))
    if details:
        print()
        print("\n".join(details))
    print("PROBLEMS: %d" % sum(len(v) for v in problems.values()))
    for kind, items in problems.items():
        print("  %s: %d" % (kind, len(items)))
        for item in items:
            print("     %s" % item[:200])
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
