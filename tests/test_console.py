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
    _serve(server)
    yield f"http://127.0.0.1:{server.server_address[1]}", song
    server.shutdown()


#: `shutdown()` blocks until the serve loop notices, and the loop polls every
#: half a second by default -- so every test that stood a server up paid 0.5s
#: to take it down again, which was two thirds of this module's runtime. The
#: real console keeps the default: there, a slow stand-down costs nothing and a
#: 10ms poll is a spin.
POLL = 0.01


def _serve(server) -> None:
    """Start *server* in a daemon thread that can be stopped promptly."""
    threading.Thread(target=lambda: server.serve_forever(POLL),
                     daemon=True).start()


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


def test_the_page_posts_the_step_it_was_clicked_on():
    """The whole bug was one line of JavaScript. Pin it: a per-step button
    sends its own step, and there is no browser here to catch it going back."""
    page = _page()
    assert "data-step=" in page
    assert "dataset.step" in page


def test_the_pages_script_parses():
    """console.html is one inline script with no build step and no linter, so a
    stray brace takes the whole console down while every test here stays green
    -- these tests assert on the page's *text*, which a syntax error does not
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


def test_the_page_says_what_is_rebuilding_where_it_can_be_seen():
    """Paolo: *"the 'rebuilding...' banner is at the bottom and I cannot see
    it. It should be contextual to what is rebuilding somehow?"* -- the log is
    the last element after every stage screen, so on a song with twelve run
    rows the only feedback for a three-minute separation was below the fold.

    Two answers, both pinned here because there is no browser in this suite:
    the row you clicked reports its own state, and a fixed bar names the
    command that is running wherever the page is scrolled to."""
    page = _page()

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


def test_the_page_gives_an_unprovenanced_file_its_own_dot():
    """`render/click.wav` is on disk and in the Reaper project, but it was
    built before provenance existed, so `stale_report` calls it `unknown`.
    `stepIcon` had no branch for that, so it fell through to the same hollow
    grey dot the console draws for a stage that does not apply -- and the click
    step read as "not applicable" for a song that has a click. Pinned on the
    page's own text, because there is no browser here."""
    page = _page()

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


def test_the_canvas_draws_a_per_side_reason_and_a_run_button():
    """No browser here, so pin the wiring the same way the busy row is."""
    page = _page()

    assert "data.sources" in page, "the canvas never reads per-side availability"
    assert 'id="make-ref"' in page, "no button for the source a command can make"
    assert "/api/run" in page


# ── the review screen's geometry, playhead and ruler ────────────────────────
#
# Paolo, using it: *"the instrument grid needs to sit under the two wavs (same
# width) so that the hits align perfectly with the waveform above"*, *"when
# playing there should be a cursor that shows me where we are"*, and *"much
# more granular bar subdivisions eg 3.1, 3.2, 3.3 and a vertical subtle grid on
# top of the wav canvas so I see where they align"*.
#
# There is no browser in this suite, so these pin the wiring on the page's own
# text the same way the busy row and the per-side canvas do -- plus
# `test_the_pages_script_parses`, which is what catches a stray brace.


def test_the_review_payload_carries_the_ruler_and_grid_resolution(reviewable):
    """The page draws Reaper bar numbers and a subdivision grid, so it needs
    the count-in and the subdivision -- and `count_in_bars` was not in the
    payload at all, which is how the ruler could only ever print musical
    bars."""
    base, song = reviewable
    data = _get(base, f"/api/review/{song.slug}")

    assert data["count_in_bars"] == song.count_in_bars == 2
    assert data["beats_per_bar"] == 4
    assert data["subdivision"] == song.drum_subdivision


def _page():
    """``console.html``, read where it is packaged.

    These tests are about what the page *says*; `test_the_page_is_served`
    covers the route that hands it over. Reading the file costs nothing, where
    the `served` fixture costs about half a second of server teardown per
    test -- which was most of this module's runtime, spent standing a threaded
    HTTP server up and down to fetch one static file.
    """
    return (Path(rambass_console.__file__).parent / "console.html").read_text(
        encoding="utf-8")


def test_the_grid_and_the_waveforms_share_one_label_gutter():
    """The bug, pinned: the A/B lanes reserved 170px + a 12px gap for their
    label and the grid rows reserved 90px + 14px, so every grid canvas was
    78px wider than the waveform above it and started 78px further left. Two
    numbers is the whole defect -- one variable is the fix, and a stray second
    width rule would silently bring it back."""
    page = _page()

    assert "--gutter:" in page, "no shared gutter variable"
    assert page.count("--gutter:") == 1, "the gutter is defined more than once"
    for who in (".lane .who", ".ruler .who", ".gridrow .who"):
        assert who in page, f"{who} lost its rule"
    assert "width:170px" not in page and "width:90px" not in page, (
        "a hard-coded label width is back; the gutter has to be one variable")


