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
import os
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


def parse_byte_range(header: str, size: int):
    """``(start, stop)`` for a ``Range:`` header over a *size*-byte file.

    ``None`` means "no usable range, send the whole thing" -- which RFC 9110
    requires for a header this cannot parse, rather than an error: a clip that
    refuses to serve is a clip that does not play at all. ``()`` means the
    range is well formed but unsatisfiable, which *is* an error (416), because
    that is the answer a player needs in order to correct itself.

    Only the single-range forms a media element actually sends: ``bytes=N-M``,
    ``bytes=N-`` to resume, and ``bytes=-N`` for a trailer.
    """
    units, _, spec = (header or "").partition("=")
    if units.strip().lower() != "bytes" or "," in spec:
        return None
    first, sep, last = spec.strip().partition("-")
    if not sep:
        return None
    try:
        if not first:
            if not last:
                return None
            start, stop = max(0, size - int(last)), size
        else:
            start = int(first)
            stop = size if not last else min(size, int(last) + 1)
    except ValueError:
        return None
    if start >= size:
        return ()          # well formed, past the end: 416
    if stop <= start:
        return None        # e.g. bytes=5-2 -- nonsense, so ignore it
    return start, stop


def _find_song(project: Project, slug: str):
    try:
        return load_song(project.find_song_dir(unquote(slug)))
    except ProjectError:
        return None


