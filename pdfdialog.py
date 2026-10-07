"""
"Make a PDF" window for the offline app.

Everything here is presentation: choosing days, hours, version, languages and
page options, then running dopdf.export() on a worker thread with a progress
bar. The office text itself always comes from the project's engine.
"""

import calendar
import datetime as dt
import json
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dooffice
import dopdf
import doui
import dovolumes

SETTINGS_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "DivinumOfficium",
    "pdf-settings.json",
)

# Rough pages per day for each hour: two languages, Letter, 10 pt (measured
# on a month of the 1960 office: 1,051 pages for 31 days). Only used for the
# estimate shown before starting.
PAGES_PER_HOUR = {
    "Matutinum": 11.0, "Laudes": 5.0, "Prima": 4.0, "Tertia": 2.3,
    "Sexta": 2.3, "Nona": 2.3, "Vespera": 5.0, "Completorium": 2.7,
}
PAPER_FACTOR = {
    "Letter (8.5 × 11 in)": 1.0, "A4": 0.93, "Half letter (5.5 × 8.5 in)": 2.3,
    "A5": 2.2, "Pocket breviary (4.5 × 7 in)": 3.3,
}
ONE_COLUMN = "— none (one column) —"


def advent_sunday(year):
    """First Sunday of Advent: the Sunday on or before 3 December."""
    dec3 = dt.date(year, 12, 3)
    return dec3 - dt.timedelta(days=(dec3.weekday() + 1) % 7)


def parse_date(text):
    text = text.strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:  # 9/23/2026, as the website writes dates
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def safe_filename(text):
    return re.sub(r'[<>:"/\\|?*]+', "", text).strip()


# Faces that set a breviary well, offered first when this PC has them; every
# other text font follows. (Typst's own list: it is Typst that must find them.)
BOOK_FONTS = ("Cambria", "Constantia", "Georgia", "Palatino Linotype", "Book Antiqua", "Garamond",
              "Goudy Old Style", "Baskerville Old Face", "Centaur", "Perpetua", "Bookman Old Style",
              "Century Schoolbook", "Lucida Bright", "High Tower Text", "Sitka Text",
              "Times New Roman", "Libertinus Serif", "New Computer Modern", "Calibri", "Segoe UI")
FONT_DIVIDER = "──────────"
# The breviary window's sample of the chosen font: a heading as the book sets
# it, the signs, the accents and ligatures the Latin uses.
PREVIEW_SIZE = 12
PREVIEW_TEXT = (("heading", "Ad Laudes"),
                ("text", "℣. Deus ✠ in adjutórium meum inténde."),
                ("text", "℟. Dómine, ad adjuvándum me festína."),
                ("text", "Æterna Christi múnera, et cœli gáudia."))
_NOT_TEXT = re.compile(r"Symbol|Wingdings|Webdings|Marlett|MDL2|Emoji|Icons|Math|MT Extra|"
                       r"Specialty|Outlook|Historic|Bookshelf|OCR", re.I)
_fonts = None


def font_families(typst):
    """The font families Typst can use, the book faces first."""
    global _fonts
    if _fonts is None:
        found = []
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            out = subprocess.run([typst, "fonts"], capture_output=True, timeout=30,
                                 creationflags=flags).stdout.decode("utf-8", "replace")
            found = [f.strip() for f in out.splitlines() if f.strip()]
        except (OSError, subprocess.SubprocessError, TypeError):
            pass
        have = set(found)
        top = [f for f in BOOK_FONTS if f in have] or [dopdf.Layout.font]
        rest = sorted((f for f in found if f not in top and f.isascii() and not _NOT_TEXT.search(f)),
                      key=str.lower)
        _fonts = top + ([FONT_DIVIDER] + rest if rest else [])
    return _fonts


RUBRIC_COLOURS = (("Breviary red", "#a31621"), ("Bright red", "#cc1111"), ("Crimson", "#b0103a"),
                  ("Burgundy", "#6d1a26"), ("Rose", "#b03a6e"), ("Blue", "#1f4e9c"),
                  ("Green", "#2e6b30"), ("Gold", "#8a6d12"))
TEXT_COLOURS = (("Black", "#000000"), ("Soft black", "#222222"), ("Dark grey", "#454545"),
                ("Sepia", "#4a3526"), ("Navy", "#1b2a4a"))
CUSTOM_COLOUR = "Custom…"


class ColourChoice:
    """A named colour from a short list, or any colour from the Windows picker."""

    def __init__(self, parent, win, presets, value):
        self.parent, self.win, self.presets = parent, win, presets
        self.value = presets[0][1]
        self.name = tk.StringVar()
        self.box = ttk.Combobox(parent, textvariable=self.name, state="readonly", width=17,
                                values=[n for n, _ in presets] + [CUSTOM_COLOUR])
        self.swatch = tk.Label(parent, width=3, relief="solid", borderwidth=1)
        self.swatch.bind("<Button-1>", lambda _e: self.pick())
        self.box.bind("<<ComboboxSelected>>", self._chosen)
        self.set(value)

    def pack(self, gap=0):
        self.box.pack(side="left")
        self.swatch.pack(side="left", padx=(6, gap))

    def set(self, value):
        self.value = dopdf.colour(value, self.presets[0][1]).lower()
        name = next((n for n, h in self.presets if h.lower() == self.value), None)
        self.name.set(name or "Custom (%s)" % self.value)
        self.swatch.config(background=self.value)

    def pick(self):
        if str(self.box.cget("state")) == "disabled":
            return
        from tkinter import colorchooser
        _rgb, chosen = colorchooser.askcolor(color=self.value, parent=self.win,
                                             title="Choose a colour")
        self.set(chosen or self.value)

    def _chosen(self, _e=None):
        name = self.name.get()
        if name == CUSTOM_COLOUR:
            self.pick()
        else:
            self.set(dict(self.presets).get(name, self.value))

    def enable(self, on):
        self.box.config(state="readonly" if on else "disabled")
        self.swatch.config(background=self.value if on else "#d0d0d0")


