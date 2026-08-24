"""The console's data layer: rebuild selection, step tables, notes, clips.

Paolo: *"if changes are made the app can re-run the underlying rambass commands
so that I can continue editing/testing without any staleness"* — and the safety
rule that makes a one-key rebuild survivable is provenance.py's own: a `stale`
or `missing` artifact is safe to rebuild, an `edited` one is a hand edit whose
risk runs the opposite way, and an `unknown` one cannot be judged at all. The
first tests here pin that split, because it is exactly the kind of rule a later
"simplification" would flatten into "rebuild everything red".
"""

from __future__ import annotations

import pytest

from rambass.provenance import PIPELINE, Staleness, stale_report, stamp, step_for
from rambass.review import (
    rebuild_selection,
    run_rebuild,
    song_screen,
    steps_for,
)


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _entry(artifact, state, command="rambass do-a-thing x"):
    return Staleness(artifact=artifact, step=artifact, command=command, state=state)


# ── the rebuild selector: what a one-key rebuild may touch ───────────────────


def test_only_stale_and_missing_are_auto_rebuilt():
    entries = [
        _entry("a.mid", "ok"),
        _entry("b.mid", "stale"),
        _entry("c.mid", "missing"),
        _entry("d.mid", "edited"),
        _entry("e.mid", "unknown"),
    ]
    auto, held = rebuild_selection(entries)
    assert [e.artifact for e in auto] == ["b.mid", "c.mid"]
    assert [e.artifact for e in held] == ["d.mid", "e.mid"]


def test_the_selection_preserves_pipeline_order():
    entries = [_entry("z.mid", "stale"), _entry("a.mid", "missing"),
               _entry("m.mid", "stale")]
    auto, _ = rebuild_selection(entries)
    assert [e.artifact for e in auto] == ["z.mid", "a.mid", "m.mid"]


def test_an_edited_artifact_is_never_in_the_auto_set_whatever_else_is_true():
    # The one-line version of the rule. If this fails, the rebuild button can
    # silently overwrite a hand edit, which is the failure provenance.py's
    # `edited` state exists to prevent.
    auto, _ = rebuild_selection([_entry("x.mid", "edited")])
    assert auto == []


# ── running the selection ────────────────────────────────────────────────────


def test_run_rebuild_invokes_each_command_in_order(song):
    ran = []
    entries = [_entry("a.mid", "stale", "rambass drums clean x"),
               _entry("b.mid", "missing", "rambass drums restore x")]
    report = run_rebuild(entries, runner=lambda cmd: (ran.append(cmd), (0, ""))[1])
    assert ran == ["rambass drums clean x", "rambass drums restore x"]
    assert report["ran"] == 2 and report["failed"] == ""


def test_run_rebuild_stops_at_the_first_failure(song):
    ran = []
    entries = [_entry("a.mid", "stale", "rambass one x"),
               _entry("b.mid", "stale", "rambass two x"),
               _entry("c.mid", "stale", "rambass three x")]
    report = run_rebuild(
        entries,
        runner=lambda cmd: (ran.append(cmd),
                            (1 if cmd.endswith("two x") else 0, ""))[1])
    assert ran == ["rambass one x", "rambass two x"]
    assert report["ran"] == 1 and report["failed"] == "rambass two x"


def test_run_rebuild_never_receives_held_entries():
    # run_rebuild trusts its input, so the selector is the only gate — feed it
    # the auto side only. This pins the calling convention.
    auto, held = rebuild_selection([_entry("a.mid", "edited")])
    report = run_rebuild(auto, runner=lambda cmd: pytest.fail(f"ran {cmd}"))
    assert report["ran"] == 0
    assert held


# ── {slug} in pipeline artifacts ─────────────────────────────────────────────


def test_new_pipeline_steps_template_the_slug():
    by_name = {s.name: s for s in PIPELINE}
    assert by_name["video ass"].artifact == "video/{slug}.ass"
    assert by_name["video render"].artifact == "video/{slug}.mp4"
    assert by_name["click"].artifact == "render/click.wav"
    assert by_name["gx100 midi"].artifact == "midi/gx100.mid"