class ConsoleHandler(BaseHTTPRequestHandler):
    """Routes only. All judgement lives in review.py, where the tests are."""

    server_version = "rambass-console"
    #: Media seeking is a stream of range requests, and HTTP/1.0 closes the
    #: connection after every one of them -- so a scrub across a 5.6 MB clip
    #: becomes a new TCP connection per move, and some media stacks will not
    #: treat a 1.0 resource as reliably seekable at all. Safe here because
    #: every reply goes through `_send` / `_send_file` / `_json`, all of which
    #: set an accurate Content-Length, which is what keep-alive needs.
    protocol_version = "HTTP/1.1"
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

    def _send_file(self, path: Path, content_type: str) -> None:
        """Serve *path*, honouring ``Range``. What makes a clip seekable.

        A browser will not let you seek in a media resource that does not
        advertise byte ranges: `element.seekable` stays empty and assigning
        `currentTime` snaps back to the start of what is buffered. So clicking
        the waveform moved the playhead and playback restarted from the
        beginning -- a bug that looked like a page bug and was not one.

        Only the requested slice is read, rather than the whole file per
        request: seeking around a 5.6 MB clip is a stream of range requests,
        and `read_bytes()` for ten bytes of it is the wrong shape.
        """
        size = path.stat().st_size
        wanted = parse_byte_range(self.headers.get("Range", ""), size)
        if wanted == ():
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, stop = wanted or (0, size)
        with path.open("rb") as handle:
            handle.seek(start)
            body = handle.read(stop - start)
        self.send_response(206 if wanted else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if wanted:
            self.send_header("Content-Range", f"bytes {start}-{stop - 1}/{size}")
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
            elif path == "/api/whoami":
                # How a starting console recognises one of its own on the port.
                # The root is the deciding field: same checkout means the old
                # one is redundant, a different checkout means hands off.
                with self._rebuilding_lock:
                    busy = sorted(self._rebuilding)
                self._json({"console": "rambass", "pid": os.getpid(),
                            "root": str(self.project.root), "rebuilding": busy})
            elif path == "/api/dashboard":
                self._json(self._dashboard())
            elif path.startswith("/api/song/"):
                self._song_screen(path.removeprefix("/api/song/"))
            elif path.startswith("/api/review/"):
                self._review(path.removeprefix("/api/review/"))
            elif path.startswith("/api/sections/"):
                self._sections(path.removeprefix("/api/sections/"))
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
        from .drummap import CANONICAL, load_drum_map
        from .midiio import read_drum_midi
        from .review import (
            candidate_state,
            clip_name,
            clip_sources,
            clip_spans,
            grid_rows,
            load_review,
            notes_payload,
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
            # The ruler is read next to Reaper's, and `qa/review.md` already
            # prints Reaper numbers, so the screen adds the count-in at the
            # drawing edge -- the same one-conversion-at-the-edge rule
            # `restore.checklist` follows. Stored notes stay musical.
            "count_in_bars": song.count_in_bars,
            # The kit a note may name, straight from `drummap.CANONICAL`:
            # `review.promote_notes` skips a note whose instrument is not in
            # it, so a panel with its own list would offer names that silently
            # never promote.
            "instruments": list(CANONICAL),
            # Whether the audio this screen is about to play still matches the
            # part it claims to show. `drums restore` is in PIPELINE and the
            # candidate render is not, so Rebuild can leave the two out of step.
            "candidate": candidate_state(song),
            "midi": midi_path.name if midi_path.exists() else "",
            "sections": [{
                "name": span.name,
                "start_bar": span.start_bar, "start_beat": span.start_beat,
                "end_bar": span.end_bar, "end_beat": span.end_beat,
                "cand_url": f"/clips/{song.slug}/{clip_name(span, 'cand')}",
                "ref_url": f"/clips/{song.slug}/{clip_name(span, 'ref')}",
            } for span in spans],
            "notes": notes_payload(notes),
            "grid": grid,
            # Per side, so a blank canvas names its own missing file instead
            # of one string listing both of them.
            "sources": {side: source.to_dict()
                        for side, source in clip_sources(song).items()},
        })

    def _sections(self, slug: str) -> None:
        """The section list, its spans and the checker's findings.

        Straight over :func:`~rambass.sections.section_table`, which is also
        what `rambass sections` prints -- so the screen and the terminal cannot
        disagree about where a section is or how long it runs.
        """
        from .drummap import load_drum_map
        from .midiio import read_drum_midi
        from .sections import section_table

        song = _find_song(self.project, slug)
        if song is None:
            self._error(404, f"no such song: {slug}")
            return
        # The pattern findings need a transcription; without one they are
        # skipped rather than guessed at, exactly as `cmd_sections` does.
        performance = None
        midi_path = song.best_drum_midi()
        if midi_path.exists():
            performance = read_drum_midi(
                midi_path, load_drum_map(song.drum_map, self.project))
            performance.timeline = song.timeline()
        table = section_table(song, performance)
        table["midi"] = midi_path.name if midi_path.exists() else ""
        self._json(table)

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
        side = "cand" if name.endswith("-cand.wav") else "ref"
        if self._clip_is_stale(song, target, side):
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
        self._send_file(target, "audio/wav")

    @staticmethod
    def _clip_is_stale(song, target: Path, side: str) -> bool:
        """Whether *target* has to be cut again before it is served.

        Clips were cut on first request and kept forever, so a re-rendered
        candidate went on being A/B'd as the audio from *before* the edit --
        the review loop looking closed while playing a stale part. An mtime
        check rather than a provenance entry per clip, because every reason the
        source moved counts: a re-render, a re-separation, a section boundary
        edited in ``song.yaml`` (which does not move the audio but does move
        where this clip starts and stops).

        Resolved from the file name rather than from :func:`clip_spans`, which
        would parse the whole 310-anchor align map -- and a scrub through one
        clip is a stream of range requests, every one of them landing here.
        """
        from .review import clip_sources

        if not target.is_file():
            return True
        cut_at = target.stat().st_mtime
        source = clip_sources(song).get(side)
        watched = [song.directory / "song.yaml"]
        if source is not None and source.available:
            watched.append(Path(source.path))
        return any(path.is_file() and path.stat().st_mtime > cut_at
                   for path in watched)

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlsplit(self.path).path
        body = self._body()
        # Before the song lookup: standing down is not about a song, and every
        # other POST here 404s without one.
        if path == "/api/shutdown":
            self._shutdown()
            return
        song = _find_song(self.project, str(body.get("song", "")))
        if song is None:
            self._error(404, f"no such song: {body.get('song')!r}")
            return
        try:
            if path == "/api/rebuild":
                self._rebuild(song, body)
            elif path == "/api/run":
                self._run(song, body)
            elif path == "/api/section":
                self._add_section(song, body)
            elif path == "/api/section/remove":
                self._remove_section(song, body)
            elif path == "/api/note":
                self._note(song, body)
            elif path == "/api/note/remove":
                self._remove_note(song, body)
            elif path == "/api/note/status":
                self._note_status(song, body)
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

    def _shutdown(self) -> None:
        """Stand down so a newer console can have the port.

        Refused while a rebuild is in flight: that is a demucs separation or a
        transcription running as a child process, and killing its parent to
        serve a fresher page trades minutes of CPU for a cosmetic win. The
        caller reports the refusal and the reader decides.
        """
        with self._rebuilding_lock:
            busy = sorted(self._rebuilding)
        if busy:
            self._error(409, f"a rebuild is running ({', '.join(busy)}) — "
                             f"not stopping. Wait for it, or use --port <n>.")
            return
        self._json({"stopping": True, "pid": os.getpid()})
        # After the reply, and from another thread: `shutdown` blocks until the
        # serve loop exits, and this handler *is* that loop's current job.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

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

    def _section_table(self, song) -> None:
        """Reply with the fresh table, so the screen re-renders off the answer."""
        from .sections import section_table

        self._json(section_table(song))

    def _add_section(self, song, body: dict) -> None:
        """Add or replace one section. The Reaper->musical subtraction happens
        in `to_musical` and nowhere else; `add_section` is what keeps the
        section's `backbeat` and `note` across a rename."""
        from .manifest import save_song
        from .project import parse_position
        from .sections import add_section, to_musical

        bar, beat = parse_position(str(body.get("position", "")))
        bar = to_musical(song, bar, reaper=bool(body.get("reaper")))
        name = str(body.get("name", "")).strip()
        if not name:
            raise ProjectError("a section needs a name")
        backbeat = body.get("backbeat")
        add_section(song, bar=bar, beat=beat, name=name,
                    backbeat=None if backbeat is None else str(backbeat))
        save_song(song)
        self._section_table(song)

    def _remove_section(self, song, body: dict) -> None:
        from .manifest import save_song
        from .project import parse_position
        from .sections import remove_section, to_musical

        bar, beat = parse_position(str(body.get("position", "")))
        bar = to_musical(song, bar, reaper=bool(body.get("reaper")))
        remove_section(song, bar=bar, beat=beat)
        save_song(song)
        self._section_table(song)

    def _note(self, song, body: dict) -> None:
        from datetime import date

        from .review import NOTE_KINDS, Note, add_note, notes_payload

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
        self._json({"notes": notes_payload(notes)})

    def _remove_note(self, song, body: dict) -> None:
        """Drop one note. For an entry filed by mistake, not for one that
        turned out to be nothing -- that is what dismissing is."""
        from .review import notes_payload, remove_note

        self._json({"notes": notes_payload(
            remove_note(song, str(body.get("key", ""))))})

    def _note_status(self, song, body: dict) -> None:
        from .review import notes_payload, set_note_status

        self._json({"notes": notes_payload(set_note_status(
            song, str(body.get("key", "")), str(body.get("status", ""))))})

    def _promote(self, song) -> None:
        from .manifest import save_song
        from .review import (
            load_review,
            notes_payload,
            promote_notes,
            write_ledger,
        )

        version, notes = load_review(song.path("qa", "review.yaml"))
        promoted, skipped = promote_notes(song, notes)
        if promoted:
            song.validate()
            save_song(song)
            write_ledger(song, notes, version=version)
        self._json({"promoted": promoted, "skipped": skipped,
                    "notes": notes_payload(notes)})

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


