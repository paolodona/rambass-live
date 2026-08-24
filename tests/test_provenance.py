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


def test_an_edited_artifact_stays_edited_when_its_input_goes_stale(song):
    """`edited` outranks the cascade. Both read as "rebuild me", but they demand
    opposite handling: a stale file is safe to regenerate, an edited one holds
    hand work that regeneration destroys. If the cascade downgraded edited to
    stale, a one-key rebuild would flatten a hand edit whenever anything
    upstream of it moved — found writing exactly that rebuild."""
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    quantized = _touch(song.directory / "midi" / "drums-quantized.mid")
    consolidated = _touch(song.directory / "midi" / "drums-consolidated.mid")
    stamp(song, quantized, step="drums clean", inputs=[raw])
    stamp(song, consolidated, step="drums consolidate", inputs=[quantized])
    raw.write_text("changed", encoding="utf-8")
    consolidated.write_text("drawn in reaper", encoding="utf-8")

    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-consolidated.mid"]
    assert entry.state == "edited"
    assert any("drums-quantized" in reason for reason in entry.reasons)


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


def test_changing_the_backbeat_velocity_makes_the_clean_stale(song):
    """The level is stamped during `drums clean`, so a new one is a rebuild —
    and the whole point of keeping it in song.yaml is that this shows up."""
    from rambass.manifest import save_song

    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    quantized = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, raw, step="drums transcribe", inputs=[])
    stamp(song, quantized, step="drums clean", inputs=[raw])

    song.drum_backbeat_velocity = 96
    save_song(song)

    states = {s.artifact: s for s in stale_report(song)}
    assert states["midi/drums-raw.mid"].state == "ok", "the transcription is untouched"
    entry = states["midi/drums-quantized.mid"]
    assert entry.state == "stale"
    assert any("backbeat_velocity" in reason for reason in entry.reasons)


# ── the Reaper project has to get the *finished* part ────────────────────────
#
# `reaper build` defaulted to drums-quantized.mid, which is the part before the
# section vote and before Stage 7. So after doing all of that work the project
# still played the version from three stages back -- silently, and looking
# exactly like a build that had worked.


def test_the_best_variant_is_the_most_finished_one_present(song):
    from rambass.manifest import DRUM_VARIANT_ORDER

    (song.directory / "midi").mkdir(parents=True, exist_ok=True)
    for variant in ("quantized", "consolidated"):
        song.drum_midi_path(variant).write_bytes(b"MThd")
    assert song.best_drum_midi().name == "drums-consolidated.mid"
    song.drum_midi_path("restored").write_bytes(b"MThd")
    assert song.best_drum_midi().name == "drums-restored.mid"
    assert DRUM_VARIANT_ORDER[-1] == "restored"


def test_with_nothing_built_it_falls_back_to_the_conventional_name(song):
    assert song.best_drum_midi().name == "drums-quantized.mid"


def test_raw_is_never_chosen_over_nothing(song):
    """drums-raw.mid is the unquantised take. It is never a backing track, so a
    project that quietly used it would be off the grid and sound broken."""
    (song.directory / "midi").mkdir(parents=True, exist_ok=True)
    song.drum_midi_path("raw").write_bytes(b"MThd")
    assert song.best_drum_midi().name == "drums-quantized.mid"


# ── the warped reference is a derived file too ───────────────────────────────
#
# Found the hard way on the first real re-separation. `rambass stems --drums-only`
# rewrote no_drums.wav, `stale` correctly flagged the whole drum chain, and said
# nothing at all about practice/no_drums-aligned.wav -- which had just become a
# warp of a file that no longer existed. It only got rebuilt because somebody
# remembered, which is precisely the thing this module exists to stop needing.


def test_the_align_map_and_the_warped_reference_are_tracked():
    from rambass.provenance import PIPELINE

    artifacts = [step.artifact for step in PIPELINE]
    assert "practice/align.yaml" in artifacts
    assert "practice/no_drums-aligned.wav" in artifacts


def test_a_new_no_drums_makes_the_warped_reference_stale(song):
    bed = _touch(song.directory / "stems" / "no_drums.wav")
    amap = _touch(song.directory / "practice" / "align.yaml")
    warped = _touch(song.directory / "practice" / "no_drums-aligned.wav")
    stamp(song, warped, step="align warp", inputs=[amap, bed])

    bed.write_text("re-separated", encoding="utf-8")
    entry = {s.artifact: s for s in stale_report(song)}[
        "practice/no_drums-aligned.wav"]
    assert entry.state == "stale"
    assert any("no_drums.wav" in reason for reason in entry.reasons)
    assert "align" in entry.command


