"""The Tables of the breviary (a part of its own, dobreviary "tabulae"): the
moveable feasts for a hundred years, Occurrence and Concurrence, and the
Calendar of feasts with each day's epacts and letter.

Each table is an office whose `table` is (kind, data); the writer sets it with
markup() instead of hours and sections. The moveable feasts and the epacts are
reckoned here, by the Gregorian Calendar; the feasts are the version's own
(the engine's Proper of the Saints); Occurrence and Concurrence are the tables
of the Monastic Diurnal of 1963, printed only in a book of that version.
"""
import datetime as dt
import re

import dopdf

YEARS = 101  # the moveable feasts: from the year the book is made, a hundred years on


# --------------------------------------------------------------------------
# the computus


def golden(y):
    return y % 19 + 1


def epact(y):
    """The year's epact (0-29), and whether it is written 25 in figures: an
    epact of 25 in a year whose Golden Number is above 11."""
    g = golden(y)
    c = y // 100 + 1
    solar = 3 * c // 4 - 12
    lunar = (8 * c + 5) // 25 - 5
    e = (11 * g + 20 + lunar - solar) % 30
    return e, (e == 25 and g > 11)


def _roman(n):
    out = ""
    for v, s in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + s, n - v
    return out


def epact_label(e, figures=False):
    return "*" if e == 0 else ("25" if figures else _roman(e))


