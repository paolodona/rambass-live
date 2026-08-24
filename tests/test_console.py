"""The console server: JSON endpoints over review.py, nothing invented there.

Started on port 0 in a thread against the throwaway project, exercised with
plain urllib — no browser. What the browser renders is these payloads; what
these tests pin is that the payloads say what `rambass status` and `rambass
stale` say, because a dashboard that disagrees with the terminal is worse
than no dashboard.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from rambass import console as rambass_console
from rambass.console import make_server
from rambass.project import ProjectError
from rambass.manifest import STAGES, Song, save_song
from rambass.provenance import stamp


@pytest.fixture
def served(project, song):
    # A second, a-cappella song so the dashboard has an n/a story to tell,
    # and a setlist that orders the two deliberately against album order.
    directory = project.songs_dir / "tutti-in-fila" / "07-la-canzone-del-tonno"
    tonno = Song(
        slug="la-canzone-del-tonno", title="La Canzone Del Tonno",
        album="tutti-in-fila", track=7, directory=directory,
        drums_origin="a-cappella",
        status={stage: "n/a" for stage in STAGES} | {"rehearsed": "todo"},
    )
    save_song(tonno, directory)
    (project.root / "setlists" / "gig.yaml").write_text(
        yaml.safe_dump({"name": "gig", "songs": [
            "tutti-in-fila/07-la-canzone-del-tonno",
            "tutti-in-fila/03-tutti-in-fila",
        ]}),
        encoding="utf-8")

    server = make_server(project, port=0, setlist="gig")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", song
    server.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(base, path, payload):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_page_is_served(served):
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        assert response.status == 200
        body = response.read().decode("utf-8")
    assert "rambass" in body.lower()


def test_the_dashboard_rows_follow_the_setlist_not_the_albums(served):
    base, _ = served
    data = _get(base, "/api/dashboard")
    slugs = [row["slug"] for row in data["rows"]]
    assert slugs == ["la-canzone-del-tonno", "tutti-in-fila"]
    assert data["setlist"] == "gig"


def test_a_cell_carries_the_manifest_status(served):
    base, song = served
    row = {r["slug"]: r for r in _get(base, "/api/dashboard")["rows"]}[song.slug]
    for stage in STAGES:
        assert row["cells"][stage]["status"] == song.status.get(stage, "todo")


def test_an_a_cappella_row_reads_na_not_unfinished(served):
    base, _ = served
    row = {r["slug"]: r for r in
           _get(base, "/api/dashboard")["rows"]}["la-canzone-del-tonno"]
    assert row["cells"]["stems"]["status"] == "n/a"


def test_a_stale_artifact_rings_its_stage_cell(served, song):
    base, _ = served
    from rambass.manifest import save_song as save

    raw = song.directory / "midi" / "drums-raw.mid"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("x", encoding="utf-8")
    quantized = song.directory / "midi" / "drums-quantized.mid"
    quantized.write_text("x", encoding="utf-8")
    stamp(song, quantized, step="drums clean", inputs=[raw])
    song.bpm = 61.0
    save(song)

    row = {r["slug"]: r for r in _get(base, "/api/dashboard")["rows"]}[song.slug]
    assert row["cells"]["quantize"]["stale"] is True
    assert row["cells"]["video"]["stale"] is False


def test_the_song_screen_collapses_the_drum_cluster(served, song):
    base, _ = served
    data = _get(base, f"/api/song/{song.slug}")
    names = [screen["stage"] for screen in data["screens"]]
    assert "drums" in names
    assert "drums_midi" not in names and "quantize" not in names
    drums = next(s for s in data["screens"] if s["stage"] == "drums")
    assert any(step["kind"] == "open" for step in drums["steps"])


def test_an_unknown_slug_is_a_404(served):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, "/api/song/no-such-song")
    assert caught.value.code == 404


def test_rebuild_dry_run_over_http_matches_stale(served, song):
    base, _ = served
    raw = song.directory / "midi" / "drums-raw.mid"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("x", encoding="utf-8")
    quantized = song.directory / "midi" / "drums-quantized.mid"
    quantized.write_text("x", encoding="utf-8")
    stamp(song, quantized, step="drums clean", inputs=[raw])
    raw.write_text("changed", encoding="utf-8")

    data = _post(base, "/api/rebuild", {"song": song.slug, "dry_run": True})
    assert any("drums clean" in command for command in data["commands"])
    assert data["ran"] == 0


def test_rebuild_over_http_refuses_a_force_that_is_not_held(served, song):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/rebuild",
              {"song": song.slug, "force": "midi/drums-quantized.mid"})
    assert caught.value.code == 400


# ── one rebuild at a time ────────────────────────────────────────────────────
#
# Paolo opened the console, clicked a step's Run button and watched **two**
# demucs separations of Manlio start at once, both writing the same
# `stems/.demucs/htdemucs_ft` directory. Two causes, both fixed here: the
# per-step buttons all posted an empty payload, so any of them rebuilt the
# whole chain from stems; and nothing stopped a second POST while the first
# was still running. A separation is three minutes -- the button sits there
# looking unpressed for all of it, so a second press is the *expected* human
# response, not a mistake to be blamed on the user.


def test_two_rebuilds_of_one_song_do_not_run_at_once(served, song, monkeypatch):
    import rambass.review as review_module

    base, _ = served
    started, release = threading.Event(), threading.Event()
    live, high_water = [], []

    def slow(song_arg, **kwargs):
        live.append(1)
        high_water.append(len(live))
        started.set()
        release.wait(10)
        live.pop()
        return {"song": song_arg.slug, "dry_run": False, "commands": [],
                "ran": 0, "failed": "", "backup": "", "held": []}

    monkeypatch.setattr(review_module, "rebuild_song", slow)
    first = threading.Thread(
        target=lambda: _post(base, "/api/rebuild", {"song": song.slug}),
        daemon=True)
    first.start()
    assert started.wait(10), "the first rebuild never started"

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/rebuild", {"song": song.slug})
    assert caught.value.code == 409
    assert "already" in caught.value.read().decode("utf-8")

    release.set()
    first.join(10)
    assert max(high_water) == 1


def test_a_second_rebuild_is_allowed_once_the_first_has_finished(served, song,
                                                                 monkeypatch):
    """The lock has to come off on the way out, including the failing way out
    -- a console that refuses every rebuild after one error is worse than one
    that races."""
    import rambass.review as review_module

    base, _ = served
    monkeypatch.setattr(review_module, "rebuild_song",
                        lambda *a, **k: (_ for _ in ()).throw(ProjectError("nope")))
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/rebuild", {"song": song.slug})
    assert caught.value.code == 400

    monkeypatch.setattr(review_module, "rebuild_song",
                        lambda song_arg, **k: {"song": song_arg.slug, "commands": [],
                                               "ran": 0, "failed": "", "backup": "",
                                               "held": [], "dry_run": True})
    assert _post(base, "/api/rebuild", {"song": song.slug})["ran"] == 0


def test_another_song_may_rebuild_while_one_is_running(served, song, monkeypatch):
    """The lock is per song, because that is where the collision is: two
    demucs runs on one song write the same output directory. Two different
    songs are merely slow."""
    import rambass.review as review_module

    base, _ = served
    started, release = threading.Event(), threading.Event()

    def slow(song_arg, **kwargs):
        if song_arg.slug == song.slug:
            started.set()
            release.wait(10)
        return {"song": song_arg.slug, "dry_run": False, "commands": [],
                "ran": 0, "failed": "", "backup": "", "held": []}

    monkeypatch.setattr(review_module, "rebuild_song", slow)
    first = threading.Thread(
        target=lambda: _post(base, "/api/rebuild", {"song": song.slug}),
        daemon=True)
    first.start()
    assert started.wait(10)
    try:
        other = _post(base, "/api/rebuild", {"song": "la-canzone-del-tonno"})
        assert other["song"] == "la-canzone-del-tonno"
    finally:
        release.set()
        first.join(10)


def test_every_run_row_can_say_what_it_would_run(served, song):
    """The button carried the *command string* and the click handler threw it
    away, so every Run button posted an empty payload and rebuilt the whole
    chain: "Find the tempo" started a demucs separation. Each row now either
    names its provenance step (rebuild, gated) or carries a command (run it,
    whitelisted) -- and never neither, which is what silently meant
    "everything"."""
    from rambass.provenance import PIPELINE

    base, _ = served
    data = _get(base, f"/api/song/{song.slug}")
    runs = [step for screen in data["screens"] for step in screen["steps"]
            if step["kind"] == "run"]
    assert runs, "no run rows to check"
    names = {step.name for step in PIPELINE}
    for step in runs:
        assert step["step"] in names or step["command"], step["label"]
        if step["step"]:
            assert step["step"] in names, f"{step['label']}: {step['step']!r}"


def test_a_row_with_no_provenance_step_runs_its_own_command(served, song,
                                                            monkeypatch):
    """`rambass analyze`, `lyrics transcribe/check`, `video card` and `countin`
    produce nothing the staleness table tracks, so there is no step to filter
    on -- and posting an empty rebuild for them is exactly the bug. They run
    their one command instead."""
    import rambass.review as review_module

    base, _ = served
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda command: (ran.append(command), (0, ""))[1])
    result = _post(base, "/api/run",
                   {"song": song.slug,
                    "command": f"rambass analyze {song.slug} --write"})
    assert ran == [f"rambass analyze {song.slug} --write"]
    assert result["ran"] == 1 and not result["failed"]


def test_a_command_the_song_does_not_offer_is_refused(served, song, monkeypatch):
    """The whitelist is the song's own screens, so the browser can only ask for
    what the console already put a button on. Anything else is a 400, not a
    subprocess."""
    import rambass.review as review_module

    base, _ = served
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda command: (ran.append(command), (0, ""))[1])
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/run", {"song": song.slug, "command": "rambass list"})
    assert caught.value.code == 400
    assert ran == []


def test_running_a_command_takes_the_same_one_at_a_time_lock(served, song,
                                                             monkeypatch):
    """A `countin` run and a rebuild both write into render/. One song, one
    job."""
    import rambass.review as review_module

    base, _ = served
    started, release = threading.Event(), threading.Event()

    def slow(cwd):
        def run(command):
            started.set()
            release.wait(10)
            return 0, ""
        return run

    monkeypatch.setattr(review_module, "subprocess_runner", slow)
    first = threading.Thread(
        target=lambda: _post(base, "/api/run",
                             {"song": song.slug,
                              "command": f"rambass analyze {song.slug} --write"}),
        daemon=True)
    first.start()
    assert started.wait(10)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, "/api/rebuild", {"song": song.slug})
        assert caught.value.code == 409
    finally:
        release.set()
        first.join(10)


def test_the_page_posts_the_step_it_was_clicked_on(served):
    """The whole bug was one line of JavaScript. Pin it: a per-step button
    sends its own step, and there is no browser here to catch it going back."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")
    assert "data-step=" in page
    assert "dataset.step" in page


