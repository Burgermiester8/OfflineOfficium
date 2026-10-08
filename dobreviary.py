"""
Build a breviary: the Divine Office arranged as a printed breviary is --
Calendar, Ordinary, Psalter, Proper of the Season, Common of the Seasons,
Proper of the Saints, Common of the Saints, with the prayers before and after
the Office -- in any order, for any version, typeset with the same machinery
as the dated PDFs.

Where dopdf.py asks the engine "what is said on this day?", this asks it for
the *books themselves*. engine_dump.pl loads every file of a part with the
engine's own setupstring() and expands every section with its own
resolve_refs(), so version conditions, cross-references, the Monastic /
Cistercian / Dominican overlays and all the shorthand ($Per Dominum, &psalm,
V./R.) are the engine's work. This module adds only what a book needs and the
engine has no notion of: which hour each section belongs to, a heading for it,
and an order. The Ordinary, the Psalter and the Common of the Seasons are not
files of their own but the frame of every day's office; dopsalter.py reads
them from the engine's own offices.

Command line:
    python dobreviary.py --parts tempora,sancti,commune -o breviary.pdf
"""

import argparse
import collections
import concurrent.futures
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace

from pypdf import PdfReader

import docgi
import dooffice
import dopdf
import dopsalter
import dotranslate
import dovolumes

# --------------------------------------------------------------------------
# the parts


@dataclass(frozen=True)
class Part:
    key: str
    la: str
    en: str
    compact: bool = False  # a page or two: no title page of its own


PARTS = {
    "kalendarium": Part("kalendarium", "Kalendarium", "Calendar"),
    "orationes": Part("orationes", "Orationes ante et post Divinum Officium",
                      "Prayers before and after the Divine Office", compact=True),
    "ordinarium": Part("ordinarium", "Ordinarium Divini Officii", "Ordinary of the Divine Office"),
    "psalterium": Part("psalterium", "Psalterium", "Psalter"),
    "tempora": Part("tempora", "Proprium de Tempore", "Proper of the Season"),
    "temporis": Part("temporis", "Commune de Tempore", "Common of the Seasons"),
    "sancti": Part("sancti", "Proprium Sanctorum", "Proper of the Saints"),
    "commune": Part("commune", "Commune Sanctorum", "Common of the Saints"),
    "missa": Part("missa", "Proprium Missarum Dominicalium", "Proper of the Sunday Masses"),
}
DEFAULT_ORDER = tuple(PARTS)
# Parts a new list leaves unticked: an addition to the breviary, not part of it.
DEFAULT_OFF = ("missa",)
# Parts since merged: the prayers before and after the Office were two parts.
FORMER = {"ante": "orationes", "post": "orationes"}


def book_part(key, lang1):
    """A part as a book in lang1 names it: an English book's own name is the
    English one, its Latin beneath where there are two languages."""
    part = PARTS[key]
    if dooffice.family(lang1) == "English":
        return replace(part, la=part.en, en=part.la)
    return part


def current_parts(keys):
    """Part keys as they are now, in order, each once (older names mapped);
    custom parts ("custom1" ...) kept as they are."""
    return list(dict.fromkeys(FORMER.get(k, k) for k in keys
                              if FORMER.get(k, k) in PARTS or str(k).startswith("custom")))
DUMPED = ("tempora", "sancti", "commune")  # the parts that are files of the engine's data
RENDERED = ("ordinarium", "psalterium", "temporis")  # read from its offices (dopsalter.py)

# --------------------------------------------------------------------------
# where each section belongs
#
# 263 distinct section names occur across the three parts of the 1960 office.
# The common ones are placed explicitly; anything unrecognised is still
# printed, under its own name, in a closing "Alia" group -- nothing is lost.

GROUPS = {
    "O": (5, "In toto Officio", "Throughout the Office"),
    "V1": (10, "In I Vesperis", "At First Vespers"),
    "M": (20, "Ad Matutinum", "At Matins"),
    "L": (30, "Ad Laudes", "At Lauds"),
    "P": (40, "Ad Primam", "At Prime"),
    "T": (50, "Ad Tertiam", "At Terce"),
    "S": (60, "Ad Sextam", "At Sext"),
    "N": (70, "Ad Nonam", "At Nones"),
    "V2": (80, "In II Vesperis", "At Second Vespers"),
    "C": (85, "Ad Completorium", "At Compline"),
    "X": (90, "Commemorationes", "Commemorations"),
    "Z": (95, "Alia", "Other texts"),
    "ALT": (97, "Aliæ lectiones", "Alternative lessons"),
}
HOUR_GROUP = {"Vespera": "V1", "Matutinum": "M", "Laudes": "L", "Prima": "P",
              "Tertia": "T", "Sexta": "S", "Nona": "N", "Completorium": "C"}

MASS = {"Missa", "Introitus", "Lectio", "Graduale", "GradualeP", "Tractus", "Offertorium",
        "Secreta", "Communio", "Postcommunio", "Sequentia", "Prefatio", "Lectio in 2 loco",
        "Oratio pro commemoratio", "Secreta pro commemoratio", "Postcommunio pro commemoratio"}


def roman(n):
    n = int(n)
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += s
            n -= v
    return out


@dataclass
class Slot:
    group: str
    order: float
    la: str
    en: str
    hymn: str = ""  # "", "M" (monastic form): which variant of a hymn this is
    slot: str = ""  # hymns sharing a slot are alternatives of one another


# Sections the engine never reads by name: the data's building blocks, pulled
# into other sections by reference ("@:AntMatutinumM:2"), a version's variant
# the section conditions already chose, or a switch (In Finem Lectio: no Tu
# autem after the lessons).
HELPERS = {
    "AntMatutinumM", "AntM Matutinum", "AntMatutinumMC", "AntMatutinumC", "AntC Matutinum",
    "Ant MatutinumC", "AntMatutinumOP", "AntMatutinum8", "AntMatutinum9", "Ant LaudesC",
    "Ant Vespera 3C", "Ant 1 Cist", "Versum 1C", "Versum 1OP", "Versum 1a", "RV1", "RespVers5",
    "In Finem Lectio", "Ant Matutinum2", "Ant MatutinumBMV", "Ant VesperaBMV", "OratioP",
    "OratioText", "Lectio_Pone", "DeVitiis", "InIllo", "LectioJudae1", "LectioJudae2",
    "LectioJudae3", "Oratio3", "Hymnus Matutinum Virgo tantum",
    # All Souls: parts of its special hours (&special('Initial'), expanded in
    # place by engine_dump.pl), which a monastic office inherits unused.
    "Initial", "Oratio mortuorum", "Oratio mortuorum1", "Oratio mortuorum2",
    # Sections nothing reads: a placeholder ("from tempora"), lessons no rule
    # names, a versicle no commemoration takes.
    "Lectio21", "Lectio91", "Versum Commemoratio",
}
# More of the Mass's texts that share these files.
_MASS_MORE = re.compile(r"(Secreta|Postcommunio)\b.*|Oratio (\d )?(in \d )?loco|Oratio \d loco"
                        r"|Oratio (pro Evangelistae|Dedicationis Altaris)|Lectio in \d loco")


def classify(key, family, version=""):
    """Where a section goes, or None to leave it out (Mass texts and pointers).

    family: "roman", "monastic", "cist" or "op" -- decides a few variants.
    version: the version's name, for the few sections read differently by year.
    """
    k = key.strip()
    if k in MASS or k in HELPERS or k.endswith("_") or _MASS_MORE.fullmatch(k):
        return None
    if k == "Evangelium":  # the Gospel read at monastic Matins; in Roman files, the Mass's
        # After the twelfth lesson's responsory (Lectio N is 10 + N), before
        # the Te decet laus (monastic.pl).
        return Slot("M", 22.9, "Evangelium", "Gospel") if family in ("monastic", "cist") else None
    if k == "Te Deum M":  # engine_dump.pl: the monastic Te Deum, after Responsory XII
        return Slot("M", 22.7, "", "") if family in ("monastic", "cist") else None
    m = re.fullmatch(r"Evangelium in (\d) loco", k)
    if m:
        if family not in ("monastic", "cist"):
            return None
        return Slot("ALT", int(m.group(1)) * 100 + 19.9, "Evangelium (%s. loco)" % m.group(1),
                    "Gospel (in place %s)" % m.group(1))

    # Sections the engine reads by these names (monastic.pl, specials/capitulis.pl,
    # horasscripts.pl ...).
    if k in ("Versum 0", "Versum 0 Pasch"):  # &versiculum_ante_laudes: Dominican only
        if family != "op":
            return None
        pasch = " (tempore paschali)" if "Pasch" in k else ""
        return Slot("L", 0.5, "Versus ante Laudes" + pasch, "Versicle before Lauds" + pasch)
    if k == "Ant Matutinum 3N":
        return Slot("M", 18.9, "Antiphona ad III Nocturnum", "Antiphon of the third Nocturn")
    if k == "MM LB":
        return Slot("M", 9.7, "Lectio brevis", "Short lesson")
    if k == "Invit 3L":
        return Slot("M", 1.1, "Invitatorium (in officio trium lectionum)",
                    "Invitatory (in an office of three lessons)")
    if k == "Oratio Matutinum":
        return Slot("M", 29, "Oratio", "Prayer")
    if re.fullmatch(r"AltLectio\d+", k):
        return None  # no rule reads them
    # Read by name: the hymns' doxology for the feast ([Rule] Doxology=...),
    # the special close of every hour (All Souls), the prayer of the weekdays
    # after the Sunday (orationes.pl), Christmas's First Vespers chapter
    # (capitulis.pl), a Common's prayer for several saints.
    if k == "Doxology":
        return Slot("O", 1, "Doxologia hymnorum", "Doxology of the hymns")
    if k == "Conclusio":
        return Slot("O", 2, "Conclusio Horarum", "The close of the Hours")
    if k == "OratioW":
        return Slot("L", 6.5, "Oratio per hebdomadam", "Prayer on the weekdays")
    if k == "Capitulum Vespera 1":
        return Slot("V1", 2, "Capitulum", "Chapter")
    if k == "Oratio pro plurium":
        return Slot("V1", 6.1, "Oratio pro pluribus", "Prayer for several")
    if k == "Octava":
        return Slot("X", 2, "Commemoratio Octavæ", "Commemoration of the Octave")
    m = re.fullmatch(r"ResponsoryT(\d+)", k)
    if m:  # specmatins.pl: the Tridentine books' own responsory, in place of the other
        if "trident" not in version.lower():
            return None
        n = int(m.group(1))
        return Slot("M", 10 + n + 0.5, "Responsorium %s" % roman(n), "Responsory %s" % roman(n))
    m = re.fullmatch(r"Responsory (Tertia|Sexta|Nona)M", k)
    if m:  # the monastic little hours'; the Cistercian take the Roman form
        if family != "monastic":
            return None
        return Slot(HOUR_GROUP[m.group(1)], 3, "Responsorium breve", "Short responsory")
    if k == "Responsory Laudes":
        return Slot("L", 3, "Responsorium breve", "Short responsory")
    if k == "Responsory Vespera":
        return Slot("V1", 3, "Responsorium breve", "Short responsory")
    if k == "Responsory Vespera 1":  # after the chapter at First Vespers (Dominican, Cistercian)
        return Slot("V1", 2.9, "Responsorium", "Responsory")
    m = re.fullmatch(r"(Special|Prelude) (Vespera|Matutinum|Laudes|Prima|Tertia|Sexta|Nona|Completorium)( 1)?", k)
    if m:
        if m.group(1) == "Prelude":
            return Slot(HOUR_GROUP[m.group(2)], 0.1, "Ante Horam", "Before the Hour")
        return Slot(HOUR_GROUP[m.group(2)], 0.2, "Ordo huius Horæ", "The order of this Hour")
    m = re.fullmatch(r"Lectio M(\d\d)(\d)?", k)
    if m:  # the Office of Our Lady on Saturday: its lessons by month (getC10readingname)
        month, sat = int(m.group(1)), m.group(2)
        # By month and Saturday in 1963 only ("M101" is October's first there);
        # elsewhere "M101" is the week of 9 to 14 September, before 1960.
        if sat and "1963" not in version:
            if k != "Lectio M101" or "196" in version:
                return None
            return Slot("M", 23.2, "Lectio a die IX ad XIV Septembris",
                        "Lesson for 9 to 14 September")
        if not sat and "1963" in version or not 1 <= month <= 12:
            return None
        la = "Lectio mensis %s" % MONTHS_LA[month - 1]
        en = "Lesson for %s" % dopdf.MONTHS[month - 1]
        if sat:
            la += ", Sabbato %s" % roman(sat)
            en += ", %s Saturday" % ("1st", "2nd", "3rd", "4th", "5th")[int(sat) - 1]
        return Slot("M", 23 + month / 100 + int(sat or 0) / 1000, la, en)

    m = re.fullmatch(r"Hymnus(1)?(M)?(M?)(Vespera|Matutinum|Laudes|Prima|Tertia|Sexta|Nona|Completorium)"
                     r"(US)?(?: (3))?|Hymnus(1)?(M)? (Vespera|Matutinum|Laudes|Prima|Tertia|Sexta|Nona|Completorium)( 3)?", k)
    if m:
        alt = m.group(1) or m.group(7)
        mon = m.group(2) or m.group(3) or m.group(8)
        hour = m.group(4) or m.group(9)
        second = (m.group(6) or (m.group(10) or "").strip()) == "3"
        group = "V2" if (hour == "Vespera" and second) else HOUR_GROUP[hour]
        return Slot(group, 3 + (0.5 if alt else 0), "Alius hymnus" if alt else "Hymnus",
                    "Another hymn" if alt else "Hymn", hymn="M" if mon else "",
                    slot="%s%s%s" % (group, "alt" if alt else "", "US" if m.group(5) else ""))

    m = re.fullmatch(r"Ant (Vespera|Laudes|Matutinum|Prima|Tertia|Sexta|Nona|Completorium)( 3)?", k)
    if m:
        group = "V2" if m.group(2) else HOUR_GROUP[m.group(1)]
        many = m.group(1) in ("Vespera", "Laudes", "Matutinum")
        # Matins sings its psalms after the Invitatory and the hymn; the
        # other hours before their chapter and hymn.
        return Slot(group, 3.9 if group == "M" else 1, "Antiphonæ" if many else "Antiphona",
                    "Antiphons" if many else "Antiphon")
    m = re.fullmatch(r"Ant Matutinum (1[12])", k)
    if m:  # one antiphon of the Common's in its place ([Rule] Ant Matutinum 11 special)
        noc, n = _matins_place(int(m.group(1)), family)
        return Slot("M", 3.95, "In %s Nocturno, antiphona %d" % (roman(noc), n),
                    "In the %s Nocturn, antiphon %d" % (NOCTURN_EN[noc - 1], n))

    m = re.fullmatch(r"Ant 4([13])", k)
    if m:  # Compline's, after First or Second Vespers (horas.pl: "Ant 4$vespera")
        first = m.group(1) == "1"
        return Slot("C", 5, "Ad Nunc dimittis (post %s Vesperas)" % ("I" if first else "II"),
                    "At the Nunc dimittis (after %s Vespers)" % ("First" if first else "Second"))

    m = re.fullmatch(r"Ant ([123])(.*)", k)
    if m:
        n, rest = m.group(1), m.group(2).strip()
        if rest in ("C", "") or rest.startswith("_") or "Pontific" in rest:
            if rest == "C" and family != "cist":
                return None  # Cistercian form
            group = {"1": "V1", "2": "L", "3": "V2"}[n]
            la = "Ad Benedictus" if n == "2" else "Ad Magnificat"
            en = "At the Benedictus" if n == "2" else "At the Magnificat"
            note = {"": "", "C": ""}.get(rest)
            if note is None:
                note = " (tempore paschali)" if "Pasch" in rest else (
                    " (pro summo Pontifice)" if "Pontific" in rest else " (alia)")
            return Slot(group, 5 + (0.1 if note else 0), la + note, en + note)

    m = re.fullmatch(r"Capitulum (Vespera|Laudes|Prima|Tertia|Sexta|Nona|Completorium)( 3)?", k)
    if m:
        group = "V2" if m.group(2) else HOUR_GROUP[m.group(1)]
        return Slot(group, 2, "Capitulum", "Chapter")
    if k == "MM Capitulum":  # monastic.pl: an office of three lessons, in place of the third Nocturn
        return Slot("M", 4.5, "Capitulum in fine II Nocturni", "Chapter at the end of the second Nocturn")

    m = re.fullmatch(r"Versum ([0-3]|Tertia|Sexta|Nona|Prima)", k)
    if m:
        v = m.group(1)
        group = {"1": "V1", "2": "L", "3": "V2", "0": "Z"}.get(v) or HOUR_GROUP[v]
        return Slot(group, 4, "Versus", "Versicle")

    m = re.fullmatch(r"Responsory Breve (Prima|Tertia|Sexta|Nona|Vespera|Laudes)", k)
    if m:
        return Slot(HOUR_GROUP[m.group(1)], 3, "Responsorium breve", "Short responsory")
    if k == "Lectio Prima":
        return Slot("P", 3, "Lectio brevis", "Short lesson")

    if k == "Invit":
        return Slot("M", 1, "Invitatorium", "Invitatory")
    m = re.fullmatch(r"Nocturn (\d) Versum", k)
    if m:
        return Slot("M", 4 + int(m.group(1)) / 10, "Versus Nocturni %s" % roman(m.group(1)),
                    "Versicle of Nocturn %s" % roman(m.group(1)))
    if k == "Scriptura":
        return Slot("M", 9.9, "Lectiones de Scriptura", "Scripture lessons")
    if k == "Benedictio":
        return Slot("M", 9.8, "Benedictiones", "Blessings")

    m = re.fullmatch(r"Lectio9([34])", k)
    if m:
        return Slot("M", 19.5, "Lectio IX (de commemoratione)", "Lesson IX (of the commemoration)")
    m = re.fullmatch(r"(Lectio|Responsory) ?M?(\d+)(.*)", k)
    if m and "loco" not in k and int(m.group(2)) <= 12:
        n = int(m.group(2))
        extra = m.group(3).strip()
        resp = m.group(1) == "Responsory"
        la = ("Responsorium %s" if resp else "Lectio %s") % roman(n)
        en = ("Responsory %s" if resp else "Lesson %s") % roman(n)
        if extra:
            la += " (alia)"
            en += " (alternative)"
        return Slot("M", 10 + n + (0.5 if resp else 0) + (0.05 if extra else 0), la, en)
    m = re.fullmatch(r"(Lectio|Responsory)(\d+) in (\d) loco", k)
    if m:
        resp = m.group(1) == "Responsory"
        n, place = int(m.group(2)), m.group(3)
        return Slot("ALT", float(place) * 100 + n + (0.5 if resp else 0),
                    "%s %s (%s. loco)" % ("Responsorium" if resp else "Lectio", roman(n), place),
                    "%s %s (in place %s)" % ("Responsory" if resp else "Lesson", roman(n), place))

    if k == "Oratio":
        return Slot("V1", 6, "Oratio", "Prayer")  # moved to Lauds when there are no First Vespers
    if k == "Oratio 2":
        return Slot("L", 6, "Oratio", "Prayer")
    if k == "Oratio 3":
        return Slot("V2", 6, "Oratio", "Prayer")

    if k.startswith("Commemoratio"):
        return Slot("X", 1, "Commemoratio", "Commemoration")
    m = re.fullmatch(r"Octava ([123])", k)
    if m:  # the commemoration of an octave at the hour (1, 2, 3: I Vespers, Lauds, II Vespers)
        n = int(m.group(1))
        at_la = ("in I Vesperis", "ad Laudes", "in II Vesperis")[n - 1]
        at_en = ("at First Vespers", "at Lauds", "at Second Vespers")[n - 1]
        return Slot("X", 2 + n / 10, "Commemoratio Octavæ (%s)" % at_la,
                    "Commemoration of the Octave (%s)" % at_en)
    if k in ("Oratio alia", "Oratio altera", "Alia oratio"):
        return Slot("Z", 1, "Alia oratio", "Another prayer")

    return Slot("Z", 50, k, k)


