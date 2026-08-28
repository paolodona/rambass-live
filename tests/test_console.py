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


def test_the_running_bar_does_not_look_like_the_page_behind_it():
    """Paolo: *"update background color of div id=\"running\" so that its a bit
    more visible (dark orange for example?)"*. It was `--raised`, the same
    colour as every card on the screen and two shades off the background, so a
    bar that means "a three-minute separation is going and every button is held"
    read as part of the furniture. It gets its own warm token, and the failure
    state keeps its own so red and orange never mean the same thing."""
    page = _page()
    css = page[page.index("  #running {"):]
    css = css[:css.index("  .step .what")]

    assert "background:var(--running)" in css, "the bar is back on a panel colour"
    assert "--running:" in page and "--running-bad:" in page
    # The command name has to stay legible against it: the periwinkle accent on
    # dark orange is the one combination this change could have broken.
    assert "var(--accent)" not in css, "accent text left on the warm background"
    assert "background:var(--running-bad)" in css, "a failure looks like a run"

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
    the wrong one.

    The empty field is the swap target: every field a human filed is in the
    key, and a note that is not a swap filed nothing there. The format is free
    to grow -- a key is handed back within one page load and is never stored --
    but it has to stay derived from the fields, which is what this pins."""
    base, song = reviewable
    _post(base, "/api/note", {"song": song.slug, "bar": 9, "beat": 3.0,
                              "kind": "missing-hit", "instrument": "splash",
                              "comment": "different sound from 3.1"})
    notes = _keyed(base, song)

    assert len(notes) == 1
    assert notes[0]["key"] == "9|3|missing-hit|splash||different sound from 3.1"


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

    # `action` says which of the two things is behind -- the audio, or the MIDI
    # it was rendered from -- because the button that fixes them is not the same
    # one. See review.midi_behind_manifest.
    assert set(state) == {"exists", "fresh", "why", "command", "action"}
    assert state["command"] == f"rambass review render {song.slug}"
    assert state["action"] == "render"


def test_the_screen_says_how_a_note_becomes_a_rebuilt_part():
    """Three buttons in a row with no stated order, and Rebuild on its own does
    nothing with a note -- which is exactly what Paolo asked about. The chain
    is on the screen now, next to the buttons that carry it out."""
    page = _page()

    assert "data.candidate" in page, "the screen ignores a stale candidate"
    assert "id=\"note-flow\"" in page, "nothing says what happens to a note"
    for word in ("Promote", "Rebuild", "re-render"):
        assert word in page, f"the chain does not mention {word}"


# ── the sections screen ──────────────────────────────────────────────────────
#
# Paolo: *"the console has one step "Sections & consolidate ... mark the
# sections first: rambass section <bar> <name>". This has a single Run action to
# consolidate. Would we split this into "Sections" with a UI to add named
# sections (name + starting reaper bar, eg 3.3), and Consolidate separately?
# (consolidate goes stale if sections change obviously)"*.
#
# One row was doing two unrelated jobs: marking a section is a hand edit to
# song.yaml, consolidating is a derived rebuild. The staleness between them was
# already wired -- `drums consolidate` carries `sections` in its provenance
# fields -- but invisible, because the row that edits and the row that reacts
# were the same row.


def test_the_drums_screen_splits_sections_from_consolidate(song):
    from rambass.review import steps_for

    labels = [step.label for step in steps_for(song, "drums")]
    assert "Sections" in labels and "Consolidate" in labels
    assert "Sections & consolidate" not in labels
    # And in that order: you cannot consolidate a list you have not written.
    assert labels.index("Sections") < labels.index("Consolidate")


def test_the_sections_row_opens_a_screen_and_knows_its_own_state(song):
    """It produces no artifact, so it establishes its own state -- which is what
    StepRow.state exists for."""
    from rambass.review import steps_for

    rows = {step.label: step for step in steps_for(song, "drums")}
    sections = rows["Sections"]
    assert sections.kind == "open" and sections.target == "sections"
    assert sections.state == "ok"          # the fixture song has three

    song.sections = []
    rows = {step.label: step for step in steps_for(song, "drums")}
    assert rows["Sections"].state == "missing"


def test_consolidate_keeps_its_artifact_and_says_what_makes_it_stale(song):
    from rambass.review import steps_for

    rows = {step.label: step for step in steps_for(song, "drums")}
    assert rows["Consolidate"].artifact == "midi/drums-consolidated.mid"
    assert rows["Consolidate"].kind == "run"
    assert "stale" in rows["Consolidate"].note


def test_the_page_builds_an_open_button_from_its_target():
    """There are two openers now, so the route cannot stay hardcoded to
    #/review/ in the one branch that draws them."""
    page = _page()

    assert "step.target" in page, "the open branch ignores the row's target"
    assert "step.open_label" in page, "every opener still says 'review tool'"
    assert "renderSections(decodeURIComponent" in page, (
        "the router does not know the sections route")


def test_the_sections_payload_lists_them_in_reaper_numbers(served, song):
    base, _ = served
    data = _get(base, f"/api/sections/{song.slug}")

    assert [row["reaper"] for row in data["sections"]] == ["3.1", "11.1", "19.1"]
    assert data["count_in_bars"] == 2
    assert data["names"] == ["chorus", "intro", "verse"]
    assert "findings" in data


def test_a_section_posted_as_a_reaper_bar_is_stored_musical(served, song):
    """The whole point of the field being labelled Reaper: 22.3 read off the
    ruler is bar 20 beat 3 in the manifest, and the subtraction happens once,
    server-side."""
    from rambass.manifest import load_song

    base, _ = served
    _post(base, "/api/section", {"song": song.slug, "position": "22.3",
                                 "reaper": True, "name": "verse-2"})

    stored = load_song(song.directory).sections
    added = next(s for s in stored if s.name == "verse-2")
    assert (added.bar, added.beat) == (20, 3.0)


def test_replacing_a_section_over_http_keeps_its_backbeat(served, song):
    """The regression that matters most: renaming Manlio's verse-2 must not
    un-declare its side-sticks."""
    from rambass.manifest import Section, load_song, save_song

    base, _ = served
    song.sections = [Section(name="verse-2", bar=20, beat=3.0,
                             backbeat="sidestick")]
    save_song(song)

    _post(base, "/api/section", {"song": song.slug, "position": "22.3",
                                 "reaper": True, "name": "verse-two"})

    stored = load_song(song.directory).sections
    assert len(stored) == 1
    assert (stored[0].name, stored[0].backbeat) == ("verse-two", "sidestick")


def test_a_section_can_be_removed_over_http(served, song):
    from rambass.manifest import load_song

    base, _ = served
    left = _post(base, "/api/section/remove",
                 {"song": song.slug, "position": "11.1", "reaper": True})

    assert [row["name"] for row in left["sections"]] == ["intro", "chorus"]
    assert [s.name for s in load_song(song.directory).sections] == \
        ["intro", "chorus"]


def test_a_position_the_parser_cannot_read_is_a_400_naming_the_form(served, song):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/section", {"song": song.slug, "position": "bananas",
                                     "name": "x"})
    assert caught.value.code == 400
    assert "bar.beat" in json.loads(caught.value.read().decode("utf-8"))["error"]


def test_a_reaper_bar_inside_the_count_in_is_a_400(served, song):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/section", {"song": song.slug, "position": "2.1",
                                     "reaper": True, "name": "too-early"})
    assert caught.value.code == 400
    assert "count-in" in json.loads(caught.value.read().decode("utf-8"))["error"]


def test_editing_a_section_makes_consolidate_stale(served, song):
    """Paolo's parenthetical, pinned. `drums consolidate` carries `sections` in
    its provenance fields, so the edit the screen just made is what the Rebuild
    button on it will act on."""
    from rambass.provenance import stale_report, stamp

    base, _ = served
    source = song.drum_midi_path("quantized")
    target = song.drum_midi_path("consolidated")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"MThd-source")
    target.write_bytes(b"MThd")
    stamp(song, target, step="drums consolidate", inputs=[source])
    assert not [e for e in stale_report(song)
                if e.artifact == "midi/drums-consolidated.mid"
                and e.state == "stale"]

    _post(base, "/api/section", {"song": song.slug, "position": "24.1",
                                 "reaper": True, "name": "bridge"})

    # Reloaded from disk: the POST wrote song.yaml, and the in-memory manifest
    # this test started with still has the old section list.
    from rambass.manifest import load_song

    entry = next(e for e in stale_report(load_song(song.directory))
                 if e.artifact == "midi/drums-consolidated.mid")
    assert entry.state == "stale"
    assert any("sections" in reason for reason in entry.reasons), entry.reasons


def test_the_sections_screen_is_on_the_page():
    page = _page()

    assert "renderSections" in page, "no sections screen"
    assert "/api/section/remove" in page and "/api/section" in page
    assert "datalist" in page, "the name field does not offer the existing names"
    assert "reaper" in page.lower()


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


# ── the band layer: hearing either side in context ──────────────────────────
#
# Paolo: *"I would like to listen to the candidate drums in context (use the
# ref aligned time warped section on top of the candidate drums), and the
# reference in context too ... a switch that layers all the other instruments
# on top that uses the most appropriate version (ref aligned or original)"*,
# and *"need to keep playing when toggling other instruments on or off"*.
#
# So: four elements, all playing all the time, and every switch on this screen
# is a mute. That is already how `s` works between candidate and reference, and
# it is the only way a toggle can be instant -- a `play()` on an element that
# was paused starts at whatever `currentTime` it was left at, a frame or two
# late, which reads as a flam in a tool built to judge flams.