def easter(y):
    """Easter Day (the 'anonymous Gregorian algorithm', Meeus)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return dt.date(y, n // 31, n % 31 + 1)


def day_epacts():
    """{(month, day): ([epacts], [as printed])}: the Kalendar's column of
    epacts. The thirty run backwards from * on 1 January, through lunations of
    30 and 29 days in turn; in a hollow one (29 days) xxv and xxiv share a day
    and the 25 in figures stands with xxvi, in a full one with xxv."""
    starts = [(1, 1), (1, 31), (3, 1), (3, 31), (4, 29), (5, 29), (6, 27), (7, 27), (8, 25),
              (9, 24), (10, 23), (11, 22), (12, 21)]
    out = {}
    for i, (m, d) in enumerate(starts):
        start = dt.date(2027, m, d)  # a common year
        end = dt.date(2027, *starts[i + 1]) if i + 1 < len(starts) else dt.date(2028, 1, 1)
        hollow = i % 2 == 1
        labels = []
        for e in [0] + list(range(29, 0, -1)):
            if hollow and e == 24:
                labels[-1] = labels[-1] + [24]
            else:
                labels.append([e])
        for k in range((end - start).days):
            day = start + dt.timedelta(days=k)
            names = [epact_label(e) for e in labels[k]]
            if (25 in labels[k] and not hollow) or (26 in labels[k] and hollow):
                names.append("25")
            out[(day.month, day.day)] = (labels[k], names)
    return out


DAY_EPACTS = day_epacts()


def easter_by_epacts(y):
    """Easter as the Kalendar finds it, for checking easter(): the paschal new
    moon is the day from 8 March to 5 April bearing the year's epact, the full
    moon thirteen days later, Easter the Sunday after it."""
    e, figures = epact(y)
    d = dt.date(y, 3, 8)
    while not (("25" in DAY_EPACTS[(d.month, d.day)][1]) if figures else (e in DAY_EPACTS[(d.month, d.day)][0])):
        d += dt.timedelta(days=1)
    full = d + dt.timedelta(days=13)
    return full + dt.timedelta(days=(7 - full.isoweekday() % 7) or 7)


def dominical(y):
    """The year's Dominical Letter; a leap year's two, the second from 1 March."""
    first_sunday = 1 + (6 - dt.date(y, 1, 1).weekday()) % 7
    letter = "ABCDEFG"[first_sunday - 1]
    if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        letter += "GABCDEF"[first_sunday - 1]
    return letter


def advent(y):
    """The first Sunday of Advent: four Sundays before the last before Christmas Day."""
    xmas = dt.date(y, 12, 25)
    return xmas - dt.timedelta(days=((xmas.isoweekday() % 7) or 7) + 21)


MONTHS_LA_SHORT = ("Jan.", "Feb.", "Mart.", "Apr.", "Maii", "Jun.", "Jul.", "Aug.", "Sept.", "Oct.",
                   "Nov.", "Dec.")


def moveable_rows(first, latin):
    def date(d):
        return ("%d %s" % (d.day, MONTHS_LA_SHORT[d.month - 1]) if latin
                else "%s %d" % (dopdf.MONTHS[d.month - 1][:3], d.day))
    rows = []
    for y in range(first, first + YEARS):
        e, figures = epact(y)
        sunday = easter(y)
        after = lambda n: date(sunday + dt.timedelta(days=n))
        rows.append([str(y), str(golden(y)), epact_label(e, figures), str((y + 3) % 15 or 15),
                     dominical(y), after(-63), after(-46), date(sunday), after(39), after(49),
                     after(60), after(56), date(advent(y))])
    return rows


# --------------------------------------------------------------------------
# the words, in Latin and English

MOVEABLE_HEADS = {
    False: ("Year", "Golden\nNumber", "Epact", "Indic-\ntion", "Domin.\nLetter", "Septua-\ngesima",
            "Ash\nWednesday", "Easter", "Ascen-\nsion", "Pente-\ncost", "Corpus\nChristi",
            "1st Sunday\nafter Pent.", "1st Sunday\nof Advent"),
    True: ("Annus", "Aureus\nNumerus", "Epacta", "Indictio", "Littera\nDominic.", "Septua-\ngesima",
           "Dies\nCinerum", "Pascha", "Ascensio", "Pente-\ncostes", "Corpus\nChristi",
           "Dominica I\npost Pent.", "Dominica I\nAdventus"),
}
MOVEABLE_NOTE = {
    False: "The Epact is the age of the moon on 1 January: * stands for none, and 25 in figures "
           "for the epact 25 in a year whose Golden Number is above 11. A leap year has two "
           "Dominical Letters, the first until the end of February, the second from 1 March. The "
           "first Sunday after Pentecost is the feast of the Most Holy Trinity.",
    True: "Epacta est ætas lunæ die 1 Januarii: * nullam significat, 25 arabicis notis epactam 25 "
          "in anno cujus Aureus Numerus est supra 11. Annus bissextilis duas habet Litteras "
          "Dominicales, priorem usque ad finem Februarii, alteram a die 1 Martii. Dominica I post "
          "Pentecosten est festum Sanctissimæ Trinitatis.",
}

# Occurrence and Concurrence, as the Monastic Diurnal (1963) prints them
# (pages xxvi-xxvii), number for number.
OCC_ROWS = {
    False: ("Feast I Class Univ.", "Feast I Class part.", "Feast II Class Univ.", "Feast II Class part.",
            "Feast III Class Univ.", "Feast III Class part.", "Vigil II Class", "Vigil III Class"),
    True: ("Festum I cl. univ.", "Festum I cl. partic.", "Festum II cl. univ.", "Festum II cl. partic.",
           "Festum III cl. univ.", "Festum III cl. partic.", "Vigilia II cl.", "Vigilia III cl."),
}
OCC_COLS = {
    False: ("and Octave-Day II Class.", "and Octave-Day I Class.", "and Feast III Class part.",
            "and Feast III Class Univ.", "and Feast II Class part.", "and Feast II Class Univ.",
            "and Feast I Class part.", "and Feast I Class Univ.", "and Vigil II Class.", "and Vigil I Class.",
            "and Feria III Class Lent, and Passiontide.", "and Feria III Class Advent.",
            "and Feria II Class.", "and Feria I Class.", "and Sunday II Class.", "and Sunday I Class."),
    True: ("et Dies octava II cl.", "et Dies octava I cl.", "et Festum III cl. partic.",
           "et Festum III cl. univ.", "et Festum II cl. partic.", "et Festum II cl. univ.",
           "et Festum I cl. partic.", "et Festum I cl. univ.", "et Vigilia II cl.", "et Vigilia I cl.",
           "et Feria III cl. Quadragesimæ et Passionis", "et Feria III cl. Adventus",
           "et Feria II cl.", "et Feria I cl.", "et Dominica II cl.", "et Dominica I cl."),
}
OCC = ("3 7 1 1 1 1 6 8 1 7 3 3 3 7 3 7",
       "3 7 1 1 1 1 8 7 1 7 3 3 3 7 3 7",
       "3 2 4 4 4 0 2 2 4 2 3 3 3 2 5 2",
       "0 2 4 4 9 5 2 2 4 2 3 3 5 2 5 2",
       "0 2 5 0 5 5 2 2 5 2 5 3 5 2 2 2",
       "0 2 9 4 5 5 2 2 5 2 5 3 5 2 2 2",
       "0 0 4 4 5 5 2 2 0 0 0 0 0 0 2 0",
       "0 0 5 0 5 0 2 0 0 0 0 0 0 0 2 0")
OCC_KEY = {
    False: ("Office of the 1st, nothing of the 2nd.",
            "Office of the 2nd, nothing of the 1st.",
            "Office of the 1st, Commemoration of the 2nd, at Lauds and Vespers.",
            "Office of the 1st, Commemoration of the 2nd, at Lauds.",
            "Office of the 2nd, Commemoration of the 1st, at Lauds.",
            "Office of the 1st, transfer of the 2nd.",
            "Office of the 2nd, transfer of the 1st.",
            "Office of the more important, transfer of the other.",
            "Office of the movable Feast, Commemoration of the other at Lauds."),
    True: ("Officium de 1°, nihil de 2°.",
           "Officium de 2°, nihil de 1°.",
           "Officium de 1°, commemoratio de 2° in Laudibus et Vesperis.",
           "Officium de 1°, commemoratio de 2° in Laudibus.",
           "Officium de 2°, commemoratio de 1° in Laudibus.",
           "Officium de 1°, translatio 2i.",
           "Officium de 2°, translatio 1i.",
           "Officium de digniore, translatio alterius.",
           "Officium de festo mobili, commemoratio alterius in Laudibus."),
}
OCC_HEAD = {
    False: "The 1st is the Office named at the left of a row, the 2nd the one at the foot of a "
           "column; where they meet, the number of the rule beside the columns.",
    True: "1um est Officium in sinistra linea nominatum, 2um quod in imo columnæ; ubi concurrunt, "
          "numerus regulæ apud columnas.",
}
CON_ROWS = {
    False: ("Sunday I Class", "Sunday II Class", "Feria I Class", "Feria II Class", "Feria III Class",
            "Feria IV Class", "Feast I Class", "Feast II Class", "Feast III Class", "Octave-Day II Class"),
    True: ("Dominica I cl.", "Dominica II cl.", "Feria I cl.", "Feria II cl.", "Feria III cl.",
           "Feria IV cl.", "Festum I cl.", "Festum II cl.", "Festum III cl.", "Dies octava II cl."),
}
CON_COLS = {
    False: ("with Feast I Class.", "with Sunday II Class.", "with Sunday I Class."),
    True: ("cum Festo I cl.", "cum Dominica II cl.", "cum Dominica I cl."),
}
CON = ("1 0 0", "2 0 0", "1 0 0", "2 0 0", "2 0 0", "3 0 0", "1 1 1", "3 1 3", "3 3 3", "2 3 0")
CON_KEY = {
    False: ("Vespers of the current Office, Commemoration of the following.",
            "Vespers of the following Office, Commemoration of the current.",
            "Vespers of the following, nothing of the current."),
    True: ("Vesperæ de currenti, commemoratio de sequenti.",
           "Vesperæ de sequenti, commemoratio de currenti.",
           "Vesperæ de sequenti, nihil de currenti."),
}
CON_HEAD = {
    False: "The current Office is named at the left of a row, the following one at the foot of a "
           "column; where they meet, the number of the rule beside the columns.",
    True: "Officium currens in sinistra linea nominatur, sequens in imo columnæ; ubi concurrunt, "
          "numerus regulæ apud columnas.",
}
NEVER = {False: "0: the two do not fall together.", True: "0: hi casus non occurrunt."}

CAL_HEADS = {False: ("Epact", "Letter", "Day", "Feast", "Class"),
             True: ("Epacta", "Littera", "Dies", "Festum", "Classis")}
LEAP = {
    False: "In a leap year February has 29 days: the 24th is reckoned twice, and St. Matthias, "
           "with each feast after him, is kept a day later.",
    True: "In anno bissextili Februarius habet dies 29: dies 24 bis computatur, et festum S. "
          "Matthiæ, cum festis sequentibus, die sequenti celebratur.",
}
# The titles of the Calendar that the data has in Latin only (the Monastic
# 1963 Calendar, November 2026): their English, for a book with English.
CALENDAR_EN = {
    "Die Octavæ Nativitatis Domini": "Octave Day of the Nativity of the Lord",
    "In Cathedra S. Petri Apostoli": "Chair of St. Peter the Apostle",
    "S. P. N. Benedicti Abbatis": "Our Holy Father St. Benedict, Abbot",
    "S. Cleti Papæ et Martyris": "St. Cletus, Pope and Martyr",
    "Ss Odonis, Majoli, Odilonis et Hugonis Abbatis O. N.": "Sts. Odo, Majolus, Odilo and Hugh, Abbots",
    "Ss. Alexándri, Evéntii et Theodúli Martyrum": "Sts. Alexander, Eventius and Theodulus, Martyrs",
    "S Pachomii Abbatis": "St. Pachomius, Abbot",
    "Ss. Marcellini et Petri Martyrum": "Sts. Marcellinus and Peter, Martyrs",
    "S. Barnabæ Apostoli": "St. Barnabas the Apostle",
    "In Solemnitate S. P. N. Benedicti Abbatis": "Solemnity of Our Holy Father St. Benedict, Abbot",
    "S. Leonis IV Papæ et Confessoris O. N.": "St. Leo IV, Pope and Confessor",
    "S. Vincentii a Paulo, Confessoris": "St. Vincent de Paul, Confessor",
    "Ss. Hieronymi Æmilianii, Josephi Calasanctii et Joannis Baptistæ de la Salle Confessorum":
        "Sts. Jerome Emiliani, Joseph Calasanz and John Baptist de la Salle, Confessors",
    "Ss. Joachim et Annæ Parentum B.M.V.": "Sts. Joachim and Anne, Parents of the Blessed Virgin Mary",
    "In dedicatione Sanctae Mariæ Virginis ad Nives": "Dedication of St. Mary of the Snows",
    "S. Cyriaci, Martyris": "St. Cyriacus, Martyr",
    "S. Tiburtii Martyris": "St. Tiburtius, Martyr",
    "Bl. Bernardi Ptolomaei Abbatis, O. N.": "Bl. Bernard Tolomei, Abbot",
    "Septem Dolorum Beatæ Mariæ Virginis": "The Seven Sorrows of the Blessed Virgin Mary",
    "S Hildegardis Virginum O. N.": "St. Hildegard, Virgin",
    "Ss Mauri et Placidi, discipulorum SPN Benedicti, Confessores":
        "Sts. Maurus and Placid, Disciples of Our Holy Father St. Benedict",
    "Beatæ Mariæ Virginis a Rosario": "Our Lady of the Rosary",
    "In Commemoratione Omnium Defunctorum Ordinis nostri": "Commemoration of All the Departed of Our Order",
    "S. Columbani Abbatis": "St. Columban, Abbot",
}

TITLES = {  # (Latin, English)
    "moveable": ("Tabula Festorum Mobilium", "Table of Moveable Feasts"),
    "occurrence": ("Tabella Occurrentiæ", "Table of Occurrence"),
    "concurrence": ("Tabella Concurrentiæ", "Table of Concurrence"),
    "calendar": ("Kalendarium cum Epactis", "Calendar of Feasts, with the Epacts"),
}
OCC_VERSIONS = re.compile(r"Monastic - 1963")  # whose Occurrence and Concurrence these are


def has_rules(version):
    """Whether a book of this version prints Occurrence and Concurrence."""
    return bool(OCC_VERSIONS.match(version or ""))


# --------------------------------------------------------------------------
# the Calendar's feasts


def _class(o, latin, monastic):
    if o.get("commemoratio"):
        return "Commemoratio" if latin else "Commemoration"
    text = " ".join(x for x in (o.get("rankname"), o.get("rank")) if x)
    m = re.search(r"\b(IV|III|II|I)\.? classis\b", text)
    if not m:
        return re.sub(r"\s+", " ", o.get("rankname") or o.get("rank") or "").strip()
    if m.group(1) == "IV" and monastic:
        return "Memoria" if latin else "Memorial"
    return "%s classis" % m.group(1) if latin else "%s Class" % m.group(1)


def _english(t):
    t = re.sub(r"^S\. ", "St. ", t)
    return re.sub(r"^Ss\. ", "Sts. ", t)


def calendar_months(sancti, english, two, monastic):
    """[[(day, epacts, letter, [(title, title2, class, kind)])] for each month]."""
    feasts = {}
    for o in (sancti or {}).get("offices", []):
        if not o.get("date") or (o.get("rank") or "").startswith("Feria"):
            continue  # an undated office; a day after the Epiphany, no feast
        mm, dd = (int(x) for x in o["date"].split("-"))
        t = (o.get("title") or [o["key"], o["key"]])[:2]
        titles = []
        for k in range(2 if two else 1):
            text = t[k] or ""
            if english[k]:
                # The data gives the Latin where it has no English (or nothing,
                # in the second language): the English of CALENDAR_EN for it.
                text = _english(CALENDAR_EN.get(text or t[0], text))
            titles.append(text)
        kind = "comm" if o.get("commemoratio") else (
            "great" if re.search(r"\b(I|II)\.? classis\b", (o.get("rankname") or "") + " " + (o.get("rank") or ""))
            else "")
        feasts.setdefault((mm, dd), []).append(
            (titles[0], titles[1] if two else "", _class(o, not english[0], monastic), kind))
    months = []
    for m in range(1, 13):
        days = []
        for d in range(1, 32):
            try:
                doy = dt.date(2027, m, d).timetuple().tm_yday
            except ValueError:
                continue  # 29 February: the note under the month
            day_feasts = sorted(feasts.get((m, d), []), key=lambda f: f[3] == "comm")
            days.append((d, " ".join(DAY_EPACTS[(m, d)][1]), "Abcdefg"[(doy - 1) % 7], day_feasts))
        months.append(days)
    return months


# --------------------------------------------------------------------------
# the offices


def table_offices(part, version, sancti, lang1, lang2, monastic, office_cls, first_year=None):
    """The part's offices, one a table: (kind, data) in office.table."""
    import dooffice

    english = (dooffice.family(lang1) == "English", dooffice.family(lang2) == "English")
    latin = not english[0]
    two = lang1 != lang2
    first = first_year or dt.date.today().year
    kinds = ["moveable"] + (["occurrence", "concurrence"] if has_rules(version) else []) + ["calendar"]
    offices = []
    for kind in kinds:
        la, en = TITLES[kind]
        if kind == "moveable":
            data = {"first": first, "rows": moveable_rows(first, latin)}
        elif kind == "calendar":
            data = {"months": calendar_months(sancti, english, two, monastic), "two": two}
        else:
            data = {}
        offices.append(office_cls(part=part, key="tab-" + kind, title=la, title2=en, table=(kind, data)))
    return offices


