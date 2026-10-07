"""
CGI server core for a local Divinum Officium instance. Standard library only.

Serves static files out of the repo's `web/` directory and executes the .pl
scripts under `web/cgi-bin` with a Perl interpreter.

Used by do-serve.py (command line) and DivinumOfficium.py (windowed app).

Note on Windows: `python -m http.server --cgi` cannot run .pl files here --
Python only prepends an interpreter for .py, so CreateProcess is handed the
script directly and rejects it. Its CGIHTTPRequestHandler is also deprecated
as of 3.13 and slated for removal. This module depends on neither.
"""

import mimetypes
import os
import socket
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Headers passed to CGI without the HTTP_ prefix, per RFC 3875.
_UNPREFIXED = {"content-type": "CONTENT_TYPE", "content-length": "CONTENT_LENGTH"}

CGI_TIMEOUT = 180  # the full-year Ordo legitimately takes several seconds


def perl_path(path):
    """`path` in a form Perl can open on Windows.

    Perl opens files through Windows' ANSI functions, and the engine joins its
    paths from pieces of more than one encoding, so a folder with an accent in
    its name (C:\\Users\\Thérèse\\...) reaches Windows garbled and no data file
    opens. Such a path is given by its short name instead (THRS~1), which is
    plain ASCII. Paths that are ASCII already, and other systems, are left be.
    A path that does not exist yet keeps its last part as it is.
    """
    if os.name != "nt" or not path or path.isascii():
        return path
    import ctypes

    if not os.path.exists(path):
        head, tail = os.path.split(path)
        return os.path.join(perl_path(head), tail) if head and head != path else path
    short = ctypes.windll.kernel32.GetShortPathNameW
    short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    short.restype = ctypes.c_uint32
    size = short(path, None, 0)
    buf = ctypes.create_unicode_buffer(size or 1)
    if size and short(path, buf, size) and buf.value:
        return buf.value
    return path  # short names are off on this drive: nothing better to give