@pytest.fixture
def with_beds(reviewable, monkeypatch):
    """Both beds on disk, and ffmpeg replaced by a recorder of what was cut."""
    base, song = reviewable
    for relative in (("stems", "drums.wav"), ("qa", "candidate.wav"),
                     ("stems", "no_drums.wav"),
                     ("practice", "no_drums-aligned.wav")):
        path = song.path(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-really-audio", encoding="utf-8")

    from rambass import review as review_module

    cut: list[tuple] = []

    def fake_cut(source, target, *, start, duration):
        cut.append((Path(source).name, round(float(start), 3),
                    round(float(duration), 3)))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RIFF....WAVE")

    monkeypatch.setattr(review_module, "cut_clip", fake_cut)
    return base, song, cut


def test_the_review_payload_offers_a_bed_url_per_side(with_beds):
    base, song, _ = with_beds
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")

    assert verse["cand_band_url"].endswith("-cand-band.wav")
    assert verse["ref_band_url"].endswith("-ref-band.wav")
    assert data["sources"]["cand-band"]["available"] is True
    assert data["sources"]["ref-band"]["available"] is True


def test_each_bed_clip_is_cut_from_its_own_file_on_its_own_clock(with_beds):
    """The trap this pins: the warped bed is on the candidate's clock and the
    album mix is on the recording's. Cutting either one on the other clock
    gives a bed that drifts against the drums it is under -- which reads as a
    fault in the programmed part, and is the reason the file exists."""
    base, song, cut = with_beds
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")
    from rambass.align import load_align
    from rambass.review import clip_spans

    span = next(s for s in clip_spans(
        song, load_align(song.path("practice", "align.yaml")))
        if s.name == "verse")

    for url in (verse["cand_band_url"], verse["ref_band_url"]):
        with urllib.request.urlopen(base + url) as response:
            assert response.status == 200
    by_source = {name: (start, duration) for name, start, duration in cut}

    assert by_source["no_drums-aligned.wav"] == (
        round(span.candidate_start, 3), round(span.duration, 3))
    assert by_source["no_drums.wav"] == (
        round(span.reference_start, 3), round(span.reference_duration, 3))


def test_a_bed_that_has_not_been_warped_yet_is_409_naming_the_warp(reviewable):
    """409, not 404: the clip exists and its source does not. Same distinction
    the candidate bounce already makes, and the message is the one the CLI
    prints so the two cannot drift."""
    base, song = reviewable
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")

    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(base + verse["cand_band_url"])
    assert caught.value.code == 409
    assert "--warp" in json.loads(
        caught.value.read().decode("utf-8"))["error"]


def test_the_bed_is_cut_again_when_the_warp_is_rebuilt(with_beds):
    """`--lam` changes the warped file without moving anything else, so a bed
    clip cached from the previous algorithm would be the one thing on the
    screen still playing the old render."""
    base, song, cut = with_beds
    data = _get(base, f"/api/review/{song.slug}")
    verse = next(s for s in data["sections"] if s["name"] == "verse")

    with urllib.request.urlopen(base + verse["cand_band_url"]) as response:
        response.read()
    bed = song.path("practice", "no_drums-aligned.wav")
    os.utime(bed, (bed.stat().st_atime, bed.stat().st_mtime + 120))
    cut.clear()
    with urllib.request.urlopen(base + verse["cand_band_url"]) as response:
        response.read()

    assert [name for name, _, _ in cut] == ["no_drums-aligned.wav"]


def test_the_page_has_a_band_switch_and_a_key_for_it():
    """No browser here, so the wiring is pinned on the page's own text."""
    page = _page()

    assert 'id="band"' in page, "no switch for the band layer"
    assert 'event.key === "b"' in page, "no keyboard toggle for the band layer"
    assert "cand_band_url" in page and "ref_band_url" in page


def test_the_page_never_pauses_to_change_what_is_audible():
    """Every switch on this screen is a mute, and that has to stay true: the
    band elements are started by `play()` alongside the drums and the toggle
    only moves `muted`."""
    page = _page()
    body = page[page.index("  toggleBand()"):]
    body = body[:body.index("\n  }")]

    assert ".play()" not in body and ".pause()" not in body
    assert "applyMute" in body


TRANSPORT_PROBE = """
/* Enough DOM for the transport, which touches lane classes and the readouts.
   Nothing here is asserted on -- what is under test is the four elements. */
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const el = () => ({classList: {toggle: () => {}, add: () => {}, remove: () => {}},
                   style: {}, textContent: "", innerHTML: "", clientWidth: 300,
                   addEventListener: () => {}});
globalThis.document = {querySelector: () => el(), addEventListener: () => {}};
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  grid: {}, notes: [], instruments: ["crash"], sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c", ref_url: "r", cand_band_url: "cb", ref_band_url: "rb"}],
  sources: {cand: {available: true}, ref: {available: true},
            "cand-band": {available: true}, "ref-band": {available: true}}};
const view = new ReviewView("manlio", data);
const fake = () => ({muted: false, volume: 1, currentTime: 0, duration: 12,
                     plays: 0, pauses: 0,
                     play() { this.plays++; return Promise.resolve(); },
                     pause() { this.pauses++; },
                     addEventListener() {}});
view.cand = fake(); view.ref = fake();
view.candBand = fake(); view.refBand = fake();
const state = () => ({
  cand: view.cand.muted, ref: view.ref.muted,
  candBand: view.candBand.muted, refBand: view.refBand.muted,
  playing: view.playing,
  plays: [view.cand.plays, view.ref.plays, view.candBand.plays,
          view.refBand.plays],
  volumes: [view.cand.volume, view.ref.volume, view.candBand.volume,
            view.refBand.volume],
  pauses: [view.cand.pauses, view.ref.pauses, view.candBand.pauses,
           view.refBand.pauses],
  at: [view.cand.currentTime, view.candBand.currentTime,
       view.ref.currentTime, view.refBand.currentTime]});
const out = {};
view.applyMute();
out.drumsOnly = state();
view.play();
[view.cand, view.ref, view.candBand, view.refBand].forEach(
  (element) => { element.currentTime = 4.2; });
out.playing = state();
view.toggleBand();
out.bandOn = state();
view.switchSide();
out.switched = state();
view.toggleBand();
out.bandOff = state();
view.toggleBand();
/* Whatever else has happened to the elements, the bed's gain is re-asserted:
   `applyMute` sets it on every call, which is the difference between a setting
   that is honoured and one that was honoured once. */
[view.cand, view.ref, view.candBand, view.refBand].forEach(
  (element) => { element.volume = 1; });
view.applyMute();
out.reasserted = state();
[view.cand, view.ref, view.candBand, view.refBand].forEach(
  (element) => { element.currentTime = 7.5; });
view.pause();
view.restart();
out.restarted = state();
console.log(JSON.stringify(out));
"""


def test_the_band_layer_is_a_mute_and_never_a_restart(tmp_path):
    """The requirement in one test: toggling the layer must not stop, restart
    or move the audio. Everything plays from `play()`; the switch is `muted`.

    Exercised in node against the page's own ReviewView, the same way the
    geometry is -- there is no browser in this suite and the alternative is
    checking it by ear and writing nothing down."""
    out = _run_geometry_probe(tmp_path, TRANSPORT_PROBE)

    assert out["drumsOnly"] == {**out["drumsOnly"],
                                "candBand": True, "refBand": True}
    # All four start together, so an unmute is instant and in phase.
    assert out["playing"]["plays"] == [1, 1, 1, 1]
    assert out["bandOn"]["candBand"] is False and out["bandOn"]["refBand"] is True
    assert out["bandOn"]["playing"] is True
    assert out["bandOn"]["pauses"] == [0, 0, 0, 0]
    assert out["bandOn"]["plays"] == [1, 1, 1, 1], "the toggle restarted playback"
    assert out["bandOn"]["at"] == [4.2, 4.2, 4.2, 4.2], "the toggle moved the playhead"
    assert out["bandOff"]["candBand"] is True and out["bandOff"]["refBand"] is True
    assert out["bandOff"]["pauses"] == [0, 0, 0, 0]


def test_the_bed_follows_whichever_side_is_audible(tmp_path):
    """"The most appropriate version": pressing `s` with the layer on moves
    both the drums and the bed under them, so what is heard is always one
    take's drums with that take's own band."""
    out = _run_geometry_probe(tmp_path, TRANSPORT_PROBE)

    assert out["bandOn"]["cand"] is False and out["bandOn"]["candBand"] is False
    assert out["switched"]["ref"] is False and out["switched"]["refBand"] is False
    assert out["switched"]["cand"] is True and out["switched"]["candBand"] is True
    assert out["switched"]["pauses"] == [0, 0, 0, 0]


def test_shift_space_plays_the_section_from_its_start(tmp_path):
    """Paolo: *"add a Shift+space keyboard shortcut to start playing from the
    beginning of the section"*. Space toggles where you are; this one is the
    "again, from the top" that a review pass does over and over -- so it seeks
    every element, including the band beds, and plays whether or not it was
    already playing."""
    out = _run_geometry_probe(tmp_path, TRANSPORT_PROBE)

    assert out["restarted"]["at"] == [0, 0, 0, 0]
    assert out["restarted"]["playing"] is True
    # The side and the layer it was left on stay as they were: this is a
    # transport control, not a reset of what is audible. The probe leaves it on
    # the reference with the band switched back on, so that is what comes back.
    assert out["restarted"]["ref"] is False
    assert out["restarted"]["refBand"] is False
    assert out["restarted"]["candBand"] is True


def test_the_page_binds_shift_space_to_the_section_start():
    page = _page()
    space = page[page.index('event.key === " "'):]
    space = space[:space.index("\n")]

    assert "shiftKey" in space, "shift+space is not distinguished from space"
    assert "restart" in space
    assert "shift" in page.lower() and "from the top" in page


SIDEPICK_PROBE = """
/* Same four fake elements as the transport probe: what is under test here is
   that naming a side is a pin, not a toggle, and that it leaves the band
   layer exactly as the reviewer left it. */
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const el = () => ({classList: {toggle: () => {}, add: () => {}, remove: () => {}},
                   style: {}, textContent: "", innerHTML: "", clientWidth: 300,
                   addEventListener: () => {}});
globalThis.document = {querySelector: () => el(), addEventListener: () => {}};
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  grid: {}, notes: [], instruments: ["crash"], sections: [
    {name: "chorus-1", start_bar: 33, start_beat: 1, end_bar: 41, end_beat: 1,
     cand_url: "c", ref_url: "r", cand_band_url: "cb", ref_band_url: "rb"}],
  sources: {cand: {available: true}, ref: {available: true},
            "cand-band": {available: true}, "ref-band": {available: true}}};
const view = new ReviewView("manlio", data);
const fake = () => ({muted: false, volume: 1, currentTime: 0, duration: 12,
                     plays: 0, pauses: 0,
                     play() { this.plays++; return Promise.resolve(); },
                     pause() { this.pauses++; },
                     addEventListener() {}});
view.cand = fake(); view.ref = fake();
view.candBand = fake(); view.refBand = fake();
const state = () => ({
  active: view.active, band: view.band,
  cand: view.cand.muted, ref: view.ref.muted,
  candBand: view.candBand.muted, refBand: view.refBand.muted,
  playing: view.playing,
  plays: [view.cand.plays, view.ref.plays, view.candBand.plays,
          view.refBand.plays],
  pauses: [view.cand.pauses, view.ref.pauses, view.candBand.pauses,
           view.refBand.pauses],
  at: [view.cand.currentTime, view.candBand.currentTime,
       view.ref.currentTime, view.refBand.currentTime]});
const out = {};
view.applyMute();
view.play();
view.toggleBand();
[view.cand, view.ref, view.candBand, view.refBand].forEach(
  (element) => { element.currentTime = 4.2; });
out.onCand = state();
view.selectSide("ref");
out.pickedRef = state();
view.selectSide("ref");
out.pickedRefAgain = state();
view.selectSide("cand");
out.pickedCand = state();
view.selectSide("nonsense");
out.pickedNonsense = state();
console.log(JSON.stringify(out));
"""


def test_naming_a_side_pins_playback_to_it_rather_than_toggling(tmp_path):
    """Paolo: *"the bold candidate or reference should be clickable ... pinning
    it to the targeted audio for what I have clicked"*. So it is `selectSide`,
    not another `switchSide`: clicking the label of the side already audible
    has to leave it audible, where a toggle would swap it out from under the
    ear -- the one thing a click on a *name* must never do."""
    out = _run_geometry_probe(tmp_path, SIDEPICK_PROBE)

    assert out["onCand"]["active"] == "cand"
    assert out["pickedRef"]["active"] == "ref"
    assert out["pickedRef"]["ref"] is False and out["pickedRef"]["cand"] is True
    # Clicked again: still the reference. A toggle would be back on candidate.
    assert out["pickedRefAgain"]["active"] == "ref"
    assert out["pickedRefAgain"]["ref"] is False
    assert out["pickedCand"]["active"] == "cand"
    # A side that is neither of the two is ignored, rather than muting both.
    assert out["pickedNonsense"]["active"] == "cand"
    assert out["pickedNonsense"]["cand"] is False


def test_picking_a_side_keeps_the_band_layer_and_never_restarts(tmp_path):
    """*"preserving band on/off setting"* -- and the standing rule for every
    switch on this screen: it is a mute on elements that are all already
    playing, so the position and the layer come through untouched and the bed
    follows whichever side is now audible."""
    out = _run_geometry_probe(tmp_path, SIDEPICK_PROBE)

    for step in ("pickedRef", "pickedRefAgain", "pickedCand"):
        assert out[step]["band"] is True, "the pick lost the band layer"
        assert out[step]["playing"] is True
        assert out[step]["pauses"] == [0, 0, 0, 0], "the pick stopped the audio"
        assert out[step]["plays"] == [1, 1, 1, 1], "the pick restarted the audio"
        assert out[step]["at"] == [4.2, 4.2, 4.2, 4.2], "the pick moved the playhead"
    # The bed under whichever side is audible, and only that one.
    assert out["pickedRef"]["refBand"] is False
    assert out["pickedRef"]["candBand"] is True
    assert out["pickedCand"]["candBand"] is False
    assert out["pickedCand"]["refBand"] is True


def test_the_lane_labels_are_clickable_and_say_so():
    """No browser here, so the wiring is pinned on the page's own text: each
    lane's bold name carries the side it pins to, and one delegated click
    handler on the stack turns it into `selectSide`."""
    page = _page()

    assert 'data-side="cand"' in page and 'data-side="ref"' in page
    assert "selectSide" in page
    # Delegated on the stack, because the lanes are inside it and a click on a
    # label must not go through `scrubFrom` (which wants a canvas) at all.
    click = page[page.index('stack.addEventListener("click"'):]
    click = click[:click.index("\n")]
    assert "pickSide" in click
    pick = page[page.index("  pickSide(event)"):]
    pick = pick[:pick.index("\n  }")]
    assert "b[data-side]" in pick and "selectSide" in pick
    # And `s` stays a toggle expressed in terms of the same pin.
    switch = page[page.index("  switchSide()"):]
    switch = switch[:switch.index("\n  }")]
    assert "selectSide" in switch


# ── zoom: the same stack, a window of it ────────────────────────────────────
#
# Paolo: *"add the ability to zoom in and out so that I can see part of the
# section more clearly (the waveforms stretch like they do in reaper by
# rotating the mouse wheel)"*. Zoom is a *view* window in section fractions,
# and every drawer and every pointer handler goes through the same two
# conversions -- `viewX` out, `sectionAt` back. That is the whole risk: a
# drawer that keeps multiplying by the full width puts its lines somewhere the
# waveform is not, which is a silent misalignment of exactly the kind this
# screen exists to catch.

ZOOM_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2,
  title: "Manlio", grid: {}, notes: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c1", ref_url: "r1"}]};
const view = new ReviewView("manlio", data);
view.index = 0;
const r = (x) => Math.round(x * 1e6) / 1e6;
const win = () => ({zoom: r(view.zoom), left: r(view.left), span: r(view.span())});
const out = {};

out.fitted = win();
// Fitted is as far out as it goes: the section is the unit of review, and a
// window wider than it would be empty space pretending to be music.
out.zoomOutRefused = view.zoomBy(0.5, 0.5);
out.stillFitted = win();

view.zoomBy(4, 0.5);
out.aboutMiddle = win();
out.anchorHeld = r(view.sectionAt(0.5));
out.roundTrip = r(view.sectionAt(view.viewX(0.42)));
// x of the window's own edges, which is what every drawer multiplies by.
out.edges = [r(view.viewX(view.left)), r(view.viewX(view.left + view.span()))];

view.fit();
view.zoomBy(4, 0);
out.atLeftEdge = win();
view.panBy(-5);
out.pannedOffTheFront = win();
view.panBy(50);
out.pannedOffTheEnd = win();
out.zoomedOutFromTheEnd = (view.zoomBy(0.25, 1), win());

view.fit();
for (let i = 0; i < 60; i++) view.zoomBy(1.35, 0.5);
out.ceiling = r(view.zoom);

view.fit();
out.followFitted = view.followTo(0.9);
view.zoomBy(4, 0);
out.followInside = view.followTo(0.1);
out.followPast = view.followTo(0.30);
out.afterFollow = win();
out.followBack = view.followTo(0);
out.afterLoopRestart = win();

view.fit();
out.perBeatFit = r(view.perBeatPixels(1000));
view.zoomBy(4, 0.5);
out.perBeatZoomed = r(view.perBeatPixels(1000));

/* The waveform itself, not just the grid: at zoom 4 the peaks have to be
   re-measured over the visible quarter, or the lanes stay 600 buckets of the
   whole section stretched -- four times wider and no more detail, which is
   the one thing "stretch like Reaper" is not. */
const samples = new Float32Array(4000);
for (let i = 3000; i < 3016; i++) samples[i] = 1;
const buffer = {getChannelData: () => samples};
out.peakWhole = r(Math.max(...peaksOf(buffer)));
out.peakFirstHalf = r(Math.max(...peaksOf(buffer, 0, 0.5)));
out.peakSecondHalf = r(Math.max(...peaksOf(buffer, 0.5, 1)));
out.peakBuckets = peaksOf(buffer, 0.5, 1).length;

const entry = {buffer: buffer, peaks: null, failed: false};
view.fit();
const first = view.windowPeaks(entry);
out.peaksCached = view.windowPeaks(entry) === first;
view.zoomBy(4, 0.5);
out.peaksRemeasured = view.windowPeaks(entry) !== first;
console.log(JSON.stringify(out));
"""


def test_the_stack_zooms_about_the_pointer(tmp_path):
    """Reaper zooms toward the cursor, and so does this: the musical position
    under the pointer is the one thing that must not move, or the gesture
    hunts. `viewX` and `sectionAt` are inverses, which is what lets a click on
    a zoomed lane still mean the bar it is drawn over."""
    out = _run_geometry_probe(tmp_path, ZOOM_PROBE)

    assert out["fitted"] == {"zoom": 1, "left": 0, "span": 1}
    assert out["zoomOutRefused"] is False, "the view zoomed out past the section"
    assert out["stillFitted"] == {"zoom": 1, "left": 0, "span": 1}
    # 4x about the middle: a quarter of the section, centred where it was.
    assert out["aboutMiddle"] == {"zoom": 4, "left": 0.375, "span": 0.25}
    assert out["anchorHeld"] == 0.5, "the position under the pointer moved"
    assert out["roundTrip"] == 0.42, "viewX and sectionAt are not inverses"
    assert out["edges"] == [0, 1]
    assert out["ceiling"] == 64, "zoom has no ceiling"


def test_a_zoomed_window_never_leaves_the_section(tmp_path):
    """A window off the front or the end of the section is blank canvas with a
    ruler over it -- and the peaks, the grid and the playhead would all be
    drawn for audio that is not there."""
    out = _run_geometry_probe(tmp_path, ZOOM_PROBE)

    assert out["atLeftEdge"] == {"zoom": 4, "left": 0, "span": 0.25}
    assert out["pannedOffTheFront"] == {"zoom": 4, "left": 0, "span": 0.25}
    assert out["pannedOffTheEnd"] == {"zoom": 4, "left": 0.75, "span": 0.25}
    # Zooming out from a window parked at the end lands back on the whole
    # section rather than on a window running past its end.
    assert out["zoomedOutFromTheEnd"] == {"zoom": 1, "left": 0, "span": 1}


def test_the_zoomed_view_follows_the_playhead(tmp_path):
    """A zoomed window that stays put while the section loops shows a quarter
    of the music and the ear hears all of it -- so the eye is looking at the
    wrong bar for most of every pass. It pages when the playhead leaves, and
    the loop's jump back to the top pages back with it."""
    out = _run_geometry_probe(tmp_path, ZOOM_PROBE)

    assert out["followFitted"] is False, "a fitted view has nothing to follow"
    assert out["followInside"] is False, "the view moved for a playhead in view"
    assert out["followPast"] is True
    assert out["afterFollow"]["left"] == 0.2625, "the playhead is not in view"
    assert out["followBack"] is True
    assert out["afterLoopRestart"]["left"] == 0


def test_zoom_stretches_the_grid_and_re_measures_the_waveform(tmp_path):
    """Both halves of "like Reaper": the beat grid gets more room per beat, so
    the dropped subdivision lines come back; and the peaks are measured again
    over the visible window, so a zoomed lane shows detail rather than the
    same 600 buckets four times wider."""
    out = _run_geometry_probe(tmp_path, ZOOM_PROBE)

    # verse-2 is 32 beats: 31.25px a beat fitted, 125 at 4x.
    assert out["perBeatFit"] == 31.25
    assert out["perBeatZoomed"] == 125

    assert out["peakWhole"] == 1
    assert out["peakFirstHalf"] == 0, "a window read samples outside itself"
    assert out["peakSecondHalf"] == 1, "the window missed its own transient"
    assert out["peakBuckets"] == 600, "a window is not a full set of buckets"
    assert out["peaksCached"] is True, "the peaks are re-measured every repaint"
    assert out["peaksRemeasured"] is True, "a zoom did not re-measure the peaks"


def test_a_click_on_a_zoomed_lane_lands_where_the_pointer_is():
    """The pointer handlers are the other half of the conversion. A click that
    kept treating canvas-x as section-x would file a note at the wrong bar --
    silently, and into `song.yaml` via promote."""
    page = _page()

    for handler in ("scrubFrom(event, start)", "openMenu(event)"):
        body = page[page.index("  " + handler):]
        body = body[:body.index("\n  }")]
        assert "sectionAt" in body, f"{handler} ignores the zoom window"


def test_the_page_zooms_on_the_wheel_and_says_so():
    """No browser here, so the wiring is pinned on the page's own text."""
    page = _page()

    wheel = page[page.index('addEventListener("wheel"'):]
    wheel = wheel[:wheel.index("\n")]
    assert "passive: false" in wheel or "passive:false" in wheel, (
        "a passive wheel listener cannot stop the page scrolling")
    body = page[page.index("  onWheel(event)"):]
    body = body[:body.index("\n  }")]
    assert "preventDefault" in body, "the wheel still scrolls the page"
    assert "shiftKey" in body, "no shift+wheel pan"
    assert "zoomBy" in body and "panBy" in body

    assert 'id="zoom"' in page, "nothing on screen says how far in it is"
    assert 'event.key === "0"' in page, "no key to fit the section again"
    # A new section starts fitted: stepping with n/p is "listen to this one
    # whole", and inheriting a 16x window from the last one hides it.
    show = page[page.index("  show() {"):]
    show = show[:show.index("\n  }")]
    assert "this.fit()" in show, "a new section inherits the old zoom"


# ── the lanes' vertical scale: seeing a ghost note ──────────────────────────
#
# Paolo: *"the waveforms in the canvas lane do not occupy the whole height, is
# it possible to maximise the waveform inside the canvas so that I can visually
# see even the fainter hits (eg low close hihats)"*, and *"this is only visual,
# no change to velocity or midi or wavs"*.
#
# Two separate problems in that one sentence. The lane wastes its height
# because the scale is absolute (a clip peaking at 0.4 uses 40% of it), and a
# closed hat is invisible because the scale is *linear* -- at 30 dB under the
# kick it is 3% of the height whatever the gain. So: a shared gain fills the
# canvas, and a dB scale lifts the quiet hits. Nothing here reads or writes a
# velocity, a note or a sample.

WAVE_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2,
  title: "Manlio", grid: {}, notes: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c1", ref_url: "r1"}]};
const view = new ReviewView("manlio", data);
view.index = 0;
const r = (x) => Math.round(x * 1000) / 1000;
const out = {};

out.defaultScale = view.waveScale;

/* One gain for both lanes, from the loudest thing visible on either. The
   stacked lanes exist so that "this snare is quieter in the candidate than in
   the reference" is a visible fact (docs/review-ui.md); a per-lane gain would
   draw the two at the same height and delete exactly that. */
view.peakCache = {
  c1: {peaks: Float32Array.from([0.10, 0.40, 0.02]), buffer: null, failed: false},
  r1: {peaks: Float32Array.from([0.20, 0.80, 0.05]), buffer: null, failed: false}};
out.sharedPeak = r(view.sharedPeak());

const at = (peak, reference) => r(view.waveFraction(peak, reference));
view.waveScale = "raw";
out.raw = [at(0.4, 0.8), at(0.8, 0.8), at(1.4, 0.8)];
view.waveScale = "fit";
out.fit = [at(0.8, 0.8), at(0.4, 0.8), at(0.08, 0.8)];
view.waveScale = "boost";
out.boostTop = at(0.8, 0.8);
// 24 dB under the loudest thing on screen is half the lane's height, where a
// linear scale gives it 6% -- which is the closed hat Paolo cannot see.
out.boostMinus24 = at(0.0630957, 1);
out.boostMinus6 = at(0.501187, 1);
out.boostFloor = at(0.001, 1);
// Ordering survives the dB scale even though the ratio does not: the quieter
// take is still drawn shorter at the same bar.
out.boostOrdered = at(0.4, 0.8) < at(0.8, 0.8);

/* A silent gap must not be amplified into a full-height lane: gain is capped,
   so a window whose loudest sample is -60 dB stays flat. */
view.waveScale = "fit";
out.silentWindow = at(0.001, 0.001);
view.waveScale = "boost";
out.silentBoost = at(0.001, 0.001);
/* The gate is absolute, so it cannot swallow a real ghost note: a hit 40 dB
   under a full-scale kick is still an order of magnitude above it. */
out.justAboveGate = at(0.009, 0.9) > 0;
out.justBelowGate = at(0.002, 0.9);

view.waveScale = "boost";
out.cycle = [];
for (let i = 0; i < 4; i++) { view.cycleWave(); out.cycle.push(view.waveScale); }
console.log(JSON.stringify(out));
"""


def test_the_lane_fills_its_height_from_one_shared_gain(tmp_path):
    """"Maximise the waveform inside the canvas": the loudest thing visible
    reaches the top, so a clip that peaks at 0.4 stops using 40% of the lane.
    The gain is shared between the two lanes on purpose -- a per-lane gain
    would draw a quiet candidate at the same height as a loud reference and
    delete the level comparison the stack was built for."""
    out = _run_geometry_probe(tmp_path, WAVE_PROBE)

    assert out["sharedPeak"] == 0.8, "the gain is not taken from both lanes"
    # Absolute, as it always was: 0.4 draws at 0.4 of the lane.
    assert out["raw"] == [0.4, 0.8, 1.0], "raw is no longer absolute"
    # Normalised: the loudest fills it, everything else keeps its ratio.
    assert out["fit"] == [1.0, 0.5, 0.1]


def test_a_quiet_hit_is_visible_on_the_lane(tmp_path):
    """The other half: a closed hat 24 dB under the kick is 6% of the height
    on any linear scale, gain or no gain. On the dB scale it is half."""
    out = _run_geometry_probe(tmp_path, WAVE_PROBE)

    assert out["defaultScale"] == "boost", "the lanes still open on a linear scale"
    assert out["boostTop"] == 1.0
    assert out["boostMinus24"] == 0.5
    assert out["boostMinus6"] == 0.875
    assert out["boostFloor"] == 0.0, "below the floor is not clamped to zero"
    assert out["boostOrdered"] is True, "the quieter take is not drawn shorter"


def test_a_silent_window_is_not_amplified_into_a_waveform(tmp_path):
    """Manlio's bars 78-79 are digital silence and every section has gaps in
    it. An uncapped gain turns the noise floor there into a full-height lane,
    which is a waveform for audio that is not playing."""
    out = _run_geometry_probe(tmp_path, WAVE_PROBE)

    assert out["silentWindow"] == 0.02
    assert out["silentBoost"] == 0.0
    # And the gate that does it is absolute, well under any real hit: a ghost
    # note 40 dB below a full-scale kick still draws.
    assert out["justAboveGate"] is True
    assert out["justBelowGate"] == 0.0


def test_the_lane_scale_cycles_and_says_which_one_it_is(tmp_path):
    out = _run_geometry_probe(tmp_path, WAVE_PROBE)

    assert out["cycle"] == ["fit", "raw", "boost", "fit"]


def test_the_page_draws_the_lanes_through_the_shared_scale():
    """No browser here, so the wiring is pinned on the page's own text."""
    page = _page()

    lane = page[page.index("  paintLane(side) {"):]
    lane = lane[:lane.index("\n  }")]
    assert "waveFraction" in lane, "the lane still draws the raw peak"
    assert "peak * (height - 8)" not in lane, "the absolute scale is back"
    # The gain is computed once for the stack, not per lane.
    repaint = page[page.index("  repaint() {"):]
    repaint = repaint[:repaint.index("\n  }")]
    assert "sharedPeak" in repaint, "each lane works out its own gain"

    assert 'id="wave"' in page, "nothing on screen says which scale is on"
    assert 'event.key === "w"' in page, "no key for the lane scale"
    # A display preference, unlike zoom: it survives stepping to a new section,
    # because "show me the ghost notes" is how somebody is working, not a
    # property of the section they happen to be on.
    show = page[page.index("  show() {"):]
    show = show[:show.index("\n  }")]
    assert "waveScale" not in show, "a new section resets the lane scale"


# ── swap-hit: the note that says "right place, wrong drum" ───────────────────


def test_a_swap_posted_from_the_browser_keeps_both_instruments(reviewable):
    base, song = reviewable
    from rambass.review import load_review

    data = _post(base, "/api/note", {
        "song": song.slug, "bar": 9, "beat": 3.0, "kind": "swap-hit",
        "instrument": "hihat_open", "swap_to": "crash",
        "comment": "section start"})

    assert data["notes"][0]["swap_to"] == "crash"
    _, notes = load_review(song.path("qa", "review.yaml"))
    assert notes[0].instrument == "hihat_open" and notes[0].swap_to == "crash"


def test_a_swap_promoted_over_http_writes_both_edits(reviewable):
    base, song = reviewable
    from rambass.manifest import load_song

    _post(base, "/api/note", {
        "song": song.slug, "bar": 9, "beat": 3.0, "kind": "swap-hit",
        "instrument": "hihat_open", "swap_to": "crash"})
    _post(base, "/api/promote", {"song": song.slug})

    saved = load_song(song.directory)
    assert [r.instrument for r in saved.drum_removals] == ["hihat_open"]
    assert [a.instrument for a in saved.drum_additions] == ["crash"]


def test_the_page_offers_the_swap_and_asks_what_to_swap_it_for():
    """A swap needs two instruments, so the panel has to grow a second select
    -- and it only makes sense for this kind, which is why it is revealed
    rather than always shown."""
    page = _page()

    assert "swap-hit" in page, "the swap kind is not offered on the screen"
    assert 'id="swap-to"' in page, "nothing on the page names the new drum"
    assert "swap_to" in page, "the swap target never reaches the server"


def test_the_waveform_lanes_are_tall_enough_to_show_a_ghost_note():
    """Paolo, using it: *"make the lane canvases twice as tall (200 instead of
    100) so I can better see the waveforms, even the fainter hits"*. 52 CSS px
    was 104 device pixels at DPR 2, which is the 100 he read off the screen;
    104 CSS px is the 200 he asked for. Pinned because it is the kind of number
    a later tidy-up rounds back to something "sensible"."""
    page = _page()
    lane = page[page.index(".lane canvas {"):]

    assert "height:104px" in lane[:lane.index("}")]


# ── the grid's right-click menu: file the edit where you heard it ────────────
#
# Paolo: *"if the playhead is for example at 16.2 and the hihat_closed grid lane
# is selected I should be able to right-click with the mouse in that specific
# hit and a small contextual popup menu should appear with add "xxx" here [go],
# remove, or swap with "xxx" [go]. this is the same as using the notes section
# below, but quicker and in context."*
#
# So it files the same three notes the panel files -- nothing here writes to
# song.yaml, `promote` still does that -- but the row names the instrument and
# the click names the position, which is the whole saving. What the menu offers
# depends on whether there is a hit under the pointer, and that decision is
# `menuModel`: pure, so node can check it, which is the only way to be sure the
# menu never offers "remove" where there is nothing to remove.

MENU_PROBE = """
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const el = () => ({classList: {toggle: () => {}, add: () => {}, remove: () => {}},
                   style: {}, textContent: "", innerHTML: "", clientWidth: 300,
                   addEventListener: () => {}, value: "",
                   /* `cycleSnap` re-renders the sidebar -- the window
                      `focusNotes` reads is half a step of this grid --
                      and that binds a handler per row, so the stub
                      answers like a node with no children rather than
                      like a node with no API. */
                   querySelectorAll: () => []});
globalThis.document = {querySelector: () => el(), addEventListener: () => {}};
/* Manlio's verse-2: starts at bar 20 beat 3, subdivision 3 (a shuffle), two
   bars of count-in -- so musical bar 21 is Reaper's 23. */
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  notes: [], instruments: ["kick", "hihat_closed", "hihat_open", "crash"],
  sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c", ref_url: "r", cand_band_url: "cb", ref_band_url: "rb"}],
  grid: {"verse-2": [
    {instrument: "hihat_closed", ticks: [
      {bar: 21, beat: 1, velocity: 80}, {bar: 21, beat: 2.333, velocity: 70}]},
    {instrument: "kick", ticks: [{bar: 21, beat: 1, velocity: 100}]}]}};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(data.sections[0]);
const out = {};
/* Beats through the section: bar 21 beat 1 is 2 beats in, beat 2 is 3. */
out.onHit = view.menuModel(0, 2 / 32);
out.betweenHits = view.menuModel(0, 3 / 32);
out.otherRow = view.menuModel(1, 2 / 32);
out.shuffled = view.menuModel(0, 3.333 / 32);
console.log(JSON.stringify(out));
"""


def test_the_grid_menu_offers_what_the_position_actually_allows(tmp_path):
    """Under a hit: remove it, or swap it. On an empty grid line: add one. The
    menu is built from the same `ticks` the row is drawn from, so what it
    offers and what the eye sees cannot disagree."""
    out = _run_geometry_probe(tmp_path, MENU_PROBE)

    def kinds(model):
        return {item["kind"]: item["enabled"] for item in model["items"]}

    assert out["onHit"]["instrument"] == "hihat_closed"
    assert out["onHit"]["hit"]["velocity"] == 80
    assert kinds(out["onHit"]) == {"missing-hit": False, "extra-hit": True,
                                   "swap-hit": True}

    assert out["betweenHits"]["hit"] is None
    assert kinds(out["betweenHits"]) == {"missing-hit": True, "extra-hit": False,
                                         "swap-hit": False}


def test_the_grid_menu_is_about_the_row_it_was_opened_on(tmp_path):
    """One position, two rows, two different answers -- the row is what names
    the instrument, which is the whole reason this is quicker than the panel."""
    out = _run_geometry_probe(tmp_path, MENU_PROBE)

    assert out["otherRow"]["instrument"] == "kick"
    assert out["otherRow"]["hit"]["velocity"] == 100


def test_the_grid_menu_speaks_reaper_numbers_and_the_songs_own_grid(tmp_path):
    """Every bar number a human reads on this screen goes through `reaperAt`,
    and the position snaps to `drums.subdivision` like every other click --
    a menu that filed at an unsnapped beat would put the note between two
    triplet lines, where no hit can be."""
    out = _run_geometry_probe(tmp_path, MENU_PROBE)

    assert out["onHit"]["reaper"] == "23.1"
    assert out["betweenHits"]["reaper"] == "23.2"
    assert out["onHit"]["bar"] == 21 and out["onHit"]["beat"] == 1
    # The shuffle's second triplet, kept as thirds rather than rounded to a
    # half or a decimal that `promote` would file off the grid.
    assert out["shuffled"]["hit"]["velocity"] == 70
    assert round(out["shuffled"]["beat"], 3) == 2.333


def test_the_grid_menu_files_the_same_notes_the_panel_does():
    """Not a second write path: it posts to /api/note like everything else, so
    a menu edit lands in the ledger, shows as a chip, and waits for `promote`
    exactly as a typed note does."""
    page = _page()
    menu = page[page.index("  menuModel("):page.index("  renderChips()")]

    assert "missing-hit" in menu and "extra-hit" in menu and "swap-hit" in menu
    assert "/api/note" in page
    # The hit's own velocity rides along on every kind that names one: a
    # promoted extra-hit can retract a declared hit, and `review demote` can
    # only restore that declaration from what the note carries.
    file_from_menu = page[page.index("  async fileFromMenu("):]
    file_from_menu = file_from_menu[:file_from_menu.index("\n  }")]
    assert "velocity: model.hit ? model.hit.velocity : 0" in file_from_menu


def test_the_grid_takes_a_right_click_and_the_page_does_not():
    page = _page()

    assert '"contextmenu"' in page, "nothing opens a menu on right-click"
    assert 'id="gridmenu"' in page, "no menu element"
    assert "closeMenu" in page
    # The swap needs somewhere to say what to swap for, and a go button, so a
    # mis-click cannot file a swap to whatever happened to be first in a list.
    assert 'id="menu-swap-to"' in page


def test_a_right_click_does_not_move_the_playhead(tmp_path):
    """Scrubbing is button 0. Right-clicking to file a note while a section is
    looping would otherwise seek the audio out from under the ear -- the menu
    is about where the pointer is, and says so in its own header."""
    page = _page()
    scrub = page[page.index("  scrubFrom(event, start) {"):]
    scrub = scrub[:scrub.index("\n  }")]

    assert "event.button" in scrub


def test_the_readouts_do_not_move_their_row_as_they_change():
    """Paolo: *"the [playhead readout] is by nature of variable width ... this
    makes the whole transport section shift left and right slightly and looks
    jittery"*. The transport row is centre-justified, so a readout that grows a
    character pushes half of it left and half right -- and that one is rewritten
    on every animation frame while playing. Fixed widths, not a re-layout.

    The same holds one row down in the settings block, where the five that moved
    out of the transport now live: a label on the left and a control pushed
    right by `margin-left:auto`, so a control that grows a character moves its
    own left edge under the pointer that is still clicking it."""
    page = _page()
    rules = page[page.index(".transport {"):page.index("/* the sections editor */")]

    for readout in ("#at", "#which"):
        assert f".transport {readout} {{" in rules, f"{readout} can still resize"
        rule = rules[rules.index(f".transport {readout} {{"):]
        assert "width:" in rule[:rule.index("}")], f"{readout} has no fixed width"
    for readout in ("#loopstate", "#band", "#zoom", "#wave", "#snap"):
        assert f".settings {readout} {{" in rules, f"{readout} can still resize"
        rule = rules[rules.index(f".settings {readout} {{"):]
        assert "width:" in rule[:rule.index("}")], f"{readout} has no fixed width"


# ── done marks and demote, over HTTP and on the screen ──────────────────────


def test_the_review_payload_says_which_sections_are_done(reviewable):
    base, song = reviewable

    before = _get(base, f"/api/review/{song.slug}")["sections"]
    assert [s["done"] for s in before] == [False] * len(before)

    verse = next(s for s in before if s["name"] == "verse")
    _post(base, "/api/section/done", {
        "song": song.slug, "bar": verse["start_bar"],
        "beat": verse["start_beat"], "done": True})
    after = _get(base, f"/api/review/{song.slug}")["sections"]

    assert {s["name"]: s["done"] for s in after} == {
        "intro": False, "verse": True, "chorus": False}


def test_a_section_can_be_reopened_over_http(reviewable):
    base, song = reviewable
    for state in (True, False):
        data = _post(base, "/api/section/done", {
            "song": song.slug, "bar": 9, "beat": 1.0, "done": state})
    assert [s["done"] for s in data["sections"]] == [False, False, False]


def test_the_done_response_carries_the_whole_list_back(reviewable):
    """So the screen redraws its progress strip from the server's answer rather
    than from what it guessed it had just done."""
    base, song = reviewable
    data = _post(base, "/api/section/done", {
        "song": song.slug, "bar": 9, "beat": 1.0, "done": True})

    assert [s["name"] for s in data["sections"]] == ["intro", "verse", "chorus"]
    assert [s["done"] for s in data["sections"]] == [False, True, False]


def test_demoting_over_http_takes_the_edit_out_of_the_manifest(reviewable):
    base, song = reviewable
    from rambass.manifest import load_song

    _post(base, "/api/note", {"song": song.slug, "bar": 9, "beat": 3.0,
                              "kind": "missing-hit", "instrument": "crash"})
    _post(base, "/api/promote", {"song": song.slug})
    assert load_song(song.directory).drum_additions

    notes = _get(base, f"/api/review/{song.slug}")["notes"]
    data = _post(base, "/api/note/demote",
                 {"song": song.slug, "key": notes[0]["key"]})

    assert data["notes"][0]["status"] == "open"
    assert not load_song(song.directory).drum_additions


def test_demoting_something_that_was_not_promoted_is_a_400(reviewable):
    base, song = reviewable
    _post(base, "/api/note", {"song": song.slug, "bar": 9, "kind": "timing",
                              "comment": "late"})
    notes = _get(base, f"/api/review/{song.slug}")["notes"]

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post(base, "/api/note/demote",
              {"song": song.slug, "key": notes[0]["key"]})
    assert caught.value.code == 400


def test_the_screen_marks_a_section_done_and_shows_which_are():
    """Paolo: *"a clear visual cue on the status"*, and *"so that when I reopen
    the project I know I can skip it"* -- which is a question about the whole
    song, not about the section you happen to be on. So: a control on the
    header, a badge on the section you are looking at, and a dot per section
    that doubles as the way to jump to one."""
    page = _page()

    assert 'id="done"' in page, "no control to mark the section done"
    assert 'id="progress"' in page, "no per-section progress strip"
    assert "/api/section/done" in page
    assert ".dot.done" in page or ".pip.done" in page, "done has no colour of its own"


def test_shift_d_marks_done_and_plain_d_still_leaves_the_screen():
    page = _page()

    assert 'event.key === "D"' in page, "shift+D does not mark the section done"
    keys = page[page.index('if (event.key === "d")'):]
    assert "location.hash" in keys[:keys.index("\n")], "d stopped going home"


def test_a_promoted_chip_offers_a_demote_and_nothing_else():
    """It used to offer nothing at all, and `remove_note` told you to go and
    edit song.yaml. Dismiss and delete still make no sense for a promoted note
    -- its edit is live -- but taking the edit back does."""
    page = _page()
    chips = page[page.index("  renderChips() {"):]
    chips = chips[:chips.index("\n  }")]

    assert "demote" in chips
    assert 'data-demote' in chips


# ── the orphaned player: a rebuilt screen left its audio running ────────────

STOP_PROBE = """
globalThis.window._review = {
  paused: false, closed: false,
  pause() { this.paused = true; }, closeMenu() { this.closed = true; }};
const before = window._review;
stopReview();
console.log(JSON.stringify({paused: before.paused, closed: before.closed,
                            cleared: window._review === null}));
"""


def test_leaving_a_review_screen_stops_the_audio_it_started(tmp_path):
    """Paolo: *"band keeps playing when I stop playing candidate/reference
    tracks"*.

    `renderReview` builds a **new** ReviewView on every rebuild and re-render,
    and the router builds one per navigation — while the four audio elements
    belong to the old instance and are not in the DOM, so nothing ever stopped
    them. They went on playing under the new screen, answering to no transport:
    the new view's space bar stops the new view's elements and the orphan plays
    on. It was true of the candidate and the reference from the start; the band
    layer is only the half you cannot miss."""
    out = _run_geometry_probe(tmp_path, STOP_PROBE)

    assert out == {"paused": True, "closed": True, "cleared": True}


def test_every_way_into_the_review_screen_stops_the_previous_one():
    """Both doors, because the leak was through the one nobody thinks about:
    not navigation, but a rebuild refreshing the screen you are already on."""
    page = _page()
    route = page[page.index("function route() {"):]
    render = page[page.index("async function renderReview(slug"):]

    assert "stopReview();" in route[:route.index("\n}")]
    assert "stopReview();" in render[:render.index("app.innerHTML")]


# ── one button for the sequence, and a finer snap ───────────────────────────


def test_promote_rebuild_render_over_http_is_one_call(reviewable, monkeypatch):
    from rambass import review as review_module
    from rambass.manifest import load_song

    ran: list[str] = []
    monkeypatch.setattr(
        review_module, "subprocess_runner",
        lambda cwd: lambda command: (ran.append(command), (0, "ok"))[1])
    base, song = reviewable
    _post(base, "/api/note", {"song": song.slug, "bar": 9, "beat": 3.0,
                              "kind": "missing-hit", "instrument": "crash"})
    result = _post(base, "/api/promote-render", {"song": song.slug})

    assert result["promoted"] == 1
    assert load_song(song.directory).drum_additions
    assert ran[-1] == f"rambass review render {song.slug}"
    assert result["notes"][0]["status"] == "promoted"


def test_the_screen_has_one_control_for_the_whole_sequence():
    """Paolo: *"three actions that are always in sequence ... one button that
    does everything and reloads the page on the same section"*."""
    page = _page()

    assert 'id="promote-render"' in page
    assert "/api/promote-render" in page
    assert 'event.key === "R"' in page, "no key for the sequence"


def test_a_refreshed_review_screen_comes_back_to_the_same_section():
    """It used to land on section 1 after every rebuild, so the loop was
    "rebuild, then step back to where I was listening" every single time."""
    page = _page()
    render = page[page.index("async function renderReview(slug"):]
    header = render[:render.index("\n")]

    assert "," in header, "renderReview cannot be told which section to open"
    # By name, which is also what the address carries -- so a refresh from the
    # screen and a refresh from the browser land the same way.
    assert "renderReview(this.slug, (this.section || {}).name)" in page


SNAP_PROBE = """
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const el = () => ({classList: {toggle: () => {}, add: () => {}, remove: () => {}},
                   style: {}, textContent: "", innerHTML: "", clientWidth: 300,
                   addEventListener: () => {}, value: "",
                   /* `cycleSnap` re-renders the sidebar -- the window
                      `focusNotes` reads is half a step of this grid --
                      and that binds a handler per row, so the stub
                      answers like a node with no children rather than
                      like a node with no API. */
                   querySelectorAll: () => []});
globalThis.document = {querySelector: () => el(), addEventListener: () => {}};
/* Manlio: 4/4, shuffle triplets, verse-2 from bar 20 beat 3 (32 beats long).
   Bar 21 beat 4.5 is 5.5 beats into the section -- exactly between the two
   triplet lines at 4.333 and 4.667, which is where Paolo could not put a
   hit. */
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  grid: {}, notes: [], instruments: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c", ref_url: "r"}]};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(data.sections[0]);
/* The arithmetic is what is under test; the repaint is canvas work, pinned
   where the canvases are. The notes lane is the same -- `cycleSnap` redraws it
   because dividing the grid changes which notes count as "at this position". */
view.repaint = () => {};
view.paintNotesLane = () => {};
view.cand = {duration: 32, currentTime: 5.5, muted: false};
const out = {};
out.base = view.snapSubdivision();
out.coarseBeat = view.playheadBeat();
out.coarseSnap = view.positionAt(view.snapFraction(5.5 / 32)).beat;
view.cycleSnap();
out.multiple = view.snapMultiple;
out.fine = view.snapSubdivision();
out.fineBeat = view.playheadBeat();
out.fineSnap = view.positionAt(view.snapFraction(5.5 / 32)).beat;
view.cycleSnap();
out.finer = view.snapSubdivision();
view.cycleSnap();
out.wrapped = view.snapSubdivision();
console.log(JSON.stringify(out));
"""


def test_the_snap_grid_can_be_divided_further(tmp_path):
    """Paolo: *"I need to add a hit between 4.4.333 and 4.4.667 (this song is
    in triplets) and I cannot snap at the correct point"*.

    `drums.subdivision` is the song's own grid and stays the default -- it is
    where the hits are. But a note is sometimes about a place between two of
    its lines, and the only alternative was alt (no snap at all), which lands
    on an arbitrary decimal that `promote` then writes into `drums.additions`.
    So the snap divides the song's grid by 1, 2 or 4, and cycles."""
    out = _run_geometry_probe(tmp_path, SNAP_PROBE)

    assert out["base"] == 3 and out["fine"] == 6 and out["finer"] == 12
    assert out["wrapped"] == 3, "the snap does not cycle back"
    # On the song's own grid, 4.5 is not reachable: it lands on a triplet.
    assert round(out["coarseBeat"], 3) in (4.333, 4.667)
    assert round(out["coarseSnap"], 3) in (4.333, 4.667)
    # Divided once, it is exactly reachable -- and that is what a note files.
    assert out["fineBeat"] == 4.5
    assert round(out["fineSnap"], 3) == 4.5


def test_the_snap_resolution_is_on_screen_and_has_a_key():
    """A grid you cannot see the resolution of is a grid that files notes at
    positions you did not mean."""
    page = _page()

    assert 'id="snap"' in page
    assert 'event.key === "g"' in page
    # The drawn grid follows it, or the finer lines are invisible and the
    # playhead snaps to somewhere there is nothing to see.
    assert "snapSubdivision()" in page[page.index("drawBeatGrid("):]


def test_the_band_sits_under_the_drums_rather_than_over_them(tmp_path):
    """Paolo: *"when the band is on, can we have the band volume a little lower
    (20%) so I can hear the drums better"*. The bed is a full band mix and the
    candidate is a bare kit, so at equal gain the thing being judged is the
    quieter of the two. A gain on the bed, not a cut on the drums: the drums
    are the signal, and attenuating them would change what a velocity sounds
    like, which is one of the things a review pass is listening for."""
    out = _run_geometry_probe(tmp_path, TRANSPORT_PROBE)

    assert out["bandOn"]["volumes"] == [1, 1, 0.5, 0.5]
    # And it survives the switch, so both sides sit the same way under the kit.
    assert out["switched"]["volumes"] == [1, 1, 0.5, 0.5]
    # Honoured rather than honoured once: `applyMute` sets the gain on every
    # call, so nothing -- a src change, an element the browser re-creates --
    # can leave a full band mix at unity under a bare kit.
    assert out["reasserted"]["volumes"] == [1, 1, 0.5, 0.5], (
        "the band gain is set once and never re-asserted")


def test_the_same_note_posted_twice_from_the_browser_is_one_chip(reviewable):
    """The grid menu files a note with two clicks and no typing, so the same
    hit gets clicked twice -- and a second copy says nothing the first did not.
    Silent, because the chip is already on screen: the ledger is right and
    there is nothing for a reader to do."""
    base, song = reviewable
    payload = {"song": song.slug, "bar": 9, "beat": 2.667, "kind": "extra-hit",
               "instrument": "snare", "comment": "from the grid at 20.2.667"}

    _post(base, "/api/note", payload)
    data = _post(base, "/api/note", payload)

    assert len(data["notes"]) == 1


# ── a note filed in a section's last part-bar was invisible ─────────────────

CHIPS_PROBE = """
globalThis.document = {querySelector: () => null, addEventListener: () => {}};
/* Manlio's real boundaries: verse-2 runs 20.3 - 28.3 and verse-2-lift takes
   over from 28.3. Bar 28 belongs to BOTH by bar number and to exactly one of
   them by position. */
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  grid: {}, notes: [], instruments: [], sources: {}, sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3},
    {name: "verse-2-lift", start_bar: 28, start_beat: 3, end_bar: 32,
     end_beat: 3}]};
const view = new ReviewView("manlio", data);
const at = (bar, beat) => ({bar: bar, beat: beat});
const out = {};
out.tomInVerse2 = view.inSection(at(28, 2.667), data.sections[0]);
out.tomInLift = view.inSection(at(28, 2.667), data.sections[1]);
out.liftStartInVerse2 = view.inSection(at(28, 3), data.sections[0]);
out.liftStartInLift = view.inSection(at(28, 3), data.sections[1]);
out.beforeVerse2 = view.inSection(at(20, 1), data.sections[0]);
out.middle = view.inSection(at(24, 1), data.sections[0]);
console.log(JSON.stringify(out));
"""


def test_a_note_in_a_sections_last_part_bar_belongs_to_that_section(tmp_path):
    """Paolo: *"I'm trying to add a missing tom_mid at 30.2.667 in manlio, but
    it won't let me"*. It let him — three times. The chips were filtered with
    `note.bar >= start_bar && note.bar < end_bar`, and Manlio's verse-2 runs
    20.3 to **28.3**, so a note at bar 28 failed `28 < 28` and vanished from
    the section it was filed in — while showing up under verse-2-lift, which
    starts at 28.3 and never contained it. Sections here rarely start on a bar
    line (CLAUDE.md), so this is the common case, not the corner one: the same
    beats-not-bars arithmetic the whole screen uses."""
    out = _run_geometry_probe(tmp_path, CHIPS_PROBE)

    assert out["tomInVerse2"] is True, "the note vanished from its own section"
    assert out["tomInLift"] is False, "and turned up in the next one"
    assert out["liftStartInVerse2"] is False and out["liftStartInLift"] is True
    assert out["beforeVerse2"] is False and out["middle"] is True


def test_the_screen_says_when_a_filing_added_nothing():
    """De-duping is silent by request, but silence plus an invisible chip is
    indistinguishable from a refusal -- which is exactly how Paolo read it.
    One line in the log the screen already uses, naming the position."""
    page = _page()
    save = page[page.index("  async saveNote() {"):]
    save = save[:save.index("\n  }")]

    assert "already noted" in save


# ── the section is part of the address ──────────────────────────────────────

URL_PROBE = """
globalThis.document = {querySelector: () => null, addEventListener: () => {}};
const sections = [{name: "theme-intro"}, {name: "verse-2"},
                  {name: "chorus-1"}, {name: "verse-2"}];
const out = {
  found: sectionIndex(sections, "verse-2"),
  duplicate: sectionIndex(sections, "verse-2"),
  missing: sectionIndex(sections, "no-such-section"),
  blank: sectionIndex(sections, ""),
  undef: sectionIndex(sections, undefined),
  empty: sectionIndex([], "verse-2"),
  hash: sectionHash("manlio", "verse-2"),
  encoded: sectionHash("manlio", "a section/2"),
};
console.log(JSON.stringify(out));
"""


def test_a_section_resolves_from_the_url_and_never_throws(tmp_path):
    """Paolo: *"if I am in verse-2 and reload the page to check the re-rendered
    candidate drums, it goes back to the first section"*. The section is part of
    what you are looking at, so it belongs in the address.

    By **name**, not by index: a name is readable in the URL and survives a
    section being added before it, and the only cost is that a repeated name
    resolves to the first of them -- which is navigation, not a stored record,
    so it does not need the position-keyed identity the done marks use. A name
    that no longer exists falls back to the first section rather than failing:
    a stale bookmark should open the song, not an error."""
    out = _run_geometry_probe(tmp_path, URL_PROBE)

    assert out["found"] == 1 and out["duplicate"] == 1
    assert out["missing"] == 0 and out["blank"] == 0 and out["undef"] == 0
    assert out["empty"] == 0
    assert out["hash"] == "#/review/manlio/verse-2"
    assert "%2F" in out["encoded"], "a name with a slash would break the route"


def test_stepping_sections_rewrites_the_address_without_reloading():
    """`location.hash = …` would fire hashchange, which re-enters `route()` and
    rebuilds the whole screen -- stopping the audio mid-listen to render the
    section you were already on. `replaceState` moves the address only."""
    page = _page()

    show = page[page.index("  show() {"):]
    show = show[:show.index("\n  }")]
    assert "history.replaceState(" in show, "stepping does not move the address"
    # And it must not re-enter the router, which would rebuild the screen and
    # stop the audio to render the section it was already on. Comments
    # stripped: this file explains itself, and the prose says `route()` too.
    code = re.sub(r"/\*.*?\*/", "", show, flags=re.S)
    assert "renderReview(" not in code and "route()" not in code
    assert "location.hash =" not in code


def test_the_review_route_accepts_a_section_and_still_works_without_one():
    page = _page()
    route = page[page.index("function route() {"):]
    route = route[:route.index("\n}")]

    assert "review" in route and "renderReview(" in route
    # Two capture groups: the slug, and an optional section after it.
    assert "?" in route or "(?:" in route


def test_a_refresh_lands_on_the_section_the_address_names():
    """The chain that makes a reload work: the route hands the name to
    renderReview, which resolves it with sectionIndex."""
    page = _page()
    render = page[page.index("async function renderReview(slug"):]
    render = render[:render.index("view.mount();")]

    assert "sectionIndex(" in render


# ── the notes lane and the sidebar ──────────────────────────────────────────
#
# Paolo: *"in a section with many notes, the buttons to regenerate sit very far
# down in the page, also it is difficult to locate a note related to a
# particular point in the section to promote it / demote it / change it ... add
# a row in the instruments grid that shows a "dot" in the timeline where there
# is a note. the "add new note" element, and details of all notes at the
# playhead ... so the whole notes + buttons section is neatly moved to a right
# sidebar that updates automatically when I select the "dots" in the timeline or
# want to add a note at the playhead"*.
#
# Two separate defects in that one sentence. A note had no position on screen at
# all -- the ledger was a list under the stack, so "which of these eleven is the
# one I am hearing" was answered by reading bar numbers -- and everything that
# acts on a note sat below that list, so the controls moved further away the
# more there was to do. The lane gives a note a place, and the sidebar keeps the
# form and the buttons at a fixed distance from the ear whatever the ledger is
# doing.

NOTES_LANE_PROBE = """
const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  sources: {}, grid: {}, instruments: ["crash"],
  sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c", ref_url: "r"},
    {name: "verse-2-lift", start_bar: 28, start_beat: 3, end_bar: 32,
     end_beat: 3, cand_url: "c2", ref_url: "r2"},
  ],
  /* Two notes at one position with different statuses, a triplet position, a
     dismissed one, and one in the NEXT section -- so the lane cannot draw a
     dot for a note that is not in the span it is drawn over. */
  notes: [
    {bar: 21, beat: 1, kind: "missing-hit", instrument: "crash",
     status: "promoted", key: "a"},
    {bar: 21, beat: 1, kind: "timing", instrument: "", status: "open",
     key: "b"},
    {bar: 24, beat: 4.667, kind: "extra-hit", instrument: "hihat_closed",
     status: "promoted", key: "c"},
    {bar: 26, beat: 2, kind: "other", instrument: "", status: "dismissed",
     key: "d"},
    {bar: 30, beat: 1, kind: "missing-hit", instrument: "crash",
     status: "open", key: "e"},
  ]};
const view = new ReviewView("manlio", data);
view.index = 0;
view.marks = view.beatMarks(view.section);
const out = {};
const marks = view.noteMarks(view.section);
out.marks = marks.map((mark) => ({
  at: view.reaperAt(mark.bar, mark.beat), status: mark.status,
  count: mark.count, frac: Number(mark.frac.toFixed(6))}));
out.counts = view.noteCounts();
/* Clicking a dot: near it in VIEW space, within the tolerance the lane draws
   its dots at, and the answer is the mark's own exact position -- never the
   snapped pixel, which on a 1/3 grid cannot reach beat 4.667's neighbours. */
out.nearOnIt = (view.markNear(marks[0].frac + 0.002, 0.01) || {}).count;
out.nearTriplet = view.reaperAt(
  view.markNear(marks[1].frac, 0.01).bar,
  view.markNear(marks[1].frac, 0.01).beat);
out.nearMiss = view.markNear(0.9, 0.001);
/* No focus yet: the sidebar has nothing to be about, and says so rather than
   claiming the section's whole ledger sits at one position. */
out.blankFocus = view.focusNotes().map((note) => note.key);
out.blankLabel = view.focusLabel();
view.focusAt(21, 1);
out.focusLabel = view.focusLabel();
out.focused = view.focusNotes().map((note) => note.key);
view.focusAt(24, 4.667);
out.focusedTriplet = view.focusNotes().map((note) => note.key);
view.focusAt(24, 1);
out.focusedElsewhere = view.focusNotes().map((note) => note.key);
/* Stepping from dot to dot, which is the other half of "locate the note at
   this point": forward from the top, and clamped at either end. */
view.focus = null;
out.stepFirst = (view.nextMark(1, 0) || {}).count;
out.stepFrom21 = view.reaperAt(view.nextMark(1, marks[0].frac).bar,
                               view.nextMark(1, marks[0].frac).beat);
out.stepBack = view.reaperAt(view.nextMark(-1, marks[1].frac).bar,
                             view.nextMark(-1, marks[1].frac).beat);
out.stepPastEnd = view.nextMark(1, 1);
view.index = 1;
view.marks = view.beatMarks(view.section);
out.liftMarks = view.noteMarks(view.section).map(
  (mark) => view.reaperAt(mark.bar, mark.beat));
out.liftCounts = view.noteCounts();
console.log(JSON.stringify(out));
"""


def test_the_notes_lane_puts_one_dot_at_every_noted_position(tmp_path):
    """One dot per *position*, not per note: two notes about the same hit are
    one thing to go and look at, and two dots at one x are one dot drawn twice.
    The lane is in the section's own beats like every other row in the stack,
    so a note in the next section cannot appear on this one."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    assert [mark["at"] for mark in out["marks"]] == ["23.1", "26.4.667", "28.2"]
    assert [mark["count"] for mark in out["marks"]] == [2, 1, 1]
    # bar 21 beat 1 is the first bar line of verse-2 (20.3 - 28.3): 2 beats in
    # of 32, so a sixteenth of the way across and nowhere near the left edge.
    assert out["marks"][0]["frac"] == pytest.approx(2 / 32, abs=1e-6)
    # The note in verse-2-lift is on verse-2-lift, and only there.
    assert out["liftMarks"] == ["32.1"]


def test_a_dot_shows_the_decision_that_is_still_outstanding(tmp_path):
    """The colours answer "what is left to do here". So where a promoted note
    and an open one share a position the dot reads **open**: the promoted half
    is already in song.yaml and needs nobody, and drawing it green would hide
    the one thing on that beat still waiting for a decision."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    assert [mark["status"] for mark in out["marks"]] == [
        "open", "promoted", "dismissed"]