def family_of(version):
    if re.search(r"Cisterc", version, re.I):
        return "cist"
    if re.search(r"^Monastic", version, re.I):
        return "monastic"
    if re.search(r"Praedicatorum", version, re.I):
        return "op"
    return "roman"


def book_title(version):
    return {
        "monastic": "Breviarium Monasticum",
        "cist": "Breviarium Cisterciense",
        "op": "Breviarium Ordinis Prædicatorum",
    }.get(family_of(version), "Breviarium Romanum")


# --------------------------------------------------------------------------
# the book model -- shaped like dooffice's, so dopdf's writer can typeset it


@dataclass
class Office:
    part: Part
    key: str
    title: str  # in the first language
    title2: str  # in the second
    left: str = ""  # contents label: "29 Sep" in the Proper of the Saints
    note: str = ""  # a line under the title
    day: str = ""  # "MM-DD" a saint's office is kept on (a Sunday feast: the last it can take)
    hours: list = field(default_factory=list)  # dooffice.Hour per group
    part_start: bool = False
    rows: list = None  # a month of the Calendar: (day, Roman date, letter, title, title2, rank)
    untitled: bool = False  # printed under its part's heading alone (the prayers)
    include: tuple = None  # a custom part's PDF: (its file, its number of pages)


MONTHS_LA = ("Januarii", "Februarii", "Martii", "Aprilis", "Maii", "Junii", "Julii", "Augusti",
             "Septembris", "Octobris", "Novembris", "Decembris")
DAYS_LA = ("Dominica", "Feria II", "Feria III", "Feria IV", "Feria V", "Feria VI", "Sabbato")
DAYS_EN = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def month_file_title(key):
    """081-0 -> the Matins readings of the first week of August, Sunday."""
    m = re.fullmatch(r"(\d\d)(\d)-(\d)", key)
    if not m:
        return key, key
    month, week, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    la = "Hebdomada %s %s — %s" % (roman(week), MONTHS_LA[month - 1], DAYS_LA[day])
    en = "Week %d of %s — %s" % (week, dopdf.MONTHS[month - 1], DAYS_EN[day])
    return la, en


PSALMI_DE_FERIA = ("Psalmi de Feria currenti.", "The psalms of the current weekday.")


def _lines(html_l1, html_l2, two, english2=False):
    """Parse a section's resolved HTML with dooffice's parser.

    english2: the second column is English (its rubrics are then in English).
    """
    # {:S-AlmaRedemptoris:}, {::} -- the chant edition's markers, not text.
    html_l1, html_l2 = (re.sub(r"\{:[^{}]*:\}", "", h or "") for h in (html_l1, html_l2))
    # A line break the data left out between two antiphons: "...;;23Christus".
    html_l1, html_l2 = (re.sub(r"(;;\d+(?:;\d+)*)(?=[A-ZÆŒÁÉÍÓÚ])", r"\1<br/>", h)
                        for h in (html_l1, html_l2))
    cells = "<TD>%s</TD>" % html_l1
    if two:
        cells += "<TD>%s</TD>" % html_l2
    day = dooffice.parse("<H2 ID='Xtop'>X</H2><TABLE><TR>%s</TR></TABLE>" % cells)
    if not day.hours or not day.hours[0].sections:
        return None
    section = day.hours[0].sections[0]
    for ci, col in enumerate(section.columns):
        out, bare = [], False
        for line in col:
            text = line.text.strip()
            was_bare, bare = bare, text == ";;"
            # "_" is the data files' stanza / paragraph break, which the
            # website turns into a blank line further down its display chain
            # than resolve_refs().
            if text == "_":
                out.append(dooffice.Line())
                continue
            # A bare ";;" in the antiphons of Matins: the next psalm, from the
            # Psalter's day of the week, under the antiphon before -- the
            # engine's "Antiphonas per Octavam cum Psalmis de Feria". One
            # rubric for a run of them.
            if bare:
                if not was_bare:
                    out.append(_rubric_line(PSALMI_DE_FERIA[1 if ci == 1 and english2 else 0]))
                continue
            # An antiphon ending ";;8" names the psalm it goes with: print it
            # the way a breviary does, as a small red "Ps. 8".
            last = line.runs[-1] if line.runs else None
            if last and last.text.count(";;") > 1:
                # "...;;112;;23": the engine takes the first as the psalm.
                head, ps, _rest = last.text.split(";;", 2)
                if re.fullmatch(r"\s*[\d;,()\s-]+", ps):
                    last.text = head + ";;" + ps
            m = last and _PSALM_REF.search(last.text)
            if m:
                tail = m.group("tail")  # "(Alleluia.)" added in Paschaltide
                nums = ", ".join(re.sub(r"\(", " (", n) for n in re.split(r"[;,]\s*", m.group("ps")) if n)
                # ";;33" alone: one more psalm under the antiphon before
                # (Eastertide, one antiphon to a Nocturn) -- "Ps. 18, 33, 44".
                prev = out[-1] if out else None
                if text.startswith(";;") and not tail and prev is not None and prev.runs \
                        and not (prev.is_blank or prev.is_heading or prev.is_rubric) \
                        and not prev.text.startswith(("℣", "℟", "V.", "R.")):
                    ps = prev.runs[-1]
                    if ps.size == "small" and ps.text.startswith(" Ps. "):
                        ps.text += ", " + nums
                    else:
                        prev.runs.append(dooffice.Run(text=" Ps. " + nums, red=True, size="small"))
                    continue
                last.text = last.text[: m.start()].rstrip() + (" " + tail if tail else "")
                line.runs.append(dooffice.Run(text=" Ps. " + nums, red=True, size="small"))
            elif last and re.search(r";;\s*$", last.text):
                last.text = re.sub(r"\s*;;\s*$", "", last.text)  # an antiphon without its psalm's number
            out.append(line)
        col[:] = out
    if not any(not line.is_blank for col in section.columns for line in col):
        return None  # nothing left (a reference to nowhere, taken out)
    return section


# ";;8", ";;44.", ";;46;", ";;88(2-19)", ";;109;110", ";;14 (Alleluia.)"; and the
# data's slips ";;138_", ";;249;250;251/"
_PSALM_REF = re.compile(
    r";;\s*(?P<ps>\d+(?:\(\d+-\d+\))?(?:\s*[;,]\s*\d+(?:\(\d+-\d+\))?)*)[.;]?[/_]?\s*"
    r"(?P<tail>\((?:Allel|allel)[^)]*\))?\s*$")

# The engine's own words for a reference that leads nowhere in its data:
# "Sancti/07-02:Capitulum Sexta is missing!" -- the website prints them too.
_MISSING = re.compile(r"\b(?P<file>[A-Z]\w*(?:/[\w.-]+)+):(?P<sec>[^:<\n]*?)(?P<ps>;;[\d;]+)? is missing!")
# ... and a whole line that is only such a note, without the file ("Oratio mortuorum2 is
# missing", in the special hours of All Souls).
_MISSING_LINE = re.compile(r"(?m)(?:^|(?<=>))[ \t]*[A-Z][A-Za-z0-9 ]{0,40} is missing!?[ \t]*(?=<|\n|$)")


def _repair(html, sections, lang):
    """A section's text without the engine's "... is missing!" notes.

    One kind is mended: "@Commune/C1:Ant Vespera:1 s/$/;;109;112/" asks for the
    first antiphon of a section with other psalms, and the data's section is
    itself a reference, which the engine resolves only after the line is cut
    (Dominican, Epiphany and the Apostles: "Commune/C1:Ant Vespera;;109;112 is
    missing!"). The office's own section of that name gives the antiphon.
    """
    if not html or "is missing" not in html:
        return html

    def mend(m):
        src = sections.get(m.group("sec").strip())
        if m.group("ps") and src and src.get(lang) and "is missing!" not in src[lang]:
            first = re.split(r"<br\s*/?>", src[lang])[0].strip()
            return re.sub(r";;.*$", "", first) + m.group("ps")
        return ""

    return _MISSING_LINE.sub("", _MISSING.sub(mend, html))


# The Commons by the website's names for them (horas.dialog [communes]), the
# Mass a variant goes with, and an English name. The files' own [Officium] is
# no guide: a variant that takes its parent whole takes its name too (the
# monastic Evangelists were "Commune Apostolorum"), so the book printed
# several Commons under one name.
COMMONS = {
    "C1": ("Commune Apostolorum", "Common of Apostles", None),
    "C1a": ("Commune Evangelistarum", "Common of Evangelists", None),
    "C1p": ("Commune Apostolorum tempore Paschali", "Common of Apostles in Paschaltide", None),
    "C2": ("Commune Unius Martyris Pontificis", "Common of One Martyr Bishop", "Statuit"),
    "C2-1": ("Commune Unius Martyris Pontificis", "Common of One Martyr Bishop", "Sacerdotes Dei"),
    "C2a": ("Commune Unius Martyris non Pontificis", "Common of One Martyr not a Bishop", "In virtute"),
    "C2a-1": ("Commune Unius Martyris non Pontificis", "Common of One Martyr not a Bishop", "Lætabitur"),
    "C2b": ("Commune Unius Martyris Summi Pontificis", "Common of One Martyr Pope", "Si diligis"),
    "C2b-1": ("Commune Unius Martyris Summi Pontificis", "Common of One Martyr Pope", "Si diligis"),
    "C2p": ("Commune Unius Martyris tempore Paschali", "Common of One Martyr in Paschaltide", "Protexisti"),
    "C3": ("Commune Plurium Martyrum Pontificum", "Common of Several Martyr Bishops", "Intret"),
    "C3a": ("Commune Plurium Martyrum non Pontificum", "Common of Several Martyrs not Bishops", "Sapientiam"),
    "C3a-1": ("Commune Plurium Martyrum non Pontificum", "Common of Several Martyrs not Bishops",
              "Salus autem"),
    "C3b": ("Commune Plurium Martyrum Summorum Pontificum", "Common of Several Martyr Popes", "Si diligis"),
    "C3p": ("Commune Plurium Martyrum tempore Paschali", "Common of Several Martyrs in Paschaltide",
            "Sancti tui"),
    "C4": ("Commune Confessoris Pontificis", "Common of a Confessor Bishop", "Statuit"),
    "C4-1": ("Commune Confessoris Pontificis", "Common of a Confessor Bishop", "Sacerdotes Dei"),
    "C4a": ("Commune Doctorum Pontificum", "Common of Doctors who were Bishops", "In medio"),
    "C4b": ("Commune Confessorum Summorum Pontificum", "Common of Confessor Popes", "Si diligis"),
    "C4b-1": ("Commune Confessorum Summorum Pontificum", "Common of Confessor Popes", "Si diligis"),
    "C4b-2": ("Commune Confessorum Summorum Pontificum", "Common of Confessor Popes", "Si diligis"),
    "C4c": ("Commune Confessorum Pontificum", "Common of Several Confessor Bishops", "Statuit"),
    "C5": ("Commune Confessoris non Pontificis", "Common of a Confessor not a Bishop", "Os justi"),
    "C5-1": ("Commune Confessoris non Pontificis", "Common of a Confessor not a Bishop", "Justus"),
    "C5a": ("Commune Doctoris non Pontificis", "Common of a Doctor not a Bishop", "In medio"),
    "C5b": ("Commune Abbatum", "Common of Abbots", "Os justi"),
    "C5c": ("Commune Confessorum non Pontificum", "Common of Several Confessors not Bishops", "Os justi"),
    "C6": ("Commune Unius Virginis Martyris", "Common of a Virgin Martyr", "Loquebar"),
    "C6-1": ("Commune Unius Virginis Martyris", "Common of a Virgin Martyr", "Me exspectaverunt"),
    "C6a": ("Commune Unius Virginis tantum", "Common of a Virgin not a Martyr", "Dilexisti"),
    "C6a-1": ("Commune Unius Virginis tantum", "Common of a Virgin not a Martyr", "Vultum tuum"),
    "C6b": ("Commune Plurium Virginum Martyrum", "Common of Several Virgin Martyrs", "Me exspectaverunt"),
    "C7": ("Commune Unius non Virginis Martyris", "Common of a Martyr not a Virgin", "Me exspectaverunt"),
    "C7a": ("Commune Unius non Virginis nec Martyris", "Common of a Holy Woman neither Virgin nor Martyr",
            "Cognovi"),
    "C7b": ("Commune Plurium non Virginum Martyrum", "Common of Several Martyrs not Virgins",
            "Me exspectaverunt"),
    "C8": ("Commune Dedicationis Ecclesiæ", "Common of the Dedication of a Church", None),
    "C11": ("Commune Beatæ Mariæ Virginis", "Common of the Blessed Virgin Mary", None),
}
# Our Lady on Saturday, by season (the Roman files' [Missa]); C10n is the
# monastic form from Christmas to the Purification.
C10_SEASONS = {
    "C10": ("a Trinitate usque ad Adventum", "from Trinity Sunday to Advent"),
    "C10a": ("in Adventu", "in Advent"),
    "C10b": ("a Nativitate usque ad Purificationem", "from Christmas to the Purification"),
    "C10n": ("a Nativitate usque ad Purificationem", "from Christmas to the Purification"),
    "C10c": ("a Purificatione usque ad Dominicam Palmarum", "from the Purification to Palm Sunday"),
    "C10Pasc": ("tempore Paschali", "in Paschaltide"),
}


