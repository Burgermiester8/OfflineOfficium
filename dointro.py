"""
"How to use this breviary": a few pages at the front, in English, on finding
the day's Office and saying an Hour from this book -- with this book's own
pages. Its examples are real offices of the book (the first Sunday of the
Proper of the Season, a saint whose office takes the rest from a Common),
so whatever parts are chosen and however they fall, every page it names is
one the book prints. A sentence about a part the book leaves out is left out.

placeholder() makes the part's office where it stands; fill() (from
dobreviary.finish) writes its paragraphs once the book's offices are final.
"""

import re

import dopdf

TITLE = ("De Usu hujus Breviarii", "How to Use This Breviary")
# What each sign in the text means.
SIGNS = (("℣.", "versicle: said by one (the officiant)"),
         ("℟.", "response: answered by all"),
         ("✠", "the sign of the cross is made"),
         ("*", "in a psalm, the pause in the middle of the verse; in an antiphon before a psalm, "
               "where it is broken off"),
         ("†", "a short pause (the flex) in a long first half-verse"),
         ("pag. 56 / p. 56", "the text is printed in full on that page: its opening words are "
                             "given here"),
         ("usque ad", "say the text from the page given as far as the words that follow"))


def placeholder(part, office_cls, english):
    return [office_cls(part=part, key="introductio", title=TITLE[1] if english else TITLE[0],
                       title2=TITLE[0] if english else TITLE[1], untitled=True,
                       table=("intro", {"paras": []}))]


def fill(offices, label, family):
    """Write the introduction among `offices` (the book or volume, final).
    label(office) gives an office's heading a label; a paragraph is a list
    of pieces, each text or ("page", label)."""
    intro = next((o for o in offices if o.table is not None and o.table[0] == "intro"), None)
    if intro is None:
        return
    body = [o for o in offices if not o.untitled and o.rows is None and o.table is None
            and not o.include]
    first = {}  # each part's first office with a heading
    for o in offices:
        if not o.untitled and not o.include and not o.part.key.startswith("custom"):
            first.setdefault(o.part.key, o)
    by_key = {o.key: o for o in body}

    def page(o):
        return ("page", label(o)) if o is not None else ""

    def part_page(key):
        return page(first.get(key))

    def name(key):
        o = first.get(key)
        return o.part.en if o is not None else ""

    paras = []
    have = lambda k: k in first

    # What the book holds.
    parts = [("ordo", "the Ordo for one year: the Office of every day, and its page"),
             ("kalendarium", "the Calendar: each month's feasts, their class and colour, and the page "
                             "of each feast's office"),
             ("tabulae", "the Tables: the moveable feasts for a hundred years, and the rules for a feast "
                         "falling on another"),
             ("orationes", "the prayers said before and after the Office"),
             ("ordinarium", "the Ordinary: the order of every Hour, and what is said the same way each day"),
             ("psalterium", "the Psalter: the psalms of each Hour, for each day of the week"),
             ("tempora", "the Proper of the Season: the Sundays and weekdays of the year, from Advent"),
             ("temporis", "the Common of the Seasons: what a season's weekdays share"),
             ("sancti", "the Proper of the Saints: the feasts of the year by their dates"),
             ("commune", "the Common of the Saints: what a saint's office takes when it has nothing of "
                         "its own"),
             ("defunctorum", "the Office of the Dead"),
             ("missa", "the Proper of the Sunday Masses"),
             ("indulgentiae", "on indulgences: what they are, their kinds and conditions, the texts "
                              "of this book that carry them, and the documents"),
             ("indices", "the indexes: the psalms and canticles, the hymns and the feasts, each with its "
                         "page")]
    listed = [(k, words) for k, words in parts if have(k)]
    if listed:
        paras.append(("head", "What this book contains"))
        for k, words in listed:
            paras.append(("item", [words[0].upper() + words[1:] + ", ", "p.", part_page(k), "."]))

    # The day.
    paras.append(("head", "Finding the Office of the day"))
    day = ["Each day has one Office, of the season or of a feast; a lesser feast may also be "
           "commemorated in it. "]
    if have("ordo"):
        day += ["The Ordo (", "p.", part_page("ordo"), ") gives the Office of every day of its year, "
                "with its class, its colour, its commemorations and the page where it is printed; "
                "a note under a day says when Vespers are of the next day's Office. "]
    if have("kalendarium"):
        day += ["The Calendar (", "p.", part_page("kalendarium"), ") gives the feasts kept on fixed "
                "dates. When a feast and a day of the season fall together, the one of higher "
                "class is said, and the other may be commemorated in it. "]
    if have("tabulae"):
        day += ["The Tables (", "p.", part_page("tabulae"), ") give the date of Easter, and so of "
                "the moveable feasts, for a hundred years, and the rules of occurrence and "
                "concurrence where the version has them. "]
    paras.append(("para", day))

    # An Hour.
    paras.append(("head", "Saying an Hour"))
    hour = []
    if have("ordinarium"):
        hour += ["The Ordinary (", "p.", part_page("ordinarium"), ") gives the order of each Hour "
                 "from beginning to end, and what does not change from day to day. "]
    if have("psalterium"):
        hour += ["The psalms, with their antiphons, are taken from the Psalter (", "p.",
                 part_page("psalterium"), ") for the day of the week, unless the feast has psalms "
                 "of its own. The running head of each page of the Psalter names the day and the "
                 "Hour. "]
    proper = [k for k in ("tempora", "sancti") if have(k)]
    if proper:
        hour += ["What belongs to the day (its antiphons, chapter, hymn, versicle and prayer) is "
                 "taken from the "]
        for i, k in enumerate(proper):
            hour += [("" if i == 0 else " or the "), name(k), " (", "p.", part_page(k), ")"]
        hour += [". "]
    if have("commune"):
        hour += ["What a feast has not of its own is taken from the Common of the Saints (", "p.",
                 part_page("commune"), "): under the feast's title, “The rest as in” names "
                 "the Common and its page. "]
    if hour:
        paras.append(("para", hour))

    # Examples.
    examples = []
    sunday = next((o for o in body if o.part.key == "tempora" and re.fullmatch(r"\w+\d-0", o.key)), None)
    if sunday is not None:
        import doordo

        name_en = sunday.title2 if sunday.title2 and sunday.title2 != sunday.title else \
            (doordo.season_english(sunday.title) or sunday.title)
        ex = ["On a Sunday, ", ("i", name_en), " for "
              "example: the Office is of the Sunday, printed in the Proper of the Season on ", "p.",
              page(sunday), "."]
        if have("psalterium") and "psalter-0" in by_key:
            ex += [" Its psalms are Sunday's in the Psalter, ", "p.", page(by_key["psalter-0"]), "."]
        examples.append(ex)
    # A saint ("S. Titi") whose office takes the rest from the Common of the Saints.
    labels = {o.anchor: o for o in offices if o.anchor}
    saint = next((o for o in body if o.part.key == "sancti" and re.match(r"^(S|Ss|SS|St|Sts)\. ", o.title)
                  and o.rest and len(o.rest) > 2 and o.rest[2]
                  and getattr(labels.get(o.rest[2]), "part", None) is not None
                  and labels[o.rest[2]].part.key == "commune"), None)
    if saint is not None:
        common = labels.get(saint.rest[2])
        ex = ["On a saint's feast, ", ("i", saint.title2 or saint.title),
              (" (%s)" % saint.left if saint.left else ""), " for example: its own texts are on ",
              "p.", page(saint), ", and under its title it names the Common it takes the rest from"]
        if common is not None:
            ex += [", on ", "p.", page(common)]
        ex += [". Say the Hour as the Ordinary has it, taking each part from the feast where it "
               "has one, and otherwise from the Common."]
        examples.append(ex)
    feria = next((o for o in body if o.part.key == "psalterium" and o.key == "psalter-3"), None)
    if feria is not None:
        ex = ["On a weekday with no feast, a Wednesday for example: the psalms are Wednesday's in "
              "the Psalter, ", "p.", page(feria), "; the prayer is the previous Sunday's, unless "
              "the weekday has its own (as in Lent)."]
        examples.append(ex)
    if examples:
        paras.append(("head", "Examples"))
        for ex in examples:
            paras.append(("para", ex))

    # Signs.
    paras.append(("head", "Signs"))
    for sign, meaning in SIGNS:
        paras.append(("sign", [sign, meaning]))
    paras.append(("gap", ""))
    note = ["Rubrics, the directions for saying the Office, are printed in red. "
            "A text said more than once in the book is printed in full the first time; later it is "
            "given by its opening words and the page of the first."]
    paras.append(("para", note))
    if family in ("monastic", "cist"):
        paras.append(("para", ["This book follows the monastic Office: Matins of three Nocturns "
                               "on Sundays and feasts, of two on weekdays, and the whole Psalter "
                               "said each week."]))
    intro.table = ("intro", {"paras": paras})