def test_refitting_the_map_makes_the_warp_stale_too(song):
    """The chain has to hold here as well: a new map means a new warp."""
    bed = _touch(song.directory / "stems" / "no_drums.wav")
    amap = _touch(song.directory / "practice" / "align.yaml")
    warped = _touch(song.directory / "practice" / "no_drums-aligned.wav")
    stamp(song, amap, step="align fit", inputs=[])
    stamp(song, warped, step="align warp", inputs=[amap, bed])

    amap.write_text("new anchors", encoding="utf-8")
    states = {s.artifact: s for s in stale_report(song)}
    assert states["practice/align.yaml"].state == "edited"
    assert states["practice/no_drums-aligned.wav"].state == "stale"


def test_the_practice_artifacts_never_block_the_drum_chain(song):
    """CLAUDE.md: practice must never gate the gig. So they are reported, and
    nothing downstream of the drums depends on them."""
    from rambass.provenance import PIPELINE, step_for

    for name in ("midi/drums-quantized.mid", "midi/drums-consolidated.mid",
                 "midi/drums-restored.mid"):
        assert not any("practice/" in given for given in step_for(name).inputs)
    warp = step_for("practice/no_drums-aligned.wav")
    assert not any("midi/" in given for given in warp.inputs)
    assert [s.artifact for s in PIPELINE][-1] == "practice/no_drums-aligned.wav"


# ── depend on the value you read, not the file it lives in ───────────────────
#
# Second instance of the same bug class as the `drums` block, so it is worth a
# mechanism rather than another special case. `drums transcribe` reads exactly one
# number out of practice/align.yaml -- `song.align_anchor()`, the bar-1 offset --
# but depended on the whole file. So `rambass align --fit`, which re-fits 300-odd
# anchors and *preserves* bar 1 by default, declared the entire drum chain stale
# for a change that cannot affect a single hit.
#
# A hand correction to bar 1 must still invalidate it. That is the whole reason
# the anchor is in that file.


def test_refitting_the_other_anchors_does_not_touch_the_drum_chain(song, tmp_path):
    from rambass.align import AlignMap, Anchor, save_align

    align = song.path("practice", "align.yaml")
    save_align(align, AlignMap(anchors=[Anchor(bar=1, at=0.692),
                                       Anchor(bar=2, at=4.83)]))
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    stamp(song, raw, step="drums transcribe", inputs=[])

    # A refit: many more anchors, bar 1 untouched.
    save_align(align, AlignMap(anchors=[Anchor(bar=1, at=0.692)]
                               + [Anchor(bar=b, at=0.692 + 4.0 * (b - 1))
                                  for b in range(2, 20)]))
    assert {s.artifact: s for s in stale_report(song)}[
        "midi/drums-raw.mid"].state == "ok"


def test_correcting_bar_one_by_ear_does_make_it_stale(song):
    from rambass.align import AlignMap, Anchor, save_align

    align = song.path("practice", "align.yaml")
    save_align(align, AlignMap(anchors=[Anchor(bar=1, at=0.692)]))
    raw = _touch(song.directory / "midi" / "drums-raw.mid")
    stamp(song, raw, step="drums transcribe", inputs=[])

    save_align(align, AlignMap(anchors=[Anchor(bar=1, at=0.750)]))
    entry = {s.artifact: s for s in stale_report(song)}["midi/drums-raw.mid"]
    assert entry.state == "stale"
    assert any("anchor" in reason for reason in entry.reasons)


def test_the_warp_still_depends_on_the_whole_map(song):
    """It stretches between every anchor, so every anchor matters to it."""
    from rambass.provenance import step_for

    assert "practice/align.yaml" in step_for("practice/no_drums-aligned.wav").inputs
    assert "practice/align.yaml" not in step_for("midi/drums-raw.mid").inputs


def test_an_unknown_scalar_name_is_not_a_crash(song):
    from rambass.provenance import scalar_hashes

    assert scalar_hashes(song, ("nonsense",)) == {}