def test_the_pages_script_parses():
    """console.html is one inline script with no build step and no linter, so a
    stray brace takes the whole console down while every test here stays green
    -- these tests assert on the *served text*, which a syntax error does not
    change. Skipped rather than required when node is absent, same rule as
    ffmpeg and librosa."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; cannot parse-check the page script")

    page = (Path(rambass_console.__file__).parent / "console.html").read_text(
        encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(blocks) == 1, f"expected one inline script, found {len(blocks)}"
    # encoding= explicitly: the page has em dashes in it, and stdin would
    # otherwise be encoded with the Windows locale codepage and die on them.
    checked = subprocess.run([node, "--check", "-"], input=blocks[0],
                             capture_output=True, text=True, encoding="utf-8")
    assert checked.returncode == 0, checked.stderr


def test_the_page_says_what_is_rebuilding_where_it_can_be_seen(served):
    """Paolo: *"the 'rebuilding...' banner is at the bottom and I cannot see
    it. It should be contextual to what is rebuilding somehow?"* -- the log is
    the last element after every stage screen, so on a song with twelve run
    rows the only feedback for a three-minute separation was below the fold.

    Two answers, both pinned here because there is no browser in this suite:
    the row you clicked reports its own state, and a fixed bar names the
    command that is running wherever the page is scrolled to."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")

    assert 'id="running"' in page, "no fixed running bar"
    assert "#running" in page and "position:fixed" in page,         "the running bar has to stay in view"
    assert ".step.busy" in page, "no marker for the row being rebuilt"
    assert 'classList.add("busy")' in page, "nothing marks the clicked row"