def markup(office, writer):
    """The introduction's paragraphs, in English."""
    q = dopdf.q
    import dooffice

    col = 1 if dooffice.family(writer.meta.lang1) != "English" and \
        dooffice.family(writer.meta.lang2) == "English" else 0

    def pieces(items):
        out = []
        for it in items:
            if isinstance(it, tuple) and it and it[0] == "page":
                out.append("#" + writer.page_ref(it[1], col, bare=True) if it[1] else "")
            elif isinstance(it, tuple) and it and it[0] == "i":
                out.append("#emph(%s)" % q(it[1]))
            elif it == "p.":
                out.append("p.~")
            elif it:
                out.append("#%s" % q(it))
        return "".join(out)

    out = ["#[", "#set par(justify: true)", '#set text(lang: "en")']
    for kind, body in office.table[1]["paras"]:
        if kind == "head":
            out.append("#block(above: 1.1em, below: 0.45em, sticky: true, text(fill: rub, weight: "
                       "\"bold\", %s))" % q(body))
        elif kind == "gap":
            out.append("#v(0.5em)")
        elif kind == "note":  # a source, under what it is the source of
            out.append("#block(above: -0.2em, below: 0.6em, text(size: 0.86em, fill: luma(80), [%s]))"
                       % pieces(body))
        elif kind == "item":
            out.append("#block(below: 0.3em, inset: (left: 1em), [• %s])" % pieces(body))
        elif kind == "sign":
            out.append("#block(below: 0.3em, grid(columns: (6em, 1fr), column-gutter: 0.6em, "
                       "text(fill: rub, %s), [%s]))" % (q(body[0]), "#" + q(body[1])))
        else:
            out.append("#block(below: 0.6em, [%s])" % pieces(body))
    out.append("]")
    return "\n".join(out)