def test_the_sidebar_counts_the_section_and_the_song(tmp_path):
    """Paolo: *"maybe this sidebar can have a short summary of how many notes,
    and how many not promoted"*. Both scopes, because "is this section done" and
    "is this song done" are different questions and the answer to the second is
    what a promote pass is about."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    counts = out["counts"]
    assert counts["here"] == 4 and counts["song"] == 5
    # Not promoted is open plus dismissed; open is what is still undecided.
    assert counts["hereOpen"] == 1 and counts["herePromoted"] == 2
    assert counts["hereDismissed"] == 1
    assert counts["songOpen"] == 2
    # And it follows the section under the ear, not the whole ledger.
    assert out["liftCounts"]["here"] == 1
    assert out["liftCounts"]["song"] == 5


def test_clicking_a_dot_lands_on_the_notes_own_position(tmp_path):
    """A dot is a click target of a few pixels, so the hit test is in **view**
    fractions -- the space the dot is actually drawn in, which is what makes it
    still work at zoom 16. And what it returns is the note's own bar and beat,
    never the snapped pixel: a note filed at 1/12 cannot be reached by a
    playhead snapping to the song's 1/3."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    assert out["nearOnIt"] == 2, "a click beside the dot did not find it"
    assert out["nearTriplet"] == "26.4.667", (
        "the dot did not answer with its own position")
    assert out["nearMiss"] is None, "a click nowhere near a dot found one"