def test_the_grid_rows_are_in_the_same_stack_as_the_waveforms():
    """Same width is not enough: the grid was a panel of its own with its own
    padding, so it could not be pixel-aligned with the lanes even at equal
    canvas width. One relatively-positioned stack, which is also what the
    playhead is measured against."""
    page = _page()

    assert 'id="stack"' in page, "the canvases are not in one stack"
    assert "#stack" in page and "position:relative" in page, (
        "the stack is not a positioning context for the playhead")
    assert ".gridbox" not in page, "the grid is still a separate panel"


def test_the_playhead_exists_and_is_animated():
    """One line across the whole stack, moved by transform rather than by
    redrawing four canvases a frame."""
    page = _page()

    assert "playhead" in page, "no playhead element"
    assert "requestAnimationFrame" in page, "the playhead never moves"
    assert "cancelAnimationFrame" in page, "the frame loop is never stopped"
    assert "translateX" in page, "the playhead is not moved by transform"


def test_the_loop_restarts_from_both_sides():
    """`ended` was bound to the candidate alone, so a song being reviewed
    against the reference only (no qa/candidate.wav yet) never looped -- and
    the cursor Paolo asked for would have sat still at the end."""
    page = _page()

    assert page.count('addEventListener("ended"') >= 2, (
        "only one side reports the end of the clip")


def test_the_ruler_prints_reaper_numbers_at_beat_resolution():
    """Paolo works from Reaper's ruler and `qa/review.md` prints Reaper
    numbers; the console printed musical bars, two lower. The conversion
    belongs at the drawing edge, exactly where `restore.checklist` puts it --
    and the stored note stays musical."""
    page = _page()

    assert "count_in_bars" in page, "the ruler cannot add the count-in"
    assert "data.subdivision" in page, (
        "the page ignores drums.subdivision, so no triplet grid")


def test_the_review_screen_maps_positions_in_beats_not_bars():
    """`end_bar - start_bar` counted `closing-fill` (77.3 - 79.1) as two bars
    when it is one and a half, and Manlio's sections routinely start mid-bar.
    Every drawer used that arithmetic, and so did the bar a logged note gets
    -- which reaches song.yaml through `review promote`."""
    page = _page()

    assert "Math.max(1, section.end_bar - section.start_bar)" not in page, (
        "the bar-count division is back")
    for helper in ("sectionBeats", "beatOffset", "beatMarks"):
        assert helper in page, f"no {helper}: the mapping is not in beats"


def test_the_waveform_peaks_are_cached_and_survive_a_resize():
    """A repaint is now an ordinary event (resize, playhead, section change),
    and `drawWave` refetched and re-decoded the clip every time. It also meant
    a window resize left every canvas at its old width, which is a
    misalignment of exactly the kind this change is about."""
    page = _page()

    assert "peakCache" in page, "the decoded peaks are not cached"
    assert '"resize"' in page, "nothing redraws when the window changes width"


# ── the review screen's position mapping, exercised in node ──────────────────
#
# `beatMarks` / `sectionBeats` / `positionAt` / `fracFor` decide where every
# grid line, every hit and the playhead go, and getting them wrong is a silent
# misalignment rather than an error. They are pure -- no canvas, no audio -- so
# node can run them, and the alternative was checking them by eye in a browser
# and writing nothing down. Skipped rather than required when node is absent,
# the same rule as `test_the_pages_script_parses`, ffmpeg and librosa.

GEOMETRY_HARNESS = """
/* Enough of a DOM for the class body to evaluate. Nothing here is called by
   the geometry: it exists because the script's top level does `$("#app")`. */
globalThis.document = {querySelector: () => null, addEventListener: () => {}};
globalThis.window = {addEventListener: () => {}, location: {hash: ""}};
globalThis.Audio = class { constructor() { this.duration = 0; } };
globalThis.fetch = () => Promise.reject(new Error("no network in this harness"));
"""

GEOMETRY_PROBE = """
/* Manlio's two awkward sections, from songs/tutti-in-fila/09-manlio/song.yaml:
   verse-2 starts mid-bar, and closing-fill is one and a half bars long. */
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2,
  title: "Manlio", grid: {}, notes: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c1", ref_url: "r1"},
    {name: "closing-fill", start_bar: 77, start_beat: 3, end_bar: 79,
     end_beat: 1, cand_url: "c2", ref_url: "r2"},
  ]};
const view = new ReviewView("manlio", data);
const show = (section) => view.beatMarks(section).map(
  (mark) => `${mark.bar}.${mark.beat}${mark.line ? "|" : ""}`);
const out = {};
view.index = 0;
out.verse2Beats = view.sectionBeats(data.sections[0]);
out.verse2Marks = show(data.sections[0]);
out.verse2At0 = view.positionAt(0);
out.verse2Half = view.positionAt(0.5);
view.index = 1;
out.fillBeats = view.sectionBeats(data.sections[1]);
out.fillMarks = show(data.sections[1]);
out.fillAt1 = view.positionAt(1);
out.barSeventyEight = view.fracFor(data.sections[1], 78, 1);
console.log(JSON.stringify(out));
"""