# --------------------------------------------------------------------------
# the markup


def _lines(s, q=dopdf.q):
    """A heading of one or more lines ("Golden\nNumber")."""
    return "[%s]" % "#linebreak()".join("#" + q(x) for x in s.split("\n"))


def _q_list(items):
    return "(%s,)" % ", ".join(dopdf.q(x) for x in items)


def markup(office, lang1):
    """The Typst for a table office, after its heading. `rub` is the rubric
    colour; a table wider than the page is set smaller, to its width."""
    import dooffice

    q = dopdf.q
    kind, data = office.table
    latin = dooffice.family(lang1) != "English"
    # Ragged lines in the tables' narrow cells, the body's own spacing kept.
    out = ["#[", "#set par(justify: false)"]
    if kind == "moveable":
        heads = ", ".join("text(fill: rub, size: 0.88em, %s)" % _lines(h) for h in MOVEABLE_HEADS[latin])
        cells = []
        for r in data["rows"]:
            cells += ["strong(%s)" % q(r[0])] + [q(c) for c in r[1:7]] + ["strong(%s)" % q(r[7])] + \
                [q(c) for c in r[8:]]
        out.append(
            "#layout(size => {\n"
            "  let t(s) = text(size: s, table(columns: 13, inset: (x: 0.32em, y: 0.22em),\n"
            "    align: (x, y) => center + if y == 0 { bottom } else { horizon },\n"
            "    stroke: (x, y) => (bottom: if y == 0 { 0.7pt + rub } else if calc.rem(y, 5) == 0 { 0.4pt + rub } else { none },\n"
            "      left: if x == 1 or x == 5 { 0.4pt } else { none }),\n"
            "    table.header(%s),\n"
            "    %s))\n"
            "  let w = measure(t(0.86em)).width\n"
            "  align(center, t(calc.min(1, size.width / w) * 0.86em))\n"
            "})" % (heads, ", ".join(cells)))
        out.append('#block(above: 0.6em, text(size: 0.8em, style: "italic", %s))' % q(MOVEABLE_NOTE[latin]))
    elif kind in ("occurrence", "concurrence"):
        occ = kind == "occurrence"
        rows = (OCC_ROWS if occ else CON_ROWS)[latin]
        cols = (OCC_COLS if occ else CON_COLS)[latin]
        cells = [r.split() for r in (OCC if occ else CON)]
        key = (OCC_KEY if occ else CON_KEY)[latin]
        head = (OCC_HEAD if occ else CON_HEAD)[latin]
        out.append('#block(below: 0.8em, text(size: 0.86em, style: "italic", %s))' % q(head))
        body = []
        for name, nums in zip(rows, cells):
            body.append("box(width: 100%%, %s + box(width: 1fr, repeat[#h(0.25em).]))" % q(name))
            body += ["text(size: 1.05em, %s)" % q(n) for n in nums]
        keyrows = ", ".join("[%d.], %s" % (i + 1, q(k)) for i, k in enumerate(key))
        # Set smaller where the page is too narrow for the columns, or too
        # short for the table and its key beside the turned names.
        out.append(
            "#layout(size => {\n"
            "  let n = %d\n"
            "  let t(s) = text(size: s, table(columns: (1fr,) + (1.45em,) * n, inset: (x: 0.2em, y: 0.42em),\n"
            "    align: (x, y) => if x == 0 { left + horizon } else if y < %d { center + horizon } else { center + top },\n"
            "    stroke: (x, y) => (left: if x >= 1 { 0.6pt } else { none }, right: if x == n { 0.6pt } else { none },\n"
            "      bottom: if y < %d { 0.5pt + rub } else { none }),\n"
            "    %s,\n"
            "    table.cell(inset: (top: 0.9em, right: 0.8em), {\n"
            "      set text(size: 0.9em)\n"
            "      grid(columns: (1.4em, 1fr), row-gutter: 0.7em, align: left + top, %s)\n"
            "      v(0.5em)\n"
            "      text(size: 0.9em, style: \"italic\", %s)\n"
            "    }),\n"
            "    ..%s.map(c => table.cell(inset: (top: 0.7em, bottom: 0.3em), rotate(90deg, reflow: true, text(size: 0.9em, c))))))\n"
            "  let fw = calc.min(1, size.width * %s / (n * 1.45em).to-absolute())\n"
            "  let h = measure(t(fw * 1em), width: size.width).height\n"
            "  t(fw * calc.min(1, size.height * 0.85 / h) * 1em)\n"
            "})" % (len(cols), len(rows), len(rows), ", ".join(body), keyrows,
                    q(NEVER[latin]), _q_list(cols), "0.7" if occ else "0.3"))
    else:  # the Calendar
        two = data["two"]
        heads = list(CAL_HEADS[latin])
        if two:
            heads.insert(4, "")
        names = ["Januarius", "Februarius", "Martius", "Aprilis", "Majus", "Junius", "Julius",
                 "Augustus", "September", "October", "November", "December"] if latin else dopdf.MONTHS
        for m, days in enumerate(data["months"]):
            # The months run on, one under another. The running head reads
            # its hour from a page where an office begins: each month begins
            # one, so that a page is headed by the month it starts.
            out.append("#metadata((d: %s, t: %s))<day>" % (q(office.part.la), q(office.title)))
            out.append("#metadata(%s)<hour>" % q(names[m]))
            out.append("#grouphead(%s)" % q(names[m]))
            cells = []
            for d, epacts, letter, feasts in days:
                main = [f for f in feasts if f[3] != "comm"]
                comm = [f for f in feasts if f[3] == "comm"]

                def styled(f, k):
                    t = f[k]
                    if f[3] == "comm":
                        return 'text(size: 0.9em, style: "italic", %s)' % q(("Comm. " if k == 0 else "") + t)
                    style = ', style: "italic"' if k == 1 else ""
                    if f[3] == "great":
                        return "text(fill: rub%s, %s)" % (style, q(t))
                    return "text(%s%s)" % (q(t), style) if style else q(t)

                def column(k):
                    parts = [styled(f, k) for f in main + comm]
                    return "[%s]" % "#linebreak()".join("#" + p for p in parts) if parts else "[]"
                row = ["text(size: 0.88em, %s)" % q(epacts),
                       'text(fill: rub, weight: "bold", %s)' % q(letter) if letter == "A" else q(letter),
                       q(str(d)), column(0)]
                if two:
                    row.append(column(1))
                row.append("[%s]" % "#linebreak()".join(
                    "#text(size: 0.88em%s, %s)" % (', style: "italic"' if f[3] == "comm" else "", q(f[2]))
                    for f in main + comm))
                cells.append(", ".join(row))
            out.append(
                "#text(size: %s, table(columns: (auto, auto, auto, %s auto), inset: (x: 0.3em, y: 0.24em),\n"
                "  align: (x, y) => (right, center, right, left, left, left).at(x) + horizon,\n"
                "  stroke: (x, y) => (bottom: if y == 0 { 0.7pt + rub } else { 0.3pt + rub.lighten(65%%) }),\n"
                "  table.header(%s),\n"
                "  %s))" % ("0.86em" if two else "0.92em", "1fr, 1fr," if two else "1fr,",
                           ", ".join("text(fill: rub, size: 0.8em, %s)" % q(h) for h in heads),
                           ",\n  ".join(cells)))
            if m == 1:
                out.append('#block(above: 0.5em, text(size: 0.8em, style: "italic", %s))' % q(LEAP[latin]))
    out.append("]")
    return "\n".join(out)


if __name__ == "__main__":
    # The two reckonings of Easter agree for these years and the next thousand.
    for y in range(1583, 4100):
        assert easter(y) == easter_by_epacts(y), y
        a = advent(y)
        assert dt.date(y, 11, 27) <= a <= dt.date(y, 12, 3) and a.isoweekday() == 7, y
    print("Easter by both reckonings agrees, 1583-4099")