def test_a_templated_artifact_resolves_against_the_song(song):
    artifact = _touch(song.directory / "video" / f"{song.slug}.ass")
    stamp(song, artifact, step="video ass", inputs=[])
    entry = {s.artifact: s for s in stale_report(song)}[f"video/{song.slug}.ass"]
    assert entry.state == "ok"


def test_step_for_matches_a_templated_artifact():
    assert step_for("video/tutti-in-fila.ass", slug="tutti-in-fila").name == "video ass"
    assert step_for("video/tutti-in-fila.ass") is None


def test_a_video_render_goes_stale_when_the_subtitles_change(song):
    subtitles = _touch(song.directory / "video" / f"{song.slug}.ass")
    stamp(song, subtitles, step="video ass", inputs=[])
    rendered = _touch(song.directory / "video" / f"{song.slug}.mp4")
    stamp(song, rendered, step="video render", inputs=[subtitles])
    subtitles.write_text("different", encoding="utf-8")
    states = {s.artifact: s for s in stale_report(song)}
    assert states[f"video/{song.slug}.mp4"].state == "stale"


def test_a_click_goes_stale_when_the_tempo_changes(song):
    from rambass.manifest import save_song

    artifact = _touch(song.directory / "render" / "click.wav")
    stamp(song, artifact, step="click", inputs=[])
    song.bpm = 61.0
    save_song(song)
    entry = {s.artifact: s for s in stale_report(song)}["render/click.wav"]
    assert entry.state == "stale"


def test_a_gx100_midi_ignores_a_lyrics_edit(song):
    from rambass.manifest import save_song

    artifact = _touch(song.directory / "midi" / "gx100.mid")
    stamp(song, artifact, step="gx100 midi", inputs=[])
    song.lyrics_file = "different.md"
    save_song(song)
    entry = {s.artifact: s for s in stale_report(song)}["midi/gx100.mid"]
    assert entry.state == "ok"


def test_an_a_cappella_song_still_has_nothing_to_rebuild(song):
    from rambass.manifest import save_song

    song.drums_origin = "a-cappella"
    save_song(song)
    assert stale_report(song) == []


def test_a_backing_track_song_keeps_its_video_and_gx100_steps(song):
    from rambass.manifest import save_song

    song.drums_origin = "backing-track"
    save_song(song)
    names = {s.step for s in stale_report(song)}
    assert "video ass" in names and "gx100 midi" in names and "click" in names
    assert "drums clean" not in names


# ── the step tables: what each stage screen lists ────────────────────────────


def test_an_extracted_song_gets_the_full_drum_sequence(song):
    labels = [row.label for row in steps_for(song, "drums")]
    assert any("ranscribe" in label for label in labels)
    assert any("onsolidate" in label for label in labels)
    assert any("estore" in label for label in labels)


def test_the_review_tool_step_is_an_open_not_a_run(song):
    rows = steps_for(song, "drums")
    opens = [row for row in rows if row.kind == "open"]
    assert len(opens) == 1 and "estore" in opens[0].label


def test_a_backing_track_song_has_no_drum_steps(song):
    song.drums_origin = "backing-track"
    assert steps_for(song, "drums") == []


def test_an_a_cappella_song_needs_a_title_card_and_nothing_else(song):
    # CLAUDE.md: `a-cappella` is not an unfinished state.
    song.drums_origin = "a-cappella"
    assert steps_for(song, "drums") == []
    assert steps_for(song, "render") == []
    video = steps_for(song, "video")
    assert len(video) == 1 and "card" in video[0].label.lower()


def test_every_run_step_command_mentions_the_song(song):
    for stage in ("drums", "render", "lyrics", "video", "gx100"):
        for row in steps_for(song, stage):
            if row.kind == "run":
                assert song.slug in row.command, (stage, row.label)


# ── the CLI command ──────────────────────────────────────────────────────────