class PdfDialog:
    settings_file = SETTINGS_FILE
    title = "Make a PDF of the Office"

    def __init__(self, parent, web_root, perl, perl_libs, typst):
        self.parent = parent
        self.engine = dooffice.Engine(web_root, perl, perl_libs)
        self.typst = typst
        self.choices = dooffice.read_choices(web_root)
        self.versions = self.choices["versions"]
        self.languages = [c for c in self.choices["languages"]
                          if c.value not in dopdf.NOT_FOR_PDF]
        self.dioceses = self.choices["dioceses"]
        self.votives = self.choices["votives"]
        self.events = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()
        self.last_pdf = None

        # Built out of sight and shown finished (doui.present): drawn in view,
        # the window filled in a widget at a time for a second.
        doui.busy(parent, True)
        self.win = tk.Toplevel(parent)
        self.win.withdraw()
        self.win.title(self.title)
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        try:
            doui.install(self.win)  # the theme first, so the window is built in it
            self._build()
            doui.install(self.win)  # and its plain Tk parts coloured
            self._load_settings()
            self.update_summary()
        finally:
            doui.busy(parent, False)
        doui.present(self.win, over=parent)
        self.win.after(120, self._drain)

    # -- layout --------------------------------------------------------------

    def _body(self):
        """Where the choices go: a frame that scrolls if the screen is too small
        for the window (doui.ScrollArea), with the footer below it, always in
        view. Returns the frame."""
        self.foot_box = ttk.Frame(self.win, style="Window.TFrame", padding=(12, 0, 12, 12))
        self.foot_box.pack(side="bottom", fill="x")
        self.scroll = doui.ScrollArea(self.win, padding=(12, 12, 12, 0))
        self.scroll.pack(fill="both", expand=True)
        return self.scroll.inner

    def _build(self):
        pad = {"padx": 10, "pady": 3}
        outer = self._body()

        # Days
        days = ttk.LabelFrame(outer, text="Days", padding=8)
        days.grid(row=0, column=0, sticky="nsew", **pad)
        today = dt.date.today()
        self.mode = tk.StringVar(value="day")
        self.day = tk.StringVar(value=today.isoformat())
        self.first = tk.StringVar(value=today.isoformat())
        self.last = tk.StringVar(value=(today + dt.timedelta(days=6)).isoformat())
        self.month = tk.StringVar(value=calendar.month_name[today.month])
        self.month_year = tk.StringVar(value=str(today.year))
        self.lit_year = tk.StringVar(value=str(today.year if today >= advent_sunday(today.year) else today.year - 1))
        self.cal_year = tk.StringVar(value=str(today.year))

        def row(r, value, label):
            ttk.Radiobutton(days, text=label, value=value, variable=self.mode,
                            command=self.update_summary).grid(row=r, column=0, sticky="w")

        row(0, "day", "One day")
        ttk.Entry(days, textvariable=self.day, width=12).grid(row=0, column=1, sticky="w")
        row(1, "range", "From")
        rng = ttk.Frame(days)
        rng.grid(row=1, column=1, sticky="w")
        ttk.Entry(rng, textvariable=self.first, width=12).pack(side="left")
        ttk.Label(rng, text=" to ").pack(side="left")
        ttk.Entry(rng, textvariable=self.last, width=12).pack(side="left")
        row(2, "month", "Month")
        mon = ttk.Frame(days)
        mon.grid(row=2, column=1, sticky="w")
        ttk.Combobox(mon, textvariable=self.month, state="readonly", width=11,
                     values=list(calendar.month_name)[1:]).pack(side="left")
        ttk.Spinbox(mon, textvariable=self.month_year, from_=1600, to=2400, width=6,
                    command=self.update_summary).pack(side="left", padx=(6, 0))
        row(3, "lityear", "Liturgical year from Advent")
        ttk.Spinbox(days, textvariable=self.lit_year, from_=1600, to=2400, width=6,
                    command=self.update_summary).grid(row=3, column=1, sticky="w")
        row(4, "calyear", "Calendar year")
        ttk.Spinbox(days, textvariable=self.cal_year, from_=1600, to=2400, width=6,
                    command=self.update_summary).grid(row=4, column=1, sticky="w")
        self.range_note = ttk.Label(days, style="Muted.TLabel")
        self.range_note.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        for var in (self.day, self.first, self.last, self.month, self.month_year,
                    self.lit_year, self.cal_year):
            var.trace_add("write", lambda *_: self.update_summary())

        # Hours
        hours = ttk.LabelFrame(outer, text="Hours", padding=8)
        hours.grid(row=1, column=0, sticky="nsew", **pad)
        self.hour_vars = {}
        for i, h in enumerate(dooffice.HOURS):
            v = tk.BooleanVar(value=True)
            self.hour_vars[h] = v
            ttk.Checkbutton(hours, text=dooffice.HOUR_LABELS[h], variable=v,
                            command=self.update_summary).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 12))
        presets = ttk.Frame(hours)
        presets.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        for label, pick in (("All", dooffice.HOURS),
                            ("Lauds & Vespers", ("Laudes", "Vespera")),
                            ("Vespers & Compline", ("Vespera", "Completorium")),
                            ("None", ())):
            ttk.Button(presets, text=label, command=lambda p=pick: self.set_hours(p)).pack(side="left", padx=(0, 4))

        # Office
        office = ttk.LabelFrame(outer, text="Office", padding=8)
        office.grid(row=0, column=1, sticky="nsew", **pad)
        self.version = tk.StringVar(value="Rubrics 1960 - 1960")
        self.diocese = tk.StringVar(value=self.dioceses[0].label if self.dioceses else "")
        self.votive = tk.StringVar(value=self.votives[0].label if self.votives else "")
        self.priest = tk.BooleanVar(value=False)
        for r, (label, var, values) in enumerate((
            ("Version", self.version, [c.label for c in self.versions]),
            ("Calendar", self.diocese, [c.label for c in self.dioceses]),
            ("Office", self.votive, [("Of the day" if c.label == "Hodie" else c.label) for c in self.votives]),
        )):
            ttk.Label(office, text=label).grid(row=r, column=0, sticky="w", pady=2)
            cb = ttk.Combobox(office, textvariable=var, state="readonly", width=34, values=values)
            cb.grid(row=r, column=1, sticky="w", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())
        if self.votive.get() == "Hodie":
            self.votive.set("Of the day")
        ttk.Checkbutton(office, text="Said by a priest or deacon", variable=self.priest).grid(
            row=3, column=1, sticky="w", pady=(2, 0))

        # Languages
        langs = ttk.LabelFrame(outer, text="Languages", padding=8)
        langs.grid(row=1, column=1, sticky="nsew", **pad)
        self.lang1 = tk.StringVar(value="Latin")
        self.lang2 = tk.StringVar(value="English")
        labels = [c.label for c in self.languages]
        ttk.Label(langs, text="Left").grid(row=0, column=0, sticky="w")
        c1 = ttk.Combobox(langs, textvariable=self.lang1, state="readonly", width=20, values=labels)
        c1.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(langs, text="Right").grid(row=1, column=0, sticky="w")
        c2 = ttk.Combobox(langs, textvariable=self.lang2, state="readonly", width=20,
                          values=[ONE_COLUMN] + labels)
        c2.grid(row=1, column=1, sticky="w", pady=2)
        for cb in (c1, c2):
            cb.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())

        self._build_page(outer, row=2)
        self._build_type(outer, row=3)
        self._build_footer(self.foot_box)


    def _build_page(self, outer, row, per_page_label="Start each day on a new page",
                    contents_label="Contents (when more than one day)", skip=(), column=0, span=2):
        pad = {"padx": 10, "pady": 3}
        page = self.page_box = ttk.LabelFrame(outer, text="Page", padding=8)
        page.grid(row=row, column=column, columnspan=span, sticky="nsew", **pad)
        self.paper = tk.StringVar(value="Letter (8.5 × 11 in)")
        ttk.Label(page, text="Paper").grid(row=0, column=0, sticky="w")
        cp = ttk.Combobox(page, textvariable=self.paper, state="readonly", width=28, values=list(dopdf.PAPERS))
        cp.grid(row=0, column=1, sticky="w")
        cp.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())
        self.opts = {
            "new_page_per_day": tk.BooleanVar(value=True),
            "new_page_per_hour": tk.BooleanVar(value=False),
            "title_page": tk.BooleanVar(value=True),
            "contents": tk.BooleanVar(value=True),
            "red_in_print": tk.BooleanVar(value=True),
            "mark_ai": tk.BooleanVar(value=True),
            "accessible": tk.BooleanVar(value=False),
        }
        for key in skip:
            self.opts.pop(key, None)
        shown = [(k, t) for k, t in (
            ("new_page_per_day", per_page_label),
            ("new_page_per_hour", "Start each hour on a new page"),
            ("title_page", "Title page"),
            ("contents", contents_label),
            ("red_in_print", "Rubrics in colour"),
            ("mark_ai", "Mark English translated by AI (“AI”)"),
            ("accessible", "Tagged for screen readers (about 3× the size)"),
        ) if k in self.opts]
        for i, (key, label) in enumerate(shown):
            ttk.Checkbutton(page, text=label, variable=self.opts[key],
                            command=self.update_summary).grid(
                row=1 + i // 2, column=(i % 2) * 2, columnspan=2 + (i % 2) * 3, sticky="w", pady=1)


    def _build_type(self, outer, row, preview=False, column=0, span=2):
        """Font, size, alignment and colours (both windows); a sample of the font
        when preview is set."""
        pad = {"padx": 10, "pady": 3}
        box = ttk.LabelFrame(outer, text="Type", padding=8)
        box.grid(row=row, column=column, columnspan=span, sticky="nsew", **pad)

        self.font = tk.StringVar(value=dopdf.Layout.font)
        self.preview = self._build_preview(box) if preview else None
        self.font.trace_add("write", lambda *_: self._show_preview())
        ttk.Label(box, text="Font").grid(row=0, column=0, sticky="w")
        cf = ttk.Combobox(box, textvariable=self.font, state="readonly", width=28, height=24,
                          values=font_families(self.typst))
        cf.grid(row=0, column=1, sticky="w")
        self._font_before = self.font.get()

        def font_chosen(_e):
            if self.font.get() == FONT_DIVIDER:  # the line between the book faces and the rest
                self.font.set(self._font_before)
            self._font_before = self.font.get()

        cf.bind("<<ComboboxSelected>>", font_chosen)
        self.size = tk.StringVar(value="10")
        sz = ttk.Frame(box)
        sz.grid(row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Label(sz, text="Size").pack(side="left", padx=(0, 6))
        ttk.Spinbox(sz, textvariable=self.size, from_=7, to=14, increment=0.5, width=5,
                    command=self.update_summary).pack(side="left")
        ttk.Label(sz, text="pt").pack(side="left", padx=(4, 0))
        self.size.trace_add("write", lambda *_: self.update_summary())

        self.align = tk.StringVar(value="justify")
        ttk.Label(box, text="Alignment").grid(row=1, column=0, sticky="w", pady=(6, 0))
        af = ttk.Frame(box)
        af.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        for value, label in (("justify", "Justified (evenly spread)"), ("left", "Left"),
                             ("right", "Right")):
            ttk.Radiobutton(af, text=label, value=value, variable=self.align).pack(
                side="left", padx=(0, 14))

        ttk.Label(box, text="Colours").grid(row=2, column=0, sticky="w", pady=(6, 0))
        cc = ttk.Frame(box)
        cc.grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(cc, text="Rubrics").pack(side="left", padx=(0, 6))
        self.rubric_colour = ColourChoice(cc, self.win, RUBRIC_COLOURS, dopdf.Layout.rubric_colour)
        self.rubric_colour.pack(gap=18)
        ttk.Label(cc, text="Text").pack(side="left", padx=(0, 6))
        self.text_colour = ColourChoice(cc, self.win, TEXT_COLOURS, dopdf.Layout.text_colour)
        self.text_colour.pack()
        # "Rubrics in colour" off prints them black: their colour is then moot.
        red = self.opts.get("red_in_print")
        if red is not None:
            red.trace_add("write", lambda *_: self.rubric_colour.enable(red.get()))
        self._show_preview()

    def _build_preview(self, box):
        """A few lines of the office in the chosen font, under the type settings."""
        ttk.Label(box, text="Preview").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        # Tk's own font commands: tkinter.font is not in the exe's bundle.
        line = int(self.win.tk.call("font", "metrics", ("Segoe UI", PREVIEW_SIZE), "-linespace"))
        frame = tk.Frame(box, height=int(line * 5.6), width=1, background="white",
                         highlightthickness=1, highlightbackground="#b8b8b8")
        doui.role(frame, "paper")  # a sample of the page: white in either mode
        frame.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        frame.pack_propagate(False)  # a taller or wider face must not resize the window
        text = tk.Text(frame, wrap="word", relief="flat", borderwidth=0, background="white",
                       padx=10, pady=6, cursor="arrow", takefocus=0, highlightthickness=0)
        text.pack(fill="both", expand=True)
        self._preview_installed = {str(f).lower() for f in self.win.tk.splitlist(
            self.win.tk.call("font", "families"))}
        return text

    def _show_preview(self):
        text = getattr(self, "preview", None)
        family = self.font.get()
        if text is None or family == FONT_DIVIDER:
            return
        shown = family if family.lower() in self._preview_installed else "Segoe UI"
        text.config(state="normal")
        text.delete("1.0", "end")
        text.tag_configure("heading", font=(shown, PREVIEW_SIZE, "bold", "italic"))
        text.tag_configure("text", font=(shown, PREVIEW_SIZE))
        text.tag_configure("note", font=("Segoe UI", 9, "italic"), foreground="#666")
        sample = PREVIEW_TEXT
        if shown != family:  # the note takes the heading's place
            text.insert("end", "%s is built into the PDF maker; Windows cannot show it here, "
                               "so this is Segoe UI.\n" % family, "note")
            sample = PREVIEW_TEXT[1:]
        for i, (tag, line) in enumerate(sample):
            text.insert("end", line + ("\n" if i < len(sample) - 1 else ""), tag)
        text.config(state="disabled")

    def _type_settings(self):
        return {"font": self.font.get(), "align": self.align.get(),
                "rubric_colour": self.rubric_colour.value, "text_colour": self.text_colour.value}

    def _load_type_settings(self, s):
        if isinstance(s.get("font"), str) and s["font"] and s["font"] != FONT_DIVIDER:
            self.font.set(s["font"])
            self._font_before = s["font"]
        if s.get("align") in dopdf.ALIGNS:
            self.align.set(s["align"])
        for key, choice in (("rubric_colour", self.rubric_colour), ("text_colour", self.text_colour)):
            if isinstance(s.get(key), str):
                choice.set(s[key])

    def _build_footer(self, parent):
        foot = ttk.Frame(parent, style="Card.TFrame", padding=(12, 6))
        foot.pack(fill="x", padx=10, pady=(10, 0))
        self.estimate = ttk.Label(foot, style="Strong.TLabel")
        self.estimate.pack(anchor="w")
        self.bar = ttk.Progressbar(foot, length=560, mode="determinate")
        self.bar.pack(fill="x", pady=(6, 2))
        self.status = ttk.Label(foot, style="Muted.TLabel")
        self.status.pack(anchor="w")
        btns = ttk.Frame(foot)
        btns.pack(fill="x", pady=(8, 0))
        self.open_btn = ttk.Button(btns, text="Open PDF", command=self.open_pdf, state="disabled")
        self.open_btn.pack(side="left")
        self.folder_btn = ttk.Button(btns, text="Show in folder", command=self.show_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=(6, 0))
        doui.mode_button(btns).pack(side="left", padx=(18, 0))
        self.go_btn = ttk.Button(btns, text="Make PDF…", command=self.start, style="Accent.TButton")
        self.go_btn.pack(side="right")
        self.close_btn = ttk.Button(btns, text="Close", command=self.close)
        self.close_btn.pack(side="right", padx=(0, 6))

    # -- choices -------------------------------------------------------------

    def set_hours(self, pick):
        for h, v in self.hour_vars.items():
            v.set(h in pick)
        self.update_summary()

    def span(self):
        """(first, last) for the chosen days, or raise ValueError with a message."""
        mode = self.mode.get()
        try:
            if mode == "day":
                d = parse_date(self.day.get())
                if not d:
                    raise ValueError("Enter the day as YYYY-MM-DD.")
                return d, d
            if mode == "range":
                a, b = parse_date(self.first.get()), parse_date(self.last.get())
                if not a or not b:
                    raise ValueError("Enter both days as YYYY-MM-DD.")
                if b < a:
                    raise ValueError("The last day is before the first.")
                return a, b
            if mode == "month":
                y = int(self.month_year.get())
                m = list(calendar.month_name).index(self.month.get())
                return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
            if mode == "lityear":
                y = int(self.lit_year.get())
                return advent_sunday(y), advent_sunday(y + 1) - dt.timedelta(days=1)
            y = int(self.cal_year.get())
            return dt.date(y, 1, 1), dt.date(y, 12, 31)
        except (TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).endswith("."):
                raise
            raise ValueError("That is not a valid date.")

    def hours(self):
        return [h for h in dooffice.HOURS if self.hour_vars[h].get()]

    def choice_value(self, items, label, default=""):
        for c in items:
            if c.label == label:
                return c.value
        return default

    def request_options(self):
        votive = self.votive.get()
        votive = "" if votive in ("Of the day", "Hodie") else self.choice_value(self.votives, votive)
        lang1 = self.choice_value(self.languages, self.lang1.get(), "Latin")
        lang2 = lang1 if self.lang2.get() == ONE_COLUMN else self.choice_value(
            self.languages, self.lang2.get(), "English")
        return dict(
            version=self.version.get(),  # the engine resolves display names itself
            lang1=lang1,
            lang2=lang2,
            votive=votive,
            dioecesis=self.choice_value(self.dioceses, self.diocese.get(), "Generale"),
            priest=self.priest.get(),
        )

    def layout(self):
        try:
            size = max(6.0, min(16.0, float(self.size.get())))
        except ValueError:
            size = 10.0
        return dopdf.Layout(paper=self.paper.get(), font_size=size,
                            font=self.font.get() or dopdf.Layout.font, align=self.align.get(),
                            rubric_colour=self.rubric_colour.value,
                            text_colour=self.text_colour.value,
                            **{k: v.get() for k, v in self.opts.items()})

    def update_summary(self):
        try:
            first, last = self.span()
        except ValueError as exc:
            self.range_note.config(text=str(exc), style="Error.TLabel")
            self.estimate.config(text="")
            self.go_btn.config(state="disabled")
            return
        n = (last - first).days + 1
        self.range_note.config(
            text="%s — %s  (%d day%s)" % (dopdf.short_date(first), dopdf.short_date(last), n, "" if n == 1 else "s")
            if n > 1 else dopdf.long_date(first),
            style="Muted.TLabel")
        hours = self.hours()
        if not hours:
            self.estimate.config(text="Choose at least one hour.")
            self.go_btn.config(state="disabled")
            return
        lay = self.layout()
        per_day = sum(PAGES_PER_HOUR[h] for h in hours)
        if self.lang2.get() == ONE_COLUMN:
            per_day *= 0.55
        pages = n * per_day * PAPER_FACTOR.get(lay.paper, 1.0) * (lay.font_size / 10.0) ** 2
        # Calibrated on the built app: a month took 24 s for 1,055 pages (7.2 MB),
        # a liturgical year 6 min 22 s for 12,317 pages (84 MB).
        calls = n if len(hours) == len(dooffice.HOURS) else n * len(hours)
        seconds = calls * (0.45 if len(hours) == len(dooffice.HOURS) else 0.12) + pages * 0.016 + 2
        if seconds < 60:
            when = "under a minute"
        elif seconds < 90:
            when = "about a minute"
        else:
            when = "about %d minutes" % round(seconds / 60)
        mb = pages * (0.021 if lay.accessible else 0.007)
        self.estimate.config(text="About %s pages, %s, roughly %s MB" % (
            "{:,}".format(max(1, int(round(pages, -1 if pages > 50 else 0)))), when,
            "{:,}".format(max(1, int(round(mb))))))
        if not self.worker:
            self.go_btn.config(state="normal")

    # -- settings ------------------------------------------------------------

    def _settings(self):
        return {
            "version": self.version.get(), "diocese": self.diocese.get(),
            "votive": self.votive.get(), "priest": self.priest.get(),
            "lang1": self.lang1.get(), "lang2": self.lang2.get(),
            "paper": self.paper.get(), "size": self.size.get(),
            "hours": self.hours(), "opts": {k: v.get() for k, v in self.opts.items()},
            "folder": os.path.dirname(self.last_pdf) if self.last_pdf else None,
            **self._type_settings(),
        }

    def _load_settings(self):
        try:
            with open(self.settings_file, encoding="utf-8") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            return
        for key, var in (("version", self.version), ("diocese", self.diocese),
                         ("votive", self.votive), ("lang1", self.lang1),
                         ("lang2", self.lang2), ("paper", self.paper), ("size", self.size)):
            if isinstance(s.get(key), str):
                var.set(s[key])
        self.priest.set(bool(s.get("priest")))
        if isinstance(s.get("hours"), list):
            for h, v in self.hour_vars.items():
                v.set(h in s["hours"])
        for k, v in (s.get("opts") or {}).items():
            if k in self.opts:
                self.opts[k].set(bool(v))
        self._load_type_settings(s)
        self.saved_folder = s.get("folder")

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as fh:
                json.dump(self._settings(), fh, indent=1)
        except OSError:
            pass

    # -- running -------------------------------------------------------------

    def default_name(self, first, last, hours):
        if first == last:
            when = first.isoformat()
        elif self.mode.get() == "lityear":
            when = "Liturgical year %d-%d" % (first.year, last.year)
        elif self.mode.get() == "calyear":
            when = str(first.year)
        elif self.mode.get() == "month":
            when = "%s %d" % (calendar.month_name[first.month], first.year)
        else:
            when = "%s to %s" % (first.isoformat(), last.isoformat())
        which = "" if len(hours) == len(dooffice.HOURS) else " " + ", ".join(dooffice.HOUR_LABELS[h] for h in hours)
        return safe_filename("Divinum Officium %s%s (%s).pdf" % (when, which, self.version.get()))

    def start(self):
        try:
            first, last = self.span()
        except ValueError as exc:
            messagebox.showerror("Make a PDF", str(exc), parent=self.win)
            return
        hours = self.hours()
        folder = getattr(self, "saved_folder", None) or os.path.join(os.path.expanduser("~"), "Documents")
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Save the PDF as", defaultextension=".pdf",
            initialdir=folder if os.path.isdir(folder) else os.path.expanduser("~"),
            initialfile=self.default_name(first, last, hours),
            filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.run(path, first, last, hours)

    def run(self, path, first, last, hours):
        """Start the export on a worker thread (also used by tests)."""
        requests = dopdf.requests_for(first, last, hours, **self.request_options())
        layout = self.layout()
        self.cancel.clear()
        self.last_pdf = path
        self._busy(True)
        self.bar.config(value=0, maximum=100)
        self.status.config(text="Starting…")

        def progress(stage, done, total):
            self.events.put(("progress", stage, done, total))

        def work():
            try:
                days, failures = dopdf.export(self.engine, requests, layout, path, self.typst,
                                              progress=progress, cancelled=self.cancel.is_set)
                self.events.put(("done", path, len(days), failures))
            except dopdf.Cancelled:
                self.events.put(("cancelled",))
            except Exception as exc:  # shown to the user; the app stays usable
                self.events.put(("error", exc))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _busy(self, busy):
        state = "disabled" if busy else "normal"
        self.go_btn.config(state=state)
        self.open_btn.config(state="disabled")
        self.folder_btn.config(state="disabled")
        self.close_btn.config(text="Cancel" if busy else "Close")

    def _drain(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._drain)

    def text_status(self, done, total):
        return "Gathering the texts from the engine: %d of %d" % (done, total)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "progress":
            _, stage, done, total = ev
            # Text is ~40% of the time, typesetting ~55%, joining the rest.
            base, span = {"text": (0, 40), "typeset": (40, 55), "assemble": (95, 5)}[stage]
            frac = done / total if total else 1
            self.bar.config(value=base + span * frac)
            if stage == "text":
                self.status.config(text=self.text_status(done, total))
            elif stage == "typeset":
                self.status.config(text="Typesetting volume %d of %d…" % (min(done + 1, total), total)
                                   if done < total else "Typesetting finished")
            else:
                self.status.config(text="Joining the volumes and adding the contents…")
        elif kind == "done":
            _, path, ndays, failures = ev
            self.worker = None
            self._busy(False)
            self.bar.config(value=100)
            try:
                from pypdf import PdfReader
                pages = len(PdfReader(path).pages)
            except Exception:
                pages = 0
            note = "Saved %s pages to %s" % ("{:,}".format(pages), os.path.basename(path))
            if failures:
                note += " — %d day(s) could not be produced by the engine; they are marked in the PDF." % len(failures)
            self.status.config(text=note)
            self.open_btn.config(state="normal")
            self.folder_btn.config(state="normal")
            self._save_settings()
        elif kind == "cancelled":
            self.worker = None
            self._busy(False)
            self.bar.config(value=0)
            self.status.config(text="Cancelled.")
            self._remove_partial()
        elif kind == "error":
            self.worker = None
            self._busy(False)
            self.bar.config(value=0)
            self.status.config(text="Failed: %s" % str(ev[1]).splitlines()[0][:160])
            self._remove_partial()
            messagebox.showerror("Make a PDF", str(ev[1])[:1500], parent=self.win)

    def _remove_partial(self):
        for p in (self.last_pdf, (self.last_pdf or "") + ".part"):
            if p and os.path.exists(p) and os.path.getsize(p) == 0:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def open_pdf(self):
        if self.last_pdf and os.path.exists(self.last_pdf):
            os.startfile(self.last_pdf)

    def show_folder(self):
        if self.last_pdf and os.path.exists(self.last_pdf):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_pdf)])

    def close(self):
        if self.worker:
            if not messagebox.askyesno("Make a PDF", "Stop making this PDF?", parent=self.win):
                return
            self.cancel.set()
            self.status.config(text="Stopping…")
            self.win.after(300, self._close_when_idle)
            return
        self.win.destroy()

    def _close_when_idle(self):
        if self.worker and self.worker.is_alive():
            self.win.after(300, self._close_when_idle)
        else:
            self.win.destroy()


