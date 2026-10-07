#!/usr/bin/perl
# Dump a part of the breviary as JSON, resolved by the Divinum Officium engine.
#
#   perl engine_dump.pl --horas <web>/cgi-bin/horas --version "Rubrics 1960 - 1960"
#        --lang1 Latin --lang2 English --part commune|sancti|tempora|fixed|canticles|missa
#        --out part.json [--only C4,C5]
#
# "fixed" is the texts that are no office's own: the prayers before and after
# the Office, the Te Deum, the absolutions and blessings of Matins, the final
# antiphons of Our Lady, and the Psalter's seasonal sets the book prints
# whole. "canticles" sets out the psalms and canticles numbered in --only.
# "missa" is the proper of each Sunday's Mass (see load_missa).
#
# Nothing here decides what a text says. Every file is loaded with the engine's
# own setupstring() (version conditions, @-references, language fallback, the
# Monastic / Cistercian / Dominican overlays) and every section is expanded
# with its own resolve_refs() ($Per Dominum, &psalm, V./R., rubrics). The only
# knowledge added here is *which files make up a part, in what order* -- and
# even that comes from the engine: the version's calendar chain for the saints
# (get_from_directorium 'kalendar') and its variant table for the season
# (get_from_directorium 'tempora').
#
# Lives outside cgi-bin on purpose, so the app's web server can never run it.

use utf8;
use strict;
no strict 'vars';
no warnings 'once';

my %opt;
BEGIN {
  my @a = @ARGV;
  while (@a) {
    my $k = shift @a;
    $opt{$1} = shift @a if $k =~ /^--(\w+)$/;
  }
  die "usage: engine_dump.pl --horas DIR --version V --lang1 L --lang2 L --part P --out F\n"
    unless $opt{horas} && -d $opt{horas} && $opt{version} && $opt{part} && $opt{out};
  # The engine finds its data relative to the calling script ($FindBin::Bin,
  # i.e. web/cgi-bin/horas). Point it there before any engine module loads.
  require FindBin;
  $FindBin::Bin = $FindBin::RealBin = $opt{horas};
  chdir $opt{horas} or die "cannot chdir to $opt{horas}: $!\n";
}

use FindBin qw($Bin);
use lib "$FindBin::Bin/..";
use CGI;
use JSON::PP;
use DivinumOfficium::LanguageTextTools qw(prayer rubric translate load_languages_data omit_regexp);
use DivinumOfficium::RunTimeOptions qw(check_version check_language);
use DivinumOfficium::Directorium qw(get_from_directorium);

require "$Bin/../DivinumOfficium/SetupString.pl";
require "$Bin/horascommon.pl";
require "$Bin/../DivinumOfficium/dialogcommon.pl";
require "$Bin/webdia.pl";
require "$Bin/../DivinumOfficium/setup.pl";
require "$Bin/horas.pl";
require "$Bin/horasscripts.pl";
require "$Bin/specials.pl";
require "$Bin/specmatins.pl";
require "$Bin/monastic.pl";
require "$Bin/altovadum.pl";

my $lang1 = $opt{lang1} || 'Latin';
my $lang2 = $opt{lang2} || 'English';

# The same initialisation officium.pl performs, without an HTTP request.
$ENV{REQUEST_METHOD} = 'GET';
$ENV{QUERY_STRING} = '';
our $q = new CGI;
getini('horas');
set_runtime_options('general');
set_runtime_options('parameters');
# The display fonts come from the user's settings, which set_runtime_options()
# cannot read outside a web request; without them setfont() returns bare text
# and the small red rubrics (/:Fit reverentia:/) would print as plain words.
# These are the site's defaults (horas.setup).
our ($smallblack, $redfont, $initiale, $largefont, $smallfont, $titlefont);
$smallblack ||= '-1';
$redfont ||= ' italic red';
$initiale ||= '+2 bold italic red';
$largefont ||= '+1 bold italic red';
$smallfont ||= '1 red';
$titlefont ||= '+1 red';
our $version = check_version($opt{version}) or die "unknown version $opt{version}\n";
our ($lang1g, $lang2g) = ($lang1, $lang2);
$main::lang1 = $lang1;
$main::lang2 = $lang2;
our $expand = 'tota';
our $missa = 0;
our $hora = 'Vespera';
our $datafolder = "$Bin/../../www/horas";
load_languages_data($lang1, $lang2, 'English', $version, 0);
# A plain ferial day fills the engine's day globals; the parts themselves are
# not tied to a date.
precedence('1-13-2026');

