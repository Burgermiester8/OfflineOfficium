#!/usr/bin/env python3
"""
Serve a local Divinum Officium instance from the command line.

    python do-serve.py --root repo/web [--port 8000] [--lib DIR]

The server itself lives in docgi.py. For a double-clickable version, see
DivinumOfficium.py / build-exe.py.
"""

import argparse
import os
import shutil
import sys

import docgi


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="repo/web", help="path to the repo web/ dir")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--perl", default=None, help="perl executable (default: from PATH)")
    ap.add_argument(
        "--lib",
        action="append",
        default=[],
        help="extra Perl lib dir (repeatable), e.g. a vendored CGI.pm",
    )
    args = ap.parse_args()

    web_root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(web_root, "cgi-bin")):
        sys.exit("error: %s has no cgi-bin; point --root at the repo web/ dir" % web_root)

    perl = args.perl or shutil.which("perl")
    if not perl:
        sys.exit("error: perl not found on PATH; install Strawberry Perl or pass --perl")

    libs = [os.path.abspath(p) for p in args.lib]
    # A perl-lib/ next to this script is picked up automatically -- that is where
    # a vendored CGI.pm lives if the system perl does not have one.
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perl-lib")
    if os.path.isdir(here) and here not in libs:
        libs.append(here)

    server, port = docgi.make_server(
        web_root, perl, perl_libs=libs, bind=args.bind, port=args.port
    )
    print("Divinum Officium   http://%s:%d/" % (args.bind, port))
    print("  web root : %s" % web_root)
    print("  perl     : %s" % perl)
    if libs:
        print("  perl -I  : %s" % ", ".join(libs))
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