def test_the_sidebar_shows_the_notes_at_the_focus_and_nothing_else(tmp_path):
    """The whole point: *"details of all notes at the playhead (so only notes in
    at the playhead are shown, not the whole list)"*. Both notes at the shared
    position, and nothing from a beat away."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    assert out["focusLabel"] == "23.1"
    assert sorted(out["focused"]) == ["a", "b"]
    assert out["focusedTriplet"] == ["c"]
    assert out["focusedElsewhere"] == []
    # Before anything has been pointed at, the focus is empty rather than
    # standing at bar 1 claiming the section's whole ledger is there.
    assert out["blankFocus"] == [] and out["blankLabel"] == "—"


def test_the_dots_can_be_stepped_through(tmp_path):
    """`[` / `]`. The lane says where the notes are; stepping is how you get the
    playhead onto one without aiming at a 10px dot, which is the other half of
    *"difficult to locate a note related to a particular point"*."""
    out = _run_geometry_probe(tmp_path, NOTES_LANE_PROBE)

    assert out["stepFirst"] == 2, "stepping from the top found no dot"
    assert out["stepFrom21"] == "26.4.667"
    assert out["stepBack"] == "23.1"
    assert out["stepPastEnd"] is None, "stepping past the last dot wrapped"


def test_the_notes_lane_is_in_the_stack_above_the_instrument_rows():
    """It is a row of the same stack, on the same gutter and the same window --
    a dot that does not sit under the transient it is about is worse than no
    dot. Its own class, not `.gridrow`: those carry a `data-row` index into the
    MIDI grid, and the right-click menu and the instrument pick both key off
    that."""
    page = _page()

    assert 'id="noteslane"' in page, "there is no notes lane"
    assert ".notelane .who" in page, "the lane is not on the shared gutter"
    stack = page[page.index('<div id="stack">'):
                 page.index('<div class="playhead"')]
    assert stack.index('id="noteslane"') > stack.index('<canvas id="ruler">'), (
        "the notes lane is not under the ruler")
    assert stack.index('id="noteslane"') < stack.index('<div id="grid">'), (
        "the notes lane is below the instrument rows")
    assert "paintNotesLane" in page, "nothing draws the dots"
    # Drawn from the same view window and the same repaint as everything else.
    repaint = page[page.index("  repaint() {"):]
    repaint = repaint[:repaint.index("\n  }")]
    assert "paintNotesLane" in repaint, "the dots do not follow a zoom or a pan"


def test_a_dot_is_coloured_by_what_is_left_to_decide():
    """Paolo: *"the dots could have different colours to distinguish promoted
    and not promoted"*. The page's own status colours, so a green dot and a
    green badge mean the same thing."""
    page = _page()

    assert "NOTE_DOT" in page, "the dot colours are not one table"
    dots = page[page.index("const NOTE_DOT"):]
    dots = dots[:dots.index("\n")]
    for status in ("open", "promoted", "dismissed"):
        assert status in dots, f"{status} has no dot colour"


def test_the_notes_panel_and_the_buttons_are_in_a_sticky_sidebar():
    """The complaint this is about: *"in a section with many notes, the buttons
    to regenerate sit very far down in the page"*. So the ledger is the only
    thing allowed to grow -- it scrolls inside itself -- and the form, the
    summary and every button keep a fixed distance from the stack."""
    page = _page()

    assert 'class="sidebar"' in page, "no sidebar"
    assert ".sidebar {" in page, "the sidebar has no rule"
    css = page[page.index("  .sidebar {"):page.index("  .sidebar {") + 700]
    assert "position:sticky" in css, (
        "the sidebar scrolls away with the page, which is the bug")
    # Every control that acts on the ledger or the build is in it.
    side = page[page.index('<aside class="sidebar">'):page.index("</aside>")]
    for control in ('id="save-note"', 'id="promote-render"', 'id="promote"',
                    'id="rebuild"', 'id="rerender"', 'id="export"',
                    'id="chips"', 'id="note-at"', 'id="instrument"'):
        assert control in side, f"{control} is not in the sidebar"
    # And the list is what scrolls, not the page.
    assert "overflow-y:auto" in page, "the note list cannot scroll inside itself"


def test_the_sidebar_only_follows_a_deliberate_placement():
    """The focus is set by a click, a step or a filing -- never by the playhead
    itself. `updatePlayhead` runs on every animation frame while playing, so a
    sidebar that followed it would rewrite its own list sixty times a second
    and be unreadable for the whole of every pass -- and every control in it
    would move under the pointer."""
    page = _page()

    playhead = page[page.index("  updatePlayhead() {"):]
    playhead = playhead[:playhead.index("\n  }")]
    code = re.sub(r"/\*.*?\*/", "", playhead, flags=re.S)
    assert "focusAt(" not in code and "renderNotes(" not in code, (
        "the sidebar is redrawn from the frame loop")
    # It does follow the two gestures that mean "look here".
    scrub = page[page.index("  scrubFrom(event, start) {"):]
    scrub = scrub[:scrub.index("\n  }")]
    assert "pointAt(" in scrub, "clicking the stack does not point the sidebar"
    # And a drag is a hundred pointermove events, so it redraws on a change
    # only: rebuilding the list per event would flicker the very panel the
    # drag is aiming at.
    point = page[page.index("  pointAt(bar, beat) {"):]
    point = point[:point.index("\n  }")]
    assert "!== before" in point, "a drag rebuilds the note list per event"


def test_stepping_the_dots_is_on_the_keyboard_and_in_the_help():
    page = _page()

    assert 'event.key === "["' in page and 'event.key === "]"' in page, (
        "the dots cannot be stepped from the keyboard")
    assert "stepNote" in page, "no handler for stepping between notes"
    help_card = page[page.index('<div id="help">'):page.index("</table>")]
    assert "kbd\">[<" in help_card, (
        "the help list does not mention the note-stepping keys")


def test_filing_a_note_points_the_sidebar_at_it():
    """A note you just filed is the one you want to see -- and if the filing
    was a de-dupe, the note it merged into is the one to look at. Either way
    the sidebar has to be about that position."""
    page = _page()

    save = page[page.index("  async saveNote() {"):]
    save = save[:save.index("\n  }")]
    assert "focusAt(" in save, "filing a note does not point the sidebar at it"

    menu = page[page.index("  async fileFromMenu(kind) {"):]
    menu = menu[:menu.index("\n  }")]
    assert "focusAt(" in menu, (
        "filing from the grid menu does not point the sidebar at it")


# ── the settings, out of the working area ───────────────────────────────────
#
# Paolo: *"move to the bottom of the sidebar a section with the following
# settings (removed from under the grid): Band on/off, zoom indicator, wave:
# fit/raw/boost, snap options, loop on/off (so that settings do not clutter the
# working area)"*. What is left under the stack is what is about the take being
# judged: play, rewind, which side is audible, and where the ear is.


def test_the_settings_moved_out_from_under_the_stack():
    page = _page()
    transport = page[page.index('<div class="transport">'):]
    transport = transport[:transport.index("</div>")]
    settings = page[page.index('<div class="settings">'):]
    settings = settings[:settings.index("\n        </div>")]

    for control in ('id="band"', 'id="zoom"', 'id="wave"', 'id="snap"',
                    'id="loopstate"'):
        assert control not in transport, f"{control} still clutters the stack"
        assert control in settings, f"{control} is not in the settings block"
    # And the settings block is the last thing in the sidebar.
    side = page[page.index('<aside class="sidebar">'):page.index("</aside>")]
    assert side.index('<div class="settings">') > side.index('class="sideacts"'), (
        "the settings sit above the buttons they are meant to be below")
    # What stays is the transport itself.
    for kept in ('id="play"', 'id="tostart"', 'id="which"', 'id="at"'):
        assert kept in transport, f"{kept} left the transport"


def test_the_two_keyboard_only_readouts_are_buttons_now():
    """`snap` and `loop` were spans with a keyboard shortcut. Sat next to three
    real buttons in the settings block, a readout that looks like its
    neighbours and does nothing when clicked is a broken button."""
    page = _page()

    assert '$("#snap").addEventListener("click"' in page, (
        "the snap readout is not clickable")
    assert '$("#loopstate").addEventListener("click"' in page, (
        "the loop readout is not clickable")
    # One door for the loop, so the key and the button cannot diverge.
    assert "toggleLoop()" in page and "showLoop()" in page
    keys = page[page.index('event.key === "l"'):]
    assert "toggleLoop" in keys[:keys.index("\n")], (
        "the l key still writes the readout itself")


def test_dividing_the_snap_redraws_what_the_sidebar_is_showing():
    """`focusNotes` reads half a step of the grid in force, so `g` changes which
    notes count as "at this position" -- and that has to be visible."""
    page = _page()
    snap = page[page.index("  cycleSnap() {"):]
    snap = snap[:snap.index("\n  }")]

    assert "renderNotes()" in snap, (
        "dividing the grid narrows the list silently")


# ── alt+wheel slides the window, and a rewind that is not a restart ─────────


def test_alt_wheel_pans_the_zoomed_window():
    """Paolo: *"can we add alt+mouse wheel to slide back/forth (right/left)
    while zoomed, like in reaper"*. Reaper's horizontal scroll is alt+wheel;
    shift+wheel already did it here, and both now do the one thing."""
    page = _page()
    wheel = page[page.index("  onWheel(event) {"):]
    wheel = wheel[:wheel.index("\n  }")]

    assert "altKey" in wheel, "alt+wheel does not pan"
    assert "panBy" in wheel and "zoomBy" in wheel
    # Plain wheel is still the zoom -- the modifier is what makes it a pan.
    assert "event.altKey || event.shiftKey" in wheel or (
        "event.shiftKey || event.altKey" in wheel), (
        "the pan is not a modifier on the same gesture")
    help_card = page[page.index('<div id="help">'):page.index("</table>")]
    assert "alt</span>+<span class=\"kbd\">wheel" in help_card, (
        "the help list does not mention alt+wheel")


def test_the_transport_can_rewind_without_starting_anything():
    """Paolo: *"can we add a "back to beginning of section" to the left of this
    button ... to move the playhead back to the start"*. `shift`+`space` is the
    other one -- "again, from the top" -- and it *plays*; this leaves the
    transport exactly as it was, which is what a looping section wants."""
    page = _page()

    assert 'id="tostart"' in page, "no rewind control"
    transport = page[page.index('<div class="transport">'):]
    transport = transport[:transport.index("</div>")]
    assert transport.index('id="tostart"') < transport.index('id="play"'), (
        "the rewind is not to the left of play")
    handler = page[page.index('$("#tostart").addEventListener'):]
    handler = handler[:handler.index("\n")]
    assert "seek(0)" in handler, "the rewind does not move the playhead"
    assert "play()" not in handler, (
        "the rewind starts playback, which is what shift+space is for")


DOT_DRAW_PROBE = """
/* A canvas that records the circles, so the one claim the lane makes can be
   checked: a dot sits at the same x as the bar line it is about, on every row
   of the stack, at any zoom. */