my $horas_data = "$Bin/../../www/horas";

sub exists_latin { -e "$horas_data/Latin/$_[0].txt" }

# Whether setupstring() finds a file, as it looks for one (checklatinfile):
# a Cistercian file may be the Monastic one, a Monastic or Dominican one the
# Roman one -- TemporaM/Pent03-2Feria is read from Tempora/.
sub found_latin {
  my $f = shift;
  return 1 if exists_latin($f);
  return 1 if $f =~ s/^(Sancti|Tempora|Commune)Cist/$1M/ && exists_latin($f);
  return 1 if $f =~ s/^(Sancti|Tempora|Commune)(?:M|OP)/$1/ && exists_latin($f);
  return 0;
}

sub with_dir {
  my ($part, $name) = @_;
  my $p = subdirname($part, $version) . $name;
  return $p if found_latin($p);
  return "$part/$name" if exists_latin("$part/$name");
  return;
}

# -- which files, in which order ---------------------------------------------

sub natural_key {
  my $s = shift;
  $s =~ s/(\d+)/sprintf('%06d', $1)/ge;
  $s;
}

sub commune_files {
  my %seen;
  my @names;
  # A version with its own Common (Monastic, Cistercian, Dominican) lists only
  # that one's offices -- setupstring() still fills each from the Roman file
  # where the version's copy is partial -- rather than adding Roman-only ones.
  my $own = subdirname('Commune', $version);
  my @dirs = ($own ne 'Commune/' && -d "$horas_data/Latin/$own") ? ($own) : ('Commune/');
  for my $dir (@dirs) {
    opendir(my $dh, "$horas_data/Latin/$dir") or next;
    for (readdir $dh) {
      next unless /^(C\d+[a-z]?(?:-\d)?[a-zA-Z]{0,4})\.txt$/;
      my $n = $1;
      next if $n =~ /^C9/;    # the Office of the Dead: its own part
      next if $n =~ /^C12/;   # the Little Office of Our Lady: its own part
      push @names, $n unless $seen{$n}++;
    }
  }
  map { { file => with_dir('Commune', $_), key => $_ } } grep { with_dir('Commune', $_) }
    sort { natural_key($a) cmp natural_key($b) } @names;
}