def _run_geometry_probe(tmp_path, probe=None):
    """Evaluate the page's ReviewView in node and return the probe's answers."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; cannot exercise the page geometry")

    page = (Path(rambass_console.__file__).parent / "console.html").read_text(
        encoding="utf-8")
    script = re.findall(r"<script>(.*?)</script>", page, re.S)[0]
    # Everything from the keyboard block down runs against a real document and
    # calls `route()`; the class above it does not.
    script = script[:script.index("/* \u2500\u2500 keyboard")]
    harness = tmp_path / "geometry.js"
    harness.write_text(GEOMETRY_HARNESS + script + (probe or GEOMETRY_PROBE),
                       encoding="utf-8")
    done = subprocess.run([node, str(harness)], capture_output=True, text=True,
                          encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_section_starting_mid_bar_maps_beat_by_beat(tmp_path):
    """Manlio's verse-2 runs 20.3 - 28.3: eight bars, but the repetitions tile
    the BAR grid, so its lines have to fall on bar 21, 22 ... and not on
    multiples of the section start. 32 beats, 33 lines with the closing one."""
    out = _run_geometry_probe(tmp_path)

    assert out["verse2Beats"] == 32
    assert len(out["verse2Marks"]) == 33
    assert out["verse2Marks"][:5] == ["20.3", "20.4", "21.1|", "21.2", "21.3"]
    assert out["verse2Marks"][-1] == "28.3"
    assert out["verse2At0"] == {"bar": 20, "beat": 3}
    # Half way through 32 beats from 20.3 is 24.3, not bar 24 beat 1.
    assert out["verse2Half"] == {"bar": 24, "beat": 3}


def test_a_section_of_one_and_a_half_bars_is_not_counted_as_two(tmp_path):
    """closing-fill is 77.3 - 79.1. Every drawer used to compute
    `end_bar - start_bar` = 2 bars, which put bar 78 half way across the clip
    when it is a third of the way in -- 9% of the section out, on every line,
    every hit and the playhead."""
    out = _run_geometry_probe(tmp_path)

    assert out["fillBeats"] == 6
    assert out["fillMarks"] == ["77.3", "77.4", "78.1|", "78.2", "78.3",
                                "78.4", "79.1|"]
    assert out["fillAt1"] == {"bar": 79, "beat": 1}
    assert out["barSeventyEight"] == pytest.approx(2 / 6, abs=1e-9)
    assert out["barSeventyEight"] != pytest.approx(0.5, abs=0.01)


RULER_PROBE = """
/* A canvas that records what was written where, so the ruler's two judgements
   can be checked: the count-in it adds, and how far it thins the labels. A
   monospace glyph at this size is a bit over half the em, hence 11 device px
   per character. */
function fakeCanvas(cssWidth) {
  const written = [];
  const context = {
    canvas: null, fillStyle: "", font: "",
    clearRect() {}, fillRect() {},
    measureText: (text) => ({width: text.length * 11}),
    fillText(text, x) { written.push({text: text, x: x}); },
  };
  const canvas = {clientWidth: cssWidth, clientHeight: 30, width: 0, height: 0,
                  getContext: () => context};
  context.canvas = canvas;
  return {canvas: canvas, written: written};
}

const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2,
  title: "Manlio", grid: {}, notes: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c1", ref_url: "r1"},
    {name: "theme-finale", start_bar: 65, start_beat: 3, end_bar: 77,
     end_beat: 3, cand_url: "c2", ref_url: "r2"},
  ]};
const view = new ReviewView("manlio", data);
const out = {};
[0, 1].forEach((index) => {
  view.index = index;
  const fake = fakeCanvas(1100);
  globalThis.document.querySelector = () => fake.canvas;
  view.marks = view.beatMarks(view.section);
  view.drawRuler(view.section);
  out[view.section.name] = fake.written.map((call) => call.text);
});
console.log(JSON.stringify(out));
"""


def test_the_ruler_labels_reaper_bars_and_thins_them_to_fit(tmp_path):
    """Two judgements, both Paolo's and both invisible until they are wrong.

    The numbers are **Reaper's** -- verse-2 is bar 20.3 in `song.yaml` and
    22.3 on the ruler he works from, with `count_in.bars: 2` -- and the
    label step adapts, because a label per beat fits verse-2's 32 beats in a
    1100px window and cannot fit theme-finale's 48. A fixed step is
    unreadable on one and wasteful on the other."""
    out = _run_geometry_probe(tmp_path, RULER_PROBE)

    verse = out["verse-2"]
    assert verse[:5] == ["22.3", "22.4", "23.1", "23.2", "23.3"], (
        "the ruler is not on Reaper numbers at beat resolution")

    finale = out["theme-finale"]
    assert finale[0] == "67.3", "the section start is not labelled"
    # Every later label is a bar line: 48 beats in this width cannot carry a
    # label each, so the step has stepped up from a beat to a bar.
    assert all(text.endswith(".1") for text in finale[1:]), finale
    assert len(finale) < 20, f"labels did not thin out: {finale}"


ALIGNMENT_PROBE = """
/* Three canvases of the same width -- which is what the shared --gutter buys
   in CSS -- recording every rectangle drawn, so the x positions can be
   compared across the row kinds. */