# --------------------------------------------------------------------------
# "Make a breviary": the Office arranged by part


BREVIARY_SETTINGS = os.path.join(os.path.dirname(SETTINGS_FILE), "breviary-settings.json")

# Pages for each part: two languages, Letter, 10 pt, measured on the 1960
# book (1,626 pages in 71 s, with the print-on-demand margins, the short
# Glória Patri and Per Dóminum, repeated texts printed once and headings run
# into their first lines). Only used for the estimate before starting.
# The Psalter is printed in full, every day (146 pages, measured on that book).
# The Sunday Masses (not in that book): measured alone at the same settings.
MISSA_PAGES = 79
PART_PAGES = {"kalendarium": 9, "orationes": 1, "ordinarium": 20, "psalterium": 146,
              "tempora": 587, "temporis": 15, "sancti": 639, "commune": 125, "missa": MISSA_PAGES}
# The Common: 208 before each variant printed only its own texts (the same
# Common alone, 181 pages -> 109).
# Every text in full instead (2,089 pages): each part against PART_PAGES.
FULL_TEXTS = {"tempora": 1.22, "sancti": 1.08, "commune": 1.29}  # the Common alone: 109 -> 141
# One language against this estimate of two (one column, the width of the
# page), and set in two columns against one; by paper, as a narrow page gains
# more from one language and less from two columns. Measured on Monastic 1963
# at 7 pt, Latin and English - Coverdale (README, One language); the pocket
# size by its width.
ONE_LANGUAGE = {"Letter (8.5 × 11 in)": 0.76, "A4": 0.76, "Half letter (5.5 × 8.5 in)": 0.5,
                "A5": 0.5, "Pocket breviary (4.5 × 7 in)": 0.47}
