"""Knowing which files are stale, and why.

Paolo: *"earlier in the session you said: 'drums-quantized.mid on disk predates
today's fixes'. In general, how do I avoid working on stale files? should we have
a staleness check or staleness alert so that old files are regenerated
automatically if needed to retrofit old steps/phases"*

There are **three** ways an artifact goes stale and only one of them is the one
`make` would catch:

1. an input file changed — a re-separated stem, a new source mix;
2. ``song.yaml`` changed — a new tempo, a moved section, a declared backbeat;
3. **the code that produced it changed.** This is the one that actually bit us:
   ``drums-raw.mid`` was two commits old, its inputs were untouched, its manifest
   was untouched, and it still held a `hihat_closed v122` where the current
   transcriber puts an open hi-hat. Nothing on disk said so, and the only reason
   it came to light is that Paolo listened.

So the stamp records all three, and the report says which one moved. It does
**not** regenerate anything on its own: a re-transcription is minutes of CPU and
it can change the part under you — which is exactly what happened here — so the
decision belongs to a human who is ready to listen to the result.
"""

from __future__ import annotations

import pytest

from rambass.provenance import (
    PIPELINE,
    fingerprint,
    manifest_hash,
    read_stamps,
    stale_report,
    stamp,
)


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── fingerprints ─────────────────────────────────────────────────────────────


def test_the_same_bytes_fingerprint_the_same_after_a_copy(tmp_path):
    """mtime alone would call a copied stem stale, and copying stems between
    machines is normal. Content, not timestamps."""
    import shutil

    first = _touch(tmp_path / "a.wav", "hello")
    second = tmp_path / "b.wav"
    shutil.copy2(first, second)
    second.touch()
    assert fingerprint(first) == fingerprint(second)


def test_changing_one_byte_changes_the_fingerprint(tmp_path):
    path = _touch(tmp_path / "a.mid", "hello")
    before = fingerprint(path)
    path.write_text("hellp", encoding="utf-8")
    assert fingerprint(path) != before


def test_a_missing_file_fingerprints_as_missing(tmp_path):
    assert fingerprint(tmp_path / "nope.wav") == ""


def test_a_big_file_is_fingerprinted_by_sampling_not_by_reading_all_of_it(tmp_path):
    """A stem is 56 MB and there are five of them per song. Head, middle, tail
    and the length is enough to notice a re-separation, and it is instant."""
    path = tmp_path / "big.wav"
    path.write_bytes(b"a" * (8 << 20))
    before = fingerprint(path)
    with path.open("r+b") as handle:              # change the middle
        handle.seek(4 << 20)
        handle.write(b"zzzz")
    assert fingerprint(path) != before


# ── the manifest hash is per concern, not per file ───────────────────────────


def test_editing_the_lyrics_field_does_not_make_the_drums_stale(song):
    before = manifest_hash(song, ("tempo", "sections", "drums"))
    song.lyrics_file = "lyrics.srt"
    assert manifest_hash(song, ("tempo", "sections", "drums")) == before


def test_editing_the_tempo_does_make_the_drums_stale(song):
    before = manifest_hash(song, ("tempo", "sections", "drums"))
    song.bpm = 61.0
    assert manifest_hash(song, ("tempo", "sections", "drums")) != before


def test_a_declared_backbeat_counts_as_a_section_change(song):
    before = manifest_hash(song, ("sections",))
    song.sections[0].backbeat = "sidestick"
    assert manifest_hash(song, ("sections",)) != before


# ── stamping and checking ────────────────────────────────────────────────────


def test_a_freshly_stamped_artifact_is_clean(song):
    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    states = {s.artifact: s for s in stale_report(song)}
    assert states["midi/drums-quantized.mid"].state == "ok"


def test_an_input_that_changed_is_reported_as_such(song):
    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    source.write_text("different", encoding="utf-8")
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "stale"
    assert any("drums-raw.mid" in reason for reason in entry.reasons)


def test_a_manifest_edit_is_reported_as_such(song):
    from rambass.manifest import save_song

    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    song.bpm = 61.0
    save_song(song)
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "stale"
    assert any("song.yaml" in reason for reason in entry.reasons)


def test_a_code_change_is_reported_as_such(song):
    """The case that actually happened, and the reason this exists."""
    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    stamps = read_stamps(song)
    stamps["midi/drums-quantized.mid"]["code"] = "0000000000"
    from rambass.provenance import write_stamps

    write_stamps(song, stamps)
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "stale"
    assert any("code" in reason for reason in entry.reasons)


def test_an_artifact_with_no_stamp_is_unknown_not_ok(song):
    """Everything built before this existed lands here, and 'unknown' is the
    honest answer: it may be fine, and nothing on disk can say so."""
    _touch(song.directory / "midi" / "drums-quantized.mid")
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "unknown"


def test_an_artifact_that_does_not_exist_is_missing(song):
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "missing"


def test_a_stamp_for_a_deleted_artifact_does_not_report_it_as_ok(song):
    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    artifact.unlink()
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "missing"


# ── the chain ────────────────────────────────────────────────────────────────


def test_staleness_flows_downstream(song):
    """The point of a chain: if the raw MIDI is stale then so is everything made
    from it, whatever its own stamp says. Otherwise you fix one file, see green
    below it, and ship the old part."""
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    quantized = _touch(song.directory / "midi" / "drums-quantized.mid")
    consolidated = _touch(song.directory / "midi" / "drums-consolidated.mid")
    stamp(song, quantized, step="drums clean", inputs=[raw])
    stamp(song, consolidated, step="drums consolidate", inputs=[quantized])
    raw.write_text("changed", encoding="utf-8")

    states = {s.artifact: s for s in stale_report(song)}
    assert states["midi/drums-quantized.mid"].state == "stale"
    downstream = states["midi/drums-consolidated.mid"]
    assert downstream.state == "stale"
    assert any("drums-quantized" in reason for reason in downstream.reasons)