function recorder(cssWidth, cssHeight) {
  const rects = [];
  const context = {
    canvas: null, fillStyle: "", font: "",
    clearRect() {}, measureText: (text) => ({width: text.length * 11}),
    fillText() {},
    fillRect(x, y, w, h) { rects.push({x: x, w: w, style: context.fillStyle}); },
  };
  const canvas = {clientWidth: cssWidth, clientHeight: cssHeight,
                  width: 0, height: 0, getContext: () => context};
  context.canvas = canvas;
  return {canvas: canvas, rects: rects};
}

const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  notes: [], sources: {},
  sections: [{name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28,
              end_beat: 3, cand_url: "c", ref_url: "r"}],
  /* A kick on bar 21 beat 1 -- a bar line -- and one on 21.4.667, which is the
     second triplet of the beat and where this shuffle actually puts it. */
  grid: {"verse-2": [{instrument: "kick", ticks: [
    {bar: 21, beat: 1, velocity: 100}, {bar: 21, beat: 4.667, velocity: 90}]}]},
};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(view.section);
const section = view.section;

const lane = recorder(1100, 52), ruler = recorder(1100, 30),
      row = recorder(1100, 26);
/* Pretend the candidate clip decoded to a flat waveform, so the lane draws its
   grid and its peaks the way it does in the browser. */
view.peakCache["c"] = {peaks: [0.5, 0.5], failed: false};
globalThis.document.querySelector = () => lane.canvas;
view.paintLane("cand");
globalThis.document.querySelector = () => ruler.canvas;
view.drawRuler(section);
globalThis.document.querySelector = () => ({querySelector: () => row.canvas});
view.paintGridRow(data.grid["verse-2"][0], 0, section);

const barLines = (rec, colour) => rec.rects
  .filter((r) => r.style === colour && r.w === 2).map((r) => r.x);
const out = {
  laneBars:  barLines(lane, "#454c5b"),
  rulerBars: barLines(ruler, "#3b414e"),
  rowBars:   barLines(row, "#3f4653"),
  // The hits: 4px wide, drawn in the tick colour.
  hits: row.rects.filter((r) => r.style === "#c7cbd3" && r.w === 4).map((r) => r.x),
  subs: row.rects.filter((r) => r.style === "#272c34").length,
};
console.log(JSON.stringify(out));
"""


def test_the_three_row_kinds_put_a_bar_line_at_the_same_x(tmp_path):
    """The other half of the alignment defect, and the half CSS cannot fix:
    `drawWave` spread over `canvas.width`, `drawRuler` over `width - 2` and
    `drawGrid` put bar lines at `width` but hits at `width - 4 + 1`. Given
    canvases of equal width -- which the shared --gutter is what buys -- every
    row kind must now agree to the pixel."""
    out = _run_geometry_probe(tmp_path, ALIGNMENT_PROBE)

    assert out["laneBars"], "the waveform lane draws no bar lines"
    assert out["laneBars"] == out["rulerBars"] == out["rowBars"], (
        "the row kinds still disagree about where a bar line goes")


def test_a_hit_is_centred_on_its_own_grid_line(tmp_path):
    """A 4px tick drawn *from* the line sits 2px late, which at this scale is
    the difference between "on the beat" and "not sure". And the second hit is
    on beat 4.667 -- Manlio is a shuffle, so `drums.subdivision: 3` has to put
    a line exactly there or the triplet reads as a mistake."""
    out = _run_geometry_probe(tmp_path, ALIGNMENT_PROBE)

    # bar 21 beat 1 is the first bar line in the section (verse-2 starts 20.3).
    on_the_bar = out["laneBars"][0]
    assert out["hits"][0] == pytest.approx(on_the_bar - 2, abs=0.01)

    # Two subdivision lines per beat, at 1/3 and 2/3, on 32 beats.
    assert out["subs"] == 64, out["subs"]
    beat_width = 2200 / 32
    assert out["hits"][1] == pytest.approx(
        on_the_bar + 3 * beat_width + 2 * beat_width / 3 - 2, abs=0.5)


# ── clicking the stack to place the playhead ─────────────────────────────────
#
# Paolo: *"we need the ability to click on the wav/grid and move the playhead
# so we can zone in on a given hit or portion"*. Every row of the stack shares
# one mapping, so any of them can be clicked; the position snaps to the song's
# own grid, because a hit is on the grid and landing between two lines is never
# what was aimed at.


def test_the_stack_can_be_clicked_and_dragged():
    """Pinned on the page's own text: pointer events, a captured drag so the
    pointer can leave the canvas mid-scrub, and an escape hatch from the snap."""
    page = _page()

    assert "pointerdown" in page, "the stack cannot be clicked"
    assert "pointermove" in page, "the playhead cannot be dragged"
    assert "setPointerCapture" in page, (
        "a drag that leaves the canvas stops following the pointer")
    assert "altKey" in page, "there is no way to place the playhead unsnapped"
    assert 'id="at"' in page, "the playhead never says where it is"


SCRUB_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  notes: [], sources: {}, grid: {},
  sections: [{name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28,
              end_beat: 3, cand_url: "c", ref_url: "r"}],
};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(view.section);
const out = {};
/* 32 beats at three subdivisions is 96 lines, so 1/96 apart. */
out.snapped = [0, 0.1, 0.10417, 0.5, 0.999, 1].map((at) => view.snapFraction(at));
/* Whatever is clicked, the beat it lands on is a whole triplet of the shuffle
   -- (beat - 1) * 3 is an integer. */
out.beats = [0.017, 0.211, 0.409, 0.6, 0.77, 0.95].map((at) => {
  const beat = view.positionAt(view.snapFraction(at)).beat;
  return Math.abs((beat - 1) * 3 - Math.round((beat - 1) * 3));
});
/* The readout: quarter of the way through 32 beats from musical 20.3 is 22.3,
   which is 24.3 on the ruler Paolo reads. */
view.cand = {duration: 12, currentTime: 3};
out.label = view.playheadLabel();
out.bar = view.playheadBar();
console.log(JSON.stringify(out));
"""


