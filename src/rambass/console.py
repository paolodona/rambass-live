"""The project console's HTTP server: stdlib, local, thin over review.py.

`rambass review serve` starts it. Every screen is a JSON endpoint plus one
static HTML file (``console.html``, packaged beside this module); every
mutation goes through the same :mod:`~rambass.review` functions the CLI uses,
so nothing the browser can do differs from what a terminal can. Bound to
127.0.0.1 only — this is a tool on Paolo's own machine, not a service.

No framework, deliberately: the console is a stepper with buttons and a grid,
and the price of keeping it a laptop-at-a-venue tool (CLAUDE.md's layering
rule) is that it stays within the stdlib.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import unquote, urlsplit

from .manifest import load_song
from .project import Project, ProjectError

#: Ends in "ss" for rambass; unregistered, and high enough to never collide
#: with anything a studio machine usually runs.
DEFAULT_PORT = 8433


def _songs_in_order(project: Project, setlist: str):
    """Every song, in the setlist's running order when one resolves.

    The setlist is the row order because that is how Paolo thinks about the
    show; songs it does not mention follow in album/track order, and excluded
    songs come last (the dashboard shows them as the cut strip, not as rows).
    """
    everything = [load_song(d) for d in project.song_dirs()]
    ordered = []
    if setlist:
        try:
            from .setlist import Setlist, find_setlist

            ordered = Setlist.load(find_setlist(project, setlist)).resolve(project)
        except ProjectError:
            ordered = []
    listed = {(s.album, s.slug) for s in ordered}
    rest = [s for s in everything
            if (s.album, s.slug) not in listed and not s.excluded]
    cut = [s for s in everything
           if (s.album, s.slug) not in listed and s.excluded]
    return ordered + rest, cut


def _find_song(project: Project, slug: str):
    try:
        return load_song(project.find_song_dir(unquote(slug)))
    except ProjectError:
        return None


class ConsoleHandler(BaseHTTPRequestHandler):
    """Routes only. All judgement lives in review.py, where the tests are."""

    server_version = "rambass-console"
    project: Project = None  # set by make_server
    setlist: str = ""

    def log_message(self, *args) -> None:  # noqa: D102 - silence per request
        pass

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # ── GET ─────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        try:
            if path == "/":
                page = resources.files("rambass").joinpath("console.html")
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/dashboard":
                self._json(self._dashboard())
            elif path.startswith("/api/song/"):
                self._song_screen(path.removeprefix("/api/song/"))
            elif path.startswith("/clips/"):
                self._clip(path.removeprefix("/clips/"))
            else:
                self._error(404, f"no such page: {path}")
        except ProjectError as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass

    def _dashboard(self) -> dict:
        from .provenance import stale_report
        from .review import dashboard_row

        rows_songs, cut = _songs_in_order(self.project, self.setlist)
        rows = [dashboard_row(song, stale_report(song)) for song in rows_songs]
        done = sum(row["done"] for row in rows)
        total = sum(row["total"] for row in rows)
        return {
            "setlist": self.setlist,
            "rows": rows,
            "cut": [{"slug": s.slug, "title": s.title,
                     "reason": s.exclude_reason} for s in cut],
            "summary": {"songs": len(rows), "done": done, "total": total},
        }

    def _song_screen(self, slug: str) -> None:
        from .provenance import stale_report
        from .review import song_screen

        song = _find_song(self.project, slug)
        if song is None:
            self._error(404, f"no such song: {slug}")
            return
        self._json(song_screen(song, stale_report(song)))

    def _clip(self, rest: str) -> None:
        """``/clips/<slug>/<clip-name>.wav`` — cut lazily on first request."""
        from .align import load_align
        from .review import (
            candidate_path,
            clip_name,
            clip_spans,
            cut_clip,
            reference_path,
        )

        slug, _, name = rest.partition("/")
        song = _find_song(self.project, slug)
        if song is None or "/" in name or not name.endswith(".wav"):
            self._error(404, f"no such clip: {rest}")
            return
        target = song.path("qa", "clips", name)
        if not target.is_file():
            amap = load_align(song.path("practice", "align.yaml"))
            wanted = None
            for span in clip_spans(song, amap):
                for side, source in (("cand", candidate_path(song)),
                                     ("ref", reference_path(song))):
                    if clip_name(span, side) == name:
                        wanted = (span, side, source)
            if wanted is None:
                self._error(404, f"no such clip: {name}")
                return
            span, side, source = wanted
            if side == "cand":
                cut_clip(source, target, start=span.candidate_start,
                         duration=span.duration)
            else:
                cut_clip(source, target, start=span.reference_start,
                         duration=span.reference_duration)
        self._send(200, target.read_bytes(), "audio/wav")

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        body = self._body()
        song = _find_song(self.project, str(body.get("song", "")))
        if song is None:
            self._error(404, f"no such song: {body.get('song')!r}")
            return
        try:
            if path == "/api/rebuild":
                self._rebuild(song, body)
            elif path == "/api/note":
                self._note(song, body)
            else:
                self._error(404, f"no such action: {path}")
        except ProjectError as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass

    def _rebuild(self, song, body: dict) -> None:
        from .review import rebuild_song

        self._json(rebuild_song(
            song, project_root=self.project.root,
            dry_run=bool(body.get("dry_run")),
            force=body.get("force") or None,
            step=body.get("step") or None))

    def _note(self, song, body: dict) -> None:
        from datetime import date

        from .review import NOTE_KINDS, Note, add_note

        kind = str(body.get("kind", "other"))
        if kind not in NOTE_KINDS:
            raise ProjectError(
                f"kind {kind!r}: expected one of {', '.join(NOTE_KINDS)}")
        notes = add_note(song, Note(
            bar=int(body["bar"]),
            beat=float(body.get("beat", 1.0)),
            section=str(body.get("section", "")),
            kind=kind,
            instrument=str(body.get("instrument", "")),
            velocity=int(body.get("velocity", 0) or 0),
            comment=str(body.get("comment", "")),
            created=date.today().isoformat(),
        ))
        self._json({"notes": [note.to_dict() for note in notes]})


def make_server(project: Project, *, port: int = DEFAULT_PORT,
                setlist: str = "gig") -> ThreadingHTTPServer:
    """A configured server, not yet serving. Tests bind port 0."""
    handler = type("BoundConsoleHandler", (ConsoleHandler,),
                   {"project": project, "setlist": setlist})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(project: Project, *, port: int = DEFAULT_PORT, setlist: str = "gig",
          open_browser: bool = True) -> None:
    """Run until interrupted. What `rambass review serve` calls."""
    server = make_server(project, port=port, setlist=setlist)
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"rambass console at {address}  (ctrl-c stops it)")
    if open_browser:
        import webbrowser

        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