def _common_name(key):
    """(Latin, English, (Latin, English) to tell it from another) of a Common, or None."""
    if key in C10_SEASONS:
        s = C10_SEASONS[key]
        return "Beata Maria in Sabbato", "Our Lady on Saturday", ("(%s)" % s[0], "(%s)" % s[1])
    if key in COMMONS:
        la, en, mass = COMMONS[key]
    elif key.endswith("p") and key[:-1] in COMMONS:  # C2ap: C2a in Paschaltide
        la, en, mass = COMMONS[key[:-1]]
        la, en = la + " tempore Paschali", en + " in Paschaltide"
    else:
        return None
    return la, en, ("(Missa %s)" % mass, "(Mass %s)" % mass) if mass else None


def commune_names(offices):
    """{file: (Latin, English)}: the Commons of a book by their names, each
    with its Mass (Our Lady on Saturday: its season) where two would read
    alike, and numbered where even that does not tell them apart."""
    named = {o["file"]: _common_name(o["key"]) for o in offices}
    named = {f: n for f, n in named.items() if n}
    out, groups = {}, collections.defaultdict(list)
    for f, (la, en, extra) in named.items():
        groups[la].append(f)
    for la, files in groups.items():
        for f in files:
            _la, en, extra = named[f]
            out[f] = ("%s %s" % (la, extra[0]), "%s %s" % (en, extra[1])) \
                if len(files) > 1 and extra else (la, en)
    seen = collections.Counter()
    for f in list(out):
        same = [g for g in out if out[g] == out[f]]
        if len(same) > 1:
            seen[out[f]] += 1
            n = roman(seen[out[f]])
            if n != "I":
                out[f] = ("%s %s" % (out[f][0], n), "%s %s" % (out[f][1], n))
    return out


def _number_antiphons(section):
    """Number the antiphons of Lauds and Vespers 1, 2, 3 ... -- one to each psalm."""
    for col in section.columns:
        antiphons = [l for l in col if not (l.is_blank or l.is_heading or l.is_rubric)]
        if len(antiphons) < 2:
            continue  # one antiphon over all the psalms
        for n, line in enumerate(antiphons, 1):
            line.runs.insert(0, dooffice.Run(text="%d. " % n, red=True))


# The antiphons of Matins by Nocturn, as the engine divides them
# (specmatins.pl getantmatutinum): three psalms to a Roman Nocturn, six to a
# monastic one, whose third Nocturn sings three canticles under one antiphon.
# A versicle closes each Nocturn -- inside the section in some offices, else
# printed after it ("Versus Nocturni II").
NOCTURN_EN = ("first", "second", "third")
_NOCTURN_LABEL = re.compile(r"In (I{1,3}) Nocturno:|In the (first|second|third) Nocturn:")
_VR_LINE = ("℣", "℟", "V.", "R.")


def _is_nocturn_label(text):
    return _NOCTURN_LABEL.fullmatch((text or "").strip()) is not None


def _nocturn_label(n, english):
    return _rubric_line("In the %s Nocturn:" % NOCTURN_EN[n - 1] if english else "In %s Nocturno:" % roman(n))


def _matins_place(index, family):
    """(Nocturn, antiphon) of a line of the engine's Matins psalms: its
    "Ant Matutinum 12 special" replaces line 12 of 15 (Roman: 3 antiphons, ℣,
    ℟ a Nocturn) or of 19 (monastic: 6, 6 and the canticles)."""
    if family in ("monastic", "cist"):
        return (1, index + 1) if index < 8 else (2, index - 7) if index < 16 else (3, 1)
    return index // 5 + 1, index % 5 + 1


def _nocturn_chunks(html, family):
    """A Matins antiphon section's lines (the data's, <br/> apart) Nocturn by
    Nocturn, or None where they are not two or three Nocturns."""
    lines = [l for l in re.split(r"<br\s*/?>", html or "") if l.strip()]
    vr = [re.sub(r"<[^>]*>", "", l).strip().startswith(_VR_LINE) for l in lines]
    per = 6 if family in ("monastic", "cist") else 3
    if any(vr):  # each Nocturn ends at its ℣. ℟.
        chunks, cur = [], []
        for i in range(len(lines)):
            cur.append(i)
            if vr[i] and (i + 1 == len(lines) or not vr[i + 1]):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        if any(all(vr[i] for i in c) for c in chunks):
            return None  # a versicle with no psalms before it
        chunks = [[lines[i] for i in c] for c in chunks]
    elif len(lines) == 3 * per == 9 or len(lines) == 2 * per == 12:
        chunks = [lines[i:i + per] for i in range(0, len(lines), per)]
    elif per == 6 and len(lines) == 13 and re.search(r";;\s*\d", lines[12]):
        chunks = [lines[:6], lines[6:12], lines[12:]]  # the canticles' antiphon last
    else:
        return None
    return chunks if 2 <= len(chunks) <= 3 else None


def _number_matins(column):
    """Number the antiphons of a column (not its ℣. ℟.), where there are two or more."""
    antiphons = [l for l in column if not (l.is_blank or l.is_heading or l.is_rubric)
                 and not l.text.strip().startswith(_VR_LINE)]
    if len(antiphons) >= 2 and not any(l.is_rubric for l in column):
        for n, line in enumerate(antiphons, 1):
            line.runs.insert(0, dooffice.Run(text="%d. " % n, red=True))


def _third_nocturn(chunk, html_3n):
    """Nocturn III's first line with the office's own antiphon of it (monastic.pl:
    [Ant Matutinum 3N] over the canticles the section names), or None."""
    own = [l for l in re.split(r"<br\s*/?>", html_3n or "") if l.strip()]
    if len(own) != 1 or not chunk:
        return None
    ant, _sep, ps = own[0].strip().partition(";;")
    _old, _sep, old_ps = chunk[0].strip().partition(";;")
    return [ant + ";;" + (ps or old_ps)] + chunk[1:]


def _matins_antiphons(s, by_key, third, family, two, lang2_is_english, lang1_is_english):
    """The antiphons of Matins as printed: under each Nocturn's heading
    ("In I Nocturno:"), numbered in it; else numbered through. third: the
    office's [Ant Matutinum 3N] (or its source's), sung over the canticles:
    (section, whether it was used)."""
    html = [_repair(s["l1"], by_key, "l1"), _repair(s["l2"], by_key, "l2")]

    def through():  # one list, numbered through
        sec = _lines(html[0], html[1], two, lang2_is_english)
        if sec is not None:
            for col in sec.columns:
                _number_matins(col)
        return sec, False

    chunks = [_nocturn_chunks(h, family) for h in (html if two else html[:1])]
    if chunks[0] is None or any(c is None or len(c) != len(chunks[0]) for c in chunks):
        return through()
    took = False
    if third and len(chunks[0]) == 3 and family in ("monastic", "cist"):
        new = [_third_nocturn(c[2], third.get(lang)) for c, lang in zip(chunks, ("l1", "l2"))]
        if all(n is not None for n in new):
            for c, n in zip(chunks, new):
                c[2] = n
            took = True
    columns = [[] for _c in chunks]
    english = (lang1_is_english, lang2_is_english)
    for k in range(len(chunks[0])):
        part = _lines(*["<br/>\n".join(c[k]) + "<br/>\n" for c in chunks], two, lang2_is_english) \
            if two else _lines("<br/>\n".join(chunks[0][k]) + "<br/>\n", "", False)
        if part is None:
            return through()
        for c, col in enumerate(part.columns):
            _number_matins(col)
            columns[c] += [_nocturn_label(k + 1, english[c])] + col
    return dooffice.Section(columns=columns), took


def _heading_line(text):
    return dooffice.Line(runs=[dooffice.Run(text=text, size="heading")])


def _rubric_line(text):
    return dooffice.Line(runs=[dooffice.Run(text=text, red=True, italic=True)])


_EX = re.compile(r"^\s*(ex|vide)\s+(\S+)", re.I | re.M)


def _source_of(office):
    """The file an office takes "the rest" from: ([Rule] ex Sancti/05-08) -> 'Sancti/05-08'."""
    m = _EX.search(office.get("rule") or "")
    return m.group(2).rstrip(";") if m else None


def _norm(path):
    return re.sub(r"^(Sancti|Tempora|Commune)(?:M|OP|Cist)?/", r"\1/", path or "")


# The Sunday Masses (engine_dump.pl --part missa): each Sunday's proper in
# the order of the Mass. The Collect is the office's own, printed with the
# Sunday in the Proper of the Season: a rubric says so where it is said.
MISSA_HEADINGS = (("Introitus", "Introitus", "Introit"), ("Lectio", "Epistola", "Epistle"),
                  ("Graduale", "Graduale", "Gradual"), ("Sequentia", "Sequentia", "Sequence"),
                  ("Evangelium", "Evangelium", "Gospel"), ("Offertorium", "Offertorium", "Offertory"),
                  ("Secreta", "Secreta", "Secret"), ("Communio", "Communio", "Communion"),
                  ("Postcommunio", "Postcommunio", "Postcommunion"))
MISSA_HOUR = ("Ad Missam", "At Mass")
MISSA_COLLECT = ("Oratio, ut in Officio.", "Collect, as in the Office.")


def missa_offices(part, data, two, lang2_is_english, lang1_is_english=False):
    """The Sunday Masses, one office each: Introit, Collect (a rubric),
    Epistle, Gradual (the Tract in Lent), Sequence, Gospel, Offertory,
    Secret, Communion, Postcommunion."""
    offices = []
    for o in data.get("offices", []):
        by_key = {s["key"]: s for s in o.get("sections", [])}
        hour = dooffice.Hour(key=MISSA_HOUR[1] if lang2_is_english else MISSA_HOUR[0],
                             heading=MISSA_HOUR[0])
        for key, la, en in MISSA_HEADINGS:
            s = by_key.get(key)
            sec = _lines(s["l1"], s["l2"], two, lang2_is_english) if s else None
            if sec is None:
                continue
            sec.columns[0].insert(0, _heading_line(en if lang1_is_english else la))
            if two and len(sec.columns) > 1:
                sec.columns[1].insert(0, _heading_line(en if lang2_is_english else la))
            hour.sections.append(sec)
            if key == "Introitus":
                cols = [[_rubric_line(MISSA_COLLECT[1] if lang1_is_english else MISSA_COLLECT[0])]]
                if two:
                    cols.append([_rubric_line(MISSA_COLLECT[1] if lang2_is_english else MISSA_COLLECT[0])])
                hour.sections.append(dooffice.Section(columns=cols))
        if not hour.sections:
            continue
        t1, t2 = (o.get("title") or [o["key"], o["key"]])[:2]
        # A feast kept on a Sunday (Christ the King) goes with the last day it can take.
        offices.append(Office(part=part, key=o["key"], title=t1, title2=t2 or t1,
                              day=(o.get("days") or [""])[-1], hours=[hour]))
    return offices