def test_a_click_lands_on_the_songs_own_grid(tmp_path):
    """Manlio is a shuffle -- `drums.subdivision: 3` -- so the grid a click
    snaps to is the triplet grid its hits actually sit on. A pixel-exact seek
    would put the playhead between two lines, which is never the position
    anybody was aiming for; alt is the way out of it."""
    out = _run_geometry_probe(tmp_path, SCRUB_PROBE)

    assert out["snapped"][0] == 0
    assert out["snapped"][1] == pytest.approx(10 / 96, abs=1e-9)
    assert out["snapped"][2] == pytest.approx(10 / 96, abs=1e-9)
    assert out["snapped"][3] == pytest.approx(0.5, abs=1e-9)
    assert out["snapped"][-1] == 1
    # Every one of them is a whole triplet away from the beat.
    assert out["beats"] == pytest.approx([0] * 6, abs=1e-9)


def test_the_playhead_says_where_it_is_in_reaper_numbers(tmp_path):
    """So a position found by ear can be typed into `rambass section` or
    matched against what `rambass drums missing` printed -- both of which are
    Reaper's numbers, with the count-in in them."""
    out = _run_geometry_probe(tmp_path, SCRUB_PROBE)

    assert out["label"] == "24.3", out["label"]
    # ...while the note it would log stays musical, as the ledger stores it.
    assert out["bar"] == 22


# ── a clip has to be seekable, which means byte ranges ───────────────────────
#
# Paolo: *"when I click on a given area of the wav the playhead should move
# there and play from there, while at the moment clicking anywhere in the
# canvas makes it restart from the beginning"*. The click was right; the audio
# element refused to move. `_clip` answered every request with 200 and the
# whole body, ignoring `Range` and never sending `Accept-Ranges` -- and a
# browser treats a media resource with no range support as NOT SEEKABLE, so
# assigning `currentTime` snaps back to the start of what is buffered. No
# amount of work on the page could have fixed it.


def _clip_url(base, song):
    data = _get(base, f"/api/review/{song.slug}")
    return next(s for s in data["sections"] if s["name"] == "verse")["ref_url"]


def _fetch(base, path, **headers):
    request = urllib.request.Request(base + path, headers=headers)
    reply = urllib.request.urlopen(request)
    with reply:
        return reply.status, dict(reply.headers), reply.read()


def test_a_clip_advertises_that_it_can_be_seeked(stem_only):
    """The header the browser reads before it will let you seek at all."""
    base, song, _ = stem_only
    status, headers, body = _fetch(base, _clip_url(base, song))

    assert status == 200
    assert headers.get("Accept-Ranges") == "bytes"
    assert int(headers["Content-Length"]) == len(body)


def test_a_ranged_clip_request_gets_that_range_and_nothing_else(stem_only):
    """206 with `Content-Range`, and only the asked-for bytes -- not the whole
    file with a misleading status, and not the whole file read into memory to
    answer a request for ten bytes of it."""
    base, song, _ = stem_only
    url = _clip_url(base, song)
    whole = _fetch(base, url)[2]

    status, headers, body = _fetch(base, url, Range="bytes=4-7")
    assert status == 206
    assert body == whole[4:8]
    assert headers["Content-Range"] == f"bytes 4-7/{len(whole)}"
    assert int(headers["Content-Length"]) == 4


def test_the_open_ended_and_suffix_range_forms_both_work(stem_only):
    """`bytes=N-` is what a media element sends to resume, and `bytes=-N` is
    how some of them read a trailer. Both are in RFC 9110 and neither is
    exotic."""
    base, song, _ = stem_only
    url = _clip_url(base, song)
    whole = _fetch(base, url)[2]

    status, headers, body = _fetch(base, url, Range="bytes=6-")
    assert (status, body) == (206, whole[6:])
    assert headers["Content-Range"] == f"bytes 6-{len(whole) - 1}/{len(whole)}"

    status, _, body = _fetch(base, url, Range="bytes=-3")
    assert (status, body) == (206, whole[-3:])