TWO_COLUMNS = {"Letter (8.5 × 11 in)": 0.69, "A4": 0.69, "Half letter (5.5 × 8.5 in)": 0.84,
               "A5": 0.84, "Pocket breviary (4.5 × 7 in)": 0.9}
# Leaving out the alternative lessons: the Common alone, 109 -> 86 pages.
NO_ALTERNATIVES = {"commune": 0.79}

# The Spacing box: (key, label, lowest, highest, step), in percent. Line
# spacing is from one line to the next and word spacing the width of a space,
# against the standard (100); letter spacing is added between letters, in
# percent of the type size (0).
SPACING_FIELDS = (("line", "Line spacing", 60, 300, 5), ("verses", "Space between verses", 0, 400, 10),
                  ("letters", "Letter spacing", -10, 30, 0.5), ("words", "Word spacing", 50, 300, 5))
SPACING_STANDARD = {"line": 100, "verses": 100, "letters": 0, "words": 100}
MARGIN_NAMES = ("Top", "Bottom", "Inside", "Outside")
POD_MARGIN = 0.5  # inches print-on-demand printers keep clear of the trimmed edge
# The list of parts shows this many rows; more scroll.
PARTS_SHOWN = 8
# The page preview: its zooms (of the printed size), and the room around the pages.
PREVIEW_ZOOMS = (25, 33, 50, 67, 75, 100, 125, 150, 200, 250, 300)
PREVIEW_EDGE, PREVIEW_GAP = 18, 10


def number(text, default):
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        return default


VOLUME_CHOICES = ("One book", "2 volumes", "3 volumes", "4 volumes")


def volume_pages(pages_by_part, seams, modern):
    """Rough pages of each volume: the parts every volume holds, and of the
    Propers each volume's share (a Sunday counted as three weekdays, a week of
    the monthly Scripture as a third of a day)."""
    tempora, sancti, seasons = dovolumes.membership(tuple(seams), modern)

    def weight(key):
        return 0.3 if key[0].isdigit() else (3.0 if key.endswith("-0") else 1.0)

    total_w = sum(weight(k) for k in tempora) or 1
    sundays = {k: vs for k, vs in tempora.items() if k.endswith("-0")}
    out = []
    for v in range(len(seams) + 1):
        pages = 0.0
        for part, n in pages_by_part.items():
            if part == "tempora":
                pages += n * sum(weight(k) for k, vs in tempora.items() if v in vs) / total_w
            elif part == "missa":  # the Sundays' share
                pages += n * sum(1 for k, vs in sundays.items() if v in vs) / max(1, len(sundays))
            elif part == "sancti":
                pages += n * sum(1 for vs in sancti.values() if v in vs) / max(1, len(sancti))
            elif part == "temporis":
                pages += n * sum(1 for vs in seasons.values() if v in vs) / max(1, len(seasons))
            else:
                pages += n
        out.append(pages)
    return out


def spacing_factor(lay):
    """Pages against the standard spacing and margins, for the estimate (rough:
    a row is about a line and a half; a line about 5.5 letters a word)."""
    leading, _par, row, letters, words = dopdf.spacing(lay)
    std = dopdf.spacing(dopdf.Layout())
    f = (1.5 * (dopdf.CAP + leading) + row) / (1.5 * (dopdf.CAP + std[0]) + std[2])
    per_letter = 0.45 + letters + 0.25 * words / 5.5
    f *= 1 + 0.6 * (per_letter / (0.45 + 0.25 / 5.5) - 1)  # lines that still fit don't grow
    if lay.margins:
        w, h = dopdf.PAPER_INCHES.get(lay.paper, (8.5, 11.0))
        band = 2.3 * lay.font_size / 72  # the running head's band and the foot's allowance
        top, bottom, inside, outside = lay.margins
        st, sb, si, so = dopdf.standard_margins(lay.paper)
        width, std_width = max(1.0, w - inside - outside), w - si - so
        height, std_height = max(1.0, h - top - bottom - band), h - st - sb - band
        f *= (std_width / width) ** 0.6 * (std_height / height)
    return f