def build_offices(part, data, titles, two, lang2_is_english, sources=None, in_book=(),
                  alternatives=True, lang1_is_english=False):
    """Turn one part's dump into Offices with their sections placed and headed.

    sources: file -> dumped office, for every file an office borrows from.
    in_book: the files printed somewhere in this book. A source that is in
    the book is referred to ("Cetera ut in ..."); one that is not -- a feast
    dropped from this version's calendar -- is merged in, so nothing the
    office needs is missing.
    alternatives: False leaves out the alternative lessons and responsories
    (the Common's second and third sets, the "(alia)" forms).
    """
    fam = family_of(data.get("version", ""))
    vname = data.get("engine_version") or data.get("version", "")
    sources = sources or {}
    in_book = {_norm(f) for f in in_book}
    commons = {_norm(x["file"]): x for x in data["offices"]} if part.key == "commune" else {}
    offices = []
    for o in data["offices"]:
        key = o["key"]
        t1, t2 = (o.get("title") or ["", ""])[:2]
        if part.key == "tempora" and re.fullmatch(r"\d{3}-\d", key):
            la, en = month_file_title(key)
            t1, t2 = (en if lang1_is_english else la), (en if lang2_is_english else la)
        if not t1:
            t1 = t2 = key
        sections = list(o.get("sections", []))
        src = _source_of(o)
        seen = {s["key"] for s in sections}
        hops = 0
        while src and _norm(src).split("/")[0] in ("Sancti", "Tempora") \
                and _norm(src) not in in_book and hops < 3:
            donor = sources.get(_norm(src))
            if not donor:
                break
            for s in donor.get("sections", []):
                if s["key"] not in seen:
                    sections.append(s)
                    seen.add(s["key"])
            src = _source_of(donor)
            hops += 1
        # A Common that takes another whole (CommuneM/C2-1 is CommuneM/C2 with
        # the prayer and third Nocturn of its own Mass): what is its own, and
        # the rest by reference -- as a breviary gives a Common's other prayer
        # and lessons, not the whole Common again.
        base = None
        by_key = {s["key"]: s for s in sections}  # all of them, for what one names of another
        if o.get("parent") and _norm(o["parent"]) != _norm(o.get("file")) \
                and _norm(o["parent"]) in commons:
            base = o["parent"]
            theirs = {s["key"]: (s.get("l1"), s.get("l2")) for s in commons[_norm(base)].get("sections", [])}
            sections = [s for s in sections if theirs.get(s["key"]) != (s.get("l1"), s.get("l2"))]
        placed = []
        for idx, s in enumerate(sections):
            slot = classify(s["key"], fam, vname)
            if slot is None:
                continue
            if not alternatives and (slot.group == "ALT" or "(alia)" in slot.la):
                continue
            m = re.fullmatch(r"Ant Matutinum (1[12])", s["key"])
            if m and not re.search(r"Ant Matutinum %s special" % m.group(1), o.get("rule") or "", re.I):
                continue  # the Roman file's, which this office's rule does not read
            placed.append((slot, idx, s))

        # The monastic short responsories (monastic_major_responsory() and the
        # little hours'): Lauds and Vespers take the office's own, else the Roman
        # one of Terce (at Lauds) and of Sext (at Vespers); the little hours take
        # Responsory TertiaM ... or the Nocturns' versicles, never the Roman ones.
        if fam == "monastic":
            keys = {s["key"] for _sl, _i, s in placed}
            kept = []
            for sl, i, s in placed:
                k = s["key"]
                if k in ("Responsory Breve Nona", "Versum Tertia", "Versum Sexta", "Versum Nona"):
                    continue
                if k == "Responsory Breve Tertia":
                    if "Responsory Laudes" in keys:
                        continue
                    sl = Slot("L", 3, "Responsorium breve", "Short responsory")
                elif k == "Responsory Breve Sexta":
                    if "Responsory Vespera" in keys:
                        continue
                    sl = Slot("V1", 3, "Responsorium breve", "Short responsory")
                kept.append((sl, i, s))
            placed = kept

        # One form of each hymn: the old text (before the revision of 1632)
        # where the engine uses it -- its tryoldhymn(): the Monastic and
        # Cistercian books, Tridentine 1570 and the Dominican -- else the Roman.
        monastic = bool(re.search(r"Monastic|1570|Praedicatorum",
                                  data.get("engine_version") or data.get("version", ""), re.I))
        has_m = {sl.slot for sl, _, _ in placed if sl.hymn == "M"}
        placed = [(sl, i, s) for sl, i, s in placed
                  if not sl.slot or (sl.hymn == "M" and monastic)
                  or (sl.hymn == "" and not (monastic and sl.slot in has_m))]

        # A Tridentine book's own responsory ([ResponsoryT2]) in place of the other.
        own = {"Responsory" + s["key"][len("ResponsoryT"):] for _sl, _i, s in placed
               if s["key"].startswith("ResponsoryT")}
        placed = [(sl, i, s) for sl, i, s in placed if s["key"] not in own]

        # The prayer is printed with First Vespers; without them, at Lauds.
        if not any(sl.group == "V1" and not sl.la.startswith("Oratio") for sl, _, _ in placed):
            for sl, _, _ in placed:
                if sl.group == "V1" and sl.la.startswith("Oratio"):
                    sl.group = "L"  # its order (6, 6.1) is the same there

        hours = {}
        used = set()  # sections printed inside another
        for sl, idx, s in sorted(placed, key=lambda p: (GROUPS[p[0].group][0], p[0].order, p[1])):
            if s["key"] in used:
                continue
            if s["key"] == "Ant Matutinum":
                # The third Nocturn's own antiphon: the office's, else that of
                # the office it takes the rest from (monastic.pl: %commune).
                third = by_key.get("Ant Matutinum 3N")
                if third is None:
                    donor = sources.get(_norm(_source_of(o) or "")) or {}
                    third = next((x for x in donor.get("sections", []) if x["key"] == "Ant Matutinum 3N"),
                                 None)
                sec, took = _matins_antiphons(s, by_key, third, fam, two, lang2_is_english,
                                              lang1_is_english)
                if took:
                    used.add("Ant Matutinum 3N")
            else:
                sec = _lines(_repair(s["l1"], by_key, "l1"), _repair(s["l2"], by_key, "l2"), two,
                             lang2_is_english)
            if sec is None:
                continue
            if sl.la == "Antiphonæ" and sl.group in ("L", "V1", "V2"):
                _number_antiphons(sec)
            en_label = sl.en if lang2_is_english else sl.la
            if sl.la:  # a rubric alone ("Te Deum, ut in Ordinario.") has no heading
                sec.columns[0].insert(0, _heading_line(sl.en if lang1_is_english else sl.la))
                if two and len(sec.columns) > 1:
                    sec.columns[1].insert(0, _heading_line(en_label))
            g = hours.get(sl.group)
            if g is None:
                order, la, en = GROUPS[sl.group]
                g = hours[sl.group] = dooffice.Hour(key=en if lang2_is_english else la, heading=la)
            g.sections.append(sec)

        note = ""
        target = base or _source_of(o)
        if target and _norm(target).split("/")[-1] == key:
            target = None  # "vide C10" in C10 itself
        if target and (_norm(target) in in_book or not _norm(target).startswith(("Sancti/", "Tempora/"))):
            name = titles.get(_norm(target)) or titles.get(re.sub(r"^\w+/", "", target))
            if name:
                note = ("The rest as in: %s" if lang1_is_english else "Cetera ut in: %s") % name[0]
                if two:
                    note += (" · The rest as in: %s" if lang2_is_english else " · Cetera ut in: %s") % name[1]

        if not hours and not note:
            continue  # nothing of its own: a feria that repeats its Sunday
        if not hours and note:
            h = dooffice.Hour(key="", heading="")
            h.sections.append(dooffice.Section(columns=[[_rubric_line(note)]]))
            hours["Z"] = h
            note = ""

        left = day = ""
        if part.key == "sancti" and o.get("date"):
            day = o["date"]
            mm, dd = o["date"].split("-")
            left = "%d %s" % (int(dd), dopdf.MONTHS[int(mm) - 1][:3])
        elif part.key == "sancti" and o.get("days"):  # a feast kept on a Sunday
            day = o["days"][-1]
            left = _day_span(o["days"][0], o["days"][-1])
        sub = t2 if two and t2 != t1 else ""
        if o.get("commemoratio"):
            sub = (("Commemoration" if lang1_is_english else "Commemoratio") + (" · " + sub if sub else ""))
        if note:
            sub = (sub + " — " if sub else "") + note
        offices.append(Office(part=part, key=key, title=t1, title2=t2, left=left, note=sub, day=day,
                              hours=[hours[g] for g in sorted(hours, key=lambda g: GROUPS[g][0])]))
    if offices:
        offices[0].part_start = True
    return offices


def _day_span(first, last):
    """"25–31 Oct", "30 Sep–6 Oct": the days a feast kept on a Sunday can take."""
    (m1, d1), (m2, d2) = ((int(x) for x in d.split("-")) for d in (first, last))
    if m1 == m2:
        return "%d–%d %s" % (d1, d2, dopdf.MONTHS[m2 - 1][:3])
    return "%d %s–%d %s" % (d1, dopdf.MONTHS[m1 - 1][:3], d2, dopdf.MONTHS[m2 - 1][:3])


# --------------------------------------------------------------------------
# typesetting


# Hymns in two columns of stanzas (BookWriter.hymn): a section headed as a
# hymn, of two stanzas or more and this many lines; a stanza begins after a
# blank line or at a red initial (the Common of the Season's hymns).
_HYMN = re.compile(r"(Hymnus|Alius hymnus|Hymn|Another hymn)\b")
HYMN_MIN_LINES = 6
HYMN_STANZA_GAP = "0.35em"


def _stanzas(lines):
    out, cur = [], []
    for line in lines:
        if line.is_blank:
            if cur:
                out.append(cur)
                cur = []
            continue
        first = next((r for r in line.runs if r.text.strip()), None)
        if cur and first is not None and first.red and not first.bold and len(first.text.strip()) == 1:
            out.append(cur)
            cur = []
        cur.append(line)
    if cur:
        out.append(cur)
    return out


def _hymn_span(col):
    """(start, body, end) of the hymn in a section's column: its heading line
    ("Hymnus"; a first line with the heading run in is itself the body), and
    its stanzas, up to the next heading, rubric or versicle -- or None."""
    for i, line in enumerate(col):
        if line.is_blank or not _HYMN.match(line.text.strip()):
            continue
        body = i + 1 if (line.is_heading or line.is_rubric) else i
        end = body
        while end < len(col):
            nxt = col[end]
            first = next((r for r in nxt.runs if r.text.strip()), None)
            initial = first is not None and len(first.text.strip()) == 1  # a stanza's red initial
            if not nxt.is_blank and ((nxt.is_heading or nxt.is_rubric) and not initial
                                     or nxt.text.lstrip().startswith(_VR_LINE)):
                break
            end += 1
        while end > body and col[end - 1].is_blank:
            end -= 1
        return i, body, end
    return None


def _hymn_cuts(columns):
    """Where each column's stanzas divide into two columns: as near the middle
    as stanzas allow, the first column the longer. Both languages at the same
    stanza when they have as many, so each stanza stands beside its own."""
    def best(sizes_list):
        n = len(sizes_list[0])
        return min(range(1, n), key=lambda k: (max(max(sum(s[:k]), sum(s[k:])) for s in sizes_list), -k))

    sizes = [[len(st) for st in col] for col in columns]
    if len({len(s) for s in sizes}) == 1:
        return [best(sizes)] * len(sizes)
    return [best([s]) for s in sizes]


