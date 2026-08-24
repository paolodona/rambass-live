"""The console server: JSON endpoints over review.py, nothing invented there.

Started on port 0 in a thread against the throwaway project, exercised with
plain urllib — no browser. What the browser renders is these payloads; what
these tests pin is that the payloads say what `rambass status` and `rambass
stale` say, because a dashboard that disagrees with the terminal is worse
than no dashboard.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from rambass.console import make_server
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


def test_a_note_posted_from_the_browser_lands_in_the_ledger(served, song):
    base, _ = served
    from rambass.review import load_review

    data = _post(base, "/api/note", {
        "song": song.slug, "bar": 43, "beat": 1.0, "kind": "missing-hit",
        "instrument": "crash", "velocity": 105, "comment": "into the lift"})
    assert data["notes"]
    _, notes = load_review(song.path("qa", "review.yaml"))
    assert notes and notes[0].bar == 43 and notes[0].section == "chorus"