function dotRecorder(cssWidth) {
  const arcs = [], rects = [];
  const context = {
    canvas: null, fillStyle: "", strokeStyle: "", font: "", lineWidth: 0,
    clearRect() {}, measureText: (text) => ({width: text.length * 11}),
    fillText() {},
    fillRect(x, y, w, h) { rects.push({x: x, w: w, style: context.fillStyle}); },
    beginPath() { this._at = null; },
    arc(x, y, r) { this._at = {x: x, r: r}; },
    fill() { arcs.push({x: this._at.x, r: this._at.r,
                        style: context.fillStyle, filled: true}); },
    stroke() { arcs.push({x: this._at.x, r: this._at.r,
                          style: context.strokeStyle, filled: false}); },
  };
  const canvas = {clientWidth: cssWidth, clientHeight: 24, width: 0, height: 0,
                  getContext: () => context};
  context.canvas = canvas;
  return {canvas: canvas, arcs: arcs, rects: rects};
}

const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  sources: {}, instruments: [],
  sections: [{name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28,
              end_beat: 3, cand_url: "c", ref_url: "r"}],
  /* A kick on bar 21 beat 1, which is the section's first bar line, and a note
     about that same hit. */
  grid: {"verse-2": [{instrument: "kick",
                      ticks: [{bar: 21, beat: 1, velocity: 100}]}]},
  notes: [
    {bar: 21, beat: 1, kind: "missing-hit", instrument: "kick",
     status: "open", key: "a"},
    {bar: 24, beat: 1, kind: "extra-hit", instrument: "snare",
     status: "promoted", key: "b"},
    {bar: 26, beat: 1, kind: "other", instrument: "", status: "dismissed",
     key: "c"},
  ]};