sub sancti_files {
  my @out;
  for my $m (1 .. 12) {
    for my $d (1 .. 31) {
      my $mmdd = sprintf('%02d-%02d', $m, $d);
      next unless exists_latin("Sancti/$mmdd") || exists_latin(subdirname('Sancti', $version) . $mmdd)
        || get_from_directorium('kalendar', $version, $mmdd);
      my $entry = get_from_directorium('kalendar', $version, $mmdd) || '';
      $entry =~ s/;;.*//;
      next if !$entry || $entry =~ /^X+$/;
      my @files = split /~/, $entry;
      for my $i (0 .. $#files) {
        my $f = with_dir('Sancti', $files[$i]) or next;
        push @out, { file => $f, key => $files[$i], date => $mmdd, commemoratio => ($i ? JSON::PP::true : JSON::PP::false) };
      }
    }
  }
  # Feasts kept on a Sunday rather than a date -- Christ the King, the last
  # Sunday of October -- are not in the calendar but in the version's transfer
  # tables ("10-29=10-DU"), year by year. They go in after the last day they
  # can fall on, with no date of their own but the days they can take.
  my %days = %{ transferred()->{sancti} };
  for my $name (sort keys %days) {
    my $f = with_dir('Sancti', $name) or next;
    my @d = sort keys %{ $days{$name} };
    my $i = 0;
    $i++ while $i < @out && ($out[$i]{date} // '') le $d[-1];
    splice @out, $i, 0, { file => $f, key => $name, date => '', days => \@d,
                          commemoratio => JSON::PP::false };
  }
  @out;
}

# What only the transfer tables name, year by year: {sancti => {name =>
# {MM-DD => 1}}} for the feasts they put on a Sunday ("10-DU", Dominica
# ultima; "09-DT", Dominica tertia), with every day each can take, and
# {tempora => {name => 1}} for the offices of the Season they put on a date
# ("01-03=Tempora/Nat2-0r": the Second Sunday after Christmas, Monastic
# 1963). Four hundred years hold every dominical letter with every date of
# Easter, as the tables are kept (Directorium load_transfers).
my $TRANSFERRED;
sub transferred {
  return $TRANSFERRED if $TRANSFERRED;
  my (%sancti, %tempora);
  for my $year (2000 .. 2399) {
    for my $m (1 .. 12) {
      for my $d (1 .. 31) {
        my $mmdd = sprintf('%02d-%02d', $m, $d);
        my $v = get_from_directorium('transfer', $version, $mmdd, $year) or next;
        $v =~ s/;;.*//;
        for (split /~/, $v) {
          $sancti{$_}{$mmdd} = 1 if /^\d\d-D[A-Z]{1,2}r?$/;
          $tempora{$1} = 1 if m{^Tempora/(\S+)$};
        }
      }
    }
  }
  $TRANSFERRED = { sancti => \%sancti, tempora => \%tempora };
}

sub tempora_files {
  my @base;
  push @base, map { my $w = $_; map { "Adv$w-$_" } 0 .. 6 } 1 .. 4;
  push @base, 'Nat1-0', 'Nat2-0';
  push @base, map { my $w = $_; map { "Epi$w-$_" } 0 .. 6 } 1 .. 6;
  push @base, map { my $w = $_; map { "Quadp$w-$_" } 0 .. 6 } 1 .. 3;
  push @base, map { my $w = $_; map { "Quad$w-$_" } 0 .. 6 } 1 .. 6;
  push @base, map { my $w = $_; map { "Pasc$w-$_" } 0 .. 6 } 0 .. 7;
  push @base, map { my $w = $_; map { sprintf("Pent%02d-%d", $w, $_) } 0 .. 6 } 1 .. 24;
  # The Matins readings "of the month" (August to November).
  opendir(my $dh, "$horas_data/Latin/Tempora") or die;
  push @base, sort grep { /^\d{3}-\d$/ } map { /^(.*)\.txt$/ ? $1 : () } readdir $dh;

  my @out;
  for my $b (@base) {
    my $key = subdirname('Tempora', $version) . $b;
    my $mapped = get_from_directorium('tempora', $version, $key, 0, 'Generale') || '';
    $mapped =~ s/;;.*//;
    $mapped =~ s/~.*//;
    my $f;
    $f = $mapped if $mapped && $mapped =~ /Tempora/ && found_latin($mapped);
    $f ||= with_dir('Tempora', $b);
    next unless $f;
    push @out, { file => $f, key => $b };
  }
  # Offices of the Season only the transfer tables name, each after the one
  # it is named from (Nat2-0r after Nat2-0) -- unless it is that office over
  # again with a change or two ("@Tempora/Nat1-0" + commemorations: the
  # Sunday within the Octave on 29 December), which the book prints once.
  my %in = map { $_->{file} => 1 } @out;
  for my $name (sort keys %{ transferred()->{tempora} }) {
    next if grep { $_->{key} eq $name } @out;
    my $f = with_dir('Tempora', $name) or next;
    next if $in{$f} || copy_of($f, \%in);
    my $i = -1;
    for my $j (0 .. $#out) {
      $i = $j if index($name, $out[$j]{key}) == 0 && ($i < 0 || length $out[$j]{key} > length $out[$i]{key});
    }
    splice @out, ($i < 0 ? scalar @out : $i + 1), 0, { file => $f, key => $name };
    $in{$f} = 1;
  }
  @out;
}

# Whether a file is wholly another of these files ("@TemporaM/Nat1-0" as its
# first line), as setupstring() reads it.
sub copy_of {
  my ($file, $in) = @_;
  my $path = "$horas_data/Latin/$file.txt";
  open(my $fh, '<:encoding(UTF-8)', $path) or return 0;
  while (my $line = <$fh>) {
    next unless $line =~ /\S/;
    return $line =~ /^\s*@(\S+?)\s*$/ && $in->{$1} ? 1 : 0;
  }
  0;
}

sub fixed_files {
  (
    { file => 'Psalterium/Common/Prayers', key => 'prayers',
      keys => ['Ante', 'Pater noster', 'Ave Maria', 'Credo', 'Post', 'Te Deum', 'Te decet'] },
    { file => 'Psalterium/Benedictions', key => 'benedictions' },
    { file => 'Psalterium/Mariaant', key => 'mariaant' },
    # The monastic Matins: the chapter closing the second nocturn in each
    # season, and the short lesson of the first on summer weekdays.
    { file => 'Psalterium/Special/Matutinum Special', key => 'matutinum',
      keys => ['MM Capitulum', map({ "MM Capitulum $_" } qw(Adv Nat Epi Quad Quad5 Pasch)),
               map({ "MM LB$_" } 1 .. 6), 'MM LB Pasch'] },
    # Eastertide: the versicles of the Nocturns on feasts that take their
    # Matins from the Psalter (psalmi_matutinum: "Pasch Ant Feria|Dominica").
    { file => 'Psalterium/Psalmi/Psalmi matutinum', key => 'psalmi_matutinum',
      keys => ['Pasch Ant Feria', 'Pasch Ant Dominica'] },
    # A ferial Saturday's Benedictus antiphon (the Saturdays sampled through
    # the year are Our Lady's, and the Ember Saturday has its own).
    { file => 'Psalterium/Special/Major Special', key => 'major', keys => ['Feria7 Ant 2'] },
    # The Office of Our Lady on Saturday has an absolution and blessings of its own.
    (with_dir('Commune', 'C10') ? { file => with_dir('Commune', 'C10'), key => 'c10',
                                     keys => ['Benedictio'] } : ()),
  );
}

# -- loading and expanding ----------------------------------------------------

my %META = map { $_ => 1 } qw(__preamble Officium Rank Rule Comment Name);

sub hour_of {
  my $k = shift;
  return 'Matutinum' if $k =~ /Matutinum|Lectio|Responsory|Invit|Nocturn|Evangelium|Te Deum/i;
  for my $h (qw(Laudes Prima Tertia Sexta Nona Completorium Vespera)) {
    return $h if $k =~ /$h/i;
  }
  return 'Vespera';
}

sub raw_section_order {
  my $file = shift;
  my @order;
  for my $path ("$horas_data/Latin/$file.txt") {
    open(my $fh, '<:encoding(UTF-8)', $path) or next;
    while (<$fh>) {
      push @order, $1 if /^\s*\[([^\]]+)\]/;
    }
  }
  my %seen;
  grep { !$seen{$_}++ } @order;
}