def test_a_range_past_the_end_is_416_and_says_how_long_the_file_is(stem_only):
    """The status a browser needs in order to correct itself. Answering 200
    with the whole body instead is what makes a player give up on seeking."""
    base, song, _ = stem_only
    url = _clip_url(base, song)
    whole = _fetch(base, url)[2]

    with pytest.raises(urllib.error.HTTPError) as caught:
        _fetch(base, url, Range=f"bytes={len(whole) + 10}-")
    assert caught.value.code == 416
    assert caught.value.headers["Content-Range"] == f"bytes */{len(whole)}"


def test_an_unparseable_range_falls_back_to_the_whole_file(stem_only):
    """RFC 9110: an unsatisfiable-because-malformed `Range` is ignored, not an
    error. A clip that 400s here is a clip that does not play at all."""
    base, song, _ = stem_only
    url = _clip_url(base, song)
    whole = _fetch(base, url)[2]

    for bad in ("bananas", "bytes=", "bytes=x-y", "items=0-1", "bytes=5-2"):
        status, _, body = _fetch(base, url, Range=bad)
        assert (status, body) == (200, whole), bad


def test_the_clip_route_speaks_http_1_1():
    """Seeking is a stream of range requests and HTTP/1.0 closes the connection
    after each one, so a scrub across a 5.6 MB clip becomes a TCP connection
    per move. Safe because every reply here sets an accurate Content-Length,
    which is the only thing keep-alive needs."""
    assert rambass_console.ConsoleHandler.protocol_version == "HTTP/1.1"


@pytest.mark.parametrize("header,expected", [
    ("bytes=0-9", (0, 10)),
    ("bytes=4-7", (4, 8)),
    ("bytes=6-", (6, 12)),
    ("bytes=-3", (9, 12)),
    ("bytes=-99", (0, 12)),      # a trailer longer than the file is the file
    ("bytes=11-99", (11, 12)),   # clipped to the end, not refused
    ("bytes=12-", ()),           # well formed, past the end: 416
    ("bytes=5-2", None),         # nonsense: ignore it, send the whole file
    ("bytes=x-y", None),
    ("bytes=", None),
    ("bytes=0-4,8-9", None),     # multipart: not what a player sends
    ("items=0-1", None),
    ("bananas", None),
    ("", None),
])
def test_the_range_parser_only_answers_what_a_player_asks(header, expected):
    """The forms a media element actually sends, and the two different ways of
    saying no: `None` is "ignore this and send the whole file" (RFC 9110's rule
    for a header that cannot be parsed -- a clip that 400s here is a clip that
    never plays), `()` is "well formed but past the end", which is a 416 the
    browser can correct itself from."""
    from rambass.console import parse_byte_range

    assert parse_byte_range(header, 12) == expected