class ConsoleServer(ThreadingHTTPServer):
    """The console's server, which refuses to share its address.

    ``allow_reuse_address`` is 1 on :class:`~http.server.HTTPServer` by default,
    and on Windows ``SO_REUSEADDR`` lets a *second* socket bind an address that
    is already being listened on. So starting a second ``rambass console``
    succeeded silently, and Paolo had two on 8433 at once: the older one went on
    serving the ``review.py`` it imported at startup, so the browser showed step
    states from before a commit and no reload could fix it. A bind that fails
    loudly is the whole point here — the port is a singleton, and the way to run
    two consoles is ``--port``.

    Safe to turn off for a listener: only accepted connections go to TIME_WAIT,
    so a console that has just been stopped does not block the next one.
    """

    allow_reuse_address = False


def make_server(project: Project, *, port: int = DEFAULT_PORT,
                setlist: str = "gig") -> ConsoleServer:
    """A configured server, not yet serving. Tests bind port 0."""
    handler = type("BoundConsoleHandler", (ConsoleHandler,),
                   {"project": project, "setlist": setlist})
    return ConsoleServer(("127.0.0.1", port), handler)


def existing_console(port: int, *, timeout: float = 0.6) -> dict | None:
    """What is already serving on *port*, if it is one of ours.

    ``None`` for a free port **and** for a foreign server: whatever else is
    listening on 8433 is not ours to shut down, so the identifying reply is the
    only thing that authorises :func:`claim_port` to act.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/whoami", timeout=timeout) as reply:
            found = json.loads(reply.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return found if isinstance(found, dict) and found.get("console") == "rambass" \
        else None


def claim_port(project: Project, port: int, *, timeout: float = 6.0) -> str:
    """Make *port* free for this project's console. Returns what it did.

    A console is a singleton view of one project, so a second one for the *same*
    project is never what anybody wanted — it is asked to stand down. One
    serving a *different* checkout is left alone: killing somebody else's
    console is not this command's business.

    Stopping is a request, not a kill, so the old console can refuse while a
    rebuild is running rather than have its demucs child orphaned.
    """
    import time
    import urllib.error
    import urllib.request

    found = existing_console(port)
    if found is None:
        return ""
    if Path(found.get("root", "")) != project.root:
        raise ProjectError(
            f"port {port} is already serving {found.get('root')} "
            f"(pid {found.get('pid')}), not {project.root}. Stop that console, "
            f"or start this one with --port <n>.")

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
    except urllib.error.HTTPError as refused:
        detail = ""
        try:
            detail = json.loads(refused.read().decode("utf-8")).get("error", "")
        except (ValueError, OSError):
            pass
        raise ProjectError(
            f"the console on port {port} would not stand down: "
            f"{detail or refused.reason}") from refused
    except OSError as unreachable:
        raise ProjectError(
            f"could not ask the console on port {port} to stop: {unreachable}"
        ) from unreachable

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if existing_console(port, timeout=0.2) is None:
            return (f"stopped the console already on port {port} "
                    f"(pid {found.get('pid')})")
        time.sleep(0.1)
    raise ProjectError(
        f"the console on port {port} (pid {found.get('pid')}) accepted the stop "
        f"but is still serving. Stop it by hand, or use --port <n>.")


def serve(project: Project, *, port: int = DEFAULT_PORT, setlist: str = "gig",
          open_browser: bool = True) -> None:
    """Run until interrupted. What `rambass review serve` calls."""
    replaced = claim_port(project, port)
    if replaced:
        print(replaced)
    try:
        server = make_server(project, port=port, setlist=setlist)
    except OSError as taken:
        # `claim_port` found nothing to negotiate with, yet the address is held.
        # The consoles running when this check was added are exactly that: no
        # `/api/whoami`, so they cannot be asked to stand down, and a bare
        # WinError 10048 tells the reader nothing about what to do next.
        raise ProjectError(
            f"port {port} is held by something that is not a console this "
            f"version can talk to ({taken}). If it is an older `rambass "
            f"console`, stop it and start this one again; otherwise use "
            f"--port <n>.") from taken
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