def test_a_command_cannot_record_an_input_its_step_does_not_declare(song):
    """The two nearly drifted apart the first time. `stamp` records whatever the
    command hands it, while the cascade reasons from `Step.inputs` -- so
    `drums transcribe` kept passing practice/align.yaml after the step had
    stopped declaring it, and the whole drum chain went on reporting stale for a
    refit that preserved bar 1. Whatever a command records has to be something
    its step knows about."""
    from rambass.provenance import PIPELINE, read_stamps

    declared = {step.artifact: set(step.inputs) for step in PIPELINE}
    (song.directory / "midi").mkdir(parents=True, exist_ok=True)
    artifact = song.drum_midi_path("raw")
    artifact.write_bytes(b"MThd")
    stray = song.path("practice", "align.yaml")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("x", encoding="utf-8")

    stamp(song, artifact, step="drums transcribe", inputs=[stray])
    recorded = set(read_stamps(song)["midi/drums-raw.mid"]["inputs"])
    assert recorded <= declared["midi/drums-raw.mid"], (
        f"recorded an undeclared input: {recorded - declared['midi/drums-raw.mid']}")


# ── the placeholders in Step.inputs ──────────────────────────────────────────
#
# `rambass stale` and the whole console dashboard died on this: the stems step
# declares `source/{source_audio}` as its input, and `inputs_for` only ever knew
# how to fill in `{slug}`, so resolving it raised `KeyError: 'source_audio'`.
# It hid for as long as it did because the report `continue`s on a missing
# artifact -- so it needed a *real* stems/drums.wav on disk to fire, and the day
# both albums got their stems separated, `rambass stale`, `rambass stems` and
# every console screen that calls stale_report stopped working at once.


def test_every_placeholder_in_the_pipeline_resolves(song):
    """The one test that would have caught it without a stem on disk: whatever
    a step writes in `artifact` or `inputs`, the resolver has to know the name.
    A placeholder nobody fills in is a KeyError at the first caller."""
    for step in PIPELINE:
        step.artifact_for(song.slug)
        step.inputs_for(song)


def test_the_stems_step_names_the_songs_own_source_mix(song):
    """`source.audio` is per song and never derivable from the slug -- the real
    files are `09 Manlio.wav`, `04_Bambolina_MST1.wav`. That is why the
    placeholder exists, and why resolving it needs the song and not the slug."""
    from rambass.provenance import step_for

    song.source_audio = "09 Manlio.wav"
    assert step_for("stems/drums.wav").inputs_for(song) == ("source/09 Manlio.wav",)


def test_an_undeclared_source_mix_drops_the_input_rather_than_naming_nothing(song):
    """`source/` is not a file. A song with no `source.audio` has nothing to
    fingerprint, so the step declares no input -- it does not declare a
    directory."""
    from rambass.provenance import step_for

    song.source_audio = ""
    assert step_for("stems/drums.wav").inputs_for(song) == ()


def test_a_stems_file_on_disk_does_not_crash_the_report(song):
    """The reproduction. Once the stem exists the report walks past `missing`
    into the cascade check, which is where the input names get resolved."""
    song.source_audio = "03 Tutti In Fila.wav"
    _touch(song.path("stems", "drums.wav"), "riff")
    states = {s.artifact: s for s in stale_report(song)}
    assert "stems/drums.wav" in states


def test_the_source_mix_is_recorded_as_the_stems_input(song):
    """Not just "does not crash": the stamp filters what a command hands it
    against the declared names, so an unresolvable placeholder silently records
    *no* inputs and a re-mastered source mix would never read as stale."""
    from rambass.provenance import read_stamps

    song.source_audio = "03 Tutti In Fila.wav"
    source = _touch(song.path("source", "03 Tutti In Fila.wav"), "mix")
    artifact = _touch(song.path("stems", "drums.wav"), "riff")

    stamp(song, artifact, step="stems", inputs=[source])
    recorded = read_stamps(song)["stems/drums.wav"]["inputs"]
    assert "source/03 Tutti In Fila.wav" in recorded


def test_a_changed_source_mix_makes_the_stems_stale(song):
    """The reason the input is declared at all."""
    song.source_audio = "03 Tutti In Fila.wav"
    source = _touch(song.path("source", "03 Tutti In Fila.wav"), "mix")
    artifact = _touch(song.path("stems", "drums.wav"), "riff")
    stamp(song, artifact, step="stems", inputs=[source])

    source.write_text("remaster", encoding="utf-8")
    entry = {s.artifact: s for s in stale_report(song)}["stems/drums.wav"]
    assert entry.state == "stale"
    assert any("03 Tutti In Fila.wav" in reason for reason in entry.reasons)