SEEK_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  notes: [], sources: {}, grid: {},
  sections: [{name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28,
              end_beat: 3, cand_url: "c", ref_url: "r"}],
};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(view.section);
view.updatePlayhead = () => {};
view.cand = {duration: 12, currentTime: 0};
view.ref = {duration: 12.08, currentTime: 0};
const out = {};
view.seek(0.5);
out.half = [view.cand.currentTime, view.ref.currentTime];
view.seek(1);
out.end = [view.cand.currentTime, view.ref.currentTime];
view.seek(-3);
out.before = view.cand.currentTime;
console.log(JSON.stringify(out));
"""


def test_seeking_to_the_end_stops_short_of_it(tmp_path):
    """Assigning `currentTime = duration` fires `ended`, and with loop on that
    restarts from the beginning -- so clicking the right-hand edge of a lane
    did exactly what Paolo was complaining about, for a different reason. Both
    sides land inside their own clip, which are not the same length."""
    out = _run_geometry_probe(tmp_path, SEEK_PROBE)

    assert out["half"] == pytest.approx([6.0, 6.04], abs=1e-9)
    assert out["end"][0] < 12 and out["end"][1] < 12.08
    assert out["end"] == pytest.approx([11.99, 12.07], abs=1e-9)
    assert out["before"] == 0


# ── a note has to say what it is about before it is committed ────────────────
#
# Paolo, after logging one: *"I was not aware I was adding a note to the
# 'crash' (no visual clue of what was selected) and I dont know what 1.1 refers
# to (again I did not select it, and nevertheless the section starts at 3.1)"*.
#
# Two defects, one cause -- the panel wrote things it never showed:
#
# * `saveNote` hard-coded `instrument: "crash"` for every missing/extra-hit
#   note, with nothing on screen saying so. `drummap.CANONICAL` has had
#   `crash_2`, `crash_choke`, `china`, `china_choke` and `splash` all along,
#   so the answer to "should we split crash into crash, splash etc?" is that it
#   already is -- the panel was hiding the vocabulary behind a default.
# * The chip printed the stored **musical** bar while the ruler above it now
#   prints **Reaper's**. Bar 1 musical is 3.1 on the ruler he was reading, so
#   the note was in the right place and looked two bars wrong. `qa/review.md`
#   has always converted; the console's own chips were the one place left that
#   did not.


def test_the_review_payload_offers_the_whole_kit(reviewable):
    """The instrument list comes from `drummap.CANONICAL`, so the panel cannot
    drift from what a note is allowed to name -- `review.promote_notes` skips
    anything not in it, so a name the panel invented would be silently
    unpromotable."""
    base, song = reviewable
    from rambass.drummap import CANONICAL

    instruments = _get(base, f"/api/review/{song.slug}")["instruments"]
    assert instruments == list(CANONICAL)
    # The cymbals Paolo asked about are already there; no split is needed.
    for name in ("crash", "crash_2", "crash_choke", "china", "splash"):
        assert name in instruments


def test_the_note_panel_shows_what_it_will_write():
    """The fix for "no visual clue": the instrument is a control on screen, the
    position it will land on is printed next to the button, and there is no
    hidden default doing either of them for you."""
    page = _page()

    assert 'id="instrument"' in page, "the instrument is still not on screen"
    assert 'id="note-at"' in page, "nothing says where the note will land"
    assert 'instrument: ["missing-hit","extra-hit"].includes' not in page, (
        "the hidden crash default is back")
    assert "data.instruments" in page, "the panel does not read the kit list"


def test_clicking_an_instrument_row_picks_that_instrument():
    """The grid rows are labelled, so the row is the visual clue -- clicking
    the snare lane at a bar means "here, snare", and the pick is highlighted."""
    page = _page()

    assert "pickInstrument" in page, "a grid row does not select its instrument"
    assert ".gridrow.picked" in page, "a picked row looks no different"


CHIP_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  sources: {}, grid: {}, instruments: ["crash", "splash"],
  sections: [{name: "theme-intro", start_bar: 1, start_beat: 1, end_bar: 4,
              end_beat: 1, cand_url: "c", ref_url: "r"}],
  /* Stored musical, as the ledger stores it: bar 1 beat 1 is 3.1 on the ruler.
     Paolo's own note, which read "1.1" and looked two bars wrong. */
  notes: [{bar: 1, beat: 1, kind: "missing-hit", instrument: "crash",
           comment: "missing cymbals at 3.2, 3.3", status: "open"}],
};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(view.section);
console.log(JSON.stringify({
  chip: view.chipPosition(data.notes[0]),
  target: view.noteTarget(),
}));
"""


def test_a_chip_and_the_ruler_agree_on_the_bar(tmp_path):
    """One clock on the screen. The note is stored musical -- bar 1 -- and every
    number a human reads on this screen is Reaper's, so the chip says 3.1 like
    the ruler above it and like `qa/review.md` beside it."""
    out = _run_geometry_probe(tmp_path, CHIP_PROBE)

    assert out["chip"] == "3.1"
    # And the same conversion for the note about to be written, so the panel
    # cannot promise one bar and store another.
    assert out["target"] == "3.1"


# ── retiring a note from the browser, and reading the ledger at all ──────────
#
# Paolo: *"there is a "[open]" string that does nothing, also how do I remove a
# wrong note?"* and *"can the notes be listed in a ordered list rather than
# like tag/pills? (they pile up horizontally and are difficult to read)"*. The
# same panel: inline pills with nowhere to put a control, and the note's status
# printed as decoration on every row including the default one.


def _keyed(base, song):
    data = _get(base, f"/api/review/{song.slug}")
    return data["notes"]


def test_every_note_comes_back_with_the_key_that_names_it(reviewable):
    """The ledger has no ids, so the key is how the browser says *this* note --
    an index into a list that re-sorts on every write would eventually delete
    the wrong one."""
    base, song = reviewable
    _post(base, "/api/note", {"song": song.slug, "bar": 9, "beat": 3.0,
                              "kind": "missing-hit", "instrument": "splash",
                              "comment": "different sound from 3.1"})
    notes = _keyed(base, song)

    assert len(notes) == 1
    assert notes[0]["key"] == "9|3|missing-hit|splash|different sound from 3.1"


def test_a_note_can_be_removed_from_the_browser(reviewable):
    base, song = reviewable
    from rambass.review import load_review

    _post(base, "/api/note", {"song": song.slug, "bar": 9, "kind": "timing",
                              "comment": "keep"})
    _post(base, "/api/note", {"song": song.slug, "bar": 17, "kind": "timing",
                              "comment": "filed at the wrong bar"})
    doomed = next(n for n in _keyed(base, song) if n["bar"] == 17)

    left = _post(base, "/api/note/remove",
                 {"song": song.slug, "key": doomed["key"]})["notes"]

    assert [n["bar"] for n in left] == [9]
    assert [n.bar for n in load_review(song.path("qa", "review.yaml"))[1]] == [9]