def test_a_note_posted_from_the_browser_lands_in_the_ledger(served, song):
    base, _ = served
    from rambass.review import load_review

    data = _post(base, "/api/note", {
        "song": song.slug, "bar": 43, "beat": 1.0, "kind": "missing-hit",
        "instrument": "crash", "velocity": 105, "comment": "into the lift"})
    assert data["notes"]
    _, notes = load_review(song.path("qa", "review.yaml"))
    assert notes and notes[0].bar == 43 and notes[0].section == "chorus"


@pytest.fixture
def reviewable(served, song):
    """The served project, with an alignment map and a real quantized MIDI."""
    base, _ = served
    (song.directory / "practice").mkdir(parents=True, exist_ok=True)
    (song.directory / "practice" / "align.yaml").write_text(
        yaml.safe_dump({"source": "x.wav", "anchors": [
            {"bar": 1, "at": 0.5}, {"bar": 33, "at": 65.0}]}),
        encoding="utf-8")
    from rambass.midiio import DrumPerformance, Hit, write_drum_midi

    timeline = song.timeline()
    at = timeline.bar_beat_to_seconds
    performance = DrumPerformance(
        [Hit("kick", at(9, 1.0), 100), Hit("snare", at(9, 3.0), 104)], timeline)
    write_drum_midi(song.drum_midi_path("quantized"), performance)
    return base, song