class BreviaryDialog(PdfDialog):
    settings_file = BREVIARY_SETTINGS
    title = "Make a breviary"

    def __init__(self, parent, web_root, perl, perl_libs, typst, dumper):
        import dobreviary

        self.book = dobreviary
        self.web_root, self.perl, self.dumper = web_root, perl, dumper
        self.part_rows = [{"key": k, "on": None} for k in dobreviary.DEFAULT_ORDER]
        super().__init__(parent, web_root, perl, perl_libs, typst)

    def _build(self):
        pad = {"padx": 10, "pady": 3}
        outer = self._body()

        office = ttk.LabelFrame(outer, text="Office", padding=8)
        office.grid(row=0, column=0, sticky="nsew", **pad)
        self.version = tk.StringVar(value="Rubrics 1960 - 1960")
        ttk.Label(office, text="Version").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(office, textvariable=self.version, state="readonly", width=34,
                          values=[c.label for c in self.versions])
        cb.grid(row=0, column=1, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())
        self.book_name = ttk.Label(office, style="Muted.TLabel")
        self.book_name.grid(row=1, column=1, sticky="w")

        langs = ttk.LabelFrame(outer, text="Languages", padding=8)
        langs.grid(row=0, column=1, sticky="nsew", **pad)
        self.lang1 = tk.StringVar(value="Latin")
        self.lang2 = tk.StringVar(value="English")
        labels = [c.label for c in self.languages]
        ttk.Label(langs, text="Left").grid(row=0, column=0, sticky="w")
        c1 = ttk.Combobox(langs, textvariable=self.lang1, state="readonly", width=20, values=labels)
        c1.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(langs, text="Right").grid(row=1, column=0, sticky="w")
        c2 = ttk.Combobox(langs, textvariable=self.lang2, state="readonly", width=20,
                          values=[ONE_COLUMN] + labels)
        c2.grid(row=1, column=1, sticky="w", pady=2)
        for w in (c1, c2):
            w.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())
        # One language: the text in two columns on the page (a third fewer
        # pages on Letter); saved with the Page options (self.opts).
        self.two_columns = tk.BooleanVar(value=False)
        self.two_columns_check = ttk.Checkbutton(
            langs, text="Two columns per page (one language)", variable=self.two_columns,
            command=self.update_summary)
        self.two_columns_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Two columns: what goes into the book on the left, how it looks on the right.
        self.parts_box = ttk.LabelFrame(outer, text="Parts, in the order they will appear", padding=8)
        self.parts_box.grid(row=1, column=0, sticky="nsew", **pad)
        for row in self.part_rows:
            row["on"] = tk.BooleanVar(value=row["key"] not in self.book.DEFAULT_OFF)
        self.alternatives = tk.BooleanVar(value=True)
        self.references = tk.BooleanVar(value=True)
        self._build_parts_list()
        self._render_parts()

        self._build_page(outer, row=2, per_page_label="Start each office on a new page",
                         contents_label="Contents", skip=("new_page_per_hour",), column=0, span=1)
        self.opts["two_columns"] = self.two_columns
        # Blank pages before the title page (in pairs) and after the last page.
        blanks = ttk.Frame(self.page_box)
        blanks.grid(row=10, column=0, columnspan=6, sticky="w", pady=(6, 0))
        ttk.Label(blanks, text="Blank pages:").pack(side="left", padx=(0, 6))
        self.blank_front, self.blank_back = tk.StringVar(value="0"), tk.StringVar(value="0")
        for label, var, step in (("front", self.blank_front, 2), ("back", self.blank_back, 1)):
            ttk.Label(blanks, text=label).pack(side="left", padx=(8, 4))
            ttk.Spinbox(blanks, textvariable=var, from_=0, to=10, increment=step, width=4,
                        command=self.update_summary).pack(side="left")
            var.trace_add("write", lambda *_: self.update_summary())
        ttk.Label(blanks, text="(each volume; the front ones in pairs)", style="Muted.TLabel").pack(
            side="left", padx=(8, 0))
        self.opts["new_page_per_day"].set(False)  # a breviary runs its offices on
        self._build_type(outer, row=1, preview=True, column=1, span=1)
        self._build_spacing(outer, row=2, column=1)
        self._build_volumes(outer, row=3, column=0)
        self._build_footer(self.foot_box)

    def _build_volumes(self, outer, row, column=0):
        """One book, or two to four divided by the liturgical year (dovolumes.py)."""
        pad = {"padx": 10, "pady": 3}
        box = ttk.LabelFrame(outer, text="Volumes", padding=8)
        box.grid(row=row, column=column, sticky="nsew", **pad)
        self.volume_count = tk.StringVar(value=VOLUME_CHOICES[0])
        count = ttk.Combobox(box, textvariable=self.volume_count, state="readonly", width=11,
                             values=VOLUME_CHOICES)
        count.grid(row=0, column=0, sticky="w")
        count.bind("<<ComboboxSelected>>", lambda _e: self.usual_division())
        self.seam_labels = {dovolumes.SHORT_NAMES[k]: k for k in dovolumes.SEAM_ORDER if k != "advent"}
        seams = ttk.Frame(box)
        seams.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.seam_label = ttk.Label(seams, text="beginning at")
        self.seam_label.pack(side="left", padx=(0, 6))
        self.seam_vars, self.seam_widgets = [], []
        for k in range(3):
            var = tk.StringVar()
            num = ttk.Label(seams, text=dovolumes.ROMANS[k + 1])
            box_k = ttk.Combobox(seams, textvariable=var, state="readonly", width=13,
                                 values=list(self.seam_labels))
            box_k.bind("<<ComboboxSelected>>", lambda _e: self.update_summary())
            num.pack(side="left", padx=(6, 3))
            box_k.pack(side="left")
            self.seam_vars.append(var)
            self.seam_widgets.append((num, box_k))
        self.volume_note = ttk.Label(box, style="Muted.TLabel", wraplength=540, justify="left")
        self.volume_note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.usual_division()

    def volumes_wanted(self):
        return VOLUME_CHOICES.index(self.volume_count.get()) + 1 if self.volume_count.get() in VOLUME_CHOICES else 1

    def seams_chosen(self):
        """Where the second, third ... volumes begin (dovolumes.SEAMS keys)."""
        n = self.volumes_wanted()
        return [self.seam_labels.get(v.get(), "") for v in self.seam_vars[:n - 1]]

    def usual_division(self, seams=None):
        n = self.volumes_wanted()
        seams = seams or dovolumes.DIVISIONS.get(n, [])
        for k, var in enumerate(self.seam_vars):
            var.set(dovolumes.SHORT_NAMES[seams[k]] if k < len(seams) else "")
        self.update_summary()

    def _show_volume_choices(self, n):
        state = "!disabled" if n > 1 else "disabled"
        self.seam_label.state([state])
        for k, (num, box_k) in enumerate(self.seam_widgets):
            shown = k < n - 1
            num.state(["!disabled" if shown else "disabled"])
            box_k.state(["readonly", "!disabled"] if shown else ["disabled"])
            if not shown:
                box_k.set("")

    def _build_spacing(self, outer, row, column=0):
        """Line, verse, letter and word spacing, the margins, and two pages to look at."""
        pad = {"padx": 10, "pady": 3}
        box = ttk.LabelFrame(outer, text="Spacing and margins", padding=8)
        box.grid(row=row, column=column, sticky="nsew", **pad)
        self.spacing = {}
        for i, (key, label, low, high, step) in enumerate(SPACING_FIELDS):
            r, c = divmod(i, 2)
            var = tk.StringVar(value=str(SPACING_STANDARD[key]))
            ttk.Label(box, text=label).grid(row=r, column=c * 3, sticky="w", padx=(18 if c else 0, 6), pady=1)
            ttk.Spinbox(box, textvariable=var, from_=low, to=high, increment=step, width=6,
                        command=self.update_summary).grid(row=r, column=c * 3 + 1, sticky="w")
            ttk.Label(box, text="%").grid(row=r, column=c * 3 + 2, sticky="w", padx=(3, 0))
            var.trace_add("write", lambda *_: self.update_summary())
            self.spacing[key] = var
        buttons = ttk.Frame(box)
        buttons.grid(row=0, column=6, rowspan=2, sticky="e", padx=(18, 0))
        ttk.Button(buttons, text="Preview pages…", command=self.show_pages).pack(fill="x")
        ttk.Button(buttons, text="Standard", command=self.standard_spacing).pack(fill="x", pady=(4, 0))
        box.columnconfigure(6, weight=1)

        mf = ttk.Frame(box)
        mf.grid(row=2, column=0, columnspan=7, sticky="w", pady=(8, 0))
        ttk.Label(mf, text="Margins").pack(side="left", padx=(0, 4))
        self.margin_vars = []
        for name in MARGIN_NAMES:
            var = tk.StringVar()
            ttk.Label(mf, text=name).pack(side="left", padx=(10, 4))
            ttk.Spinbox(mf, textvariable=var, from_=0.2, to=2.5, increment=0.05, width=5,
                        format="%.2f", command=self.update_summary).pack(side="left")
            var.trace_add("write", lambda *_: self.update_summary())
            self.margin_vars.append(var)
        ttk.Label(mf, text="in").pack(side="left", padx=(4, 0))
        self.margin_note = ttk.Label(box, style="Muted.TLabel", wraplength=600, justify="left")
        self.margin_note.grid(row=3, column=0, columnspan=7, sticky="w", pady=(4, 0))
        self._set_margins(dopdf.standard_margins(self.paper.get()))
        self._preview = None  # the preview window, while open

    def _set_margins(self, values):
        self._margins_paper = self.paper.get()  # first: setting them calls update_summary
        for var, v in zip(self.margin_vars, values):
            var.set("%.2f" % v)

    def margins_entered(self):
        """The four margins as entered (inches), or None if one is not a number."""
        values = [number(v.get(), None) for v in self.margin_vars]
        return None if None in values else tuple(min(3.0, max(0.1, v)) for v in values)

    def standard_spacing(self):
        for key, var in self.spacing.items():
            var.set(str(SPACING_STANDARD[key]))
        self._set_margins(dopdf.standard_margins(self.paper.get()))
        self.update_summary()

    def layout(self):
        lay = super().layout()
        if not hasattr(self, "margin_vars"):
            return lay  # still building the window
        s = {k: number(v.get(), SPACING_STANDARD[k]) for k, v in self.spacing.items()}
        lay.line_spacing, lay.verse_spacing = s["line"] / 100, s["verses"] / 100
        lay.letter_spacing, lay.word_spacing = s["letters"] / 100, s["words"] / 100
        margins = self.margins_entered()
        standard = dopdf.standard_margins(lay.paper)
        lay.margins = None if margins is None or all(abs(a - b) < 0.005 for a, b in zip(margins, standard)) \
            else margins
        lay.blank_front = int(number(self.blank_front.get(), 0)) if hasattr(self, "blank_front") else 0
        lay.blank_back = int(number(self.blank_back.get(), 0)) if hasattr(self, "blank_back") else 0
        return lay

    def _margin_note(self, lay):
        margins = self.margins_entered()
        if margins is None:
            return "Enter each margin in inches.", "Error.TLabel"
        w, h = dopdf.PAPER_INCHES.get(lay.paper, (8.5, 11.0))
        top, bottom, inside, outside = margins
        if w - inside - outside < 2 or h - top - bottom < 3:
            return "These margins leave too little room for the text.", "Error.TLabel"
        note = ("Top is from the page's edge to the running head; inside is the side of the "
                "binding (left on right-hand pages, right on left-hand ones).")
        if min(margins) < POD_MARGIN:
            return (note + " Print-on-demand printers ask for at least %.1f in on every side."
                    % POD_MARGIN), "Warn.TLabel"
        return note, "Muted.TLabel"

    # -- the preview of two pages ---------------------------------------------
    #
    # A window of its own: two facing pages as the book sets them, at a zoom
    # (Fit: both pages in the window; or 25% to 300% of their printed size).
    # Each zoom is set again by Typst at its own resolution, so the type stays
    # sharp; the pages follow the settings as they change.

    def _screen_ppi(self):
        return max(72.0, float(self.win.winfo_fpixels("1i")))

    def _preview_state(self):
        """What the pages depend on: the text's version and languages, and the layout."""
        lay = self.layout()
        return (self.version.get(), self.languages_chosen(), repr(lay))

    def show_pages(self):
        """Open the two pages (or bring them forward), set with the current settings."""
        if self._preview is None or not self._preview["win"].winfo_exists():
            self._open_preview()
        else:
            self._preview["win"].deiconify()
            self._preview["win"].lift()
        self._render_preview()

    def _open_preview(self):
        win = tk.Toplevel(self.win)
        win.withdraw()  # shown finished (doui.present)
        win.title("Two pages of the breviary")
        win.transient(self.win)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        size = (min(1500, int(sw * 0.72)), min(1100, int(sh * 0.82)))
        win.minsize(560, 420)
        doui.install(win)
        zoom = tk.StringVar(value="Fit")
        outer = ttk.Frame(win, padding=10, style="Window.TFrame")
        outer.pack(fill="both", expand=True)
        bar = ttk.Frame(outer, style="Card.TFrame", padding=(4, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="−", width=3, style="Small.TButton",
                   command=lambda: self._zoom_step(-1)).pack(side="left")
        box = ttk.Combobox(bar, textvariable=zoom, state="readonly", width=7,
                           values=["Fit"] + ["%d%%" % z for z in PREVIEW_ZOOMS])
        box.pack(side="left", padx=6)
        box.bind("<<ComboboxSelected>>", lambda _e: self._zoom_to(zoom.get()))
        ttk.Button(bar, text="+", width=3, style="Small.TButton",
                   command=lambda: self._zoom_step(1)).pack(side="left")
        ttk.Button(bar, text="Fit", style="Small.TButton",
                   command=lambda: self._zoom_to("Fit")).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Actual size", style="Small.TButton",
                   command=lambda: self._zoom_to("100%")).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        doui.mode_button(bar, style="Small.TButton").pack(side="right", padx=(0, 8))
        info = ttk.Label(bar, style="Muted.TLabel")
        info.pack(side="left", padx=(16, 0))

        body = ttk.Frame(outer, style="Window.TFrame")
        body.pack(fill="both", expand=True, pady=(10, 0))
        canvas = doui.role(tk.Canvas(body, highlightthickness=0, borderwidth=0), "preview")
        ys = ttk.Scrollbar(body, orient="vertical", command=canvas.yview, style="Window.Vertical.TScrollbar")
        xs = ttk.Scrollbar(body, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        ttk.Label(outer, style="Window.Muted.TLabel", wraplength=900, justify="left", text=(
            "Sunday Lauds as the Psalter sets it, on a left-hand and a right-hand page. The dashed "
            "line is %.1f in from the trimmed edge: print-on-demand printers keep everything "
            "inside it. Drag to move about; Ctrl + mouse wheel, or Ctrl + and Ctrl −, to zoom; "
            "Ctrl 0 to fit. The pages follow the settings as you change them." % POD_MARGIN)
                  ).pack(fill="x", pady=(8, 0))

        # Moving about: drag; the wheel scrolls (with Shift, sideways); Ctrl zooms.
        canvas.bind("<ButtonPress-1>", lambda e: (canvas.scan_mark(e.x, e.y), canvas.config(cursor="fleur")))
        canvas.bind("<B1-Motion>", lambda e: canvas.scan_dragto(e.x, e.y, gain=1))
        canvas.bind("<ButtonRelease-1>", lambda e: canvas.config(cursor=""))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.bind("<Shift-MouseWheel>", lambda e: canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.bind("<Control-MouseWheel>", lambda e: self._zoom_step(1 if e.delta > 0 else -1, (e.x, e.y)))
        for seq, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-minus>", -1),
                          ("<Control-KP_Add>", 1), ("<Control-KP_Subtract>", -1)):
            win.bind(seq, lambda _e, k=step: self._zoom_step(k))
        win.bind("<Control-0>", lambda _e: self._zoom_to("Fit"))
        win.bind("<Escape>", lambda _e: win.destroy())
        canvas.bind("<Configure>", lambda _e: self._preview_resized())
        self._preview = {"win": win, "canvas": canvas, "zoom": zoom, "info": info, "images": [],
                         "state": None, "ppi": None, "busy": False, "again": False, "anchor": None,
                         "size": None}
        # Until the pages are set: a word in the middle, not an empty grey.
        canvas.create_text(size[0] // 2 - 20, size[1] // 2 - 60, text="Setting the pages…",
                           fill=doui.colours()["muted"], font=(doui.FONT, 12))
        doui.present(win, over=self.win, size=size)
        self.win.after(700, self._follow_settings)

    def _preview_ppi(self, paper):
        """The resolution for the zoom chosen: Fit puts both pages in the window."""
        p = self._preview
        w_in, h_in = dopdf.PAPER_INCHES.get(paper, (8.5, 11.0))
        z = p["zoom"].get() if p else "Fit"
        if z == "Fit":
            c = p["canvas"] if p else None
            cw = c.winfo_width() if c is not None and c.winfo_width() > 50 else 1000
            ch = c.winfo_height() if c is not None and c.winfo_height() > 50 else 700
            ppi = min((ch - 2 * PREVIEW_EDGE) / h_in, (cw - 2 * PREVIEW_EDGE - PREVIEW_GAP) / (2 * w_in))
        else:
            ppi = int(z.rstrip("%")) / 100 * self._screen_ppi()
        return max(20, min(int(ppi), int(PREVIEW_ZOOMS[-1] / 100 * self._screen_ppi())))

    def _zoom_percent(self):
        p = self._preview
        if p and p["ppi"]:
            return 100 * p["ppi"] / self._screen_ppi()
        return 100

    def _zoom_step(self, step, at=None):
        """The next zoom in or out (from Fit, from the size it shows)."""
        now = self._zoom_percent()
        if step > 0:
            nxt = next((z for z in PREVIEW_ZOOMS if z > now + 0.5), PREVIEW_ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(PREVIEW_ZOOMS) if z < now - 0.5), PREVIEW_ZOOMS[0])
        self._zoom_to("%d%%" % nxt, at)

    def _zoom_to(self, value, at=None):
        p = self._preview
        if p is None:
            return
        c = p["canvas"]
        # Keep the point under the pointer (or the middle) where it is.
        wx, wy = at if at else (c.winfo_width() / 2, c.winfo_height() / 2)
        size = p.get("size")
        if size and size[0] and size[1]:
            p["anchor"] = (c.canvasx(wx) / size[0], c.canvasy(wy) / size[1], wx, wy)
        p["zoom"].set(value)
        if p.get("zoom_after"):
            self.win.after_cancel(p["zoom_after"])
        p["zoom_after"] = self.win.after(180, self._render_preview)  # one setting for quick steps

    def _preview_resized(self):
        p = self._preview
        if not p or p["zoom"].get() != "Fit":
            return
        if p.get("resize_after"):
            self.win.after_cancel(p["resize_after"])
        p["resize_after"] = self.win.after(350, self._render_preview)

    def _render_preview(self):
        p = self._preview
        if p is None or not p["win"].winfo_exists():
            return
        p["zoom_after"] = None
        version, (lang1, lang2), layout = self.version.get(), self.languages_chosen(), self.layout()
        ppi = self._preview_ppi(layout.paper)
        state = self._preview_state()
        if (state, ppi) == (p["state"], p["ppi"]) and p["images"]:
            return
        if p["busy"]:
            p["again"] = True  # set once more when this one is done
            return
        p["busy"] = True
        p["info"].config(text="Setting the pages…")
        if not getattr(self, "_preview_dir", None):
            import tempfile
            self._preview_dir = tempfile.mkdtemp(prefix="do-preview-")

        def work():
            try:
                if getattr(self, "_preview_key", None) != (version, lang1, lang2):
                    self._preview_day = self.book.preview_text(self.engine, version, lang1, lang2)
                    self._preview_key = (version, lang1, lang2)
                pngs = self.book.preview_pages(self._preview_day, lang1, lang2, layout, self.typst,
                                               self._preview_dir, ppi)
                self.events.put(("preview", pngs, layout.paper, ppi, state))
            except Exception as exc:  # shown in the status line; the window stays usable
                self.events.put(("preview-error", exc))

        threading.Thread(target=work, daemon=True).start()

    def _show_pages(self, pngs, paper, ppi, state):
        p = self._preview
        if p is None or not p["win"].winfo_exists():
            return
        canvas = p["canvas"]
        images = [tk.PhotoImage(file=f) for f in pngs]
        p.update(images=images, state=state, ppi=ppi)  # Tk shows an image only while it is kept
        edge, gap = PREVIEW_EDGE, PREVIEW_GAP
        w = max(i.width() for i in images)
        h = max(i.height() for i in images)
        canvas.delete("all")
        safe = POD_MARGIN * ppi
        dark = doui.mode() == "dark"
        for n, img in enumerate(images):
            x = edge + n * (w + gap)
            canvas.create_rectangle(x + 3, edge + 4, x + img.width() + 3, edge + img.height() + 4,
                                    fill="#000000" if dark else "#9aa3ad", outline="")  # its shadow
            canvas.create_image(x, edge, image=img, anchor="nw")
            canvas.create_rectangle(x, edge, x + img.width(), edge + img.height(), outline="#59616b")
            canvas.create_rectangle(x + safe, edge + safe, x + img.width() - safe,
                                    edge + img.height() - safe, outline="#2f6fb5", dash=(4, 3))
        total_w, total_h = 2 * edge + 2 * w + gap, 2 * edge + h
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        # Centred when smaller than the window.
        x0 = -max(0, (cw - total_w) / 2)
        y0 = -max(0, (ch - total_h) / 2)
        canvas.config(scrollregion=(x0, y0, x0 + max(total_w, cw), y0 + max(total_h, ch)))
        p["size"] = (max(total_w, cw), max(total_h, ch))
        anchor, p["anchor"] = p.get("anchor"), None
        if anchor:
            fx, fy, wx, wy = anchor
            sw, sh = p["size"]
            canvas.xview_moveto(max(0.0, (fx * sw - wx) / sw))
            canvas.yview_moveto(max(0.0, (fy * sh - wy) / sh))
        if p["zoom"].get() == "Fit":
            p["info"].config(text="Fit: %d%%" % round(self._zoom_percent()))
        else:
            p["info"].config(text="")
        self.status.config(text="")

    def _follow_settings(self):
        """While the preview is open, set the pages again when a setting changes."""
        p = self._preview
        if p is None or not p["win"].winfo_exists():
            self._preview = None
            return
        if not p["busy"] and self._preview_state() != p.get("state"):
            self._render_preview()
        self.win.after(700, self._follow_settings)

    def _handle(self, ev):
        if ev[0] == "progress" and ev[1] == "book":
            _, _stage, done, total = ev
            self.bar.config(value=40 + 55 * (done / total if total else 1))
            self.status.config(text="Typesetting volume %d of %d…" % (min(total // 100, done // 100 + 1),
                                                                      total // 100))
        elif ev[0] == "done-volumes":
            paths = ev[1]
            self.worker = None
            self._busy(False)
            self.bar.config(value=100)
            pages = []
            try:
                from pypdf import PdfReader
                pages = [len(PdfReader(p).pages) for p in paths]
            except Exception:
                pass
            self.last_pdf = paths[0]
            self.status.config(text="Saved %d volumes%s to %s" % (
                len(paths), " (%s pages)" % ", ".join("{:,}".format(n) for n in pages) if pages else "",
                os.path.dirname(paths[0])))
            self.open_btn.config(state="normal")
            self.folder_btn.config(state="normal")
            self._save_settings()
        elif ev[0] == "preview":
            if self._preview:
                self._preview["busy"] = False
            self._show_pages(*ev[1:])
            if self._preview and self._preview.pop("again", False):
                self._preview["again"] = False
                self._render_preview()
        elif ev[0] == "preview-error":
            if self._preview:
                self._preview["busy"] = False
                self._preview["info"].config(text="")
            self.status.config(text="The preview failed: %s" % str(ev[1]).splitlines()[0][:150])
        else:
            super()._handle(ev)

    def close(self):
        super().close()
        if getattr(self, "_preview_dir", None) and not self.win.winfo_exists():
            import shutil
            shutil.rmtree(self._preview_dir, ignore_errors=True)

    def _build_parts_list(self):
        """The parts, in a list that scrolls once it is longer than PARTS_SHOWN
        rows; under it, "Add custom part…" and the two choices about texts."""
        wrap = ttk.Frame(self.parts_box)
        wrap.grid(row=0, column=0, sticky="nsew")
        canvas = doui.role(tk.Canvas(wrap, highlightthickness=0, borderwidth=0), "card-bg")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        self.parts_inner = ttk.Frame(canvas)
        canvas.create_window(0, 0, window=self.parts_inner, anchor="nw")
        self.parts_canvas, self.parts_bar = canvas, bar

        def wheel(e):
            if bar.winfo_ismapped():
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        below = ttk.Frame(self.parts_box)
        below.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        below.columnconfigure(1, weight=1)
        ttk.Checkbutton(below, variable=self.alternatives, command=self.update_summary,
                        text="Include the alternative lessons (the Common is nearly a third "
                             "shorter without them)").grid(row=0, column=0, sticky="w")
        # A .txt, .md or .pdf of the user's own, as a part of the book.
        ttk.Button(below, text="Add custom part…", style="Small.TButton",
                   command=self.add_custom).grid(row=0, column=1, sticky="e", padx=(12, 0))
        ttk.Checkbutton(below, variable=self.references, command=self.update_summary,
                        text="Print a repeated text once; later copies give its first words "
                             "and page").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _render_parts(self, show_last=False):
        inner = self.parts_inner
        for child in inner.winfo_children():
            child.destroy()
        last = len(self.part_rows) - 1
        for i, row in enumerate(self.part_rows):
            key = row["key"]
            if self.book.is_custom(key):
                path = row.get("file") or ""
                there = os.path.isfile(path)
                title = self.book.custom_title(path) if there else os.path.basename(path)
                ttk.Checkbutton(inner, text=title, variable=row["on"], command=self.update_summary).grid(
                    row=i, column=0, sticky="w", padx=(0, 8))
                ttk.Label(inner, text=os.path.basename(path) if there else "file missing",
                          style="Muted.TLabel" if there else "Error.TLabel").grid(
                    row=i, column=1, sticky="w", padx=(0, 8))
                ttk.Button(inner, text="Change…", style="Small.TButton",
                           command=lambda r=row: self.change_custom(r)).grid(row=i, column=2, padx=(0, 3))
                ttk.Button(inner, text="✕", width=2, style="Small.TButton",
                           command=lambda r=row: self.remove_custom(r)).grid(row=i, column=3, padx=(0, 8))
            else:
                part = self.book.PARTS[key]
                ttk.Checkbutton(inner, text="%s — %s" % (part.la, part.en), variable=row["on"],
                                command=self.update_summary).grid(row=i, column=0, columnspan=4,
                                                                  sticky="w", padx=(0, 12))
            up = ttk.Button(inner, text="▲", width=2, style="Small.TButton",
                            command=lambda i=i: self._move(i, -1))
            down = ttk.Button(inner, text="▼", width=2, style="Small.TButton",
                              command=lambda i=i: self._move(i, 1))
            up.grid(row=i, column=4, pady=1)
            down.grid(row=i, column=5, pady=1, padx=(3, 0))
            if i == 0:
                up.state(["disabled"])
            if i == last:
                down.state(["disabled"])
        # The canvas as wide as the rows, as tall as PARTS_SHOWN of them; the
        # scroll bar only when there are more.
        inner.update_idletasks()
        row_h = max(1, inner.winfo_reqheight() / max(1, len(self.part_rows)))
        shown = min(inner.winfo_reqheight(), int(row_h * PARTS_SHOWN))
        self.parts_canvas.configure(width=inner.winfo_reqwidth(), height=shown,
                                    scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight()))
        if inner.winfo_reqheight() > shown + 1:
            self.parts_bar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        else:
            self.parts_bar.grid_remove()
            self.parts_canvas.yview_moveto(0)
        if show_last:
            self.parts_canvas.yview_moveto(1.0)

    # -- custom parts: a file of the user's own, as a part of the book ---------

    def _ask_custom_file(self, current=""):
        start = os.path.dirname(current) if current else getattr(self, "_custom_dir", "") or ""
        path = filedialog.askopenfilename(
            parent=self.win, title="A file for a custom part", initialdir=start or None,
            filetypes=[("Text, Markdown or PDF", "*.txt *.md *.markdown *.pdf"),
                       ("Text", "*.txt"), ("Markdown", "*.md *.markdown"), ("PDF", "*.pdf")])
        if path:
            self._custom_dir = os.path.dirname(path)
        return os.path.normpath(path) if path else ""

    def add_custom(self):
        path = self._ask_custom_file()
        if not path:
            return
        keys = {r["key"] for r in self.part_rows}
        n = 1
        while "custom%d" % n in keys:
            n += 1
        self.part_rows.append({"key": "custom%d" % n, "on": tk.BooleanVar(value=True), "file": path})
        self._render_parts(show_last=True)
        self.update_summary()

    def change_custom(self, row):
        path = self._ask_custom_file(row.get("file") or "")
        if path:
            row["file"] = path
            self._render_parts()
            self.update_summary()

    def remove_custom(self, row):
        self.part_rows = [r for r in self.part_rows if r is not row]
        self._render_parts()
        self.update_summary()

    def customs(self):
        """{"custom1": file, ...} of the custom parts ticked."""
        return {r["key"]: r.get("file") or "" for r in self.part_rows
                if self.book.is_custom(r["key"]) and r["on"].get()}

    def _custom_pages(self, path, lay):
        """A custom part's pages, roughly: a PDF's own; text by its length."""
        try:
            if path.lower().endswith(".pdf"):
                cache = getattr(self, "_pdf_pages", {})
                self._pdf_pages = cache
                stamp = (path, os.path.getmtime(path))
                if stamp not in cache:
                    from pypdf import PdfReader
                    cache[stamp] = len(PdfReader(path).pages)
                return cache[stamp]
            chars = os.path.getsize(path)
        except Exception:
            return 0
        return max(1.0, chars / 3800 * PAPER_FACTOR.get(lay.paper, 1.0) * (lay.font_size / 10.0) ** 2)

    def _move(self, i, step):
        j = i + step
        if 0 <= j < len(self.part_rows):
            self.part_rows[i], self.part_rows[j] = self.part_rows[j], self.part_rows[i]
            top = self.parts_canvas.yview()[0]
            self._render_parts()
            self.parts_canvas.yview_moveto(top)  # the list stays where it was
            self.update_summary()

    def parts(self):
        return [r["key"] for r in self.part_rows if r["on"].get()]

    def languages_chosen(self):
        lang1 = self.choice_value(self.languages, self.lang1.get(), "Latin")
        lang2 = lang1 if self.lang2.get() == ONE_COLUMN else self.choice_value(
            self.languages, self.lang2.get(), "English")
        return lang1, lang2

    def update_summary(self):
        if not hasattr(self, "estimate"):
            return  # still building the window
        self.book_name.config(text=self.book.book_title(self.version.get()))
        parts = self.parts()
        if not parts:
            self.estimate.config(text="Choose at least one part.")
            self.go_btn.config(state="disabled")
            return
        paper = self.paper.get()
        if paper != self._margins_paper:
            # Margins left at the paper's own follow the paper; chosen ones stay.
            old = dopdf.standard_margins(self._margins_paper)
            entered = self.margins_entered()
            if entered is None or all(abs(a - b) < 0.005 for a, b in zip(entered, old)):
                self._set_margins(dopdf.standard_margins(paper))
            self._margins_paper = paper
        lay = self.layout()
        note, look = self._margin_note(lay)
        self.margin_note.config(text=note, style=look)
        alt = self.alternatives.get()
        customs = self.customs()
        missing = [os.path.basename(f) or "(no file)" for f in customs.values() if not os.path.isfile(f)]
        if missing:
            self.estimate.config(text="A custom part's file is missing: %s. Change it or remove "
                                      "the part." % ", ".join(missing))
            self.go_btn.config(state="disabled")
            return
        custom_pages = sum(self._custom_pages(f, lay) for f in customs.values())
        parts = [p for p in parts if p not in customs]
        pages = sum(PART_PAGES[p] * (1 if alt else NO_ALTERNATIVES.get(p, 1))
                    * (1 if self.references.get() else FULL_TEXTS.get(p, 1)) for p in parts)
        one = self.lang2.get() == ONE_COLUMN
        self.two_columns_check.state(["!disabled"] if one else ["disabled"])
        if one:
            pages *= ONE_LANGUAGE.get(lay.paper, 0.6)
            if self.two_columns.get():
                pages *= TWO_COLUMNS.get(lay.paper, 0.8)
        pages *= PAPER_FACTOR.get(lay.paper, 1.0) * (lay.font_size / 10.0) ** 2 * spacing_factor(lay)
        n = self.volumes_wanted()
        self._show_volume_choices(n)
        if n > 1:
            seams = self.seams_chosen()
            problem = dovolumes.check(seams) if all(seams) else "Choose where each volume begins."
            if problem:
                self.volume_note.config(text=problem, style="Error.TLabel")
                self.estimate.config(text="")
                self.go_btn.config(state="disabled")
                return
            names = dovolumes.names(seams)
            whole = [self.book.PARTS[p].en for p in parts
                     if p not in ("tempora", "sancti", "temporis", "missa")]
            if customs:
                whole.append("custom part" + ("s" if len(customs) > 1 else ""))
            self.volume_note.config(style="Muted.TLabel", text=(
                " · ".join("%s. %s, from %s" % (dovolumes.ROMANS[i], names[i][0], dovolumes.SEAMS[b][2])
                           for i, b in enumerate(["advent"] + seams))
                + ". " + ("Each has the %s entire; " % (", ".join(whole[:-1]) + " and " + whole[-1]
                                                       if len(whole) > 1 else whole[0])
                          if whole else "")
                + "a saint or Sunday that can fall in two volumes is in both."))
            by_part = {p: PART_PAGES[p] * (1 if alt else NO_ALTERNATIVES.get(p, 1))
                       * (1 if self.references.get() else FULL_TEXTS.get(p, 1)) for p in parts}
            factor = pages / max(1.0, sum(by_part.values()))  # paper, type, spacing, columns
            blanks = sum(dopdf.blank_pages(lay)) + custom_pages  # in every volume
            each = [v * factor + blanks for v in volume_pages(by_part, seams, "196" in self.version.get())]
            seconds = 10 + sum(each) * 0.02 + 5 * n
            when = "under a minute" if seconds < 60 else (
                "about a minute" if seconds < 90 else "about %d minutes" % round(seconds / 60))
            self.estimate.config(text="%d volumes of about %s pages, %s, roughly %s MB in all" % (
                n, ", ".join("{:,}".format(int(round(p, -1))) for p in each), when,
                "{:,}".format(max(1, int(round(sum(each) * (0.021 if lay.accessible else 0.007)))))))
            if not self.worker:
                self.go_btn.config(state="normal")
            return
        self.volume_note.config(text="One book holding every part chosen above.", style="Muted.TLabel")
        pages += sum(dopdf.blank_pages(lay)) + custom_pages
        seconds = 10 + pages * 0.02
        when = "under a minute" if seconds < 60 else (
            "about a minute" if seconds < 90 else "about %d minutes" % round(seconds / 60))
        mb = pages * (0.021 if lay.accessible else 0.007)
        self.estimate.config(text="About %s pages, %s, roughly %s MB" % (
            "{:,}".format(int(round(pages, -1))), when, "{:,}".format(max(1, int(round(mb))))))
        if not self.worker:
            self.go_btn.config(state="normal")

    def text_status(self, done, total):
        return "Reading the parts from the engine: %d of %d" % (done, total)

    def _settings(self):
        return {
            "version": self.version.get(), "lang1": self.lang1.get(), "lang2": self.lang2.get(),
            "paper": self.paper.get(), "size": self.size.get(),
            "parts": [[r["key"], r["on"].get()] for r in self.part_rows],
            "custom_files": {r["key"]: r.get("file") or "" for r in self.part_rows
                             if self.book.is_custom(r["key"])},
            "alternatives": self.alternatives.get(),
            "references": self.references.get(),
            "spacing": {k: v.get() for k, v in self.spacing.items()},
            "margins": [v.get() for v in self.margin_vars],
            "volumes": self.volumes_wanted(),
            "seams": self.seams_chosen(),
            "opts": {k: v.get() for k, v in self.opts.items()},
            "blank_pages": list(dopdf.blank_pages(self.layout())),
            "folder": os.path.dirname(self.last_pdf) if self.last_pdf else None,
            **self._type_settings(),
        }

    def _load_settings(self):
        try:
            with open(self.settings_file, encoding="utf-8") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            return
        for key, var in (("version", self.version), ("lang1", self.lang1), ("lang2", self.lang2),
                         ("paper", self.paper), ("size", self.size)):
            if isinstance(s.get(key), str):
                var.set(s[key])
        saved, seen = [], {}
        for p in s.get("parts") or []:
            if not (isinstance(p, list) and p and isinstance(p[0], str)):
                continue
            key = self.book.FORMER.get(p[0], p[0])  # parts since merged keep their place
            files = s.get("custom_files") if isinstance(s.get("custom_files"), dict) else {}
            if key not in self.book.PARTS and not (self.book.is_custom(key) and isinstance(files.get(key), str)):
                continue
            on = bool(p[1]) if len(p) > 1 else True
            if key in seen:
                saved[seen[key]][1] = saved[seen[key]][1] or on
            else:
                seen[key] = len(saved)
                saved.append([key, on])
        if saved:
            files = s.get("custom_files") if isinstance(s.get("custom_files"), dict) else {}
            rows = [{"key": k, "on": tk.BooleanVar(value=bool(on)), "file": files.get(k, "")}
                    for k, on in saved]
            # Parts added since these choices were saved go where they stand in
            # the default order: after the nearest part before them.
            for i, k in enumerate(self.book.DEFAULT_ORDER):
                if any(r["key"] == k for r in rows):
                    continue
                if k in self.book.DEFAULT_OFF:  # an addition: unticked, at the end
                    rows.append({"key": k, "on": tk.BooleanVar(value=False)})
                    continue
                at = 0
                for prev in reversed(self.book.DEFAULT_ORDER[:i]):
                    idx = next((j for j, r in enumerate(rows) if r["key"] == prev), None)
                    if idx is not None:
                        at = idx + 1
                        break
                rows.insert(at, {"key": k, "on": tk.BooleanVar(value=k not in self.book.DEFAULT_OFF)})
            self.part_rows = rows
            self._render_parts()
        blanks = s.get("blank_pages")
        if isinstance(blanks, list) and len(blanks) == 2 and all(isinstance(n, int) for n in blanks):
            self.blank_front.set(str(blanks[0]))
            self.blank_back.set(str(blanks[1]))
        if isinstance(s.get("alternatives"), bool):
            self.alternatives.set(s["alternatives"])
        if isinstance(s.get("references"), bool):
            self.references.set(s["references"])
        for k, v in (s.get("spacing") or {}).items():
            if k in self.spacing and number(v, None) is not None:
                self.spacing[k].set(str(v))
        n = s.get("volumes")
        if isinstance(n, int) and 1 <= n <= len(VOLUME_CHOICES):
            self.volume_count.set(VOLUME_CHOICES[n - 1])
            seams = [k for k in (s.get("seams") or []) if k in dovolumes.SHORT_NAMES]
            self.usual_division(seams if len(seams) == n - 1 else None)
        margins = s.get("margins")
        if isinstance(margins, list) and len(margins) == 4 and None not in [number(v, None) for v in margins]:
            self._set_margins([number(v, 0) for v in margins])
        else:
            self._set_margins(dopdf.standard_margins(self.paper.get()))
        for k, v in (s.get("opts") or {}).items():
            if k in self.opts:
                self.opts[k].set(bool(v))
        self._load_type_settings(s)
        self.saved_folder = s.get("folder")

    def start(self):
        folder = getattr(self, "saved_folder", None) or os.path.join(os.path.expanduser("~"), "Documents")
        name = safe_filename("%s (%s).pdf" % (self.book.book_title(self.version.get()), self.version.get()))
        n = self.volumes_wanted()
        # Volumes: one PDF each, this name with the volume's added
        # ("… - I. Pars Hiemalis.pdf"), so each can be printed by itself.
        seams = self.seams_chosen()
        title = ('Save the %d volumes as (one PDF each: this name + "%s", "%s" …)' % (
            n, dovolumes.file_suffix(seams, 0), dovolumes.file_suffix(seams, 1))
                 if n > 1 else "Save the breviary as")
        path = filedialog.asksaveasfilename(
            parent=self.win, title=title, defaultextension=".pdf",
            initialdir=folder if os.path.isdir(folder) else os.path.expanduser("~"),
            initialfile=name, filetypes=[("PDF", "*.pdf")])
        if path:
            self.run(path)

    def run(self, path):
        """Build on a worker thread (also used by tests)."""
        parts = self.parts()
        version = self.version.get()
        lang1, lang2 = self.languages_chosen()
        layout = self.layout()
        alternatives = self.alternatives.get()
        references = self.references.get()
        customs = self.customs()
        seams = self.seams_chosen() if self.volumes_wanted() > 1 else []
        self.cancel.clear()
        self.last_pdf = self.book.volume_path(path, seams, 0) if seams else path
        self._busy(True)
        self.bar.config(value=0, maximum=100)
        self.status.config(text="Starting…")

        def progress(stage, done, total):
            self.events.put(("progress", stage, done, total))

        def work():
            try:
                if seams:
                    books = self.book.build_volumes(
                        parts, version, lang1, lang2, layout, path, self.perl, self.dumper,
                        self.web_root, self.typst, seams, progress=progress,
                        cancelled=self.cancel.is_set, alternatives=alternatives,
                        perl_libs=self.engine.perl_libs, references=references, customs=customs)
                    self.events.put(("done-volumes", [p for p, _o in books]))
                    return
                offices = self.book.build(parts, version, lang1, lang2, layout, path, self.perl,
                                          self.dumper, self.web_root, self.typst,
                                          progress=progress, cancelled=self.cancel.is_set,
                                          alternatives=alternatives, perl_libs=self.engine.perl_libs,
                                          references=references, customs=customs)
                self.events.put(("done", path, len(offices), []))
            except dopdf.Cancelled:
                self.events.put(("cancelled",))
            except Exception as exc:  # shown to the user; the app stays usable
                self.events.put(("error", exc))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