def make_handler(web_root, perl, perl_libs=(), log=None):
    """Build a request handler bound to one web root and Perl interpreter."""
    web_root = perl_path(os.path.abspath(web_root))
    perl = perl_path(perl)
    perl_libs = [perl_path(os.path.abspath(p)) for p in perl_libs]
    emit = log or (lambda msg: sys.stderr.write(msg + "\n"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "divinum-officium-local/1.0"
        protocol_version = "HTTP/1.0"  # one request per connection; keeps this simple

        def do_GET(self):
            self.respond()

        def do_HEAD(self):
            self.respond()

        def do_POST(self):
            self.respond()

        # -- routing -----------------------------------------------------

        def respond(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            if path in ("", "/"):
                path = "/index.html"
            script = self.find_script(path)
            if script:
                self.run_cgi(script, parsed.query)
            else:
                self.serve_static(path)

        def find_script(self, path):
            """Return (fs_path, script_name, path_info) if the URL hits a .pl."""
            parts = [p for p in path.split("/") if p not in ("", ".", "..")]
            for i, part in enumerate(parts):
                if not part.endswith(".pl"):
                    continue
                fs = os.path.join(web_root, *parts[: i + 1])
                if os.path.isfile(fs):
                    tail = parts[i + 1 :]
                    name = "/" + "/".join(parts[: i + 1])
                    return fs, name, ("/" + "/".join(tail)) if tail else ""
            return None

        def safe_path(self, path):
            parts = [p for p in path.split("/") if p not in ("", ".", "..")]
            return os.path.join(web_root, *parts) if parts else web_root

        # -- static ------------------------------------------------------

        def serve_static(self, path):
            fs = self.safe_path(path)
            if os.path.isdir(fs):
                fs = os.path.join(fs, "index.html")
            if not os.path.isfile(fs):
                self.send_error(404, "Not Found")
                return
            ctype, _ = mimetypes.guess_type(fs)
            ctype = ctype or "application/octet-stream"
            if ctype.startswith("text/") and "charset" not in ctype:
                ctype += "; charset=utf-8"
            try:
                with open(fs, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(404, "Not Found")
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # -- cgi ---------------------------------------------------------

        def cgi_env(self, script_name, path_info, query, body_len):
            env = dict(os.environ)
            # We pass -I instead: PERL5LIB is split on ':', which mangles
            # Windows drive letters.
            env.pop("PERL5LIB", None)
            env.update(
                {
                    "GATEWAY_INTERFACE": "CGI/1.1",
                    "SERVER_SOFTWARE": self.server_version,
                    "SERVER_PROTOCOL": "HTTP/1.1",
                    "SERVER_NAME": self.server.server_name,
                    "SERVER_PORT": str(self.server.server_port),
                    "REQUEST_METHOD": self.command,
                    "REQUEST_URI": self.path,
                    "SCRIPT_NAME": script_name,
                    "SCRIPT_FILENAME": self.safe_path(script_name),
                    "PATH_INFO": path_info,
                    "QUERY_STRING": query,
                    "REMOTE_ADDR": self.client_address[0],
                    "REMOTE_HOST": self.client_address[0],
                    "DOCUMENT_ROOT": web_root,
                    "REDIRECT_STATUS": "200",
                }
            )
            if body_len:
                env["CONTENT_LENGTH"] = str(body_len)
            for name, value in self.headers.items():
                key = name.lower()
                if key in _UNPREFIXED:
                    env[_UNPREFIXED[key]] = value
                else:
                    env["HTTP_" + key.upper().replace("-", "_")] = value
            return env

        def run_cgi(self, script, query):
            fs, script_name, path_info = script
            body = b""
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = self.rfile.read(length)

            env = self.cgi_env(script_name, path_info, query, len(body))
            # Pass the bare script name, not its full path: the scripts inspect
            # $0 to work out who they are. officium.pl takes everything after
            # the last '/' as its own URL (a backslashed Windows path has none,
            # so the whole C:\... path became the form action and every hour
            # link went nowhere), then treats a leading 'C' as Cofficium.pl --
            # which a drive letter satisfies. webdia.pl also matches $0 against
            # /missa/ and /Pofficium/, which a full path could hit by accident.
            # The working directory is the script's own, so FindBin still
            # resolves correctly.
            cmd = (
                [perl]
                + [a for lib in perl_libs for a in ("-I", lib)]
                + [os.path.basename(fs)]
            )

            creationflags = 0
            if os.name == "nt":
                # Keep a console window from flashing up for every request
                # when we are running as a windowed app.
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            try:
                proc = subprocess.run(
                    cmd,
                    input=body,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    # The scripts resolve their data files relative to their
                    # own directory; both of the project's .psgi files chdir too.
                    cwd=os.path.dirname(fs),
                    env=env,
                    timeout=CGI_TIMEOUT,
                    creationflags=creationflags,
                )
            except subprocess.TimeoutExpired:
                self.send_error(504, "CGI script timed out")
                return
            except OSError as exc:
                self.send_error(500, "Cannot run perl: %s" % exc)
                return

            if proc.stderr:
                emit(proc.stderr.decode("utf-8", "replace").rstrip())

            if not proc.stdout:
                self.send_error(
                    500,
                    "CGI script produced no output (exit %d)" % proc.returncode,
                )
                return

            self.send_cgi_response(proc.stdout)

        def send_cgi_response(self, out):
            head, sep, body = out.partition(b"\r\n\r\n")
            if not sep:
                head, sep, body = out.partition(b"\n\n")
            if not sep:  # no header block at all
                head, body = b"", out

            status, headers = 200, []
            for line in head.decode("iso-8859-1").splitlines():
                name, _, value = line.partition(":")
                value = value.strip()
                if name.lower() == "status":
                    try:
                        status = int(value.split()[0])
                    except (ValueError, IndexError):
                        status = 500
                elif name.strip():
                    headers.append((name.strip(), value))

            if status == 200 and any(h[0].lower() == "location" for h in headers):
                status = 302

            self.send_response(status)
            for name, value in headers:
                if name.lower() != "content-length":
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            emit("%s %s" % (self.log_date_time_string(), fmt % args))

        def log_error(self, fmt, *args):
            emit("%s %s" % (self.log_date_time_string(), fmt % args))

    return Handler


def free_port(bind, preferred, tries=40):
    """First free port at or after `preferred`, so two copies do not clash."""
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((bind, port))
                return port
            except OSError:
                continue
    raise OSError("no free port in range %d-%d" % (preferred, preferred + tries))


def make_server(web_root, perl, perl_libs=(), bind="127.0.0.1", port=8000, log=None):
    """Return (server, port). Call serve_forever() on the result."""
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/javascript", ".js")
    port = free_port(bind, port)
    handler = make_handler(web_root, perl, perl_libs, log)
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    return server, port