def test_the_review_payload_addresses_both_clocks(reviewable):
    base, song = reviewable
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")
    assert verse["cand_url"].startswith(f"/clips/{song.slug}/")
    assert verse["ref_url"] != verse["cand_url"]
    assert data["approximate"] is False
    assert data["beats_per_bar"] == 4
    kicks = [row for row in data["grid"]["verse"] if row["instrument"] == "kick"]
    assert kicks and kicks[0]["ticks"][0]["bar"] == 9


def test_review_without_an_alignment_map_is_a_400_naming_the_fix(served, song):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(base, f"/api/review/{song.slug}")
    assert caught.value.code == 400
    assert "align" in json.loads(caught.value.read().decode("utf-8"))["error"]


def test_export_section_writes_a_zero_based_slice(reviewable, no_windows_pop_open):
    base, song = reviewable
    from rambass.drummap import GENERAL_MIDI
    from rambass.midiio import read_drum_midi

    data = _post(base, "/api/export-section",
                 {"song": song.slug, "section": "verse"})
    path = song.directory / data["path"]
    assert path.exists()
    sliced = read_drum_midi(path, GENERAL_MIDI)
    assert len(sliced.hits) == 2
    assert min(hit.time for hit in sliced.hits) < 0.05
    # The reveal still happens -- it goes through a seam the suite can stub, so
    # a full pytest run no longer opens a File Explorer window per tmp dir.
    assert no_windows_pop_open == [path.parent]