# The file a file takes whole (its first line "@CommuneM/C2"), or ''.
sub raw_parent {
  my $file = shift;
  open(my $fh, '<:encoding(UTF-8)', "$horas_data/Latin/$file.txt") or return '';
  my $first = <$fh> // '';
  $first =~ s/^\x{FEFF}//;
  $first =~ /^\s*@([^:\s]+)\s*$/ ? $1 : '';
}

sub plain {
  my $t = shift // '';
  $t =~ s/<[^>]*>//g;
  $t =~ s/\s+/ /g;
  $t =~ s/^\s+|\s+$//g;
  $t;
}

# The last steps of the website's display chain (webdia.pl) that do not depend
# on a date: the version's Latin spelling (1960 writes "Iesu", "eúndem"; older
# versions "Génitrix"), and the chant edition's markers, which are not text.
sub display {
  my ($t, $lang) = @_;
  $t = spell_var($t) if $lang =~ /Latin(\-bea)?$/i;
  $t =~ s/wait[0-9]+//ig;
  $t =~ s/\{\:.*?\:\}//sg;
  $t =~ s/\`//g;
  # setfont() of nothing (a "!" line with no text after it: the English
  # Introit of the Sunday after Epiphany) is a <FONT> with no </FONT>, and
  # everything after it took its colour. Nothing to set: no tag.
  $t =~ s/<FONT[^>]*>(?=[ \t]*(?:<br\s*\/?>|\n|\z))//gi;
  $t;
}

my $RANKTABLE;
sub ranktable {
  $RANKTABLE ||= [split /
/, (setupstring($lang1, 'Psalterium/Comment.txt')->{Festa} // '')];
}

# A lesson that ends "&teDeum" would bring the whole hymn with it, 492 times
# over. A book prints the Te Deum once, in the Ordinary, and a rubric where
# it is said.
sub te_deum_rubric {
  my ($t, $lang) = @_;
  return $t unless defined $t;
  my $rubric = $lang =~ /^English/ ? 'Te Deum, as in the Ordinary.' : 'Te Deum, ut in Ordinario.';
  $t =~ s/^[ \t]*&teDeum\S*[ \t]*$/!$rubric/mg;
  $t;
}

# A section as the book expands it: the Te Deum as a rubric, and no "~" at
# the very end -- it joins a line to the next, and resolve_refs() drops a last
# line that waits for one (the Mass Gospels of Pasc2-0 and Pent07-0).
sub for_book {
  my ($t, $lang) = @_;
  return $t unless defined $t;
  $t = te_deum_rubric($t, $lang);
  $t =~ s/~\s*\z//;
  # "v.~" and the title on the next line (Palm Sunday's Passion, 1960): the
  # engine makes a "v." line's first letter a large initial before it joins
  # the lines, so the initial is of nothing -- setfont() then returns its
  # <FONT> without the </FONT>, and the whole Passion was set as the title.
  $t =~ s/^([ \t]*v\.)~[ \t]*\n[ \t]*/$1 /mg;
  # The order of Prime of All Souls (and of the monastic commemoration of the
  # Order's dead) says the Martyrology there: expanded, it was the entry for
  # the day the dump is set to (14 January). A book says where it is read.
  my $martyrology = $lang =~ /^English/ ? 'The Martyrology, as in the Ordinary.'
    : 'Martyrologium, ut in Ordinario.';
  $t =~ s/^[ \t]*&special\('#Martyrologium'[^)]*\)[ \t]*$/!$martyrology/mg;
  $t;
}

# &special('Initial', 'Latin') in a section -- the order of an hour of All
# Souls: its [Initial], its [Oratio mortuorum] -- is the office's own section
# of that name. The engine expands it against the day it is saying; the dump's
# day is another, so it came out empty. The section goes in its place (and
# not again on its own: see specials_inside).
sub expand_specials {
  my ($t, @hashes) = @_;
  return $t unless defined $t && $t =~ /&special\(/;
  $t =~ s{^[ \t]*&special\('([^'#][^']*)'[^)]*\)[ \t]*$}{
    my ($name, $whole) = ($1, $&);
    my ($own) = grep { defined } map { $_->{$name} } @hashes;
    defined $own ? $own =~ s/\n+\z//r : $whole
  }mge;
  $t;
}

sub specials_inside {
  my ($s) = @_;
  my %inside;
  for my $v (values %$s) {
    next unless defined $v;
    $inside{$1} = 1 while $v =~ /&special\('([^'#][^']*)'/g;
  }
  %inside;
}

# setupstring() leaves the references in [Commemoratio ...] and [Evangelium ...]
# sections as they are ("@Sancti/06-26:Evangelium"): the engine follows them
# only when it says the section -- the breviary file's section, else the Mass's
# (monastic.pl lectioE). The same here, so the book prints the text.
sub late_refs {
  my ($t, $lang, $key, $depth) = @_;
  return $t unless defined $t && $t =~ /^\s*@/m && ($depth // 0) < 4;
  my @out;
  for my $line (split /\n/, $t) {
    if ($line =~ /^\s*@([A-Za-z]+\/[^:\s]+)(?::([^:]*))?(?::(.*))?\s*$/) {
      my ($file, $sec, $subs) = ($1, $2, $3);
      $sec = $key if !defined $sec || $sec eq '';
      my $text;
      my $h = setupstring($lang, "$file.txt");
      $text = $h->{$sec} if ref $h && defined $h->{$sec};
      if (!defined $text) {
        (my $mass = $file) =~ s/^(Sancti|Tempora|Commune)(?:M|OP|Cist)\//$1\//;
        my $mh = setupstring("../missa/$lang", "$mass.txt");
        $text = $mh->{$sec} if ref $mh && defined $mh->{$sec};
      }
      if (defined $text && $text =~ /\S/) {
        &do_inclusion_substitutions(\$text, $subs) if defined $subs && $subs ne '';
        $text =~ s/\n+$//;
        push @out, late_refs($text, $lang, $sec, ($depth // 0) + 1);
        next;
      }
    }
    push @out, $line;
  }
  join("\n", @out);
}

# The Gospel of monastic Matins, read after the twelfth lesson and before the
# Te decet laus (monastic.pl: lectioE, in its Matins of three Nocturns). The
# engine takes the office's own [Evangelium], else its Mass's -- the same file
# under ../missa, the M/Cist folder dropped as lectioE drops it -- else the
# Common's. The data keeps most of them with the Mass, where a book built
# from the office files alone never looks; the Common's the book prints with
# the Common.
sub twelve_lessons {
  my $s = shift;
  my $rule = $s->{Rule} // '';
  return 1 if $rule =~ /12 lectiones/;
  return 0 if $rule =~ /3 lectiones/i;
  my @rank = split /;;/, ($s->{Rank} // '');
  my $r = ($rank[2] // '') =~ /^\s*([\d.]+)/ ? $1 : 0;
  # $dayname[1] is the rank's name and class (horascommon.pl).
  return 0 if join(' ', grep { defined } @rank[0, 1]) =~ /feria|sabbato|infra octavam/i;
  ($r >= 4 && $version =~ /divino/i) || ($r >= 2 && $version =~ /trident/i);
}

sub mass_gospel {
  my ($file, $lang) = @_;
  (my $win = "$file.txt") =~ s/(?:M|OP|Cist)//g;    # lectioE: "no M or OP folder in missa"
  my $m = setupstring("../missa/$lang", $win);
  return unless ref $m && defined $m->{Evangelium} && $m->{Evangelium} =~ /\S/;
  late_refs($m->{Evangelium}, $lang, 'Evangelium');
}

sub monastic_gospel {
  my ($item, $s1) = @_;
  return unless $version =~ /^Monastic/ && $item->{file} =~ /^(?:Sancti|Tempora)/;
  return if defined $s1->{Evangelium} || !twelve_lessons($s1);
  my $g1 = mass_gospel($item->{file}, $lang1);
  return unless defined $g1 && $g1 =~ /\S/;
  local $hora = 'Matutinum';
  my $l2 = '';
  if ($lang2 ne $lang1) {
    my $g2 = mass_gospel($item->{file}, $lang2);
    $g2 = $g1 unless defined $g2 && $g2 =~ /\S/;
    $l2 = display(eval { resolve_refs(for_book($g2, $lang2), $lang2) } // '', $lang2);
  }
  return { key => 'Evangelium', from => 'missa', l2 => $l2,
           l1 => display(eval { resolve_refs(for_book($g1, $lang1), $lang1) } // '', $lang1) };
}

# Where a monastic Matins says the Te Deum: only after the twelfth
# responsory. The engine takes it off every lesson (specmatins.pl, and
# monastic.pl for Our Lady on Saturday) and says it where tedeum_required()
# holds, "$num == 12" in the monastic versions: a Matins of three lessons
# (Easter Monday, a feast of the third class) has none. The data puts it where
# a Roman Matins would, at the end of the last lesson, so the book printed
# "Te Deum" before Responsory XII, on days of three lessons, and not at all
# where the lessons have none. For a monastic version, a function (section,
# text) -> text that takes it off every lesson -- in every office alike, so a
# lesson still matches its copy elsewhere -- and load_office() gives an
# office of twelve lessons a section of its own ("Te Deum M"), which the book
# puts after Responsory XII. Not added to the responsory itself: that would
# no longer match the same responsory printed under another number.
sub monastic_te_deum {
  return unless $version =~ /^Monastic/;
  sub {
    my ($k, $t) = @_;
    return $t unless defined $t && $k =~ /^Lectio/;
    $t =~ s/^[ \t]*_?[ \t]*\n(?=[ \t]*&teDeum)//mg;    # with the stanza break before it
    $t =~ s/^[ \t]*&teDeum\S*[ \t]*\n?//mg;
    $t;
  };
}

sub load_office {
  my ($item) = @_;
  my $file = "$item->{file}.txt";
  my %by_lang;
  for my $lang ($lang1, $lang2) {
    next if $by_lang{$lang};
    my $s = setupstring($lang, $file);
    $by_lang{$lang} = ref $s ? $s : {};
  }
  my $s1 = $by_lang{$lang1};
  my $s2 = $by_lang{$lang2};
  return unless %$s1;

  my @keys = raw_section_order($item->{file});
  my %have = map { $_ => 1 } @keys;
  push @keys, sort grep { !$have{$_} } keys %$s1;
  @keys = @{ $item->{keys} } if $item->{keys};    # only these sections, in this order

  my $te_deum = monastic_te_deum();
  my %inside = specials_inside($s1);
  my @sections;
  for my $k (@keys) {
    next if $META{$k} || $inside{$k} || !defined $s1->{$k} || $s1->{$k} !~ /\S/;
    local $hora = hour_of($k);
    my $late = $k =~ /Commemoratio|Evangelium|LectioE/;
    my $t1 = $late ? late_refs($s1->{$k}, $lang1, $k) : $s1->{$k};
    my $t2 = $s2->{$k} // $s1->{$k};
    $t2 = late_refs($t2, $lang2, $k) if $late;
    $t1 = expand_specials($t1, $s1);
    $t2 = expand_specials($t2, $s2, $s1);
    ($t1, $t2) = map { $te_deum->($k, $_) } $t1, $t2 if $te_deum;
    my $h1 = display(eval { resolve_refs(for_book($t1, $lang1), $lang1) } // '', $lang1);
    my $h2 = $lang2 eq $lang1 ? ''
      : display(eval { resolve_refs(for_book($t2, $lang2), $lang2) } // '', $lang2);
    push @sections, { key => $k, l1 => $h1, l2 => $h2 };
  }
  if ($te_deum && $item->{file} =~ /^(?:Sancti|Tempora|Commune)/
      && (twelve_lessons($s1) || (defined $s1->{Responsory12} && $s1->{Responsory12} =~ /\S/))) {
    local $hora = 'Matutinum';
    push @sections, { key => 'Te Deum M',
      l1 => display(eval { resolve_refs(for_book('&teDeum', $lang1), $lang1) } // '', $lang1),
      l2 => $lang2 eq $lang1 ? ''
        : display(eval { resolve_refs(for_book('&teDeum', $lang2), $lang2) } // '', $lang2) };
  }
  if (my $gospel = monastic_gospel($item, $s1)) {
    push @sections, $gospel;
  }
  # Most files name the office in [Officium]; some (1 January, Epiphany ...)
  # only in the first field of [Rank], which setupstring() has already
  # resolved for the version ("Die Octavæ Nativitatis Domini" in 1960).
  my @rank = split /;;/, ($s1->{Rank} // '');
  my @rank2 = split /;;/, ($s2->{Rank} // '');
  # The rank's name as the version calls it: the engine's own table (the
  # [Festa] list of Psalterium/Comment.txt, "III. classis" under the 1960
  # rubrics, "Duplex majus" before), indexed by the rank's number.
  my $rankname = ($rank[2] // '') =~ /^\s*(\d+)/ ? ranktable()->[$1] : '';
  my $title1 = plain($s1->{Officium}) || plain($rank[0]);
  my $title2 = plain($s2->{Officium}) || plain($rank2[0]) || $title1;
  return {
    %$item,
    parent => raw_parent($item->{file}),
    title => [$title1, $title2],
    rank => ($rank[1] // ''),
    rankname => ($rankname // ''),
    rule => ($s1->{Rule} // ''),
    sections => \@sections,
  };
}

# -- the Sunday Masses -----------------------------------------------------------

# The proper of each Sunday's Mass, for a book that prints them as a part of
# their own: of each Sunday of the Season, and each feast the transfer tables
# keep on a Sunday (Christ the King), the Mass file the engine reads its
# monastic Gospel from (lectioE: the office's file under ../missa, without the
# M/Cist folder), loaded by setupstring() and expanded as an office's sections
# are. The Collect is the office's own; the Kyrie, Gloria, Credo and the
# Ordinary of the Mass are not proper.
my @MISSA = qw(Introitus Lectio Graduale Sequentia Evangelium Offertorium Secreta Communio Postcommunio);

# Each named as the office of the day is (the Proper of the Season's title):
# read before any Mass file, as setupstring()'s caches, keyed by the file's
# name, can hand a later office the Mass's file under the same name (a Mass
# that has no file of its own is read from the office's), and the engine
# then went round its references for ever (Pent03-0o, Monastic 1963).
sub missa_files {
  my @out = grep { $_->{key} =~ /^(?:Adv|Nat|Epi|Quadp|Quad|Pasc|Pent)\d+-0/ } tempora_files();
  push @out, grep { $_->{days} } sancti_files();
  map {
    my $item = $_;
    my @title = map {
      my $s = setupstring($_, "$item->{file}.txt");
      $s = {} unless ref $s;
      my @rank = split /;;/, ($s->{Rank} // '');
      plain($s->{Officium}) || plain($rank[0]);
    } $lang1, $lang2;
    $title[0] ||= $item->{key};
    $title[1] ||= $title[0];
    +{ %$item, office => $item->{file}, title => \@title };
  } @out;
}

sub load_missa {
  my ($item) = @_;
  (my $file = "$item->{file}.txt") =~ s/(?:M|OP|Cist)//g;    # as lectioE
  my %mass = map { $_ => setupstring("../missa/$_", $file) } grep { defined } $lang1, $lang2;
  return unless ref $mass{$lang1} && %{ $mass{$lang1} };
  local $hora = 'Laudes';
  my @sections;
  for my $k (@MISSA) {
    my $t1 = $mass{$lang1}{$k};
    next unless defined $t1 && $t1 =~ /\S/;
    my $t2 = ref $mass{$lang2} ? $mass{$lang2}{$k} : undef;
    $t2 = $t1 unless defined $t2 && $t2 =~ /\S/;
    $t1 = late_refs($t1, $lang1, $k);
    $t2 = late_refs($t2, $lang2, $k);
    my $h1 = display(eval { resolve_refs(for_book($t1, $lang1), $lang1) } // '', $lang1);
    my $h2 = $lang2 eq $lang1 ? ''
      : display(eval { resolve_refs(for_book($t2, $lang2), $lang2) } // '', $lang2);
    push @sections, { key => $k, l1 => $h1, l2 => $h2 };
  }
  return { %$item, file => ($file =~ s/\.txt$//r), rank => '', rule => '', sections => \@sections };
}

# -- main ----------------------------------------------------------------------

my @items =
    $opt{part} eq 'commune' ? commune_files()
  : $opt{part} eq 'sancti'  ? sancti_files()
  : $opt{part} eq 'tempora' ? tempora_files()
  : $opt{part} eq 'fixed'   ? fixed_files()
  : $opt{part} eq 'missa'   ? missa_files()
  : $opt{part} eq 'canticles' ? ()
  : die "unknown part $opt{part}\n";
if ($opt{only} && $opt{part} ne 'canticles') {
  my %only = map { $_ => 1 } split /,/, $opt{only};
  @items = grep { $only{$_->{key}} } @items;
}

my @offices;
my $n = 0;
# The canticles (and psalms) named in --only, as the engine sets them out
# (&psalm: its title, the verses numbered, the Gloria Patri): the monastic
# third Nocturns sing canticles that no weekly Psalter holds.
if ($opt{part} eq 'canticles') {
  my @sections;
  for my $num (grep { /^\d+$/ } split /,/, ($opt{only} // '')) {
    local $hora = 'Matutinum';
    my $h1 = display(eval { resolve_refs("&psalm($num)", $lang1) } // '', $lang1);
    my $h2 = $lang2 eq $lang1 ? '' : display(eval { resolve_refs("&psalm($num)", $lang2) } // '', $lang2);
    push @sections, { key => $num, l1 => $h1, l2 => $h2 } if $h1 =~ /\S/;
  }
  push @offices, { key => 'canticles', file => 'Psalterium/Psalmorum', title => ['', ''],
                   sections => \@sections };
}
for my $item (@items) {
  my $o = eval { $opt{part} eq 'missa' ? load_missa($item) : load_office($item) };
  if ($@) {
    push @offices, { %$item, error => "$@", sections => [] };
  } elsif ($o) {
    push @offices, $o;
  }
  printf STDERR "\r%s %d/%d", $opt{part}, ++$n, scalar @items if $opt{progress};
}
print STDERR "\n" if $opt{progress};

# Offices that take "the rest" from another feast ([Rule] ex Sancti/05-08):
# that feast may not be in this version's calendar at all (1960 dropped
# 8 May, yet Michaelmas still borrows from it), so load every such source
# too. The builder merges it in, or refers to it when it is in the book.
my %have = map { $_->{file} => 1 } @offices;
my @referenced;
my @queue = @offices;
while (my $o = shift @queue) {
  for my $line (split /\n/, ($o->{rule} // '')) {
    next unless $line =~ /^\s*(?:ex|vide)\s+((?:Sancti|Tempora)\w*\/[^\s;]+)/i;
    my ($dir, $name) = $1 =~ m{^(Sancti|Tempora)\w*/(.+)$}i or next;
    my $file = with_dir(ucfirst lc $dir, $name);
    next if !$file || $have{$file}++;
    my $r = eval { load_office({ file => $file, key => $name }) };
    next unless $r;
    push @referenced, $r;
    push @queue, $r;    # a source can itself borrow from another
  }
}

open(my $out, '>:raw', $opt{out}) or die "cannot write $opt{out}: $!\n";
print $out JSON::PP->new->utf8->canonical->encode({
  version => $opt{version},
  engine_version => $version,
  lang1 => $lang1,
  lang2 => $lang2,
  part => $opt{part},
  offices => \@offices,
  referenced => \@referenced,
});
close $out;
printf STDERR "%s: %d offices\n", $opt{part}, scalar @offices;