def test_a_note_can_be_dismissed_and_reopened_from_the_browser(reviewable):
    base, song = reviewable
    _post(base, "/api/note", {"song": song.slug, "bar": 9,
                              "kind": "missing-hit", "instrument": "crash"})
    key = _keyed(base, song)[0]["key"]

    notes = _post(base, "/api/note/status",
                  {"song": song.slug, "key": key, "status": "dismissed"})["notes"]
    assert notes[0]["status"] == "dismissed"

    notes = _post(base, "/api/note/status",
                  {"song": song.slug, "key": key, "status": "open"})["notes"]
    assert notes[0]["status"] == "open"


def test_removing_a_note_that_has_gone_is_a_400_that_says_to_reload(reviewable):
    """Two tabs, or qa/review.yaml edited by hand. Better than deleting
    whatever now sits where that note used to."""
    base, song = reviewable
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/note/remove",
              {"song": song.slug, "key": "9|1|timing||gone"})
    assert caught.value.code == 400
    assert "reload" in json.loads(caught.value.read().decode("utf-8"))["error"]


def test_the_notes_read_as_a_list_with_a_control_per_row():
    """A vertical list, and `[open]` gone: it was the default status printed on
    every row, which said nothing and read as a button."""
    page = _page()

    assert ".notelist" in page, "the notes are not a list"
    assert 'class="chip"' not in page, "the pills are still there"
    assert "dropNote" in page and "restatus" in page, (
        "a note cannot be retired from the row it is on")
    assert "/api/note/remove" in page and "/api/note/status" in page
    # The badge is only drawn for a status that is not the default.
    assert 'note.status === "open" ? ""' in page, (
        "the default status is printed as decoration again")


def test_a_stale_clip_is_cut_again_rather_than_served_from_the_cache(reviewable,
                                                                    monkeypatch):
    """The other half of the loop. Clips were cut on first request and kept
    forever, so a rebuilt candidate went on being A/B'd as the audio from
    before the edit. Any reason the source moved counts, which is why this is
    an mtime check and not a provenance entry per clip."""
    import os
    import time

    from rambass import review as review_module

    base, song = reviewable
    source = song.path("stems", "drums.wav")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"first")

    cuts: list[str] = []

    def fake_cut(src, target, *, start, duration):
        cuts.append(Path(src).read_text(encoding="utf-8"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RIFF....WAVE")

    monkeypatch.setattr(review_module, "cut_clip", fake_cut)
    url = next(s for s in _get(base, f"/api/review/{song.slug}")["sections"]
               if s["name"] == "verse")["ref_url"]

    urllib.request.urlopen(base + url).close()
    urllib.request.urlopen(base + url).close()
    assert cuts == ["first"], "the second request re-cut an unchanged source"

    # The source is replaced -- a re-render, a re-separation, anything.
    source.write_bytes(b"second")
    os.utime(source, (time.time() + 100,) * 2)
    urllib.request.urlopen(base + url).close()
    assert cuts == ["first", "second"], "a moved source was served from cache"


def test_the_review_payload_says_whether_the_candidate_is_current(reviewable):
    base, song = reviewable
    state = _get(base, f"/api/review/{song.slug}")["candidate"]

    assert set(state) == {"exists", "fresh", "why", "command"}
    assert state["command"] == f"rambass review render {song.slug}"


def test_the_screen_says_how_a_note_becomes_a_rebuilt_part():
    """Three buttons in a row with no stated order, and Rebuild on its own does
    nothing with a note -- which is exactly what Paolo asked about. The chain
    is on the screen now, next to the buttons that carry it out."""
    page = _page()

    assert "data.candidate" in page, "the screen ignores a stale candidate"
    assert "id=\"note-flow\"" in page, "nothing says what happens to a note"
    for word in ("Promote", "Rebuild", "re-render"):
        assert word in page, f"the chain does not mention {word}"


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
    _serve(server)
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
    _serve(old)

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
    _serve(old)
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


def test_the_page_shows_a_failed_commands_output():
    """A log that prints only "FAIL" is the bug: the refusal from
    `analyze --write` has to be readable where the button was pressed. Pinned
    on the page's own text, because there is no browser in this suite."""
    page = _page()
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


def test_the_page_forces_a_held_row_and_asks_first():
    """No browser here, so pin the wiring: the held row posts its artifact as
    `force`, and forcing is a confirmed act rather than a click."""
    page = _page()

    assert "data-force=" in page, "no held row carries its artifact"
    assert "dataset.force" in page, "the click handler ignores it"
    assert "confirm(" in page, "forcing a held artifact is not confirmed"
    assert "result.backup" in page, "the page never says a .bak was kept"


def test_the_page_keeps_its_log_across_the_refresh():
    """The re-render after a rebuild rebuilt `#log` empty, so a reply that ran
    nothing -- or a fetch that failed -- left the reader with the screen they
    started on and no output at all. The text outlives the re-render, and a
    failure is reported in the fixed bar, which is the one thing that cannot
    scroll away."""
    page = _page()

    assert "lastLog" in page, "the log does not survive the re-render"
    assert "showProblem" in page, "a failure never reaches the fixed bar"
    assert "result.ran" in page, "the page re-renders even when nothing ran"
    assert "result.held" in page, "an empty selection never says what is held"
