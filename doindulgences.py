"""
"On Indulgences": a few pages, in English, on what the Church teaches and
lays down about indulgences -- what one is, its kinds, the conditions for
gaining it, the kinds the older books name, the texts of this book that
carry one (with their pages here), and the documents, with their dates.

The norms are those of the Apostolic Penitentiary's Enchiridion
Indulgentiarum, fourth edition (16 July 1999), with the canons of both Codes
and Paul VI's Indulgentiarum doctrina (1967) beside them; the grants for the
texts of the Office are that Enchiridion's (its "concessiones", by number).
The grants of 1933 for the prayers before and after the Office are the
breviary's own rubric (Psalterium/Common/Rubricae, [Ante] and [Post]).

placeholder() makes the part's office where it stands; fill() (from
dobreviary.finish) writes it once the book's offices are final, so that each
text it names has the page where this book (or this volume) prints it.
dointro.markup sets it.
"""

import itertools
import re
import unicodedata

import doindex

TITLE = ("De Indulgentiis", "On Indulgences")
_PSALM = doindex._PSALM


def placeholder(part, office_cls, english):
    return [office_cls(part=part, key="indulgentiae", title=TITLE[1] if english else TITLE[0],
                       title2=TITLE[0] if english else TITLE[1], untitled=True,
                       table=("indulg", {"paras": []}))]


def _norm(text):
    """A line's letters, lower case, without accents: "salve regina mater"."""
    text = text.replace("æ", "ae").replace("Æ", "ae").replace("œ", "oe").replace("Œ", "oe")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # "Magníficat ✠ * ánima mea": the signs gone, one space between words.
    return " ".join(re.sub(r"[^a-z]+", " ", text.lower()).split())


def _find(body, test, mark, ids):
    """The marker of the first line printed in full that test(line) accepts,
    in any column; None if the book (or volume) has none."""
    for o in body:
        for hour in o.hours:
            for si, section in enumerate(hour.sections):
                for c, col in enumerate(section.columns):
                    for j, line in enumerate(col):
                        if line.is_blank or getattr(line, "ref", None):
                            continue
                        if test(line):
                            return mark(hour, si, j, ids, c)
    return None


def _starts(*openings):
    def test(line):
        t = _norm(doindex._plain(line))
        return any(t.startswith(w) for w in openings)
    return test


def _psalm(n):
    def test(line):
        m = (line.is_heading or line.is_rubric) and _PSALM.match(line.text.strip())
        return bool(m) and int(m.group(1)) == n
    return test