class BookWriter(dopdf.Writer):
    """dopdf's writer, arranged as a book: part > office > hour > section."""

    def page_furniture(self, small):
        """Letter and A4: as little margin above and below as print-on-demand
        allows -- they keep everything 0.5 in from the trimmed edge, running head
        and page number included. So the page number goes into the running head,
        at its outer corner (right on odd pages, left on even), and the text runs
        to 0.5 in from the foot. The page number is the book's, not the volume's,
        so a volume starting on an even page still puts it outside. Margins
        chosen in the window (Layout.margins) set any paper this way."""
        margins = self.layout.margins
        if small and not margins:
            return super().page_furniture(small)
        top, bottom, inside, outside = margins or dopdf.STANDARD_MARGINS["A4"]
        inch = lambda v: ("%.3f" % v).rstrip("0").rstrip(".") + "in"
        return [
            # The head sits between the top margin and the text: 1.9em of band
            # above it. The bottom margin is to the text's descenders.
            "  margin: (inside: %s, outside: %s, top: %s + 1.9em, bottom: %s + 0.4em),"
            % (inch(inside), inch(outside), inch(top), inch(bottom)),
            "  header-ascent: 0.8em,",
            "  header: context {"] + dopdf.RUNNING_HEAD_FIND + [
            "    if d != none and not quiet {",
            '      set text(size: 0.78em, fill: luma(90), hyphenate: false)',
            # A volume's first page: its head comes before the counter is set
            # to the volume's first number, which <firstpage> holds.
            "      let fp = query(<firstpage>)",
            "      let n = if pg == 1 and fp.len() > 0 { fp.first().value } else { counter(page).get().first() }",
            '      let title = text(style: "italic", d.value.d + "  ·  " + d.value.t)',
            '      let hour = if h != none { h.value } else { "" }',
            "      let num = text(fill: luma(40), str(n))",
            "      if calc.odd(n) {",
            "        grid(columns: (1fr, auto, auto), column-gutter: 1em,",
            "          align: (left, right, right), title, hour, num)",
            "      } else {",
            "        grid(columns: (auto, 1fr, auto), column-gutter: 1em,",
            "          align: (left, left, right), num, title, hour)",
            "      }",
            "      v(-0.45em)",
            "      line(length: 100%, stroke: 0.4pt + luma(170))",
            "    }",
            "  },",
            "  footer: none,",
        ]

    def heading_rules(self):
        return [
            # A part: its own page, its title large and centred.
            "#show heading.where(level: 1): it => {",
            "  v(28%)",
            "  align(center, block(width: 100%, text(fill: rub, size: 2.1em, weight: \"regular\", it.body)))",
            "}",
            # An office (a feast, a Sunday, a Common): red, bold, centred --
            # a little smaller in a column half the page wide.
            "#show heading.where(level: 2): it => block(width: 100%, sticky: true, above: 1.1em, below: 0.3em,",
            "  align(center, text(fill: rub, size: %s, weight: \"bold\", hyphenate: false, it.body)))"
            % ("1.12em" if self.two_columns() else "1.25em"),
            # A hymn in two columns of stanzas (BookWriter.hymn): set so where
            # every line (all but one in ten) fits half its language's column,
            # measured at the book's own type; else as before. Kept on one
            # page, so a column is never read across a page turn.
            "#let hy(a, b) = grid(columns: (1fr, 1fr), column-gutter: 1.2em, a, b)",
            "#let hymnfit(two, lines, split, plain) = layout(size => {",
            "  let col = if two { (size.width - 1.1em.to-absolute()) / 2 } else { size.width }",
            "  let half = (col - 1.2em.to-absolute()) / 2",
            "  let over = lines.filter(l => measure(l).width > half).len()",
            "  if over <= calc.max(1, calc.floor(lines.len() / 10)) {",
            "    block(breakable: false, width: 100%, split)",
            "  } else { plain }",
            "})",
            # The hour within it.
            "#let grouphead(body) = block(width: 100%, sticky: true, above: 0.6em, below: 0.3em,",
            "  align(center, text(fill: rub, size: 1.08em, style: \"italic\", body)))",
            "#let officenote(body) = block(width: 100%, sticky: true, below: 0.3em,",
            "  align(center, text(size: 0.88em, style: \"italic\", body)))",
            # A short part (the prayers before and after): its heading, no title page.
            "#let smallpart(la, en) = {",
            "  show heading.where(level: 1): it => block(width: 100%, sticky: true, above: 2.4em,",
            "    below: 0.45em, align(center, text(fill: rub, size: 1.45em, weight: \"regular\",",
            "    hyphenate: false, it.body)))",
            "  heading(level: 1, la)",
            "  if en != \"\" { align(center, text(size: 1.02em, style: \"italic\", en)) }",
            "  v(0.8em)",
            "}",
            # A month of the Calendar.
            "#let caltable(two, ..cells) = table(",
            "  columns: if two { (1.5em, 4.2em, 1em, 1fr, 1fr, auto) } else { (1.5em, 4.2em, 1em, 1fr, auto) },",
            "  stroke: (x, y) => if y > 0 { (top: 0.3pt + luma(210)) },",
            "  inset: (x: 0.3em, y: 0.3em),",
            "  align: (x, y) => if x == 0 { right + top } else { left + top },",
            "  ..cells.pos())",
        ]

    def runs(self, line):
        out = super().runs(line)
        for mark in reversed(getattr(line, "marks", ())):
            # The first copy of a text printed again later: a hidden heading,
            # whose bookmark gives its page once the volume is typeset, and a
            # label, for references in this same volume. Inside a paragraph
            # (one language) Typst ignores a place(), so the marker and its
            # label were lost and every reference to it failed: there it is
            # an empty box instead, on the line itself.
            hidden = "hide([#heading(level: 3)[%s%s] <%s>])" % (REF_MARK, mark, mark)
            if getattr(self, "in_paragraph", False):
                out = "#box(width: 0pt, height: 0pt, %s)" % hidden + out
            else:
                out = "#place(%s)" % hidden + out
        ref = getattr(line, "ref", None)
        if ref:
            ident, col = ref[0], ref[1]
            where = ref[2] if len(ref) > 2 else ""
            lang = dooffice.family(self.meta.lang1 if col == 0 else self.meta.lang2)
            page = getattr(self.meta, "ref_pages", {}).get(ident)
            if page is not None:  # in an earlier volume
                num = str(page)
            elif ident in self.marks_here:
                num = "#context str(counter(page).at(<%s>).first())" % ident
            else:
                num = ""
            # Where to look ("Resp. IV, "), then the page. The number is in a box
            # of fixed width, last, so filling it in moves nothing.
            out += " #text(fill: rub, size: 0.86em)[%s%s~#box(width: 2.2em)[%s]]" % (
                "#" + dopdf.q(where + ", ") if where else "", "pag." if lang == "Latin" else "p.", num)
        return out

    def two_columns(self):
        """One language set in two columns (Layout.two_columns)."""
        return bool(getattr(self.layout, "two_columns", False)) and self.meta.lang1 == self.meta.lang2

    def columns_for(self, part):
        """The columns a part's text is set in: two for one language when asked,
        but the Calendar's table, as wide as the page, keeps one."""
        return 2 if self.two_columns() and part.key != "kalendarium" else 1

    def document(self, offices, start_page=1):
        self.start_page = start_page
        # A volume (or a piece of the book) may begin inside a part.
        self.cols = self.columns_for(offices[0].part) if offices else 1
        self.marks_here = {m for o in offices if o.rows is None
                           for h in o.hours for s in h.sections for c in s.columns for l in c
                           for m in getattr(l, "marks", ())}
        self.out = []
        self.styles = set()
        for i, office in enumerate(offices):
            self.office(office, i)
        head = [self.preamble()]
        head += [dopdf.style_def(k) for k in sorted(self.styles)]
        if self.two_columns():
            head.append("#set columns(gutter: 1.5em)")
            head.append("#set page(columns: %d)" % (self.columns_for(offices[0].part) if offices else 1))
        head.append("#counter(page).update(%d)" % start_page)
        head.append("#metadata(%d)<firstpage>" % start_page)  # for the first page's head
        return "\n".join(head + self.out) + "\n"

    def office(self, office, index):
        q = dopdf.q
        two = self.meta.lang2 != self.meta.lang1
        if office.include:
            # A custom part's PDF: each of its pages a page of the book, scaled
            # to it, with no running head; a hidden heading gives the part its
            # bookmark and its line in the contents.
            name, count = office.include
            for n in range(1, count + 1):
                mark = "#place(hide(heading(level: 1)[#%s]))" % q(office.part.la) \
                    if n == 1 and office.part_start else ""
                self.emit('#page(margin: 0pt, header: none, footer: none, columns: 1)[%s'
                          '#image(%s, page: %d, width: 100%%, height: 100%%, fit: "contain")]'
                          % (mark, q(name), n))
            return
        if office.part_start:
            part = office.part
            cols = self.columns_for(part)
            self.emit("#pagebreak(weak: true)")
            if part.compact:
                if cols != self.cols:  # a new page, set in the part's columns
                    self.emit("#set page(columns: %d)" % cols)
                    self.cols = cols
                self.emit("#metadata(none)<nohead>")  # its heading is the page's head
                self.emit("#smallpart(%s, %s)" % (q(part.la), q(part.en if two else "")))
            else:
                if self.cols != 1:  # the title page, across the page
                    self.emit("#set page(columns: 1)")
                    self.cols = 1
                self.emit("#metadata(none)<nohead>")  # no running head on a part's title page
                self.emit("#heading(level: 1)[#%s]" % q(part.la))
                if two:
                    self.emit('#align(center, text(size: 1.2em, style: "italic", %s))' % q(part.en))
                if self.two_columns():
                    # The part begins on the next page, in its columns (a page
                    # rule after content starts a new page).
                    self.emit("#set page(columns: %d)" % cols)
                    self.cols = cols
                else:
                    self.emit("#pagebreak()")
        elif self.layout.new_page_per_day and index:
            self.emit("#pagebreak(weak: true)")
        self.emit("#metadata((d: %s, t: %s))<day>" % (q(office.part.la), q(office.title)))
        if not office.untitled:
            self.emit("#heading(level: 2)[#%s]" % q(office.title))
        if office.note:
            self.emit("#officenote(%s)" % q(office.note))
        if office.rows is not None:
            self.calendar(office, two)
            return
        for hour in office.hours:
            if hour.heading:
                self.emit("#metadata(%s)<hour>" % q(hour.heading))
                self.emit("#grouphead(%s)" % q(hour.heading))
            for section in hour.sections:
                if self.hymn(section):
                    pass
                elif len(section.columns) >= 2:
                    self.pairs(section.columns[0], section.columns[1])
                else:
                    self.single(section.columns[0])
                self.emit("#v(0.4em, weak: true)")

    def _captured(self, emit):
        """What emit() writes, as one block of markup instead."""
        out, self.out = self.out, []
        try:
            emit()
            return "[\n%s\n]" % "\n".join(self.out)
        finally:
            self.out = out

    def hymn(self, section):
        """A hymn whose lines are short, set in two columns of stanzas in each
        language -- half the height. Both settings go to Typst, which measures
        the lines at the book's type and width (hymnfit): where a line will not
        fit half a column, the hymn stays as it was. False if no hymn."""
        cols = section.columns[:2]
        spans = [_hymn_span(c) for c in cols]
        if not cols or any(s is None for s in spans):
            return False
        parts = []
        for col, (start, body, end) in zip(cols, spans):
            stanzas = _stanzas(col[body:end])
            if len(stanzas) < 2 or sum(len(s) for s in stanzas) < HYMN_MIN_LINES:
                return False
            parts.append((col[start:body], stanzas))
        cuts = _hymn_cuts([s for _h, s in parts])
        two = len(cols) == 2

        def lines(segments):  # the rest of the section, as before
            if any(segments):
                self.pairs(segments[0], segments[1]) if two else self.single(segments[0])

        def stack(stanzas):
            items = []
            for i, stanza in enumerate(stanzas):
                if i:
                    items.append("v(%s)" % HYMN_STANZA_GAP)
                items += ["verse[%s]" % self.runs(line) for line in stanza]
            return "stack(spacing: %s, %s)" % (dopdf._em(dopdf.spacing(self.layout)[2]), ", ".join(items))

        def split():
            lines([h for h, _s in parts])  # a heading on a line of its own ("Hymnus")
            self.in_paragraph = True  # each line a paragraph: markers as boxes (runs)
            halves = ["hy(%s, %s)" % (stack(s[:c]), stack(s[c:])) for (_h, s), c in zip(parts, cuts)]
            self.in_paragraph = False
            self.emit("#bi([#%s], R[#%s])" % tuple(halves) if two else "#" + halves[0])

        lines([c[:s[0]] for c, s in zip(cols, spans)])  # before the hymn: a chapter, a responsory
        plain = self._captured(lambda: lines([c[s[0]:s[2]] for c, s in zip(cols, spans)]))
        widest = ", ".join("[%s]" % dopdf.Writer.runs(self, line)
                           for _h, s in parts for stanza in s for line in stanza)
        self.emit("#hymnfit(%s, (%s,), %s, %s)"
                  % ("true" if two else "false", widest, self._captured(split), plain))
        lines([c[s[2]:] for c, s in zip(cols, spans)])  # after it: the versicle
        return True

    def calendar(self, office, two):
        q = dopdf.q
        cells = []
        for day, rdate, letter, t1, t2, rank in office.rows:
            row = [q(day), "text(size: 0.84em, %s)" % q(rdate), "text(fill: rub, %s)" % q(letter),
                   q(t1)]
            if two:
                row.append('text(style: "italic", %s)' % q(t2))
            row.append("text(size: 0.84em, hyphenate: false, %s)" % q(rank))
            cells.append(", ".join(row))
        if cells:
            self.emit("#caltable(%s,\n  %s)" % ("true" if two else "false", ",\n  ".join(cells)))

    # The joined book gets one clean bookmark tree built from these entries;
    # a volume that starts mid-part would otherwise contribute its offices as
    # top-level bookmarks.
    rebuild_outline = True

    def volume_entries(self, vol, pdf_path, next_page):
        """Parts (level 1) and offices (level 2), with their pages.

        The volume's headings appear in its bookmarks in document order: a
        part heading before its first office, then one heading per office.
        That sequence is known from `vol`, so the two are simply paired.
        """
        reader = PdfReader(pdf_path)
        pages = []
        # The first copies' hidden headings: their book pages, for references
        # in the volumes still to come.
        if not hasattr(self.meta, "ref_pages"):
            self.meta.ref_pages = {}

        def walk(items):
            for item in items:
                if isinstance(item, list):
                    walk(item)
                elif str(item.title).startswith(REF_MARK):
                    self.meta.ref_pages.setdefault(str(item.title)[len(REF_MARK):],
                                                   next_page + reader.get_destination_page_number(item))
                else:
                    pages.append(reader.get_destination_page_number(item))

        walk(reader.outline)
        expected = []
        for office in vol:
            if office.part_start:
                expected.append((1, "", office.part.la))
            if not office.untitled:
                expected.append((2, office.left, office.title))
        if len(expected) != len(pages):
            raise RuntimeError("volume bookmarks do not match its contents (%d vs %d)"
                               % (len(pages), len(expected)))
        return [(lvl, left, title, next_page + idx) for (lvl, left, title), idx in zip(expected, pages)]

    def title_page(self):
        meta = self.meta
        langs = dopdf.languages_named(meta)
        parts = [p.la for p in getattr(meta, "parts", []) if not p.compact]
        return dopdf.title_block(book_title(meta.version), meta.version, parts, langs,
                                 ai_note=bool(getattr(meta, "ai_lines", 0)),
                                 volume=getattr(meta, "volume", None))


def office_rows(office):
    if office.rows is not None:
        return 20 + 2 * len(office.rows)
    return 20 + sum(max((len(c) for c in s.columns), default=0)
                    for h in office.hours for s in h.sections)


# --------------------------------------------------------------------------
# running it