def test_the_reveal_never_launches_a_process_during_tests(reviewable, monkeypatch):
    """The seam is the only route out to the OS: patching it has to be enough,
    with no `subprocess` left behind in the handler for it to reach around."""
    import subprocess

    from rambass import console

    def refuse(*args, **kwargs):
        raise AssertionError(f"a test spawned {args!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(console.subprocess, "Popen", refuse)
    base, song = reviewable
    _post(base, "/api/export-section", {"song": song.slug, "section": "verse"})


def test_promote_over_http_updates_ledger_and_manifest(reviewable):
    base, song = reviewable
    from rambass.manifest import load_song

    _post(base, "/api/note", {
        "song": song.slug, "bar": 43, "kind": "missing-hit",
        "instrument": "crash", "velocity": 105, "comment": "into the lift"})
    data = _post(base, "/api/promote", {"song": song.slug})
    assert data["promoted"] == 1
    assert load_song(song.directory).drum_additions
    assert data["notes"][0]["status"] == "promoted"


def test_the_page_gives_an_unprovenanced_file_its_own_dot(served):
    """`render/click.wav` is on disk and in the Reaper project, but it was
    built before provenance existed, so `stale_report` calls it `unknown`.
    `stepIcon` had no branch for that, so it fell through to the same hollow
    grey dot the console draws for a stage that does not apply -- and the click
    step read as "not applicable" for a song that has a click. Pinned on the
    served text, because there is no browser here."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")

    assert '"unknown"' in page, "stepIcon does not branch on the unknown state"
    assert ".dot.unknown" in page, "the unknown state has no dot of its own"
    assert "no provenance" in page, "nothing on the page explains an unknown"


# ── a blank canvas has to say which of the two sources is missing ────────────


@pytest.fixture
def stem_only(reviewable, monkeypatch):
    """Manlio's real state: a separated drum stem, no bounced candidate."""
    base, song = reviewable
    path = song.path("stems", "drums.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-really-audio", encoding="utf-8")

    from rambass import review as review_module

    cut: list[tuple] = []

    def fake_cut(source, target, *, start, duration):
        # No ffmpeg in this suite; what is under test is which source got
        # picked, not the trim.
        cut.append((str(source), float(start), float(duration)))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RIFF....WAVE")

    monkeypatch.setattr(review_module, "cut_clip", fake_cut)
    return base, song, cut


def test_a_missing_candidate_does_not_blank_the_reference(stem_only):
    """The reference clip is servable whenever the stem is on disk.

    Both sources used to be resolved eagerly into one tuple, so
    `candidate_path`'s ProjectError killed the request for the *other* side
    too -- and the reference canvas went blank for a file that was there."""
    base, song, cut = stem_only
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")

    with urllib.request.urlopen(base + verse["ref_url"]) as response:
        assert response.status == 200
    assert cut and cut[0][0].endswith("drums.wav")


def test_a_missing_candidate_clip_is_409_and_names_the_bounce(stem_only):
    """404 means "no such clip"; this one exists and has no source yet."""
    base, song, _ = stem_only
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")

    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(base + verse["cand_url"])
    assert caught.value.code == 409
    assert "qa/candidate.wav" in json.loads(
        caught.value.read().decode("utf-8"))["error"]


def test_the_review_payload_says_which_side_is_missing(stem_only):
    """So the canvas draws the real reason, not one string naming both."""
    base, song, _ = stem_only
    sources = _get(base, f"/api/review/{song.slug}")["sources"]

    assert sources["ref"]["available"] is True
    assert sources["cand"]["available"] is False
    assert "qa/candidate.wav" in sources["cand"]["hint"]
    assert sources["cand"]["command"] == ""


def test_the_reference_side_offers_the_stems_command(reviewable):
    """A button can make this one, and `/api/run` already whitelists it."""
    base, song = reviewable
    from rambass.provenance import stale_report
    from rambass.review import runnable_commands

    sources = _get(base, f"/api/review/{song.slug}")["sources"]
    assert sources["ref"]["available"] is False
    assert sources["ref"]["command"] in runnable_commands(
        song, stale_report(song))


def test_the_canvas_draws_a_per_side_reason_and_a_run_button(served):
    """No browser here, so pin the wiring the same way the busy row is."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")

    assert "data.sources" in page, "the canvas never reads per-side availability"
    assert 'id="make-ref"' in page, "no button for the source a command can make"
    assert "/api/run" in page


# ── one console per project ──────────────────────────────────────────────────
#
# Paolo ended up with two consoles on port 8433 at once: the older one kept
# serving its already-imported `review.py`, so the browser showed step states
# from before a commit and no reload could fix it. On Windows a second bind of
# the same address *succeeds* when SO_REUSEADDR is set, which is what
# `allow_reuse_address` does by default -- so the second console never got the
# "address already in use" that would have told anyone. Two answers: the socket
# refuses to share, and a starting console asks an existing one to stand down.


def test_the_listening_socket_refuses_to_be_shared(project):
    """The bind must fail rather than silently double-serve."""
    from rambass.console import make_server

    first = make_server(project, port=0, setlist="gig")
    port = first.server_address[1]
    try:
        with pytest.raises(OSError):
            make_server(project, port=port, setlist="gig")
    finally:
        first.server_close()


def test_a_console_says_who_it_is(served, project):
    base, _ = served
    who = _get(base, "/api/whoami")
    assert who["console"] == "rambass"
    assert Path(who["root"]) == project.root
    assert who["pid"] == os.getpid()
    assert who["rebuilding"] == []


def test_existing_console_finds_one_and_reads_its_root(served, project):
    from rambass.console import existing_console

    base, _ = served
    port = int(base.rsplit(":", 1)[1])
    found = existing_console(port)
    assert found and Path(found["root"]) == project.root
    assert existing_console(_a_free_port()) is None


def test_a_foreign_server_on_the_port_is_not_mistaken_for_a_console(project):
    """Whatever else is on 8433, we must not ask it to shut down."""
    import http.server

    from rambass.console import existing_console

    server = http.server.HTTPServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert existing_console(port) is None
    finally:
        server.shutdown()
        server.server_close()


def test_claim_port_stops_a_console_for_the_same_project(project, song):
    """The replacement case: same project, so the old one is redundant."""
    from rambass.console import claim_port, existing_console, make_server

    old = make_server(project, port=0, setlist="gig")
    port = old.server_address[1]
    thread = threading.Thread(target=old.serve_forever, daemon=True)
    thread.start()

    note = claim_port(project, port)
    assert "stopped" in note.lower() and str(os.getpid()) in note
    old.server_close()
    assert existing_console(port) is None


def test_claim_port_refuses_a_console_serving_another_project(project, tmp_path):
    """Killing someone else's console is not ours to do."""
    from rambass.console import claim_port, make_server
    from rambass.project import Project, ProjectError

    other = Project(tmp_path / "elsewhere")
    old = make_server(project, port=0, setlist="gig")
    port = old.server_address[1]
    thread = threading.Thread(target=old.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProjectError) as caught:
            claim_port(other, port)
        assert "--port" in str(caught.value)
    finally:
        old.shutdown()
        old.server_close()


def test_a_console_will_not_stand_down_mid_rebuild(served):
    """A three-minute separation is running as a child process. Refuse, and say
    so, rather than orphaning it to serve a fresher page."""
    from rambass.console import ConsoleHandler

    base, _ = served
    ConsoleHandler._rebuilding.add("manlio")
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(base, "/api/shutdown", {})
        assert caught.value.code == 409
        body = json.loads(caught.value.read().decode("utf-8"))
        assert "manlio" in body["error"]
    finally:
        ConsoleHandler._rebuilding.discard("manlio")


def test_claim_port_on_a_free_port_does_nothing(project):
    from rambass.console import claim_port

    assert claim_port(project, _a_free_port()) == ""


def _a_free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_page_shows_a_failed_commands_output(served):
    """A log that prints only "FAIL" is the bug: the refusal from
    `analyze --write` has to be readable where the button was pressed. Pinned on
    the served text, because there is no browser in this suite."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")
    assert "result.outputs" in page, "the page ignores the captured output"


def test_a_port_held_by_something_unidentifiable_gets_a_useful_error(project):
    """The transition case, and it is Paolo's: the consoles already running when
    this check was written have no `/api/whoami`, so nothing can negotiate with
    them. The bind then fails, and a bare Windows socket error does not tell
    anyone what to do about it."""
    import socket

    from rambass.console import serve
    from rambass.project import ProjectError

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(ProjectError) as caught:
            serve(project, port=port, open_browser=False)
        message = str(caught.value)
        assert str(port) in message
        assert "--port" in message
    finally:
        holder.close()


# ── a held row's button has to be able to do the thing its row asks for ──────
#
# Manlio's click row reads *"on disk, but no provenance — built before this was
# recorded, or by hand. Re-run it to make it checkable."* and carried a plain
# Run button. `render/click.wav` is `unknown`, so `rebuild_selection` holds it:
# the POST selected nothing, answered 200, and the page re-rendered the same
# sentence 600 ms later -- taking the log with it, so neither the reply nor the
# running bar was ever visible. A button that can never do what its own row
# asks for is worse than no button.


def _unprovenanced_click(song):
    path = song.directory / "render" / "click.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bounced by hand", encoding="utf-8")
    return path


def test_a_held_row_carries_its_artifact_so_it_can_be_forced(served, song):
    """The page can only post `force` if the payload names the artifact per
    row."""
    base, _ = served
    _unprovenanced_click(song)
    data = _get(base, f"/api/song/{song.slug}")
    click = [step for screen in data["screens"] for step in screen["steps"]
             if step["step"] == "click"]
    assert click, "no click row on the render screen"
    assert click[0]["state"] == "unknown"
    assert click[0]["artifact"] == "render/click.wav"


def test_forcing_a_held_step_over_http_runs_only_that_command(served, song,
                                                              monkeypatch):
    import rambass.review as review_module

    base, _ = served
    click = _unprovenanced_click(song)
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda cmd: (ran.append(cmd), (0, ""))[1])
    result = _post(base, "/api/rebuild", {"song": song.slug, "step": "click",
                                          "force": "render/click.wav"})
    assert ran == [f"rambass click {song.slug}"]
    assert result["backup"] == "click.wav.bak"
    assert click.with_suffix(".wav.bak").exists()


def test_the_page_forces_a_held_row_and_asks_first(served):
    """No browser here, so pin the wiring: the held row posts its artifact as
    `force`, and forcing is a confirmed act rather than a click."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")

    assert "data-force=" in page, "no held row carries its artifact"
    assert "dataset.force" in page, "the click handler ignores it"
    assert "confirm(" in page, "forcing a held artifact is not confirmed"
    assert "result.backup" in page, "the page never says a .bak was kept"


def test_the_page_keeps_its_log_across_the_refresh(served):
    """The re-render after a rebuild rebuilt `#log` empty, so a reply that ran
    nothing -- or a fetch that failed -- left the reader with the screen they
    started on and no output at all. The text outlives the re-render, and a
    failure is reported in the fixed bar, which is the one thing that cannot
    scroll away."""
    base, _ = served
    with urllib.request.urlopen(base + "/") as response:
        page = response.read().decode("utf-8")

    assert "lastLog" in page, "the log does not survive the re-render"
    assert "showProblem" in page, "a failure never reaches the fixed bar"
    assert "result.ran" in page, "the page re-renders even when nothing ran"
    assert "result.held" in page, "an empty selection never says what is held"