def fill(offices, mark, label):
    """Write the part among `offices` (the book or volume, final). mark(hour,
    index, j, ids, col) gives a printed line a marker and returns it;
    label(office) gives an office's heading one."""
    part = next((o for o in offices if o.table is not None and o.table[0] == "indulg"), None)
    if part is None:
        return
    body = [o for o in offices if o.rows is None and o.table is None and not o.include
            and not o.part.key.startswith("custom")]
    by_key = {o.key: o for o in body}
    ids = itertools.count(200000)  # apart from the references' and the indexes' (100000 on)

    def at(ident):
        """", p. 56" where the book prints it; nothing where it does not."""
        return [", ", "p.", ("page", ident)] if ident else []

    def office(key):
        o = by_key.get(key)
        return label(o) if o is not None else None

    te_deum = _find(body, _starts("te deum laudamus", "we praise thee o god"), mark, ids)
    veni = _find(body, _starts("veni creator spiritus", "come o creator spirit", "come holy ghost creator"),
                 mark, ids)
    magnificat = _find(body, _starts("magnificat anima mea", "my soul doth magnify"), mark, ids)
    salve = _find(body, _starts("salve regina mater misericordiae", "hail holy queen"), mark, ids)
    regina = _find(body, _starts("regina caeli laetare", "queen of heaven rejoice"), mark, ids)
    ps50, ps129 = _find(body, _psalm(50), mark, ids), _find(body, _psalm(129), mark, ids)
    credo, ante, post = office("prayer-credo"), office("prayer-ante"), office("prayer-post")

    def hour_begins(key):
        """An Hour's first printed line: the Office of the Dead's first Hour's
        heading is the part's title page (an "ex C9" gives the part)."""
        o = by_key.get(key)
        for hour in (o.hours if o is not None else ()):
            for si, section in enumerate(hour.sections):
                j = next((j for j, line in enumerate(section.columns[0]) if not line.is_blank), None)
                if j is not None:
                    return mark(hour, si, j, ids, 0)
        return None

    lauds, vespers = hour_begins("def-Laudes"), hour_begins("def-Vespera")

    paras = []
    paras.append(("para", [
        "An indulgence is the remission before God of the temporal punishment due to sins whose "
        "guilt is already forgiven. One who is rightly disposed gains it, under the conditions the "
        "Church has laid down, through the Church, which as the minister of the Redemption "
        "dispenses with authority the treasury of the satisfactions of Christ and the saints. An "
        "indulgence does not forgive sin, which is forgiven by contrition and in the sacrament of "
        "Penance; still less is it leave to sin."]))
    paras.append(("note", ["EI norm 1; CIC 1983 can. 992; CIC 1917 can. 911; Catechism of the "
                           "Catholic Church, nn. 1471–1479."]))

    paras.append(("head", "The teaching of the Church"))
    paras.append(("para", [
        "Sin, though forgiven, may leave a debt of temporal punishment, to be paid in this life or "
        "in Purgatory. The superabundant merits of Christ, with those of the Blessed Virgin and of "
        "the saints, are a treasury entrusted to the Church (Clement VI, 1343). The Church has from "
        "Christ the power of granting indulgences, and their use is most salutary to Christian "
        "people; the Council of Trent anathematized those who call them useless or deny the "
        "Church's power to grant them, and at the same time bade that they be granted with "
        "moderation and that all evil gain from them be done away with (Session XXV, 1563)."]))

    paras.append(("head", "Plenary and partial"))
    paras.append(("item", [
        "A plenary indulgence remits the whole of the temporal punishment due to sin; a partial "
        "indulgence, part of it (EI norm 2; CIC 1983 can. 993)."]))
    paras.append(("item", [
        "One who does with a contrite heart a work that carries a partial indulgence receives, "
        "besides the remission the work itself earns, as much again through the Church (EI norm 4)."]))
    paras.append(("item", [
        "A plenary indulgence can be gained only once a day, and besides at the hour of death; a "
        "partial indulgence, more than once (EI norm 18)."]))
    paras.append(("item", [
        "Any indulgence may be gained for oneself or applied to the dead by way of suffrage, but not "
        "to another of the living (EI norm 3; CIC 1983 can. 994; CIC 1917 can. 930). Some are "
        "granted for the dead alone."]))

    paras.append(("head", "The conditions"))
    paras.append(("item", [
        "To gain any indulgence one must be baptized, not excommunicated, and in the state of grace "
        "at least when the work is completed; one must intend, at least in general, to gain it, and "
        "do the work at the time and in the way the grant lays down (EI norm 17; CIC 1983 can. 996; "
        "CIC 1917 can. 925)."]))
    paras.append(("item", [
        "For a plenary indulgence, besides the work: sacramental confession, Holy Communion, and "
        "prayer for the intention of the Sovereign Pontiff (an Our Father and a Hail Mary suffice, "
        "or any other prayer), with all attachment to sin, even venial, put away. The three may be "
        "fulfilled some days before or after the work; one confession serves for several plenary "
        "indulgences, but each needs its own Communion and prayer. Where anything is wanting, the "
        "indulgence is partial (EI norm 20)."]))
    paras.append(("item", [
        "An indulgence is not gained by a work already owed by law or precept, unless the grant "
        "says so (EI norm 21; CIC 1917 can. 932). But a work enjoined as a sacramental penance, if "
        "it carries an indulgence, both fulfils the penance and gains the indulgence; and religious "
        "gain the indulgences of the prayers their rule binds them to (EI norm 21 §§ 2–3)."]))
    paras.append(("item", [
        "An indulgenced prayer may be said in any approved translation, in alternation with "
        "another, or followed in the mind while another says it (EI norms 22–23). A confessor may "
        "commute the work or the conditions for one lawfully hindered from them (EI norm 24)."]))

    paras.append(("head", "Kinds named in the older books"))
    days = ["Days and years. Until 1967 a partial indulgence was reckoned in days, quarantines "
            "(forty days) or years"]
    if ante or post:
        days += [", as the rubrics before and after the Office in this book reckon theirs"]
        days += at(ante or post)
    days += [". The measure was that of the canonical penance of the early Church which the "
             "indulgence stood for, never a length of time in Purgatory. Paul VI did away with it: a "
             "partial indulgence is now called that and nothing more (ID norm 4)."]
    paras.append(("item", days))
    paras.append(("item", [
        "Personal, real and local: attached to persons, to objects (a crucifix, a rosary, a medal) "
        "or to places (a church, an altar). The division was abolished in 1967: an indulgence "
        "belongs to an act of the faithful, even where it is joined to an object or a place "
        "(ID norm 12)."]))
    paras.append(("item", [
        "Toties quoties: gained as often as the work was done, as on the Portiuncula (2 August) "
        "and on All Souls' Day. Since 1967 these are plenary indulgences like the rest, gained once "
        "a day by a visit to the church with an Our Father and the Creed (ID norms 6, 15–16, 19)."]))
    paras.append(("item", [
        "Apostolic indulgences: attached to the pious objects the Pope, or a priest with his "
        "faculty, has blessed. Now the devout use of a duly blessed crucifix or cross, rosary, "
        "scapular or medal carries a partial indulgence; if the Pope or a bishop blessed it, also "
        "a plenary indulgence on the feast of SS. Peter and Paul, with a profession of faith "
        "(ID norm 17; EI norm 15)."]))
    paras.append(("item", [
        "The Jubilee: the plenary indulgence of the Holy Year, first proclaimed by Boniface VIII "
        "for the year 1300."]))
    paras.append(("item", [
        "At the hour of death: the priest who brings the sacraments to one in danger of death gives "
        "the Apostolic Blessing, with a plenary indulgence. Where no priest can be had, the Church "
        "grants the plenary indulgence at the point of death to one rightly disposed who was in "
        "the habit of saying some prayers in life, and herself supplies the three conditions; the "
        "use of a crucifix is commended (EI conc. 12)."]))
    paras.append(("item", [
        "The Papal Blessing: a plenary indulgence for receiving devoutly the blessing Urbi et Orbi, "
        "or that a bishop gives three times a year, even by following it live on radio or "
        "television (EI conc. 4; norm 7)."]))

    paras.append(("head", "In this book"))
    paras.append(("para", ["The Enchiridion grants indulgences for these texts, among others:"]))
    if te_deum:
        paras.append(("item", ["The Te Deum"] + at(te_deum) + [
            ": partial; plenary for taking part in its recitation or singing on the last day of the "
            "year, in thanksgiving for the year (EI conc. 26)."]))
    if veni:
        paras.append(("item", ["The Veni, Creator Spiritus"] + at(veni) + [
            ": partial; plenary for taking part in its recitation or singing on 1 January, for "
            "God's help through the year, or on Pentecost (EI conc. 26)."]))
    if magnificat:
        paras.append(("item", ["The Magnificat"] + at(magnificat) + [": partial (EI conc. 17 § 2)."]))
    marian = ["The Salve Regina"] + at(salve) + [
        ", and any approved prayer to Our Lady, as the antiphons said after Compline: partial. The "
        "Regina caeli"] + at(regina) + [
        " with its prayer, said in Paschaltide at dawn, noon or evening in place of the Angelus: "
        "partial (EI conc. 17 § 2)."]
    paras.append(("item", marian))
    paras.append(("item", ["The Miserere, Psalm 50"] + at(ps50) + [", the De profundis, Psalm 129"]
                  + at(ps129) + [", and the gradual and penitential psalms, said as an act of "
                                 "contrition, above all before confession: partial (EI conc. 9)."]))
    if credo:
        paras.append(("item", ["The Apostles' Creed"] + at(credo) + [", or the Nicene Creed: "
                                                                    "partial (EI conc. 28 § 2)."]))
    paras.append(("item", ["The sign of the cross with its words, In the name of the Father…: "
                           "partial (EI conc. 28 § 2)."]))
    if lauds or vespers:
        dead = ["Lauds of the Office of the Dead"] + at(lauds) + [", or its Vespers"] + at(vespers) + \
            [", or the Requiem aeternam: partial, for the souls in Purgatory alone. A plenary "
                 "indulgence, for them alone, is gained by visiting a cemetery and praying for the "
                 "dead on any day from 1 to 8 November, and by visiting a church with an Our Father "
                 "and the Creed on All Souls' Day (EI conc. 29)."]
        paras.append(("item", dead))
    if ante or post:
        office_prayers = ["The prayers before and after the Office, Aperi, Domine"] + at(ante) + \
            [" and Sacrosanctæ"] + at(post) + [
            ": by the rubrics printed with them, three years each (Pius XI, 17 November and "
            "1 December 1933); and to clerics in sacred orders who say the Sacrosanctæ devoutly "
            "after the Office, Leo X pardoned the faults committed in saying it through human "
            "frailty. These are grants of the older reckoning."]
        paras.append(("item", office_prayers))

    paras.append(("head", "The documents"))
    # Each document: (before its title, its title, after), the title in italics.
    for before, title, after in (
            ("Boniface VIII, bull ", "Antiquorum habet fida relatio", ", 22 February 1300: the first Holy Year."),
            ("Clement VI, bull ", "Unigenitus Dei Filius", ", 27 January 1343: the treasury of the Church."),
            ("Leo X, decree ", "Cum postquam", ", 9 November 1518: what the Church teaches of indulgences "
             "(DH 1447–1449)."),
            ("Leo X, bull ", "Exsurge Domine", ", 15 June 1520: Luther's propositions on indulgences "
             "condemned (propositions 17–22; DH 1467–1472)."),
            ("Council of Trent, Session XXV, ", "Decree on Indulgences", ", 4 December 1563 (DH 1835)."),
            ("Pius VI, constitution ", "Auctorem fidei", ", 28 August 1794: the Synod of Pistoia's "
             "propositions on indulgences condemned (propositions 40–43; DH 2640–2643)."),
            ("", "Code of Canon Law", " (CIC), 1917: canons 911–936."),
            ("Sacred Penitentiary, ", "Preces et pia opera indulgentiis ditata", " (the Raccolta), 1938, "
             "the official collection before 1967."),
            ("Paul VI, apostolic constitution ", "Indulgentiarum doctrina", " (ID), 1 January 1967 "
             "(AAS 59 [1967] 5–24)."),
            ("Apostolic Penitentiary, ", "Enchiridion Indulgentiarum", " (EI): 1968, 1986, and the fourth "
             "edition, 16 July 1999 (in English, Manual of Indulgences, 2006)."),
            ("", "Code of Canon Law", " (CIC), 1983: canons 992–997."),
            ("", "Catechism of the Catholic Church", ", 1992: nn. 1471–1479.")):
        paras.append(("item", [before, ("i", title), after]))
    paras.append(("note", ["DH: Denzinger–Hünermann, Enchiridion symbolorum. The norms and "
                           "grants of the Enchiridion are given as the fourth edition numbers them."]))
    part.table = ("indulg", {"paras": paras})