const view = new ReviewView("manlio", data);
view.marks = view.beatMarks(view.section);
const out = {};

const row = dotRecorder(1100);
globalThis.document.querySelector = () => ({querySelector: () => row.canvas});
view.paintGridRow(data.grid["verse-2"][0], 0, view.section);
out.hitX = row.rects.filter((r) => r.style === "#c7cbd3" && r.w === 4)[0].x;

const lane = dotRecorder(1100);
globalThis.document.querySelector = () => lane.canvas;
view.paintNotesLane();
out.dots = lane.arcs.map((arc) => ({x: arc.x, style: arc.style,
                                    filled: arc.filled}));

/* Focused, and zoomed in eight times about the same dot: the ring appears and
   every dot is still on its own bar line. */
view.focusAt(21, 1);
const focused = dotRecorder(1100);
globalThis.document.querySelector = () => focused.canvas;
view.paintNotesLane();
out.focusedRings = focused.arcs.filter((arc) => !arc.filled
  && arc.style === "#e7e9ec").length;

view.zoom = 8;
view.setLeft(view.fracFor(view.section, 21, 1) - 0.5 / 8);
const zoomed = dotRecorder(1100);
globalThis.document.querySelector = () => zoomed.canvas;
view.paintNotesLane();
const zoomedRow = dotRecorder(1100);
globalThis.document.querySelector = () => ({querySelector: () => zoomedRow.canvas});
view.paintGridRow(data.grid["verse-2"][0], 0, view.section);
out.zoomedHitX = zoomedRow.rects
  .filter((r) => r.style === "#c7cbd3" && r.w === 4)[0].x;