def dump_part(part, version, lang1, lang2, perl, dumper, horas_dir, workdir, cancelled=None,
              only=None, perl_libs=()):
    out = os.path.join(workdir, "%s.json" % part)
    p = docgi.perl_path  # plain ASCII: Perl cannot open a path with an accent in it
    cmd = [p(perl)] + [a for lib in perl_libs for a in ("-I", p(lib))] +         [p(dumper), "--horas", p(horas_dir), "--version", version,
           "--lang1", lang1, "--lang2", lang2, "--part", part, "--out", p(out)]
    if only and part != "fixed":  # the fixed texts are never restricted
        cmd += ["--only", ",".join(only)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    while True:
        try:
            _o, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                proc.kill()
                proc.communicate()
                raise dopdf.Cancelled()
    if proc.returncode != 0 or not os.path.isfile(out):
        name = PARTS[part].en if part in PARTS else "fixed texts"
        raise RuntimeError("the engine could not produce the %s: %s"
                           % (name, err.decode("utf-8", "replace").strip()[-800:]))
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


def canticle_numbers(dumps):
    """The canticles (Ps. 151 and above) named at Matins in the dumped offices."""
    found = set()
    for p in DUMPED:
        d = dumps.get(p) or {}
        for o in d.get("offices", []) + d.get("referenced", []):
            for s in o.get("sections", []):
                if "Matutinum" not in s["key"]:
                    continue
                text = re.sub(r"<[^>]*>", "", s.get("l1") or "")
                for group in re.findall(r";;\s*([\d;,()\s-]+)", text):
                    for tok in re.split(r"[;,]", group):
                        m = re.match(r"\s*(\d+)", tok)
                        if m and int(m.group(1)) > 150:
                            found.add(int(m.group(1)))
    return sorted(found)


def needed_dumps(parts):
    """Which of the engine's dumps a book of these parts reads."""
    need = [p for p in DUMPED if p in parts]
    if need:
        need.append("commune")  # offices elsewhere refer to the Common by name
    if "temporis" in parts or "psalterium" in parts:
        need.append("tempora")  # what the Proper of the Season already holds
    if "kalendarium" in parts:
        need.append("sancti")
    if any(p in parts for p in ("orationes", "ordinarium", "psalterium", "temporis")):
        need.append("fixed")
    if "missa" in parts:
        need.append("missa")
    return list(dict.fromkeys(need))


def assemble(parts, version, lang1, lang2, dumps, renders, alternatives=True, references=True,
             customs=None):
    """The book's offices, part by part, from the dumps and the rendered sample days.
    references: print a repeated text once, later copies referring to it.
    customs: {"custom1": file, ...} for the custom parts among `parts`."""
    offices = gather(parts, version, lang1, lang2, dumps, renders, alternatives=alternatives,
                     customs=customs)
    return finish(offices, parts, lang1, lang2, references)


def gather(parts, version, lang1, lang2, dumps, renders, alternatives=True, customs=None):
    """The offices of the parts, translated where the English lacks a text, not
    yet shortened or referred to (see finish)."""
    two = lang1 != lang2
    langs = dopsalter.Langs(lang1, lang2, version)
    english = (dooffice.family(lang1) == "English", dooffice.family(lang2) == "English")
    commons = (dumps.get("commune") or {}).get("offices", [])
    names = commune_names(commons)  # the names that tell them apart
    for o in commons:
        name = names.get(o["file"])
        if name:
            o["title"] = [name[1] if english[0] else name[0], name[1] if english[1] else name[0]]
    titles, sources = {}, {}
    for p in DUMPED:
        for o in (dumps.get(p) or {}).get("offices", []) + (dumps.get(p) or {}).get("referenced", []):
            sources.setdefault(_norm(o["file"]), o)
            t = o.get("title") or [o["key"], o["key"]]
            if t[0]:
                titles.setdefault(_norm(o["file"]), t)
                titles.setdefault(o["key"], t)
                titles.setdefault(re.sub(r"^\w+/", "", o["file"]), t)
    # Files printed in this book (so a reference to them can just point there).
    in_book = {o["file"] for p in parts if p in DUMPED for o in dumps[p]["offices"]}

    built = {}

    english1 = dooffice.family(lang1) == "English"

    def dumped_part(p):
        if p not in built:
            built[p] = build_offices(book_part(p, lang1), dumps[p], titles, two,
                                     dooffice.family(lang2) == "English", sources=sources,
                                     in_book=in_book, alternatives=alternatives,
                                     lang1_is_english=english1)
        return built[p]

    # The Proper of the Season as the book prints it -- not the raw files, whose
    # Mass texts and borrowed-from files it leaves out.
    proper = dopsalter.section_texts(dumped_part("tempora")) if dumps.get("tempora") else None
    offices = []
    for p in parts:
        if is_custom(p):
            offices += custom_offices(p, (customs or {}).get(p))
            continue
        part = book_part(p, lang1)
        if p in DUMPED:
            offices += dumped_part(p)
            continue
        if p == "kalendarium":
            offs = dopsalter.calendar_offices(part, dumps["sancti"], two)
        elif p == "orationes":
            offs = dopsalter.prayer_offices(part, dumps["fixed"], two)
        elif p == "ordinarium":
            offs = dopsalter.ordinary_offices(part, renders, dumps["fixed"], langs, family_of(version))
        elif p == "psalterium":
            offs = dopsalter.psalter_offices(part, renders, langs, proper, dumps.get("fixed"),
                                             family_of(version))
            offs += dopsalter.canticle_offices(part, dumps.get("canticles"), langs)
        elif p == "missa":
            offs = missa_offices(part, dumps["missa"], two, dooffice.family(lang2) == "English",
                                 lang1_is_english=english1)
        else:  # temporis
            psalter = dopsalter.psalter_offices(PARTS["psalterium"], renders, langs, proper,
                                                dumps.get("fixed"), family_of(version))
            ordinary = dopsalter.ordinary_offices(PARTS["ordinarium"], renders, dumps["fixed"],
                                                  langs, family_of(version))
            known = dopsalter.known_texts(
                renders, None, dumps.get("fixed"),
                extra_sections=[s for o in psalter + ordinary + dumped_part("tempora")
                                for h in o.hours for s in h.sections])
            extra = {}
            if family_of(version) in ("roman", "op") and "trident" not in version.lower():
                extra = {"pasch": {"Matutinum": dopsalter.paschal_versicles(dumps.get("fixed"),
                                                                             langs)}}
            offs = dopsalter.season_offices(part, renders, known, langs, extra)
        for o in offs:
            # Titled in Latin, then English: an English book's title is the English.
            if english1 and p != "missa" and o.title2:
                o.title, o.title2 = o.title2, o.title
            if two and "English" in (dooffice.family(lang1), dooffice.family(lang2)) \
                    and o.title2 != o.title and not (o.untitled or o.note):
                o.note = o.title2
        if offs:
            offs[0].part_start = True
        offices += offs
    if english1:  # the hours named in English too
        for o in offices:
            for h in o.hours:
                h.heading = HOUR_EN.get(h.heading, h.heading)
    translate_offices([o for o in offices if not is_custom(o.part.key)], lang1, lang2)
    return offices


def finish(offices, parts, lang1, lang2, references=True):
    """A book's offices as printed: the usual texts by their first words,
    repeated texts referred to (references), headings run in."""
    shorten_offices(offices, keep_first="ordinarium" not in parts)
    if references:
        cross_reference(offices, (lang1, lang2))
    run_in_headings(offices)
    return offices


def translate_offices(offices, lang1, lang2, path=None):
    """The English column's Latin -- where the engine's English data has no
    translation -- replaced from translations-en.json (dotranslate.py)."""
    return dotranslate.translate_days(offices, lang1, lang2, path)


# The doxology after a psalm and the usual ending of a collect: a breviary
# prints them in full in the Ordinary and elsewhere by their first words. Only
# these exact texts are shortened -- Per eúndem, Qui vivis, Qui tecum, the
# ending "in unitáte eiúsdem Spíritus Sancti" and the hymns' own doxologies stay
# in full, so that an unusual ending is seen.
_SHORT_FORMS = (
    ("gloria", "Glória Patri, et Fílio, * et Spirítui Sancto.", "Glória Patri."),
    ("sicut", "Sicut erat in princípio, et nunc, et semper, * et in sǽcula sæculórum. Amen.",
     "Sicut erat."),
    ("per", "Per Dóminum nostrum Jesum Christum, Fílium tuum: qui tecum vivit et regnat in "
            "unitáte Spíritus Sancti, Deus, per ómnia sǽcula sæculórum.", "Per Dóminum."),
    ("gloria", "Glory be to the Father, and to the Son, * and to the Holy Ghost.",
     "Glory be to the Father."),
    ("sicut", "As it was in the beginning, is now, * and ever shall be, world without end. Amen.",
     "As it was."),
    ("per", "Through Jesus Christ, thy Son our Lord, Who liveth and reigneth with thee, in the "
            "unity of the Holy Ghost, God, world without end.", "Through our Lord."),
)
_SHORT = {dotranslate.key(full): (kind, short) for kind, full, short in _SHORT_FORMS}
# The parts that keep them in full: where the reader learns them.
FULL_TEXT_PARTS = ("ordinarium", "orationes")


def _endings(column):
    """[(kind, start, end, [(line, short)])]: the doxologies and standard
    endings in one column, with the lines each spans."""
    found, i = [], 0
    while i < len(column):
        kind, short = _SHORT.get(dotranslate.key(column[i].text), (None, None))
        nxt = column[i + 1] if i + 1 < len(column) else None
        nkind, nshort = _SHORT.get(dotranslate.key(nxt.text), (None, None)) if nxt else (None, None)
        # The Mass's Introit sets a blank line between its ℣. Glória Patri and
        # ℟. Sicut erat: the one line replaces both, and the gap.
        j = i + 1
        while kind == "gloria" and j < len(column) and column[j].is_blank:
            j += 1
        if kind == "gloria" and j > i + 1 and j < len(column):
            jkind, jshort = _SHORT.get(dotranslate.key(column[j].text), (None, None))
            if jkind == "sicut":
                found.append(("gloria", i, j + 1, [(column[i], short), (column[j], jshort)]))
                i = j + 1
                continue
        if kind == "gloria" and nkind == "sicut":
            found.append(("gloria", i, i + 2, [(column[i], short), (nxt, nshort)]))
            i += 2
            continue
        if kind == "per":
            spans = [(column[i], short)]
            amen = nxt is not None and dotranslate.split_prefix(nxt.text)[1].strip() == "Amen."
            if amen:
                spans.append((nxt, None))  # "℟. Amen." joins the same line
            found.append(("per", i, i + 1 + amen, spans))
            i += 1 + amen
            continue
        i += 1
    return found


def _one_line(spans):
    runs = []
    for line, short in spans:
        new = dotranslate.with_body(line, short) if short else line
        if runs:
            runs.append(dooffice.Run(text=" "))
        runs += new.runs
    return dooffice.Line(runs=runs)


def shorten_offices(offices, keep_first=False):
    """Print the Glória Patri after a psalm and the standard Per Dóminum on one
    line by their first words ("℣. Glória Patri. ℟. Sicut erat.", "Per Dóminum.
    ℟. Amen."), outside the Ordinary and the prayers. A section is changed only
    where every column has the same ones, so the languages stay side by side.
    keep_first: leave the first of each in full (a book without the Ordinary).
    How many were shortened."""
    seen, n = set(), 0
    for office in offices:
        if office.part.key in FULL_TEXT_PARTS or is_custom(office.part.key):
            continue
        for hour in office.hours:
            for section in hour.sections:
                found = [_endings(c) for c in section.columns]
                if not found or not found[0]:
                    continue
                kinds = [[f[0] for f in col] for col in found]
                if any(k != kinds[0] for k in kinds[1:]):
                    continue
                full = set()  # the book's first of each kind, when it is to stay
                for j, kind in enumerate(kinds[0]):
                    if keep_first and kind not in seen:
                        seen.add(kind)
                        full.add(j)
                for c, col in enumerate(found):
                    column = section.columns[c]
                    for j in reversed(range(len(col))):  # from the end: indices stay valid
                        if j not in full:
                            _kind, start, end, spans = col[j]
                            column[start:end] = [_one_line(spans)]
                n += len(kinds[0]) - len(full)
    return n


# Texts printed more than once. The first copy stays; each later one becomes
# its opening words and the page of the first ("℟. Súscipe verbum, Virgo
# María… pag. 34"), as printed breviaries refer back ("ut in Dominica").
# These parts are never replaced by references (they can be referred to).
# The Psalter is printed in full, every day's psalms on that day, so that a
# reader at Terce turns no pages: printed breviaries do the same.
NEVER_REFERENCED = ("ordinarium", "orationes", "kalendarium", "psalterium")
# ...and these keep their headings on lines of their own too (run_in_headings).
AS_THEY_ARE = ("ordinarium", "orationes", "kalendarium")
# A bookmark title marking a first copy in a typeset volume (see BookWriter).
REF_MARK = "§ref:"


def _split_head(column):
    """(the section's heading lines, the rest)."""
    n = 0
    while n < len(column) and (column[n].is_blank or column[n].is_heading or column[n].is_rubric):
        n += 1
    return column[:n], column[n:]


def _incipit(line):
    """A line's opening words: up to the asterisk, else six words; never past
    a ℟. or ℣. inside the line ("Glória Patri. ℟. Sicut erat." -> "Glória Patri…")."""
    words = dotranslate.split_prefix(line.text)[1].split()
    label = next((k for k, w in enumerate(words) if k and w in ("℟.", "℣.")), None)
    if label is not None:
        words = words[:label]
    star = words.index("*") if "*" in words else -1
    if 3 <= star <= 8:
        words = words[:star]
    else:
        words = [w for w in words if w not in ("*", "†")][:6]
    return " ".join(words).rstrip(",;:.·!?") + "…"


def cross_reference(offices, langs=("Latin", "English")):
    """Print each repeated text once; later copies refer to the first, by its
    opening words and page (which the writer fills in). First whole sections:
    one whose text, in every column, is that of an earlier section keeps its
    heading and becomes one line. Then runs of lines inside a section printed
    before (see _partial_references). Sections of one line are left alone (a
    reference saves nothing). langs: the columns' languages. How many references."""
    langs = tuple(dooffice.family(l) for l in langs)
    _unshare(offices)
    ids = iter(range(10 ** 9))
    where = _locators(offices, langs)
    return _whole_references(offices, ids, where) + _partial_references(offices, ids, langs, where)


# Where a reference sends the reader, besides the page: the heading of the text
# referred to, short ("Resp. IV", "Lect. VII"), and its hour too when the office
# has that heading at more than one hour ("Cap. ad Laud.", "Chapter at Lauds").
HEAD_SHORT = {
    "Latin": (("Responsorium breve", "Resp. br."), ("Responsorium", "Resp."), ("Lectio", "Lect."),
              ("Antiphonæ", "Ant."), ("Antiphona", "Ant."), ("Capitulum", "Cap."),
              ("Hymnus", "Hymn."), ("Versus", "Vers."), ("Oratio", "Orat."),
              ("Invitatorium", "Invit."), ("Ad Benedictus", "Ad Bened."),
              ("Ad Magnificat", "Ad Magnif."), ("Psalmus", "Ps."), ("Canticum", "Cant."),
              ("Commemoratio", "Comm."), ("Evangelium", "Evang."), ("Introitus", "Introit."),
              ("Epistola", "Epist."), ("Graduale", "Grad."), ("Sequentia", "Seq."),
              ("Offertorium", "Offert."), ("Secreta", "Secr."), ("Communio", "Commun."),
              ("Postcommunio", "Postcomm.")),
    "English": (("Short Responsory", "Short Resp."), ("Responsory", "Resp."),
                ("Antiphons", "Ant."), ("Antiphon", "Ant."), ("At the Benedictus", "Benedictus"),
                ("At the Magnificat", "Magnificat"), ("Invitatory", "Invit."),
                ("Versicle", "Vers."), ("Psalm", "Ps."), ("Canticle", "Cant."),
                ("Commemoration", "Comm."), ("Chapter", "Chapter"), ("Lesson", "Lesson"),
                ("Hymn", "Hymn"), ("Prayer", "Prayer")),
}
# An English book's hours.
HOUR_EN = {la: en for _o, la, en in GROUPS.values()}
HOUR_EN.update({"Ad Vesperas": "At Vespers", "Ad Laudes II": "At Lauds (II)", "De hymnis": "The Hymns",
                "Ad Missam": "At Mass", "Ad Nonam": "At None"})
HOUR_SHORT = {
    "Latin": {"In I Vesperis": "in I Vesp.", "Ad Matutinum": "ad Mat.", "Ad Laudes": "ad Laud.",
              "Ad Primam": "ad Prim.", "Ad Tertiam": "ad Tert.", "Ad Sextam": "ad Sext.",
              "Ad Nonam": "ad Non.", "In II Vesperis": "in II Vesp.", "Ad Vesperas": "ad Vesp.",
              "Ad Completorium": "ad Compl.", "Ad Missam": "ad Miss."},
    "English": {"In I Vesperis": "at I Vespers", "Ad Matutinum": "at Matins", "Ad Laudes": "at Lauds",
                "Ad Primam": "at Prime", "Ad Tertiam": "at Terce", "Ad Sextam": "at Sext",
                "Ad Nonam": "at None", "In II Vesperis": "at II Vespers", "Ad Vesperas": "at Vespers",
                "Ad Completorium": "at Compline", "Ad Missam": "at Mass"},
}
for _la, _en in HOUR_EN.items():  # an English book's hour headings: the same short forms
    for _forms in HOUR_SHORT.values():
        if _la in _forms:
            _forms.setdefault(_en, _forms[_la])


_TITLES = tuple(full for pairs in HEAD_SHORT.values() for full, _short in pairs)


def _is_title(line):
    """A heading a reference can name: heading type longer than a red initial
    (a collect's or a stanza's first letter is not one), or a red title such as
    a psalm's ("Psalmus 50 [3]")."""
    first = next((r for r in line.runs if r.text.strip()), None)
    if first is None:
        return False
    if first.size == "heading" and len(first.text.strip()) > 1:
        return True
    return line.is_rubric and line.text.strip().startswith(_TITLES)


def _heading_text(column):
    line = next((l for l in column if _is_title(l)), None)
    # Without the engine's note after it ("Martyrologium {anticipatur}"),
    # which is not part of the heading's name.
    return re.sub(r"\s*\{[^{}]*\}\s*$", "", line.text.strip()).rstrip(".:") if line else ""


def _short_heading(text, lang):
    """"Resp. IV", "Orat. mortuorum"; a heading naming several texts
    ("Capitulum Responsorium Hymnus Versus") by the first, where it starts."""
    pairs = HEAD_SHORT.get(lang, ())
    for full, short in pairs:
        if text == full or text.startswith(full + " "):
            rest = text[len(full):]
            nxt = rest.split()[0] if rest.split() else ""
            if any(f.split()[0] == nxt for f, _s in pairs):
                rest = ""  # the next text's heading, not this one's
            return short + rest
    return text


def _locators(offices, langs):
    """(id(hour), section index) -> where, in each column's language."""
    where = {}
    for office in offices:
        if office.rows is not None:
            continue
        count = collections.Counter(_heading_text(s.columns[0]) for h in office.hours
                                    for s in h.sections if s.columns)
        for hour in office.hours:
            for i, section in enumerate(hour.sections):
                out = []
                for c, col in enumerate(section.columns):
                    lang = langs[c] if c < len(langs) else langs[0]
                    head = _heading_text(col)
                    parts = [_short_heading(head, lang)] if head else []
                    if not head or count[_heading_text(section.columns[0])] > 1:
                        # an hour only: the Commemorations, De hymnis ... are not
                        hour_name = HOUR_SHORT.get(lang, HOUR_SHORT["Latin"]).get(hour.heading)
                        if hour_name:
                            parts.append(hour_name)
                    out.append(" ".join(parts))
                where[(id(hour), i)] = out
    return where


def _unshare(offices):
    """Copy any office or hour met a second time, so that changing one place
    never changes another."""
    seen = set()
    for o, office in enumerate(offices):
        if office.rows is not None:
            continue
        if id(office) in seen:
            office = offices[o] = copy.copy(office)
            office.hours = list(office.hours)
        seen.add(id(office))
        for k, hour in enumerate(office.hours):
            if id(hour) in seen:
                hour = office.hours[k] = copy.copy(hour)
                hour.sections = list(hour.sections)
            seen.add(id(hour))


def _printed_sections(offices):
    """(office, hour, index of the section in the hour), in book order -- not
    the custom parts', which are neither referred to nor run in."""
    for office in offices:
        if office.rows is None and not is_custom(office.part.key):
            for hour in office.hours:
                for i in range(len(hour.sections)):
                    yield office, hour, i


# A section of one line is left as it is -- an antiphon, a versicle, a short
# lesson: its reference would be hardly shorter, and send the reader to
# another page for a line. But a lesson is mostly one paragraph, one long
# line: it is referred to like any other section once it is this long.
LONG_LESSON = 200


def _long_lesson(head, bodies):
    title = " ".join(l.text for l in head if l.is_heading).strip()
    return (re.match(r"(Lectio|Lesson)\b", title) is not None
            and re.match(r"(Lectio brevis|Short lesson)", title) is None
            and min(len(b[0].text) for b in bodies) >= LONG_LESSON)


def _whole_references(offices, ids, where):
    first = {}  # signature -> [hour, index, [reference id of each column]]
    n = 0
    for office, hour, i in _printed_sections(offices):
        section = hour.sections[i]
        split = [_split_head(c) for c in section.columns]
        bodies = [[l for l in body if not l.is_blank] for _head, body in split]
        if not bodies or not all(bodies):
            continue
        if max(len(b) for b in bodies) < 2 and not _long_lesson(split[0][0], bodies):
            continue
        sig = tuple(tuple(l.text for l in b) for b in bodies)
        if sig not in first:
            first[sig] = [hour, i, None]
            continue
        if office.part.key in NEVER_REFERENCED:
            continue
        src = first[sig]
        if src[2] is None:
            # A marker on each column's first line: where one language's text
            # starts a row lower (its lesson beside the Latin's second
            # paragraph), a page may end between the two.
            src[2] = []
            for c in range(len(section.columns)):
                col = src[0].sections[src[1]].columns[c]
                src[2].append(_mark(src[0], src[1], next(k for k, l in enumerate(col) if not l.is_blank),
                                    ids, c))
        new = copy.copy(section)  # sections may be shared: never changed in place
        new.columns = []
        for c, (head, _body) in enumerate(split):
            ref = dotranslate.with_body(bodies[c][0], _incipit(bodies[c][0]))
            ref.ref = (src[2][min(c, len(src[2]) - 1)], c, _where(where, src[0], src[1], c))
            if getattr(bodies[c][0], "ai", False):
                ref.ai = True  # it quotes English translated by AI
            # The reference is to every Nocturn's antiphons, not to the first's.
            new.columns.append([l for l in head if not _is_nocturn_label(l.text)] + [ref])
        hour.sections[i] = new
        n += 1
    return n


# Runs of lines inside a section that were printed before -- a lesson that
# begins as an earlier one does, a psalm inside a long hour of the Triduum.
# Such a run becomes one line when that saves at least PARTIAL_MIN_LINES
# printed lines (reckoned at 70 characters a line): its opening words, and,
# where the first copy goes on past the run, where to stop -- "Beáti immaculáti
# in via… usque ad 118:16 In iustificatiónibus tuis… pag. 210".
PARTIAL_MIN_ROWS = 3
PARTIAL_MIN_LINES = 3
UNTIL = {"Latin": "usque ad", "English": "as far as"}


def _printed_lines(row):
    return max(1, max(-(-len(t) // 70) for t in row))


def _where(where, hour, index, c):
    names = where.get((id(hour), index), [])
    return names[c] if c < len(names) else (names[0] if names else "")


def _where_inside(where, hour, index, row, c, langs):
    """For part of a section: the nearest heading above that row inside the
    section ("Ps. 50 [3]" in a long Psalmi), else the section's own."""
    col = hour.sections[index].columns[c]
    _head, body = _split_head(col)
    if row < len(body) and _is_title(body[row]):
        return ""  # the words quoted are themselves the title ("Psalmus 50 [1]…")
    inner = next((l for l in reversed(body[:row]) if _is_title(l)), None)
    if inner is None:
        return _where(where, hour, index, c)
    lang = langs[c] if c < len(langs) else langs[0]
    return _short_heading(inner.text.strip().rstrip(".:"), lang)


def _cells(bodies):
    """The rows of a section's bodies as printed: [[line or None for each column]].
    Two columns are paired as the writer pairs them (dopdf.align_columns), so a
    run of rows is the same verses in both, also where the translation divides
    them otherwise (Coverdale's psalms)."""
    if len(bodies) == 2:
        return [list(p) for p in dopdf.align_columns(bodies[0], bodies[1])]
    return [[l] for l in bodies[0]]


def _positions(cells):
    """Where each row's lines stand in each column's body: [(index, ...)]."""
    out, counts = [], [0] * (len(cells[0]) if cells else 0)
    for row in cells:
        out.append(tuple(counts))
        counts = [k + (l is not None) for k, l in zip(counts, row)]
    return out


def _partial_references(offices, ids, langs, where):
    # PARTIAL_MIN_ROWS rows -> (hour, section index, column 0's head length,
    #                           each column's body index of the row, row, rows)
    index = {}
    n = 0
    for office, hour, i in _printed_sections(offices):
        section = hour.sections[i]
        split = [_split_head(c) for c in section.columns]
        heads = [list(h) for h, _b in split]
        bodies = [list(b) for _h, b in split]
        if not bodies or not all(bodies):
            continue
        if any(getattr(l, "ref", None) for b in bodies for l in b):
            continue  # already a reference
        cells = _cells(bodies)
        rows = [tuple(l.text if l is not None else "" for l in row) for row in cells]
        # A section other sections refer to as a whole stays whole: a reader
        # sent to it must find the text there, not another reference.
        first_copy = any(getattr(l, "marks", None) for col in section.columns for l in col)
        runs = ([] if office.part.key in NEVER_REFERENCED or first_copy
                else _runs_printed_before(rows, index))
        for start, length, (shour, si, shead, spos, _srow, _srows), early in reversed(runs):
            ident = _mark(shour, si, shead + spos[0], ids)
            full = [r for r in range(start, start + length) if all(t.strip() for t in rows[r])]
            last = full[-1]
            refs = []
            for c in range(len(bodies)):
                run = [cells[r][c] for r in range(start, start + length) if cells[r][c] is not None]
                text = _incipit(cells[start][c])
                if early:
                    lead = dotranslate.split_prefix(cells[last][c].text)[0]
                    text += " %s %s%s" % (UNTIL.get(langs[c] if c < len(langs) else "", "→"),
                                          lead, _incipit(cells[last][c]))
                ref = dotranslate.with_body(cells[start][c], text)
                ref.ref = (ident, c, _where_inside(where, shour, si, spos[c], c, langs))
                if any(getattr(l, "ai", False) for l in run):
                    ref.ai = True
                # A line in the run that is itself referred to passes its marker on.
                ref.marks = [m for l in run for m in getattr(l, "marks", ())]
                refs.append(ref)
            cells[start:start + length] = [refs]
            rows[start:start + length] = [None]  # a reference: never itself a first copy
            n += 1
        if runs:
            bodies = [[row[c] for row in cells if row[c] is not None] for c in range(len(bodies))]
            new = copy.copy(section)
            new.columns = [h + b for h, b in zip(heads, bodies)]
            hour.sections[i] = new
        positions = _positions(cells)
        for r in range(len(rows) - PARTIAL_MIN_ROWS + 1):
            key = tuple(rows[r:r + PARTIAL_MIN_ROWS])
            if None not in key and all(t.strip() for t in key[0]) and not _is_nocturn_label(key[0][0]):
                index.setdefault(key, (hour, i, len(heads[0]), positions[r], r, rows))
    return n


def _runs_printed_before(rows, index):
    """[(start, length, where printed before, whether the first copy goes on past it)]."""
    found, r = [], 0
    while r + PARTIAL_MIN_ROWS <= len(rows):
        hit = index.get(tuple(rows[r:r + PARTIAL_MIN_ROWS])) if all(t.strip() for t in rows[r]) else None
        if hit:
            srows, srow = hit[5], hit[4]
            length = PARTIAL_MIN_ROWS
            while (r + length < len(rows) and srow + length < len(srows)
                   and rows[r + length] == srows[srow + length]):
                length += 1
            while length > PARTIAL_MIN_ROWS and _is_nocturn_label(rows[r + length - 1][0]):
                length -= 1  # "usque ad" names an antiphon, not the next Nocturn's heading
            early = srow + length < len(srows)
            saved = sum(_printed_lines(x) for x in rows[r:r + length]) - (2 if early else 1)
            if saved >= PARTIAL_MIN_LINES:
                found.append((r, length, hit, early))
                r += length
                continue
        r += 1
    return found


def _mark(hour, index, j, ids, col=0):
    """The reference id of line j of a section's column (the first unless
    said), which is given one (on a copy of the section) if it has none yet."""
    section = hour.sections[index]
    line = section.columns[col][j]
    if getattr(line, "marks", None):
        return line.marks[0]
    ident = "ref%d" % next(ids)
    section = copy.copy(section)
    section.columns = [list(c) for c in section.columns]
    marked = dooffice.Line(runs=list(line.runs))
    marked.__dict__.update({k: v for k, v in line.__dict__.items() if k != "runs"})
    marked.marks = [ident]
    section.columns[col][j] = marked
    hour.sections[index] = section
    return ident


# Section headings set at the start of their first line ("Lectio I. Isa
# 41:8-10", "Responsorium II. ℟. Non auferétur…") rather than on a line of
# their own. Psalm and canticle titles keep theirs, and the Ordinary, the
# prayers and the Calendar are left as they are (AS_THEY_ARE).
OWN_LINE = ("Psalmus", "Psalm", "Canticum", "Canticle")


def run_in_headings(offices):
    """Move each section's heading onto its first line; how many sections."""
    n = 0
    for office, hour, i in _printed_sections(offices):
        if office.part.key in AS_THEY_ARE:
            continue
        section = hour.sections[i]
        counts = []
        for col in section.columns:
            k = 0
            while k < len(col) and col[k].is_heading:
                k += 1
            if (k == 0 or k >= len(col) or col[k].is_blank or col[k].is_heading
                    or any(col[j].text.strip().startswith(OWN_LINE) for j in range(k))):
                counts = None
                break
            counts.append(k)
        if not counts or len(set(counts)) != 1:
            continue
        new = copy.copy(section)
        new.columns = [[_run_in(col[:k], col[k])] + col[k + 1:]
                       for col, k in zip(section.columns, counts)]
        hour.sections[i] = new
        n += 1
    return n


def _run_in(heads, line):
    """One line: the headings, in their red bold italic at the text's size, then the line."""
    style = dict(red=True, bold=True, italic=True)
    runs = []
    for h in heads:
        if runs:
            runs.append(dooffice.Run(text=" · ", **style))
        runs += [dooffice.Run(text=r.text, **style) for r in h.runs if r.text]
    if not runs[-1].text.rstrip().endswith((".", ":", ";")):
        runs[-1] = dooffice.Run(text=runs[-1].text.rstrip() + ".", **style)
    runs.append(dooffice.Run(text=" "))
    body = list(line.runs)
    first = next((x for x, r in enumerate(body) if r.text.strip()), None)
    if (first is not None and body[first].size in ("heading", "initial")
            and len(body[first].text.strip()) == 1):
        body[first] = dooffice.Run(text=body[first].text)  # no drop capital in mid-line
    merged = dooffice.Line(runs=runs + body)
    marks = [m for l in heads + [line] for m in getattr(l, "marks", ())]
    if marks:
        merged.marks = marks
    if getattr(line, "ref", None):
        merged.ref = line.ref
    if getattr(line, "ai", False):
        merged.ai = True
    return merged


def build(parts, version, lang1, lang2, layout, pdf_path, perl, dumper, web_root, typst,
          progress=None, cancelled=None, workdir=None, keep_source=False, only=None,
          alternatives=True, perl_libs=(), references=True, customs=None):
    """Make a breviary PDF of `parts` (keys of PARTS, in the order given).

    only: restrict the dumped parts to these office keys (e.g. ["C4"]) -- for
    the app's self-test, which wants a real build in seconds.
    alternatives: False leaves out the alternative lessons.
    references: False prints every text in full, however often it recurs.
    """
    parts = current_parts(parts)
    if not parts:
        raise ValueError("choose at least one part")
    own_dir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="do-breviary-")
    os.makedirs(workdir, exist_ok=True)
    try:
        dumps, renders = gather_text(parts, version, lang1, lang2, perl, dumper, web_root, workdir,
                                     progress, cancelled, only, perl_libs)
        offices = assemble(parts, version, lang1, lang2, dumps, renders, alternatives=alternatives,
                           references=references, customs=customs)
        if not offices:
            raise RuntimeError("nothing to print for this version")
        _stage_includes(offices, workdir)

        meta = dopdf.Meta(version=version, lang1=lang1, lang2=lang2)
        meta.parts = _book_parts(parts, lang1, customs or {})
        meta.ref_pages = {}  # first copies' pages, filled in volume by volume
        dopdf._typeset(offices, meta, layout, pdf_path, typst, workdir, progress, cancelled,
                       writer=BookWriter, rows_of=office_rows)
        return offices
    finally:
        if own_dir and not keep_source:
            shutil.rmtree(workdir, ignore_errors=True)


def gather_text(parts, version, lang1, lang2, perl, dumper, web_root, workdir, progress=None,
                cancelled=None, only=None, perl_libs=()):
    """(dumps, renders): the parts read through the engine, and the sample days
    rendered for the Ordinary, the Psalter and the Common of the Seasons."""
    horas_dir = os.path.join(os.path.abspath(web_root), "cgi-bin", "horas")
    need = needed_dumps(parts)
    rendering = any(p in RENDERED for p in parts)
    total = len(need) + (dopsalter.render_count(lang1, lang2) if rendering else 0)
    count = {"dumps": 0, "renders": 0}

    def tick():
        if progress:
            progress("text", count["dumps"] + count["renders"], total)

    def rendered(done, _total):
        count["renders"] = done
        tick()

    tick()
    dumps, renders = {}, None
    # The dumps run in their own processes while the sample days render.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(need))) as pool:
        futs = {pool.submit(dump_part, p, version, lang1, lang2, perl, dumper, horas_dir,
                            workdir, cancelled, only, perl_libs): p for p in need}
        if rendering:
            engine = dooffice.Engine(web_root, perl, perl_libs)
            renders = dopsalter.render(engine, version, lang1, lang2, progress=rendered,
                                       cancelled=cancelled)
        for fut in concurrent.futures.as_completed(futs):
            dumps[futs[fut]] = fut.result()
            count["dumps"] += 1
            tick()
    # The monastic third Nocturns sing canticles no weekly Psalter holds:
    # those the offices name are set out by the engine too.
    wanted = canticle_numbers(dumps) if "psalterium" in parts and \
        family_of(version) in ("monastic", "cist") else []
    if wanted:
        dumps["canticles"] = dump_part("canticles", version, lang1, lang2, perl, dumper,
                                       horas_dir, workdir, cancelled,
                                       [str(n) for n in wanted], perl_libs)
    return dumps, renders


# --------------------------------------------------------------------------
# A breviary in volumes (dovolumes.py): each a book of its own, with its own
# title page, contents, page numbers and references.


def volume_offices(offices, seams, i, version):
    """Copies of the offices of volume i (from 0) of a breviary divided at
    `seams`: every office of the parts each volume holds entire, and of the
    Proper of the Season and of the Saints those that can fall in its time."""
    modern = "196" in version
    every = set(range(len(seams) + 1))
    chosen, last = [], {}
    for o in offices:
        vols = dovolumes.volumes_of(o, seams, modern, fallback=last.get(o.part.key, every))
        last[o.part.key] = vols
        if i in vols:
            chosen.append(o)
    # The Sundays after Epiphany said again in November: in a volume without
    # their January, before the last Sunday after Pentecost, where they fall
    # (their offices, and their Masses).
    for p in ("tempora", "missa"):
        keys = {o.key for o in chosen if o.part.key == p}
        if "Pent24-0" in keys and "Epi1-0" not in keys:
            resumed = [o for o in chosen if o.part.key == p and o.key.startswith("Epi")]
            ids = {id(o) for o in resumed}
            rest = [o for o in chosen if id(o) not in ids]
            at = next(j for j, o in enumerate(rest) if o.part.key == p and o.key == "Pent24-0")
            chosen = rest[:at] + resumed + rest[at:]
    # The saints in the order of the volume's time: the Winter Part's from late
    # November through December into March.
    place = dovolumes.saints_order(seams, i, modern)
    saints = [o for o in chosen if o.part.key == "sancti"]
    if saints:
        at = next(j for j, o in enumerate(chosen) if o.part.key == "sancti")
        saints.sort(key=lambda o: place.get(o.day or o.key[:5], 0))
        chosen = chosen[:at] + saints + chosen[at + len(saints):]
    chosen = copy.deepcopy(chosen)
    started = set()
    for o in chosen:  # each part's first office in this volume opens the part
        o.part_start = o.part.key not in started
        started.add(o.part.key)
    return chosen


def volume_path(pdf_path, seams, i):
    """"Breviarium Monasticum - I. Pars Hiemalis.pdf" beside the path chosen."""
    stem, ext = os.path.splitext(pdf_path)
    return stem + dovolumes.file_suffix(seams, i) + (ext or ".pdf")


def build_volumes(parts, version, lang1, lang2, layout, pdf_path, perl, dumper, web_root, typst,
                  seams, progress=None, cancelled=None, workdir=None, keep_source=False, only=None,
                  alternatives=True, perl_libs=(), references=True, customs=None):
    """Make a breviary in volumes divided at `seams` (keys of dovolumes.SEAMS,
    where the second, third ... volumes begin): one PDF each, named after
    pdf_path (volume_path). [(path, offices)] in order."""
    parts = current_parts(parts)
    if not parts:
        raise ValueError("choose at least one part")
    problem = dovolumes.check(list(seams))
    if problem:
        raise ValueError(problem)
    own_dir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="do-breviary-")
    os.makedirs(workdir, exist_ok=True)
    try:
        dumps, renders = gather_text(parts, version, lang1, lang2, perl, dumper, web_root, workdir,
                                     progress, cancelled, only, perl_libs)
        whole = gather(parts, version, lang1, lang2, dumps, renders, alternatives=alternatives,
                       customs=customs)
        names = dovolumes.names(seams)
        out = []
        for i, name in enumerate(names):
            if cancelled and cancelled():
                raise dopdf.Cancelled()
            offices = finish(volume_offices(whole, seams, i, version), parts, lang1, lang2, references)
            meta = dopdf.Meta(version=version, lang1=lang1, lang2=lang2)
            meta.parts = _book_parts(parts, lang1, customs or {})
            meta.ref_pages = {}
            meta.volume = (dovolumes.ROMANS[i],) + tuple(name)
            path = volume_path(pdf_path, seams, i)
            vdir = os.path.join(workdir, "volume-%d" % (i + 1))
            os.makedirs(vdir, exist_ok=True)
            _stage_includes(offices, vdir)

            def part_progress(stage, done, total, i=i):
                # Each volume a share of the typesetting: "book", in hundredths.
                if progress and stage in ("typeset", "assemble"):
                    frac = (done / total if total else 1.0) * (0.9 if stage == "typeset" else 0.1) \
                        + (0.9 if stage == "assemble" else 0.0)
                    progress("book", int(100 * (i + frac)), 100 * len(names))

            dopdf._typeset(offices, meta, layout, path, typst, vdir, part_progress, cancelled,
                           writer=BookWriter, rows_of=office_rows)
            out.append((path, offices))
        return out
    finally:
        if own_dir and not keep_source:
            shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Custom parts: a file of the user's own -- plain text, Markdown or a PDF --
# printed as a part of the book where the list of parts puts it (keys
# "custom1", "custom2" ...). Text is set in the book's type: a .txt line by
# line as written, Markdown as it reads (headings, paragraphs, bold and
# italic, lists, quotations); a PDF's own pages go in whole, each scaled to
# the page. Their text is left as it is: never shortened, referred to,
# referred from or "translated".

CUSTOM_KINDS = (".txt", ".md", ".markdown", ".pdf")


def is_custom(key):
    return str(key).startswith("custom")


def _read_text(path):
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _is_markdown(path):
    return path.lower().endswith((".md", ".markdown"))


def custom_title(path):
    """A custom part's name: a Markdown file's first heading, if it begins
    with one ("# Prayers before Mass"), else the file's name."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if _is_markdown(path):
        try:
            first = next((l.strip() for l in _read_text(path).splitlines() if l.strip()), "")
        except OSError:
            first = ""
        m = re.match(r"^#\s+(.+?)\s*#*$", first)
        if m:
            return m.group(1)
    return stem


def custom_part(key, path):
    # A part of its own, its title as a heading where it begins (no title page).
    return Part(key, custom_title(path), "", compact=True)


_INLINE = re.compile(r"(\*\*[^*]+?\*\*|(?<!\w)__[^_]+?__(?!\w)|\*[^*\s][^*]*?\*|(?<!\w)_[^_\s][^_]*?_(?!\w)"
                     r"|`[^`]+`|!?\[[^\]]*\]\([^)]*\))")


def _md_runs(text, italic=False):
    """A line of Markdown's inline marks as runs: **bold**, *italic*, `code`, [links]."""
    runs = []
    for tok in _INLINE.split(text):
        if not tok:
            continue
        if tok.startswith(("**", "__")) and len(tok) > 4:
            runs.append(dooffice.Run(text=tok[2:-2], bold=True, italic=italic))
        elif tok[0] in "*_" and len(tok) > 2 and tok[-1] == tok[0]:
            runs.append(dooffice.Run(text=tok[1:-1], italic=not italic))
        elif tok.startswith("`") and len(tok) > 2:
            runs.append(dooffice.Run(text=tok[1:-1], italic=italic))
        elif re.fullmatch(r"!?\[[^\]]*\]\([^)]*\)", tok):
            runs.append(dooffice.Run(text=re.match(r"!?\[([^\]]*)\]", tok).group(1), italic=italic))
        else:
            runs.append(dooffice.Run(text=tok, italic=italic))
    return runs


def _line(runs, prose=False):
    line = dooffice.Line(runs=runs)
    line.prose = prose  # a paragraph: no hanging indent (dopdf Writer.single)
    return line


def custom_lines(path, title=None):
    """The lines of a .txt or .md file, as the book sets them."""
    md = _is_markdown(path)
    out, para = [], []

    def flush():
        if para:
            out.append(_line(_md_runs(" ".join(para)), prose=True))
            para.clear()

    def blank():
        flush()
        if out and not out[-1].is_blank:
            out.append(_line([], prose=True))  # a whole line's space (dopdf Writer.single)

    for raw in _read_text(path).splitlines():
        s = raw.rstrip().replace("\t", "    ")
        st = s.strip()
        if not st:
            blank()
            continue
        if not md:  # plain text: each line as it is written
            out.append(_line([dooffice.Run(text=s)]))
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", st)
        if m:
            flush()
            if not out and len(m.group(1)) == 1 and m.group(2) == title:
                continue  # the part's title, set as its heading already
            out.append(_line([dooffice.Run(text=m.group(2), size="heading")]))
            continue
        if re.fullmatch(r"(-\s*){3,}|(\*\s*){3,}|(_\s*){3,}", st):
            blank()
            continue
        m = re.match(r"^[-*+]\s+(.*)", st)
        if m:
            flush()
            out.append(_line([dooffice.Run(text="• ")] + _md_runs(m.group(1))))
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)", st)
        if m:
            flush()
            out.append(_line([dooffice.Run(text=m.group(1) + ". ")] + _md_runs(m.group(2))))
            continue
        m = re.match(r"^>\s?(.*)", st)
        if m:
            flush()
            out.append(_line(_md_runs(m.group(1), italic=True), prose=True))
            continue
        para.append(st)
    flush()
    while out and out[-1].is_blank:
        out.pop()
    while out and out[0].is_blank:
        out.pop(0)
    return out


def custom_offices(key, path):
    """The custom part of `path` as an office: its text, or its PDF's pages."""
    if not path or not os.path.isfile(path):
        raise ValueError("the file of a custom part is missing: %s" % (path or "(none chosen)"))
    if not path.lower().endswith(CUSTOM_KINDS):
        raise ValueError("a custom part takes a .txt, .md or .pdf file: %s" % path)
    part = custom_part(key, path)
    if path.lower().endswith(".pdf"):
        pages = len(PdfReader(path).pages)
        return [Office(part=part, key=key, title=part.la, title2=part.la, untitled=True, part_start=True,
                       include=(path, pages))]
    section = dooffice.Section(columns=[custom_lines(path, part.la)])
    return [Office(part=part, key=key, title=part.la, title2=part.la, untitled=True, part_start=True,
                   hours=[dooffice.Hour(key="", heading="", sections=[section])])]


def _stage_includes(offices, directory):
    """Copy each custom PDF beside the volume's Typst source, which can only
    read files there; the office then names its copy."""
    for o in offices:
        if o.include and os.path.isabs(o.include[0]):
            name = "custom-%s.pdf" % o.key
            shutil.copyfile(o.include[0], os.path.join(directory, name))
            o.include = (name, o.include[1])


def _book_parts(parts, lang1, customs):
    return [custom_part(p, customs.get(p)) if is_custom(p) else book_part(p, lang1) for p in parts]


# The breviary window's preview: an hour set as the Psalter sets it, on a
# left-hand and a right-hand page, so the margins on both sides show.
PREVIEW_DATE = "2026-10-04"  # a Sunday after Pentecost: the Psalter's own Lauds
PREVIEW_HOUR = "Laudes"


def preview_text(engine, version, lang1, lang2):
    """The engine's text for the preview (a Day), to keep while only the
    type or spacing change."""
    import datetime as dt
    return engine.day(dooffice.Request(date=dt.date.fromisoformat(PREVIEW_DATE), hour=PREVIEW_HOUR,
                                       version=version, lang1=lang1, lang2=lang2))


def preview_pages(day, lang1, lang2, layout, typst, workdir, ppi=60):
    """PNG files of the book's pages 2 and 3 holding that hour, as the book
    would set them with this layout."""
    english1 = dooffice.family(lang1) == "English"
    hours = copy.deepcopy(day.hours)
    for hour in hours:
        hour.heading = hour.heading or dopsalter.HOUR_NAMES[PREVIEW_HOUR][1 if english1 else 0]
    office = Office(part=book_part("psalterium", lang1), key="preview",
                    title="Sunday" if english1 else "Dominica", title2="Dominica" if english1 else "Sunday",
                    hours=hours)
    dotranslate.translate_days([office], lang1, lang2)
    shorten_offices([office], keep_first=False)
    run_in_headings([office])
    meta = dopdf.Meta(version=day.version, lang1=lang1, lang2=lang2)
    meta.parts, meta.ref_pages = [book_part("psalterium", lang1)], {}
    os.makedirs(workdir, exist_ok=True)
    for old in os.listdir(workdir):
        if old.startswith("page-") and old.endswith(".png"):
            os.remove(os.path.join(workdir, old))
    typ = os.path.join(workdir, "preview.typ")
    with open(typ, "w", encoding="utf-8") as fh:
        fh.write(BookWriter(layout, meta).document([office], start_page=2))
    dopdf._run([typst, "compile", typ, os.path.join(workdir, "page-{p}.png"),
                "--ppi", str(int(ppi)), "--pages", "1-2"])
    return [os.path.join(workdir, f) for f in sorted(os.listdir(workdir))
            if f.startswith("page-") and f.endswith(".png")]


# --------------------------------------------------------------------------


def _default_dumper():
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "engine_dump.pl"),
              os.path.join(here, "dist", "DivinumOfficium", "engine_dump.pl")):
        if os.path.isfile(c):
            return c
    return None


