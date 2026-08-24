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
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
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

    #: Songs with a rebuild in flight. The server is threaded, a separation is
    #: three minutes, and the button gives nothing away while it runs -- so a
    #: second press is what a person does next, and two demucs runs then write
    #: the same `stems/.demucs` directory at once. Per song, because that is
    #: where the collision is: two *different* songs rebuilding is only slow.
    _rebuilding: set[str] = set()
    _rebuilding_lock = threading.Lock()

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
            elif path.startswith("/api/review/"):
                self._review(path.removeprefix("/api/review/"))
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

    def _review(self, slug: str) -> None:
        """Everything the A/B review screen needs, addressed on both clocks."""
        from .align import load_align
        from .drummap import load_drum_map
        from .midiio import read_drum_midi
        from .review import (
            clip_name,
            clip_sources,
            clip_spans,
            grid_rows,
            load_review,
        )

        song = _find_song(self.project, slug)
        if song is None:
            self._error(404, f"no such song: {slug}")
            return
        amap = load_align(song.path("practice", "align.yaml"))
        spans = clip_spans(song, amap)

        grid: dict = {}
        midi_path = song.best_drum_midi()
        if midi_path.exists():
            performance = read_drum_midi(
                midi_path, load_drum_map(song.drum_map, self.project))
            performance.timeline = song.timeline()
            grid = {span.name: grid_rows(performance, span) for span in spans}

        _, notes = load_review(song.path("qa", "review.yaml"))
        self._json({
            "slug": song.slug,
            "title": song.title,
            "approximate": bool(spans and spans[0].approximate),
            "beats_per_bar": song.timeline().time_signature[0],
            "subdivision": song.drum_subdivision,
            "midi": midi_path.name if midi_path.exists() else "",
            "sections": [{
                "name": span.name,
                "start_bar": span.start_bar, "start_beat": span.start_beat,
                "end_bar": span.end_bar, "end_beat": span.end_beat,
                "cand_url": f"/clips/{song.slug}/{clip_name(span, 'cand')}",
                "ref_url": f"/clips/{song.slug}/{clip_name(span, 'ref')}",
            } for span in spans],
            "notes": [note.to_dict() for note in notes],
            "grid": grid,
            # Per side, so a blank canvas names its own missing file instead
            # of one string listing both of them.
            "sources": {side: source.to_dict()
                        for side, source in clip_sources(song).items()},
        })

    def _clip(self, rest: str) -> None:
        """``/clips/<slug>/<clip-name>.wav`` — cut lazily on first request."""
        from . import review as review_module
        from .align import load_align
        from .review import clip_name, clip_sources, clip_spans

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
                for side in ("cand", "ref"):
                    if clip_name(span, side) == name:
                        wanted = (span, side)
            if wanted is None:
                self._error(404, f"no such clip: {name}")
                return
            span, side = wanted
            # Resolved here and per side: building both sources up front meant
            # a missing candidate raised for a *reference* request too, and
            # blanked a canvas whose stem was already on disk.
            source = clip_sources(song)[side]
            if not source.available:
                # 404 is "no such clip"; this one exists and has no source
                # yet, which is a different thing to tell the reader.
                self._error(409, source.hint)
                return
            start, duration = ((span.candidate_start, span.duration)
                               if side == "cand" else
                               (span.reference_start, span.reference_duration))
            review_module.cut_clip(source.path, target,
                                   start=start, duration=duration)
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
            elif path == "/api/run":
                self._run(song, body)
            elif path == "/api/note":
                self._note(song, body)
            elif path == "/api/promote":
                self._promote(song)
            elif path == "/api/export-section":
                self._export_section(song, body)
            else:
                self._error(404, f"no such action: {path}")
        except ProjectError as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass

    def _claim(self, song) -> bool:
        """Take this song's one build slot, or answer 409 and take nothing."""
        with self._rebuilding_lock:
            if song.slug in self._rebuilding:
                self._error(409, f"{song.slug}: a rebuild is already running — "
                                 f"wait for it to finish, or watch the terminal "
                                 f"the console was started from")
                return False
            self._rebuilding.add(song.slug)
        return True

    def _release(self, song) -> None:
        # On the way out of *every* path, including the raise that becomes a
        # 400: a console that refuses every later rebuild after one error is
        # worse than one that races.
        with self._rebuilding_lock:
            self._rebuilding.discard(song.slug)

    def _rebuild(self, song, body: dict) -> None:
        from . import review

        if not self._claim(song):
            return
        try:
            self._json(review.rebuild_song(
                song, project_root=self.project.root,
                dry_run=bool(body.get("dry_run")),
                force=body.get("force") or None,
                step=body.get("step") or None))
        finally:
            self._release(song)

    def _run(self, song, body: dict) -> None:
        """One screen row's own command, for the rows staleness cannot track."""
        from . import review
        from .provenance import stale_report

        if not self._claim(song):
            return
        try:
            self._json(review.run_step_command(
                song, str(body.get("command", "")),
                project_root=self.project.root, report=stale_report(song)))
        finally:
            self._release(song)

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


    def _promote(self, song) -> None:
        from .manifest import save_song
        from .review import load_review, promote_notes, write_ledger

        version, notes = load_review(song.path("qa", "review.yaml"))
        promoted, skipped = promote_notes(song, notes)
        if promoted:
            song.validate()
            save_song(song)
            write_ledger(song, notes, version=version)
        self._json({"promoted": promoted, "skipped": skipped,
                    "notes": [note.to_dict() for note in notes]})

    def _export_section(self, song, body: dict) -> None:
        """Write one section's MIDI slice where EZdrummer's browser can see it.

        Not drag-and-drop out of the browser — a file in a folder, which is
        what already works: EZdrummer 3 reads plain .mid from a linked folder
        (drums-rebuild.md). Opening the folder is best-effort and local-only.
        """
        from .align import load_align
        from .drummap import load_drum_map
        from .midiio import read_drum_midi, write_drum_midi
        from .project import slugify
        from .review import clip_spans, section_performance

        wanted = str(body.get("section", ""))
        amap = load_align(song.path("practice", "align.yaml"))
        span = next((s for s in clip_spans(song, amap) if s.name == wanted),
                    None)
        if span is None:
            self._error(404, f"no such section: {wanted!r}")
            return
        midi_path = song.best_drum_midi()
        if not midi_path.exists():
            raise ProjectError(
                f"{song.slug}: no drum MIDI to slice — run the drum pipeline "
                f"first")
        drum_map = load_drum_map(song.drum_map, self.project)
        performance = read_drum_midi(midi_path, drum_map)
        performance.timeline = song.timeline()
        target = song.path("midi", "sections", f"{slugify(wanted)}.mid")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_drum_midi(target, section_performance(performance, span),
                        drum_map)
        reveal_in_file_manager(target.parent)
        self._json({"path": str(target.relative_to(song.directory))})


def reveal_in_file_manager(folder: Path) -> None:
    """Show *folder* to the user. A seam on purpose.

    Exporting a section is a "now drag it into Reaper" gesture, so opening the
    folder is the point. Done inline in the handler it was also unstubbable, and
    a full ``pytest`` run opened a File Explorer window on every throwaway
    ``midi/sections`` directory the suite made. One module-level function is the
    whole of the OS surface here, so ``tests/conftest.py`` patches it once.
    """
    opener = {"win32": "explorer", "darwin": "open"}.get(sys.platform, "xdg-open")
    try:
        subprocess.Popen([opener, str(folder)],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass  # headless, or no opener: the path in the reply is enough


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