out.zoomedDotX = zoomed.arcs.filter((arc) => arc.filled)[0].x;
/* Off the window entirely: bar 26 is outside a 1/8th window round bar 21, so
   it must not be drawn at all rather than clamped to the edge. */
out.zoomedDots = zoomed.arcs.filter((arc) => arc.filled).length;
console.log(JSON.stringify(out));
"""


def test_a_dot_sits_on_the_hit_it_is_about(tmp_path):
    """The claim the lane makes. A note filed at bar 21 beat 1 has to be drawn
    at the same x as that hit in the instrument grid below it -- the dot is
    centred on the position, the hit's 4px tick is drawn 2px before it -- and
    that has to survive a zoom, where a clamped dot would invent a note at the
    edge of the window."""
    out = _run_geometry_probe(tmp_path, DOT_DRAW_PROBE)

    assert out["dots"][0]["x"] == pytest.approx(out["hitX"] + 2, abs=0.01), (
        "the dot is not over the hit it is about")
    # Fitted, all three are on the lane; the dismissed one is hollow.
    assert [dot["style"] for dot in out["dots"]] == [
        "#eaa23e", "#3fcf8e", "#6b7280"]
    assert [dot["filled"] for dot in out["dots"]] == [True, True, False]
    # The focused dot wears a ring, which is how the timeline says where the
    # sidebar is pointing.
    assert out["focusedRings"] == 1

    assert out["zoomedDotX"] == pytest.approx(out["zoomedHitX"] + 2, abs=0.01), (
        "the dot leaves its hit behind once the window narrows")
    assert out["zoomedDots"] == 1, (
        "a dot outside the window is drawn anyway, which invents a note")


# ── the shared gain has to be re-measured when a clip lands ─────────────────
#
# Paolo: *"wave boost shows incorrectly on first load (truncated waveforms).
# When I cycle fit, raw and boost again, then it shows correctly (no longer
# truncated/saturated)"*.
#
# `waveRef` -- the loudest thing visible on either lane, which is the gain both
# are drawn with -- is measured in `repaint()`. A clip decodes asynchronously
# and `ensurePeaks` painted *its own lane* when it landed, so the gain was still
# the 0 from before anything had decoded: `waveFraction` floors the reference at
# `WAVE_FLOOR`, so every peak over 0.05 came out at full height. That is the
# saturation, and it stayed until something called `repaint()` -- which is
# exactly what cycling the scale does.
#
# It is also wrong the other way round for the *second* clip to land: the gain
# is shared, so when the reference decodes the candidate lane has to be redrawn
# with the new number too, and painting one lane can never do that.

PEAKS_LANDING_PROBE = """
function recorder(cssWidth, cssHeight) {
  const rects = [];
  const context = {
    canvas: null, fillStyle: "", strokeStyle: "", font: "", lineWidth: 0,
    clearRect() {}, measureText: (t) => ({width: t.length * 11}), fillText() {},
    beginPath() {}, arc() {}, fill() {}, stroke() {},
    fillRect(x, y, w, h) { rects.push({x: x, y: y, w: w, h: h}); },
  };
  const canvas = {clientWidth: cssWidth, clientHeight: cssHeight,
                  width: 0, height: 0, getContext: () => context};
  context.canvas = canvas;
  return {canvas: canvas, rects: rects};
}