def main(argv=None, web=None, perl=None, typst=None, dumper=None, perl_libs=()):
    ap = argparse.ArgumentParser(prog="--make-breviary" if web else None,
                                 description="Make a breviary PDF: the Office arranged by part.")
    ap.add_argument("--parts", default=",".join(DEFAULT_ORDER),
                    help="comma list, in order, of: %s" % ",".join(PARTS))
    ap.add_argument("--version", default="Rubrics 1960 - 1960")
    ap.add_argument("--lang1", default="Latin")
    ap.add_argument("--lang2", default="English", help="same as --lang1 for one column")
    ap.add_argument("--paper", default="Letter (8.5 × 11 in)", choices=list(dopdf.PAPERS))
    ap.add_argument("--size", type=float, default=10.0)
    ap.add_argument("--black", action="store_true")
    ap.add_argument("--new-page-per-office", action="store_true")
    ap.add_argument("--two-columns", action="store_true",
                    help="one language (--lang2 the same as --lang1): the text in two columns")
    ap.add_argument("--custom", action="append", metavar="FILE",
                    help="a custom part (.txt, .md or .pdf): custom1, custom2 ... in the order "
                         "given; put them in --parts where they go, else they come last")
    ap.add_argument("--blank-front", type=int, default=0, metavar="N",
                    help="blank pages before the title page of each book or volume (in pairs)")
    ap.add_argument("--blank-back", type=int, default=0, metavar="N",
                    help="blank pages after the last page of each book or volume")
    dopdf.add_type_arguments(ap)
    ap.add_argument("--no-alternatives", action="store_true",
                    help="leave out the alternative lessons (a shorter Common)")
    ap.add_argument("--full-texts", action="store_true",
                    help="print a recurring text in full each time, not by reference")
    ap.add_argument("--volumes", type=int, choices=sorted(dovolumes.DIVISIONS), metavar="N",
                    help="make N volumes (2-4), divided as usual for N (see --divide)")
    ap.add_argument("--divide", metavar="KEYS",
                    help="where the second, third ... volumes begin, a comma list of: %s"
                         % ",".join(k for k in dovolumes.SEAM_ORDER if k != "advent"))
    ap.add_argument("-o", "--output", default="breviary.pdf",
                    help="the PDF, or with volumes the name each volume's is made from")
    ap.add_argument("--keep-source", metavar="DIR")
    args = ap.parse_args(argv)
    seams = args.divide.split(",") if args.divide else dovolumes.DIVISIONS.get(args.volumes or 1, [])
    customs = {"custom%d" % (i + 1): os.path.abspath(f) for i, f in enumerate(args.custom or [])}
    parts = args.parts.split(",")
    parts += [k for k in customs if k not in parts]

    if not (web and perl):
        web, perl = dopdf._default_paths()
        perl_libs = perl_libs or dopdf._default_libs(perl)
    typst = typst or dopdf._default_typst()
    dumper = dumper or _default_dumper()
    if not (typst and dumper):
        sys.exit("typst.exe or engine_dump.pl not found")
    layout = dopdf.Layout(paper=args.paper, font_size=args.size, red_in_print=not args.black,
                          new_page_per_day=args.new_page_per_office, two_columns=args.two_columns,
                          blank_front=args.blank_front, blank_back=args.blank_back,
                          **dopdf.type_options(args))

    def progress(stage, done, total):
        sys.stderr.write("\r%-8s %d/%d   " % (stage, done, total))

    if seams:
        problem = dovolumes.check(seams)
        if problem:
            sys.exit(problem)
        books = build_volumes(parts, args.version, args.lang1, args.lang2, layout,
                              args.output, perl, dumper, web, typst, seams, progress=progress,
                              workdir=args.keep_source, keep_source=bool(args.keep_source),
                              alternatives=not args.no_alternatives, perl_libs=perl_libs,
                              references=not args.full_texts, customs=customs)
        for path, offices in books:
            sys.stderr.write("\nwrote %s (%d offices)" % (path, len(offices)))
        sys.stderr.write("\n")
        return 0
    offices = build(parts, args.version, args.lang1, args.lang2, layout,
                    args.output, perl, dumper, web, typst, progress=progress,
                    workdir=args.keep_source, keep_source=bool(args.keep_source),
                    alternatives=not args.no_alternatives, perl_libs=perl_libs,
                    references=not args.full_texts, customs=customs)
    sys.stderr.write("\nwrote %s (%d offices)\n" % (args.output, len(offices)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
