#!/usr/bin/perl
# Engine probe (breviary-builder research): calls the engine's own loader,
# setupstring(), and its display expansion, resolve_refs(), outside any web
# request. Copy into web/cgi-bin/horas/ and run from there with the bundled perl:
#   perl engine-probe.pl "Rubrics 1960 - 1960" English Commune/C4.txt Oratio
# Delete the copy afterwards: anything in cgi-bin is servable by the app.
use utf8;
use FindBin qw($Bin);
use CGI;
use lib "$Bin/..";
use DivinumOfficium::LanguageTextTools qw(prayer rubric translate load_languages_data omit_regexp);
use DivinumOfficium::RunTimeOptions qw(check_version check_language);
binmode(STDOUT, ':encoding(UTF-8)');

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

my ($v, $lang, $file, $section, $date) = @ARGV;
$ENV{QUERY_STRING} = "version=$v&lang1=Latin&lang2=$lang";
$ENV{REQUEST_METHOD} = 'GET';
our $q = new CGI;
getini('horas');
set_runtime_options('general');
set_runtime_options('parameters');
our $version = check_version($v);
our ($lang1, $lang2) = ('Latin', $lang);
our $expand = 'tota';
our $missa = 0;
our $hora = 'Vespera';
load_languages_data('Latin', $lang, 'English', $version, 0);
precedence($date || '1-13-2026');    # a plain ferial day: fills the engine's day globals

my $s = setupstring($lang, $file);
my $t = $s->{$section} // die "no [$section] in $file (have: " . join(', ', sort keys %$s) . ")\n";
print "==== raw [$section] ====\n$t\n==== resolve_refs ====\n", resolve_refs($t, $lang), "\n";