const data = {
  beats_per_bar: 4, subdivision: 3, count_in_bars: 2, title: "Manlio",
  grid: {}, notes: [], sources: {}, instruments: [], sections: [
    {name: "verse-2", start_bar: 20, start_beat: 3, end_bar: 28, end_beat: 3,
     cand_url: "c1", ref_url: "r1"}]};
const view = new ReviewView("manlio", data);
view.index = 0;
view.marks = view.beatMarks(view.section);
const lane = recorder(1100, 104);
globalThis.document.querySelector = () => lane.canvas;
/* Not under test here, and both want a DOM the recorder does not pretend to
   be. The gain is the whole question. */
view.updatePlayhead = () => {};
view.paintNotesLane = () => {};
view.drawRuler = () => {};
const r = (x) => Math.round(x * 1000) / 1000;
const out = {};

/* Nothing decoded: the lanes draw no peaks at all (`paintLane` returns early),
   so this is only the state the gain starts in. */
view.repaint();
out.blind = view.waveRef;
/* ...and it is the state that saturates: with no gain measured, the reference
   is floored at WAVE_FLOOR = 0.05, so a peak of 0.4 is drawn 8x over the top
   and clamped to the full height of the lane. */
out.blindFraction = r(view.waveFraction(0.4, view.waveRef));

/* The candidate lands. Painting its lane draws the peaks and measures
   nothing -- which is the bug: this is what `ensurePeaks` used to do. */
view.peakCache.c1 = {peaks: Float32Array.from([0.10, 0.40]), buffer: null,
                     failed: false};
view.paintLane("cand");
out.afterPaintLane = view.waveRef;
view.repaint();
out.afterRepaint = r(view.waveRef);
out.candFraction = r(view.waveFraction(0.4, view.waveRef));

/* The reference lands after it, louder. The gain is SHARED, so this has to
   move the candidate lane as well -- one repaint, not one lane. */
view.peakCache.r1 = {peaks: Float32Array.from([0.20, 0.80]), buffer: null,
                     failed: false};
view.repaint();
out.afterBoth = r(view.waveRef);
out.candAgainstBoth = r(view.waveFraction(0.4, view.waveRef));
console.log(JSON.stringify(out));
"""


def test_a_clip_landing_re_measures_the_shared_gain(tmp_path):
    """The mechanism of the saturation, pinned. Painting one lane never touches
    `waveRef`, so a clip that lands into a gain of 0 is drawn against
    `WAVE_FLOOR` -- 20x, which clamps everything over 0.05 to the top of the
    lane. Only `repaint()` measures, which is why cycling the scale fixed it."""
    out = _run_geometry_probe(tmp_path, PEAKS_LANDING_PROBE)

    assert out["blind"] == 0, "the gain starts somewhere other than unmeasured"
    assert out["blindFraction"] == 1.0, (
        "the saturation this is about does not reproduce")
    assert out["afterPaintLane"] == 0, (
        "paintLane measures the gain now, which would hide the real defect")
    assert out["afterRepaint"] == 0.4, "a repaint does not measure the gain"
    assert out["candFraction"] == 1.0  # it IS the loudest thing on screen
    # And the second clip moves the number the first lane was drawn with, which
    # is why the landing has to repaint the whole stack.
    assert out["afterBoth"] == 0.8, "the gain is not shared between the lanes"
    # 0.4 against a gain of 0.8 is -6 dB, which the boost scale draws at 87.5%
    # of the lane -- not at the top, which is the whole visible difference.
    assert out["candAgainstBoth"] == 0.875, (
        "the candidate is not re-drawn against the louder reference")


def test_a_landing_clip_repaints_the_stack_not_its_own_lane():
    """The fix, where it has to be: `ensurePeaks` cannot paint one lane, because
    the gain it would be drawn with is measured for the stack in `repaint()`."""
    page = _page()
    ensure = page[page.index("  async ensurePeaks(url, side) {"):]
    ensure = ensure[:ensure.index("\n  }")]
    code = re.sub(r"/\*.*?\*/", "", ensure, flags=re.S)

    assert "this.repaint()" in code, "a landing clip does not repaint the stack"
    assert "paintLane(" not in code, (
        "a landing clip paints its own lane against a gain measured before it")
    # Still only when the section it belongs to is the one on screen: n/p while
    # a clip decodes must not paint the previous section over the new one.
    assert "this.section" in code


def test_the_band_gain_is_re_applied_after_every_src_change():
    """Where "honoured" would break in the browser rather than in node:
    `show()` assigns four new `src` values on every section change, and the
    gain has to be re-asserted after them. Set once in the constructor it
    would be a setting that used to be true."""
    page = _page()
    show = page[page.index("  show() {"):]
    show = show[:show.index("\n  }")]

    assert show.index("applyMute()") > show.index("this.cand.src"), (
        "the band gain is not re-applied after the srcs are assigned")
    mute = page[page.index("  applyMute() {"):]
    mute = mute[:mute.index("\n  }")]
    assert "BAND_VOLUME" in mute, "the gain is not set from the one constant"