def test_the_report_is_in_pipeline_order(song):
    order = [entry.artifact for entry in stale_report(song)]
    assert order.index("midi/drums-raw.mid") < order.index(
        "midi/drums-quantized.mid")
    assert order.index("midi/drums-quantized.mid") < order.index(
        "midi/drums-consolidated.mid")


def test_every_pipeline_step_names_a_command_to_fix_it():
    """A report that says "stale" and not "run this" is a report nobody acts on."""
    for step in PIPELINE:
        assert step.command, step.name
        assert step.artifact


def test_a_song_that_needs_no_drums_is_not_asked_for_them(song):
    song.drums_origin = "backing-track"
    artifacts = [entry.artifact for entry in stale_report(song)]
    assert not any("drums-" in artifact for artifact in artifacts)


def test_an_a_cappella_song_has_nothing_to_rebuild(song):
    song.drums_origin = "a-cappella"
    assert stale_report(song) == []


# ── it never regenerates anything by itself ──────────────────────────────────


def test_the_report_does_not_touch_the_files(song):
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    before = raw.read_text(encoding="utf-8"), fingerprint(raw)
    stale_report(song)
    assert (raw.read_text(encoding="utf-8"), fingerprint(raw)) == before


def test_stamping_writes_one_file_per_song_not_one_per_artifact(song):
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    quantized = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, quantized, step="drums clean", inputs=[raw])
    stamp(song, raw, step="drums transcribe", inputs=[])
    sidecars = list(song.directory.glob("**/*.provenance.yaml"))
    assert sidecars == []
    assert (song.directory / "provenance.yaml").is_file()
    assert len(read_stamps(song)) == 2


# ── the reason has to name the field, not the watch list ─────────────────────
#
# "song.yaml changed in bars, drums, sections, tempo" is what the first version
# said, which is every field the step watches and tells you nothing about what
# you did. Hashing per field costs nothing and turns the message into something
# you can act on.


def test_the_reason_names_the_field_that_actually_changed(song):
    from rambass.manifest import save_song

    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    song.sections[0].backbeat = "sidestick"
    save_song(song)
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    reasons = "; ".join(entry.reasons)
    assert "sections" in reasons
    assert "tempo" not in reasons, "only what moved"


def test_an_old_single_hash_stamp_still_reads_as_a_manifest_change(song):
    """Provenance written before per-field hashing has one string where a dict
    now goes. Treat it as "cannot tell which" rather than crashing on it."""
    from rambass.provenance import write_stamps

    source = _touch(song.directory / "midi" / "drums-raw.mid")
    artifact = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, artifact, step="drums clean", inputs=[source])
    stamps = read_stamps(song)
    stamps["midi/drums-quantized.mid"]["manifest"] = "deadbeefdeadbeef"
    write_stamps(song, stamps)
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-quantized.mid"]
    assert entry.state == "stale"
    assert any("song.yaml" in reason for reason in entry.reasons)


# ── a field spec can name a sub-key ──────────────────────────────────────────
#
# Watching the whole `drums` block made adding one entry to `drums.additions`
# declare the transcription, the clean and the consolidate all stale — which is
# false, since Stage 7 edits are applied after all three and cannot affect them.
# It took about ten seconds of real use to hit, and a report that cries wolf is
# worse than no report: the first thing anyone does with one is stop reading it.


def test_a_field_spec_can_name_one_sub_key(song):
    from rambass.provenance import manifest_hashes

    before = manifest_hashes(song, ("drums/subdivision",))
    song.drum_additions = [__import__("rambass.manifest", fromlist=["Addition"])
                           .Addition(bar=1, instrument="crash")]
    assert manifest_hashes(song, ("drums/subdivision",)) == before


def test_the_sub_key_still_notices_its_own_change(song):
    from rambass.provenance import manifest_hashes

    before = manifest_hashes(song, ("drums/subdivision",))
    song.drum_subdivision = 3
    assert manifest_hashes(song, ("drums/subdivision",)) != before


def test_a_stage_seven_edit_does_not_make_the_transcription_stale(song):
    from rambass.manifest import Addition, save_song

    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    quantized = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, raw, step="drums transcribe", inputs=[])
    stamp(song, quantized, step="drums clean", inputs=[raw])

    song.drum_additions = [Addition(bar=32, beat=3.0, instrument="crash")]
    save_song(song)

    states = {s.artifact: s for s in stale_report(song)}
    assert states["midi/drums-raw.mid"].state == "ok"
    assert states["midi/drums-quantized.mid"].state == "ok"


def test_a_stage_seven_edit_does_make_the_restore_stale(song):
    from rambass.manifest import Addition, save_song

    consolidated = _touch(song.directory / "midi" / "drums-consolidated.mid")
    restored = _touch(song.directory / "midi" / "drums-restored.mid")
    stamp(song, consolidated, step="drums consolidate", inputs=[])
    stamp(song, restored, step="drums restore", inputs=[consolidated])

    song.drum_additions = [Addition(bar=32, beat=3.0, instrument="crash")]
    save_song(song)

    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-restored.mid"]
    assert entry.state == "stale"
    assert any("additions" in reason for reason in entry.reasons)