def _rebuildable(song):
    """One stamped-then-stale artifact and one hand-edited one."""
    source = _touch(song.directory / "midi" / "drums-raw.mid")
    stale = _touch(song.directory / "midi" / "drums-quantized.mid")
    stamp(song, stale, step="drums clean", inputs=[source])
    source.write_text("re-transcribed", encoding="utf-8")

    edited = _touch(song.directory / "midi" / "drums-consolidated.mid")
    stamp(song, edited, step="drums consolidate", inputs=[stale])
    edited.write_text("drawn in reaper", encoding="utf-8")
    return stale, edited


def test_rebuild_dry_run_prints_the_commands_and_runs_nothing(cwd_song, capsys,
                                                              monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    _rebuildable(cwd_song)
    monkeypatch.setattr(
        review_module, "subprocess_runner",
        lambda cwd: pytest.fail("dry run must not build a runner"))
    assert main(["review", "rebuild", cwd_song.slug, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"rambass drums clean {cwd_song.slug}" in out


def test_rebuild_runs_the_auto_set_and_reports_the_held(cwd_song, capsys,
                                                        monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    _rebuildable(cwd_song)
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda cmd: (ran.append(cmd), (0, ""))[1])
    assert main(["review", "rebuild", cwd_song.slug]) == 0
    assert any("drums clean" in cmd for cmd in ran)
    # The hand edit is reported with stale's own wording, never run.
    assert not any("drums consolidate" in cmd for cmd in ran)
    out = capsys.readouterr().out
    assert "edited" in out.lower()


def test_rebuild_force_backs_the_file_up_first(cwd_song, monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    _, edited = _rebuildable(cwd_song)
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda cmd: (ran.append(cmd), (0, ""))[1])
    assert main(["review", "rebuild", cwd_song.slug,
                 "--force", "midi/drums-consolidated.mid"]) == 0
    backup = edited.with_suffix(".mid.bak")
    assert backup.read_text(encoding="utf-8") == "drawn in reaper"
    assert any("drums consolidate" in cmd for cmd in ran)


def test_rebuild_force_refuses_an_artifact_that_is_not_held(cwd_song):
    from rambass.cli import main

    _rebuildable(cwd_song)
    # drums-quantized is stale, not edited/unknown: --force is not how it is
    # rebuilt, and accepting it would teach the habit of forcing everything.
    assert main(["review", "rebuild", cwd_song.slug,
                 "--force", "midi/drums-quantized.mid"]) == 2


def test_rebuild_stops_and_fails_loudly_when_a_command_fails(cwd_song,
                                                             monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    _rebuildable(cwd_song)
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda cmd: (1, "boom"))
    assert main(["review", "rebuild", cwd_song.slug]) == 1


# ── clip spans: where a section is, on both clocks ───────────────────────────


class _FakeMap:
    """An alignment map by duck type: reference = grid seconds + 1, times 1.05.

    A deliberate rate != 1.0, so a test that confused the two clocks would not
    accidentally pass.
    """

    mode = "piecewise"

    def source_at(self, grid_seconds, timeline):
        return 1.0 + grid_seconds * 1.05


class _OffsetMap(_FakeMap):
    mode = "offset"

    def source_at(self, grid_seconds, timeline):
        return 1.0 + grid_seconds


def test_a_mid_bar_section_starts_where_the_timeline_says(song):
    from rambass.manifest import Section
    from rambass.review import clip_spans

    song.sections = [Section("verse-2", 9, beat=3.0), Section("chorus", 17)]
    spans = {s.name: s for s in clip_spans(song, _FakeMap())}
    timeline = song.timeline()
    assert spans["verse-2"].candidate_start == pytest.approx(
        timeline.bar_beat_to_seconds(9, 3.0))
    assert spans["verse-2"].duration == pytest.approx(
        timeline.bar_beat_to_seconds(17, 1.0) - timeline.bar_beat_to_seconds(9, 3.0))


def test_the_candidate_clock_ignores_the_count_in(song):
    """The Stage 9 bounce starts at musical bar 1 — no count-in in the file.
    Using audio_time here would shift every candidate clip by the count-in
    length, which is CLAUDE.md's two-clocks mistake arriving through a new
    door. So: editing count_in.bars must not move a candidate clip."""
    from rambass.review import clip_spans

    before = clip_spans(song, _FakeMap())[0].candidate_start
    song.count_in_bars = 7
    after = clip_spans(song, _FakeMap())[0].candidate_start
    assert before == after


def test_the_reference_clock_comes_from_the_alignment_map(song):
    from rambass.review import clip_spans

    span = clip_spans(song, _FakeMap())[-1]
    timeline = song.timeline()
    grid = timeline.bar_beat_to_seconds(span.start_bar, span.start_beat)
    assert span.reference_start == pytest.approx(1.0 + grid * 1.05)
    assert span.reference_duration == pytest.approx(span.duration * 1.05)
    assert not span.approximate


def test_an_offset_only_map_is_flagged_approximate(song):
    from rambass.review import clip_spans

    assert all(s.approximate for s in clip_spans(song, _OffsetMap()))


def test_no_alignment_at_all_names_the_command_that_fixes_it(song):
    from rambass.project import ProjectError
    from rambass.review import clip_spans

    class NoMap:
        mode = "none"

    with pytest.raises(ProjectError, match="align"):
        clip_spans(song, NoMap())


def test_the_last_section_runs_to_the_end_of_the_part(song):
    from rambass.review import clip_spans

    last = clip_spans(song, _FakeMap())[-1]
    timeline = song.timeline()
    # `bars` is the band's part (CLAUDE.md): the last span ends at its end.
    assert last.candidate_start + last.duration == pytest.approx(
        timeline.bar_beat_to_seconds(song.bars + 1, 1.0))


def test_a_song_with_no_sections_is_one_span(song):
    from rambass.review import clip_spans

    song.sections = []
    spans = clip_spans(song, _FakeMap())
    assert len(spans) == 1 and spans[0].start_bar == 1


# ── the notes ledger ─────────────────────────────────────────────────────────


def test_notes_round_trip_through_yaml(song, tmp_path):
    from rambass.review import Note, load_review, save_review

    path = tmp_path / "review.yaml"
    note = Note(bar=43, beat=1.0, section="verse-2", kind="missing-hit",
                instrument="crash", velocity=105,
                comment="no crash going into the lift", created="2026-08-24")
    save_review(path, [note], version="drums-quantized.mid")
    version, notes = load_review(path)
    assert version == "drums-quantized.mid"
    assert notes == [note]


def test_a_missing_review_file_is_an_empty_ledger(tmp_path):
    from rambass.review import load_review

    version, notes = load_review(tmp_path / "nope.yaml")
    assert version == "" and notes == []


def test_promote_turns_a_missing_hit_into_an_addition(song):
    from rambass.review import Note, promote_notes

    notes = [Note(bar=43, kind="missing-hit", instrument="crash",
                  velocity=105, comment="into the lift")]
    promoted, skipped = promote_notes(song, notes)
    assert promoted == 1 and skipped == 0
    assert notes[0].status == "promoted"
    addition = song.drum_additions[-1]
    assert (addition.bar, addition.instrument, addition.velocity) == (43, "crash", 105)


def test_promote_turns_an_extra_hit_into_a_removal(song):
    from rambass.review import Note, promote_notes

    notes = [Note(bar=26, beat=3.0, kind="extra-hit", instrument="hihat_closed",
                  comment="sounds doubled")]
    promoted, _ = promote_notes(song, notes)
    assert promoted == 1
    removal = song.drum_removals[-1]
    assert (removal.bar, removal.beat, removal.instrument) == (26, 3.0, "hihat_closed")


def test_promote_leaves_the_unconfident_kinds_for_a_human(song):
    """A timing complaint is not a MIDI edit, and a missing hit with no named
    instrument is a listening job — same split as restore.propose_additions."""
    from rambass.review import Note, promote_notes

    notes = [
        Note(bar=1, kind="timing", comment="rushes"),
        Note(bar=2, kind="missing-hit", comment="something here"),  # no instrument
        Note(bar=3, kind="missing-hit", instrument="crash", status="dismissed"),
        Note(bar=4, kind="missing-hit", instrument="crash", status="promoted"),
        Note(bar=5, kind="missing-hit", instrument="not-a-drum", velocity=90),
    ]
    promoted, skipped = promote_notes(song, notes)
    assert promoted == 0 and skipped == 5
    assert song.drum_additions == [] and song.drum_removals == []
    assert notes[0].status == "open"


def test_promote_does_not_double_an_existing_addition(song):
    from rambass.manifest import Addition
    from rambass.review import Note, promote_notes

    song.drum_additions = [Addition(bar=43, beat=1.0, instrument="crash")]
    notes = [Note(bar=43, kind="missing-hit", instrument="crash", velocity=90)]
    promoted, skipped = promote_notes(song, notes)
    assert promoted == 0 and skipped == 1
    assert len(song.drum_additions) == 1


def test_the_markdown_speaks_reaper_numbers(song):
    """Same rule as restore.checklist: the stored data is musical, the document
    read next to the ruler adds count_in.bars once, at the edge."""
    from rambass.review import Note, review_markdown

    text = review_markdown(
        song.title, [Note(bar=43, kind="missing-hit", instrument="crash",
                          comment="into the lift")],
        count_in_bars=2)
    assert "45.1" in text and "43" not in text.replace("45.1", "")


# ── the notes CLI ────────────────────────────────────────────────────────────


def test_review_note_writes_the_ledger(cwd_song):
    from rambass.cli import main
    from rambass.review import load_review

    assert main(["review", "note", cwd_song.slug, "43",
                 "no crash going into the lift",
                 "--kind", "missing-hit", "--instrument", "crash",
                 "--velocity", "105"]) == 0
    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert len(notes) == 1
    assert notes[0].bar == 43 and notes[0].instrument == "crash"
    assert notes[0].section == "chorus"          # derived from the section list
    assert cwd_song.path("qa", "review.md").exists()


def test_review_promote_moves_notes_into_the_manifest(cwd_song):
    from rambass.cli import main
    from rambass.manifest import load_song
    from rambass.review import load_review

    main(["review", "note", cwd_song.slug, "43", "into the lift",
          "--kind", "missing-hit", "--instrument", "crash", "--velocity", "105"])
    assert main(["review", "promote", cwd_song.slug]) == 0
    reloaded = load_song(cwd_song.directory)
    assert len(reloaded.drum_additions) == 1
    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert notes[0].status == "promoted"


def test_review_status_counts_open_notes(cwd_song, capsys):
    from rambass.cli import main

    main(["review", "note", cwd_song.slug, "43", "x",
          "--kind", "missing-hit", "--instrument", "crash"])
    assert main(["review", "status"]) == 0
    assert "1 open" in capsys.readouterr().out


def test_review_note_reaper_bar_subtracts_the_count_in(cwd_song):
    from rambass.cli import main
    from rambass.review import load_review

    assert main(["review", "note", cwd_song.slug, "45", "x",
                 "--reaper-bar"]) == 0
    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert notes[0].bar == 45 - cwd_song.count_in_bars


def test_clips_without_a_candidate_says_where_to_bounce_one(cwd_song, capsys):
    from rambass.cli import main

    assert main(["review", "clips", cwd_song.slug]) == 2
    assert "qa/candidate.wav" in capsys.readouterr().err


def test_clips_without_a_stem_names_the_stems_command(cwd_song, capsys):
    from rambass.cli import main

    _touch(cwd_song.path("qa", "candidate.wav"), "not-really-audio")
    assert main(["review", "clips", cwd_song.slug]) == 2
    assert "rambass stems" in capsys.readouterr().err


# ── the instrument grid, and the section export ──────────────────────────────


def _perf(song):
    from rambass.midiio import DrumPerformance, Hit

    timeline = song.timeline()
    at = timeline.bar_beat_to_seconds
    hits = [
        Hit("kick", at(9, 1.0), 100),
        Hit("snare", at(9, 3.0), 104),
        Hit("hihat_closed", at(10, 1.0), 80),
        Hit("kick", at(20, 1.0), 100),      # outside verse (bars 9-16)
    ]
    return DrumPerformance(hits, timeline)


def _span(song, name="verse", start=9, end=17):
    from rambass.review import ClipSpan

    timeline = song.timeline()
    a, b = timeline.bar_beat_to_seconds(start, 1.0), timeline.bar_beat_to_seconds(end, 1.0)
    return ClipSpan(name=name, start_bar=start, start_beat=1.0,
                    end_bar=end, end_beat=1.0, candidate_start=a,
                    duration=b - a, reference_start=a, reference_duration=b - a,
                    approximate=False)


def test_grid_rows_filter_to_the_span_and_keep_canonical_order(song):
    from rambass.review import grid_rows

    rows = grid_rows(_perf(song), _span(song))
    assert [row["instrument"] for row in rows] == ["kick", "snare", "hihat_closed"]
    kick = rows[0]["ticks"]
    assert kick == [{"bar": 9, "beat": 1.0, "velocity": 100}]


def test_section_performance_shifts_the_slice_to_zero(song):
    from rambass.review import section_performance

    sliced = section_performance(_perf(song), _span(song))
    assert len(sliced.hits) == 3
    assert min(hit.time for hit in sliced.hits) == pytest.approx(0.0)
    # EZdrummer's browser plays a groove from its own zero; a slice that
    # kept absolute song time would import with nine bars of silence.


# ── step state for the rows provenance does not track ────────────────────────
#
# A row with no provenance artifact used to report `state: ""`, which the
# console's `stepIcon` renders as the same grey dot it uses for a stage that
# does not apply. So "the mix is in place" and "not applicable" looked
# identical, and — worse — the analyze row always showed a todo dot with a Run
# button next to it, which is what invited the click that overwrote Manlio's
# settled 60.0 BPM with a re-detected 60.02. These rows carry their own state.


def test_the_source_row_reads_the_file_on_disk(song):
    """The mix's presence *is* the completion test for that step."""
    (song.dir / "source").mkdir(parents=True, exist_ok=True)
    assert steps_for(song, "source")[0].state == "missing"
    (song.dir / "source" / "09 Manlio.wav").write_bytes(b"RIFF----WAVE")
    assert steps_for(song, "source")[0].state == "ok"


def test_the_source_row_honours_an_explicit_source_audio(song):
    """`source.audio` names the file; a name that is not there is not ok."""
    (song.dir / "source").mkdir(parents=True, exist_ok=True)
    (song.dir / "source" / "09 Manlio.wav").write_bytes(b"RIFF----WAVE")
    song.source_audio = "not-here.wav"
    assert steps_for(song, "source")[0].state == "missing"
    song.source_audio = "09 Manlio.wav"
    assert steps_for(song, "source")[0].state == "ok"


def test_the_analyze_row_reads_the_manifests_own_status(song):
    """A settled tempo must not read as an unfinished step."""
    song.status["analyze"] = "todo"
    assert steps_for(song, "analyze")[0].state == "missing"
    song.status["analyze"] = "done"
    assert steps_for(song, "analyze")[0].state == "ok"


def test_the_optional_a_cappella_reference_stays_untracked(song):
    """Optional means optional: it must not nag with a todo dot."""
    song.drums_origin = "a-cappella"
    assert steps_for(song, "source")[0].state == ""


def test_song_screen_carries_a_rows_own_state_when_provenance_has_none(song):
    (song.dir / "source").mkdir(parents=True, exist_ok=True)
    (song.dir / "source" / "mix.wav").write_bytes(b"RIFF----WAVE")
    song.status["analyze"] = "done"
    screens = {s["stage"]: s for s in song_screen(song, [])["screens"]}
    assert screens["source"]["steps"][0]["state"] == "ok"
    assert screens["analyze"]["steps"][0]["state"] == "ok"


def test_a_row_naming_an_untracked_artifact_falls_back_to_the_file(song):
    """`render/sticks.wav` is the one row that names an artifact no
    :data:`~rambass.provenance.PIPELINE` step produces, so it never got an
    entry and read as the grey n/a dot -- for a file sitting on disk next to
    the click track. Existence is the only fact available for it, so use it."""
    (song.dir / "render").mkdir(parents=True, exist_ok=True)
    sticks = [r for r in steps_for(song, "render") if "sticks" in r.artifact]
    assert len(sticks) == 1, "the count-in row stopped naming render/sticks.wav"

    screens = {s["stage"]: s for s in song_screen(song, [])["screens"]}
    row = [s for s in screens["render"]["steps"] if "sticks" in s["artifact"]][0]
    assert row["state"] == "missing"

    (song.dir / "render" / "sticks.wav").write_bytes(b"RIFF----WAVE")
    screens = {s["stage"]: s for s in song_screen(song, [])["screens"]}
    row = [s for s in screens["render"]["steps"] if "sticks" in s["artifact"]][0]
    assert row["state"] == "ok"


def test_a_tracked_artifact_still_takes_its_state_from_provenance(song):
    """The disk fallback must not shout over a real verdict: a click track that
    provenance calls `unknown` stays `unknown`, not `ok`, however present it is."""
    from rambass.provenance import Staleness

    (song.dir / "render").mkdir(parents=True, exist_ok=True)
    (song.dir / "render" / "click.wav").write_bytes(b"RIFF----WAVE")
    report = [Staleness(artifact="render/click.wav", step="click",
                        command="rambass click x", state="unknown",
                        reasons=["no provenance recorded"])]
    screens = {s["stage"]: s for s in song_screen(song, report)["screens"]}
    row = [s for s in screens["render"]["steps"] if "click" in s["artifact"]][0]
    assert row["state"] == "unknown"


def test_a_manual_row_with_no_artifact_stays_untracked(song):
    """"Bounce the base" is a human act with nothing to look for. It keeps the
    grey dot -- that dot is only wrong when something *could* have been checked."""
    rows = {r.label: r for r in steps_for(song, "render")}
    bounce = [r for r in rows.values() if "ounce" in r.label][0]
    assert bounce.artifact == "" and bounce.state == ""


# ── which clip source is missing, and whether a button can make it ───────────


def test_clip_sources_reports_each_side_independently(cwd_song):
    """A missing candidate must not be reported as a missing reference.

    The two sides fail for unrelated reasons and have unrelated fixes: the
    stem is one command away, the candidate is a bounce out of a DAW. One
    combined error message is what left both canvases blank with the same
    string while the stem was already on disk."""
    from rambass.review import clip_sources

    _touch(cwd_song.path("stems", "drums.wav"), "not-really-audio")
    sources = clip_sources(cwd_song)

    assert sources["ref"].path == cwd_song.path("stems", "drums.wav")
    assert sources["cand"].path is None
    assert "qa/candidate.wav" in sources["cand"].hint


def test_only_the_reference_side_offers_a_command(cwd_song):
    """`rambass stems` makes the stem; nothing here makes the bounce.

    The candidate is a render through the kit in a DAW, so a Run button for it
    would be a lie. The reference's command is the same string the stems step
    row puts on the song screen, which is what `run_step_command` whitelists."""
    from rambass.review import clip_sources, steps_for

    sources = clip_sources(cwd_song)
    assert sources["cand"].command == ""
    assert sources["ref"].command == f"rambass stems {cwd_song.slug} --drums-only"
    assert sources["ref"].command in {
        row.command for row in steps_for(cwd_song, "stems")}


# ── what a command said, where the click happened ────────────────────────────
#
# `subprocess_runner` returned only an exit code, so a refusal or a traceback
# went to the terminal `rambass console` was started from and the browser said
# nothing but "FAIL". The reason has to reach the page: the guard on
# `analyze --write` is worthless if the console shows a red row and no cause.


def test_the_runner_returns_the_commands_output_with_its_code(tmp_path):
    from rambass.review import subprocess_runner

    run = subprocess_runner(tmp_path)
    code, output = run("rambass --version")
    assert code == 0 and output.strip(), "no output captured"


def test_the_runner_captures_a_failures_message(tmp_path):
    """`ProjectError` is printed to stderr and is exactly what a reader needs."""
    from rambass.review import subprocess_runner

    code, output = subprocess_runner(tmp_path)("rambass show no-such-song")
    assert code != 0
    assert "could not find" in output, "stderr never reached the caller"


def test_the_runner_still_echoes_to_the_terminal(tmp_path, capfd):
    """Capturing must not silence the terminal it used to print to."""
    from rambass.review import subprocess_runner

    subprocess_runner(tmp_path)("rambass --version")
    assert capfd.readouterr().out.strip(), "the terminal went quiet"


def test_run_rebuild_keeps_each_commands_output():
    entries = [_entry("a.mid", "stale", "rambass one x"),
               _entry("b.mid", "stale", "rambass two x")]

    def runner(command):
        return (1, "REFUSED: 60.02 is not 60.0") if "two" in command else (0, "fine")

    report = run_rebuild(entries, runner=runner)
    assert report["failed"].endswith("two x")
    assert "REFUSED" in report["outputs"][report["failed"]]


def test_rebuild_song_reports_the_output_of_what_it_ran(song, monkeypatch):
    from rambass import review as review_module

    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda command: (0, f"did {command}"))
    _touch(song.dir / "source" / "mix.wav")
    song.source_audio = "mix.wav"
    result = review_module.rebuild_song(song, project_root=song.dir.parent)
    assert set(result["outputs"]) <= set(result["commands"])
    for command, text in result["outputs"].items():
        assert text == f"did {command}"


# ── a step filter that selects a held artifact ───────────────────────────────
#
# Paolo clicked Run on Manlio's click-track row -- `render/click.wav`, on disk
# with no provenance, so `unknown` -> held. The POST answered 200 with an empty
# command list, the page re-rendered the same "no provenance" sentence, and
# nothing anywhere said why. `--step <held>` reads the same way in a terminal:
# "nothing stale or missing" is exactly the wrong sentence for a file the same
# report has just called un-provenanced.


def _held_click(song):
    """A bounced-by-hand click track: on disk, unstamped, so `unknown`."""
    return _touch(song.directory / "render" / "click.wav", "bounced by hand")


def test_a_step_filter_reports_only_that_steps_hold(cwd_song):
    from rambass.review import rebuild_song

    _held_click(cwd_song)
    _touch(cwd_song.directory / "midi" / "gx100.mid", "typed in by hand")
    result = rebuild_song(cwd_song, project_root=cwd_song.directory.parent,
                          step="click", dry_run=True)
    assert result["commands"] == []
    # Not the whole held set: the gx100 file is held too, and listing it under
    # a click-track button is how a reader concludes the button did something
    # to it.
    assert [h["artifact"] for h in result["held"]] == ["render/click.wav"]


def test_a_held_step_says_how_to_run_it_anyway(cwd_song, capsys, monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    _held_click(cwd_song)
    monkeypatch.setattr(
        review_module, "subprocess_runner",
        lambda cwd: pytest.fail("a held step must not run without --force"))
    assert main(["review", "rebuild", cwd_song.slug, "--step", "click"]) == 0
    out = capsys.readouterr().out
    assert "nothing stale or missing" not in out
    assert "render/click.wav" in out
    assert "--force render/click.wav" in out


def test_forcing_a_held_step_runs_that_one_command(cwd_song, monkeypatch):
    from rambass import review as review_module
    from rambass.cli import main

    click = _held_click(cwd_song)
    ran = []
    monkeypatch.setattr(review_module, "subprocess_runner",
                        lambda cwd: lambda cmd: (ran.append(cmd), (0, ""))[1])
    assert main(["review", "rebuild", cwd_song.slug, "--step", "click",
                 "--force", "render/click.wav"]) == 0
    assert ran == [f"rambass click {cwd_song.slug}"]
    assert click.with_suffix(".wav.bak").read_text(
        encoding="utf-8") == "bounced by hand"


def test_a_dry_run_force_keeps_its_hands_off_the_file(cwd_song, monkeypatch):
    """`--dry-run` prints; it does not write. The backup was copied before the
    dry-run check, so asking what a force *would* do left a .bak behind."""
    from rambass import review as review_module
    from rambass.cli import main

    click = _held_click(cwd_song)
    monkeypatch.setattr(
        review_module, "subprocess_runner",
        lambda cwd: pytest.fail("dry run must not build a runner"))
    assert main(["review", "rebuild", cwd_song.slug, "--step", "click",
                 "--force", "render/click.wav", "--dry-run"]) == 0
    assert not click.with_suffix(".wav.bak").exists()
