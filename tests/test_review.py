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
    """It needs ears, not a report. There are two openers on this screen now --
    the sections editor is the other -- so each has to name its own route
    rather than the page assuming the only one is the review tool."""
    rows = steps_for(song, "drums")
    opens = {row.target: row for row in rows if row.kind == "open"}
    assert set(opens) == {"review", "sections"}
    assert "estore" in opens["review"].label
    assert all(row.open_label for row in opens.values())


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


# ── retiring a note ─────────────────────────────────────────────────────────
#
# Paolo: *"there is a "[open]" string that does nothing, also how do I remove a
# wrong note?"*. The string was the note's `status` rendered as decoration, and
# there was no answer to the second question at all: `NOTE_STATUSES` has had
# `dismissed` since the ledger was written and nothing could set it.
#
# Two different acts, deliberately kept apart. **Dismiss** is a judgement --
# "I listened, it was nothing" -- and it is a record worth keeping, which is
# why `review_markdown` already has a `[-]` mark for it. **Remove** is for an
# entry that should never have existed, filed at the wrong bar or against the
# wrong instrument, and it leaves nothing behind.


def _ledger(song):
    from rambass.review import load_review

    return load_review(song.path("qa", "review.yaml"))[1]


def _note(bar, **kwargs):
    from rambass.review import Note

    return Note(bar=bar, **kwargs)


def test_a_notes_key_tells_two_notes_at_one_bar_apart():
    """The ledger is a hand-editable YAML file and has no ids, so identity is
    the fields a human filed. Two notes at the same bar about different things
    are two notes, and a client has to be able to name one of them."""
    from rambass.review import note_key

    crash = _note(9, kind="missing-hit", instrument="crash")
    splash = _note(9, kind="missing-hit", instrument="splash")
    timing = _note(9, kind="timing", comment="late")

    assert note_key(crash) != note_key(splash) != note_key(timing)
    assert note_key(crash) == note_key(_note(9, kind="missing-hit",
                                             instrument="crash"))


def test_removing_a_note_rewrites_both_files(song):
    """The YAML is the source of truth and the markdown is for reading beside
    the ruler; a removal that left the note in one of them would be worse than
    no removal at all."""
    from rambass.review import add_note, note_key, remove_note

    add_note(song, _note(9, kind="missing-hit", instrument="crash",
                         comment="keep me"))
    add_note(song, _note(17, kind="timing", comment="wrong bar, delete"))
    doomed = next(n for n in _ledger(song) if n.bar == 17)

    left = remove_note(song, note_key(doomed))

    assert [n.bar for n in left] == [9]
    assert [n.bar for n in _ledger(song)] == [9]
    text = song.path("qa", "review.md").read_text(encoding="utf-8")
    assert "wrong bar" not in text and "keep me" in text


def test_removing_only_takes_one_of_two_identical_notes(song):
    """Removing one of two identical entries must not take both.

    `add_note` cannot make a pair like this any more (one observation, one
    note), so this writes the ledger directly — which is exactly how a pair
    still arrives: `qa/review.yaml` is a hand-editable file, and one filed
    before the de-duping existed reads the same way."""
    from rambass.review import note_key, remove_note, write_ledger

    write_ledger(song, [_note(9, kind="extra-hit", instrument="crash"),
                        _note(9, kind="extra-hit", instrument="crash")])

    left = remove_note(song, note_key(_ledger(song)[0]))
    assert len(left) == 1


def test_removing_a_note_that_is_not_there_says_so(song):
    from rambass.project import ProjectError
    from rambass.review import remove_note

    with pytest.raises(ProjectError) as caught:
        remove_note(song, "9|1|missing-hit|crash|")
    assert "no such note" in str(caught.value)


def test_a_promoted_note_cannot_be_quietly_removed(song):
    """Its edit is in `song.yaml` now, and `drums restore` applies that blindly.
    Removing the ledger entry would leave the addition behind with nothing on
    record saying where it came from -- so the refusal names what to remove
    instead, the way every other ProjectError here does."""
    from rambass.manifest import save_song
    from rambass.project import ProjectError
    from rambass.review import (
        add_note,
        load_review,
        note_key,
        promote_notes,
        remove_note,
        write_ledger,
    )

    add_note(song, _note(9, beat=3.0, kind="missing-hit", instrument="crash"))
    version, notes = load_review(song.path("qa", "review.yaml"))
    promote_notes(song, notes)
    save_song(song)
    write_ledger(song, notes, version=version)

    promoted = _ledger(song)[0]
    assert promoted.status == "promoted"
    with pytest.raises(ProjectError) as caught:
        remove_note(song, note_key(promoted))
    message = str(caught.value)
    assert "drums.additions" in message and "9.3" in message


def test_dismissing_a_note_keeps_it_and_stops_it_promoting(song):
    """The record of having listened is the point: `review_markdown` marks it
    `[-]`, and `promote` only ever touches an open note."""
    from rambass.review import (
        add_note,
        load_review,
        note_key,
        promote_notes,
        set_note_status,
    )

    add_note(song, _note(9, kind="missing-hit", instrument="crash",
                         comment="thought I heard a crash"))
    set_note_status(song, note_key(_ledger(song)[0]), "dismissed")

    kept = _ledger(song)
    assert len(kept) == 1 and kept[0].status == "dismissed"
    assert "- [-]" in song.path("qa", "review.md").read_text(encoding="utf-8")

    version, notes = load_review(song.path("qa", "review.yaml"))
    assert promote_notes(song, notes) == (0, 1)
    assert not song.drum_additions


def test_a_dismissed_note_can_be_reopened(song):
    """Changing your mind is the normal case in a review pass."""
    from rambass.review import add_note, note_key, set_note_status

    add_note(song, _note(9, kind="timing"))
    key = note_key(_ledger(song)[0])
    set_note_status(song, key, "dismissed")
    set_note_status(song, key, "open")

    assert _ledger(song)[0].status == "open"


def test_a_status_the_ledger_does_not_know_is_refused(song):
    from rambass.project import ProjectError
    from rambass.review import add_note, note_key, set_note_status

    add_note(song, _note(9, kind="timing"))
    with pytest.raises(ProjectError) as caught:
        set_note_status(song, note_key(_ledger(song)[0]), "maybe")
    assert "open, promoted, dismissed" in str(caught.value)


# ── the review loop has to actually close ────────────────────────────────────
#
# Paolo: *"once I have added a note, how is that processed? should I hit
# "rebuild?" and the notes should be incorporated?"*. The honest answer was no,
# and nothing said so. `drums restore` watches `drums/additions`, so promoting
# a note does make `midi/drums-restored.mid` stale and Rebuild does regenerate
# it -- but `qa/candidate.wav` is not in `PIPELINE` and the cut clips in
# `qa/clips/` were cached forever, so the A/B went on playing the audio from
# before the edit. Promote, rebuild, hear no change, conclude the note did
# nothing.


def test_a_candidate_older_than_the_midi_is_reported_as_stale(song):
    """The one fact the review screen needs in order not to lie: the audio it
    is about to play is older than the part it claims to be showing."""
    import os

    from rambass.review import candidate_state

    midi = song.drum_midi_path("restored")
    midi.parent.mkdir(parents=True, exist_ok=True)
    midi.write_bytes(b"MThd")
    candidate = song.path("qa", "candidate.wav")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"RIFF")

    # Rendered after the MIDI: current.
    os.utime(candidate, (os.path.getmtime(midi) + 10,) * 2)
    state = candidate_state(song)
    assert state["fresh"] is True and state["exists"] is True

    # ...and then the MIDI is rebuilt under it.
    os.utime(midi, (os.path.getmtime(candidate) + 10,) * 2)
    state = candidate_state(song)
    assert state["fresh"] is False
    assert "drums-restored.mid" in state["why"]
    assert state["command"] == f"rambass review render {song.slug}"


def test_a_missing_candidate_is_not_called_stale(song):
    """Missing and out of date are different things, and the screen already has
    a per-side "no clip yet" message for the first one."""
    from rambass.review import candidate_state

    state = candidate_state(song)
    assert state["exists"] is False and state["fresh"] is False


def test_rendering_a_candidate_is_a_step_the_console_may_run(song):
    """`run_step_command` will only run what a stage screen put a button on, so
    the review screen's re-render button has to come from the same list -- the
    two cannot be allowed to drift."""
    from rambass.provenance import stale_report
    from rambass.review import runnable_commands

    assert (f"rambass review render {song.slug}"
            in runnable_commands(song, stale_report(song)))


# ── the lyrics rows: two ways to get cues, neither of them provenanced ───────


def test_the_whisper_row_reads_the_draft_on_disk(song):
    """`lyrics transcribe` writes `lyrics.draft.srt` and nothing in PIPELINE,
    so provenance has no verdict for the row and it read as a todo dot for
    ever — including straight after a successful three-minute Whisper run on
    Manlio that printed 25 cues and wrote the file. The draft's presence is
    the completion test, exactly as the mix's is for `source`."""
    rows = {r.label: r for r in steps_for(song, "lyrics")}
    assert rows["Draft cues with Whisper"].state == "missing"
    _touch(song.path("lyrics.draft.srt"), "1\n00:00:01,000 --> 00:00:02,000\nx\n")
    rows = {r.label: r for r in steps_for(song, "lyrics")}
    assert rows["Draft cues with Whisper"].state == "ok"


def test_a_hand_timed_file_makes_the_whisper_row_not_applicable(song):
    """The two rows are alternatives — "import a hand-timed file or draft one
    below". With an imported SRT there is nothing to draft, so the draft row
    goes grey (n/a), not green: it was never run."""
    _touch(song.path("lyrics.srt"), "1\n00:00:01,000 --> 00:00:02,000\nx\n")
    rows = {r.label: r for r in steps_for(song, "lyrics")}
    assert rows["Timed cues in lyrics.srt"].state == "ok"
    assert rows["Draft cues with Whisper"].state == ""


def test_the_lyrics_md_template_is_not_a_timed_cue_file(song):
    """`rambass new` leaves a `lyrics.md` stub behind, so `lyrics_path()` is
    non-None for a song with no words at all. Only the hand-timed subtitle
    formats tick this row, or every song in the repo reads as done."""
    _touch(song.path("lyrics.md"), "# Tutti In Fila\n\n[bar 1]\n")
    rows = {r.label: r for r in steps_for(song, "lyrics")}
    assert rows["Timed cues in lyrics.srt"].state == "missing"
    assert rows["Draft cues with Whisper"].state == "missing"


# ── the band layer: the same section with the rest of the band under it ──────
#
# Paolo: *"I want to listen to the candidate drums in context ... a switch that
# layers all the other instruments on top that uses the most appropriate
# version (ref aligned or original)"*. "Most appropriate" is not a preference:
# the candidate sits on the fixed grid and the reference is the take that
# breathes, so each side has exactly one bed that lines up with it, and pairing
# them the other way flams by up to a quarter second (docs/practice-tracks.md).


def test_each_side_gets_the_only_bed_that_lines_up_with_it(cwd_song):
    from rambass.review import clip_sources

    _touch(cwd_song.path("practice", "no_drums-aligned.wav"), "not-really-audio")
    _touch(cwd_song.path("stems", "no_drums.wav"), "not-really-audio")
    sources = clip_sources(cwd_song)

    assert sources["cand-band"].path == cwd_song.path(
        "practice", "no_drums-aligned.wav")
    assert sources["ref-band"].path == cwd_song.path("stems", "no_drums.wav")


def test_the_candidate_never_borrows_the_unwarped_bed(cwd_song):
    """The album mix under the candidate drums is the whole bug this avoids.

    Manlio drifts -88..+258 ms across the song, so the unwarped bed is a
    quarter second out by the end -- a reviewer would hear the *band* flam and
    file it against the programmed part. Missing is the honest answer, and the
    hint names the command that makes it."""
    from rambass.review import clip_sources

    _touch(cwd_song.path("stems", "no_drums.wav"), "not-really-audio")
    band = clip_sources(cwd_song)["cand-band"]

    assert not band.available
    assert "practice/no_drums-aligned.wav" in band.hint
    assert "--warp" in band.hint


def test_only_the_recorded_bed_offers_a_command(cwd_song):
    """`stems --drums-only` writes no_drums.wav too, and the console already
    whitelists that string. The warp is a practice step and deliberately not on
    any stage screen (`review.STAGE_OF_STEP`: practice must never gate the
    gig), so the console cannot offer a button for it -- the hint is the fix."""
    from rambass.review import clip_sources, steps_for

    sources = clip_sources(cwd_song)
    assert sources["ref-band"].command == (
        f"rambass stems {cwd_song.slug} --drums-only")
    assert sources["ref-band"].command in {
        row.command for row in steps_for(cwd_song, "stems")}
    assert sources["cand-band"].command == ""


def test_a_band_clip_is_cut_on_the_clock_of_the_side_it_layers(song):
    """Two clocks, and the band layer does not get a third one. The warped bed
    is on the fixed grid (`align.warp_plan` targets `bar_beat_to_seconds`, so
    its first sample is musical bar 1, exactly like the candidate render); the
    recorded bed is the original file the reference clips come from."""
    from rambass.review import clip_offsets, clip_spans

    span = clip_spans(song, _FakeMap())[1]

    assert clip_offsets(span, "cand-band") == clip_offsets(span, "cand")
    assert clip_offsets(span, "ref-band") == clip_offsets(span, "ref")
    assert clip_offsets(span, "cand") == (span.candidate_start, span.duration)
    assert clip_offsets(span, "ref") == (span.reference_start,
                                         span.reference_duration)


def test_a_clip_name_says_which_side_cut_it(song):
    """The route reads the side back off the file name, and it used to do it
    with `endswith("-cand.wav") else ref` -- which calls every band clip a
    reference and would have cut the warped bed on the recording's clock."""
    from rambass.review import clip_name, clip_spans, side_of_clip

    span = clip_spans(song, _FakeMap())[0]
    for side in ("cand", "ref", "cand-band", "ref-band"):
        assert side_of_clip(clip_name(span, side)) == side
    assert side_of_clip("nonsense.wav") is None


def test_a_section_named_after_a_side_still_resolves(song):
    """`slugify` puts the section name in the same string as the side, so a
    section called "ref" or "cand-band" must not be able to rename the side."""
    from rambass.manifest import Section
    from rambass.review import clip_name, clip_spans, side_of_clip

    song.sections = [Section("cand-band", 1), Section("ref", 9)]
    for span in clip_spans(song, _FakeMap()):
        for side in ("cand", "ref", "cand-band", "ref-band"):
            assert side_of_clip(clip_name(span, side)) == side


# ── swap-hit: one note that means "right place, wrong drum" ──────────────────
#
# Paolo: *"a hit may be correct but with the wrong item, for example swapping
# an open hi-hat to a crash or a crash to a crash2"*. `restore.apply_edits`
# already calls that the commonest edit there is -- removals run before
# additions precisely so a replacement is two lines of YAML -- but the ledger
# had no way to say it in one note, so a swap took two notes that promote could
# not tell were one decision.


def test_a_swap_promotes_to_a_removal_and_an_addition_at_one_position(song):
    from rambass.review import promote_notes

    note = _note(9, beat=3.0, kind="swap-hit", instrument="hihat_open",
                 swap_to="crash", comment="section start")
    promoted, skipped = promote_notes(song, [note])

    assert (promoted, skipped) == (1, 0)
    assert [(r.bar, r.beat, r.instrument) for r in song.drum_removals] == [
        (9, 3.0, "hihat_open")]
    assert [(a.bar, a.beat, a.instrument) for a in song.drum_additions] == [
        (9, 3.0, "crash")]
    assert note.status == "promoted"


def test_a_swap_survives_restore_as_one_hit_of_the_new_instrument(song):
    """End to end, because the point of the kind is the part that comes out:
    `apply_edits` removes first, so the swapped-in hit is not deleted by its
    own swap -- and it lands at the position the old hit had."""
    from rambass.midiio import Hit
    from rambass.restore import apply_edits
    from rambass.review import promote_notes

    promote_notes(song, [_note(9, beat=3.0, kind="swap-hit",
                               instrument="hihat_open", swap_to="crash")])
    timeline = song.timeline()
    at = timeline.bar_beat_to_seconds(9, 3.0)
    performance = _perf(song)
    performance.hits.append(Hit("hihat_open", at, 90))
    out, report = apply_edits(performance, additions=song.drum_additions,
                              removals=song.drum_removals)

    landed = sorted(h.instrument for h in out.hits if abs(h.time - at) < 0.03)
    assert landed == ["crash", "snare"]
    assert report["removed"] == 1 and report["added"] == 1


def test_a_swap_with_no_target_is_left_for_a_human(song):
    """A swap is confident because *both* ends are named. Without the second
    instrument it is a `wrong-instrument` observation, and promote must not
    guess -- the same rule as a missing-hit with no instrument."""
    from rambass.review import promote_notes

    note = _note(9, kind="swap-hit", instrument="hihat_open")
    assert promote_notes(song, [note]) == (0, 1)
    assert note.status == "open"
    assert not song.drum_removals and not song.drum_additions


def test_a_swap_to_the_same_drum_changes_nothing_and_is_skipped(song):
    from rambass.review import promote_notes

    assert promote_notes(song, [_note(9, kind="swap-hit", instrument="crash",
                                      swap_to="crash")]) == (0, 1)
    assert not song.drum_removals and not song.drum_additions


def test_a_swap_to_something_that_is_not_a_drum_is_skipped(song):
    from rambass.review import promote_notes

    assert promote_notes(song, [_note(9, kind="swap-hit", instrument="crash",
                                      swap_to="kazoo")]) == (0, 1)
    assert not song.drum_additions


def test_promoting_a_swap_twice_does_not_double_the_edit(song):
    from rambass.review import promote_notes

    first = _note(9, kind="swap-hit", instrument="hihat_open", swap_to="crash")
    promote_notes(song, [first])
    second = _note(9, kind="swap-hit", instrument="hihat_open", swap_to="crash")
    assert promote_notes(song, [second]) == (0, 1)
    assert len(song.drum_removals) == 1 and len(song.drum_additions) == 1


def test_a_swap_round_trips_through_the_ledger(song, tmp_path):
    from rambass.review import Note, load_review, save_review

    path = tmp_path / "review.yaml"
    save_review(path, [Note(bar=9, kind="swap-hit", instrument="hihat_open",
                            swap_to="crash")], version="x.mid")
    _, notes = load_review(path)
    assert notes[0].swap_to == "crash"
    assert "swap_to" in path.read_text(encoding="utf-8")


def test_two_swaps_of_one_hit_to_different_drums_are_two_notes(song):
    """The key is the fields a human filed, and the target is one of them --
    without it the console could only ever name one of the two."""
    from rambass.review import note_key

    to_crash = _note(9, kind="swap-hit", instrument="hihat_open",
                     swap_to="crash")
    to_china = _note(9, kind="swap-hit", instrument="hihat_open",
                     swap_to="china")
    assert note_key(to_crash) != note_key(to_china)


def test_a_promoted_swap_names_both_lists_when_it_cannot_be_removed(song):
    """`remove_note` refuses a promoted note and says where its edit went. A
    swap went to both lists, and naming only one of them sends the reader off
    to delete half an edit."""
    from rambass.project import ProjectError
    from rambass.review import add_note, note_key, promote_notes, remove_note, write_ledger

    note = _note(9, kind="swap-hit", instrument="hihat_open", swap_to="crash")
    add_note(song, note)
    promote_notes(song, [note])
    write_ledger(song, [note])

    with pytest.raises(ProjectError) as caught:
        remove_note(song, note_key(note))
    assert "drums.removals" in str(caught.value)
    assert "drums.additions" in str(caught.value)


def test_the_markdown_shows_a_swap_as_an_arrow(song):
    from rambass.review import review_markdown

    text = review_markdown("X", [_note(9, kind="swap-hit",
                                       instrument="hihat_open",
                                       swap_to="crash")], count_in_bars=2)
    assert "hihat_open" in text and "crash" in text and "→" in text


def test_review_note_files_a_swap_from_the_terminal(cwd_song):
    from rambass.cli import main
    from rambass.review import load_review

    assert main(["review", "note", cwd_song.slug, "9.3", "wrong cymbal",
                 "--kind", "swap-hit", "--instrument", "hihat_open",
                 "--swap-to", "crash"]) == 0
    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert notes[0].swap_to == "crash" and notes[0].instrument == "hihat_open"


# ── demote: promote is not a one-way door ───────────────────────────────────
#
# Paolo: *"ability to demote promoted notes in case I have by mistake promoted
# a wrong one"*. Until now `promote` was irreversible from the tool -- the note
# went to `promoted`, the chip lost its controls, and `remove_note` refused it
# and told you to go and edit song.yaml by hand. That is the one moment in the
# loop where the answer was "open the manifest", which is exactly what the
# console exists to avoid.


def test_demoting_a_missing_hit_takes_its_addition_back_out(song):
    from rambass.review import demote_note, note_key, promote_notes

    note = _note(9, beat=3.0, kind="missing-hit", instrument="crash")
    promote_notes(song, [note])
    demoted, removed = demote_note(song, [note], note_key(note))

    assert demoted.status == "open"
    assert removed == 1
    assert not song.drum_additions


def test_demoting_a_swap_takes_both_edits_back_out(song):
    from rambass.review import demote_note, note_key, promote_notes

    note = _note(9, beat=3.0, kind="swap-hit", instrument="hihat_open",
                 swap_to="crash")
    promote_notes(song, [note])
    _, removed = demote_note(song, [note], note_key(note))

    assert removed == 2
    assert not song.drum_additions and not song.drum_removals


def test_demoting_leaves_every_other_edit_alone(song):
    """One note, one position, one instrument — a demote that matched on bar
    alone would take out the crash somebody added by hand at the same bar."""
    from rambass.manifest import Addition
    from rambass.review import demote_note, note_key, promote_notes

    keep = Addition(bar=9, beat=3.0, instrument="splash", note="by hand")
    song.drum_additions = [keep]
    note = _note(9, beat=3.0, kind="missing-hit", instrument="crash")
    promote_notes(song, [note])
    demote_note(song, [note], note_key(note))

    assert [a.instrument for a in song.drum_additions] == ["splash"]


def test_demoting_a_note_whose_edit_has_already_gone_still_reopens_it(song):
    """The edit can be gone for a good reason — somebody deleted the line in
    song.yaml. Refusing then would leave a note stuck in `promoted` with
    nothing behind it, which is the state that is actually wrong."""
    from rambass.review import demote_note, note_key, promote_notes

    note = _note(9, kind="missing-hit", instrument="crash")
    promote_notes(song, [note])
    song.drum_additions = []
    demoted, removed = demote_note(song, [note], note_key(note))

    assert demoted.status == "open" and removed == 0


def test_demoting_a_note_that_was_never_promoted_says_so(song):
    from rambass.project import ProjectError
    from rambass.review import demote_note, note_key

    note = _note(9, kind="missing-hit", instrument="crash")
    with pytest.raises(ProjectError, match="not promoted"):
        demote_note(song, [note], note_key(note))


def test_a_demoted_note_can_be_promoted_again(song):
    """Round trip, because the point is to fix a mis-click and carry on."""
    from rambass.review import demote_note, note_key, promote_notes

    note = _note(9, kind="missing-hit", instrument="crash")
    promote_notes(song, [note])
    demote_note(song, [note], note_key(note))
    assert promote_notes(song, [note]) == (1, 0)
    assert len(song.drum_additions) == 1


def test_removing_a_promoted_note_now_names_demote(song):
    """The old message sent the reader to edit song.yaml by hand. There is a
    command for it now, and the refusal should name it."""
    from rambass.project import ProjectError
    from rambass.review import add_note, note_key, promote_notes, remove_note, write_ledger

    note = _note(9, kind="missing-hit", instrument="crash")
    add_note(song, note)
    promote_notes(song, [note])
    write_ledger(song, [note])

    with pytest.raises(ProjectError, match="demote"):
        remove_note(song, note_key(note))


def test_review_demote_from_the_terminal(cwd_song):
    from rambass.cli import main
    from rambass.manifest import load_song
    from rambass.review import load_review

    assert main(["review", "note", cwd_song.slug, "9.3", "crash here",
                 "--kind", "missing-hit", "--instrument", "crash"]) == 0
    assert main(["review", "promote", cwd_song.slug]) == 0
    assert load_song(cwd_song.directory).drum_additions

    assert main(["review", "demote", cwd_song.slug, "9.3"]) == 0
    assert not load_song(cwd_song.directory).drum_additions
    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert notes[0].status == "open"


def test_review_demote_takes_a_reaper_bar_like_every_other_command(cwd_song):
    from rambass.cli import main
    from rambass.manifest import load_song

    main(["review", "note", cwd_song.slug, "9.3", "crash here",
          "--kind", "missing-hit", "--instrument", "crash"])
    main(["review", "promote", cwd_song.slug])
    assert main(["review", "demote", cwd_song.slug,
                 f"{9 + cwd_song.count_in_bars}.3", "--reaper-bar"]) == 0
    assert not load_song(cwd_song.directory).drum_additions


# ── done: which sections have already been listened to ──────────────────────
#
# Paolo: *"ability to mark a section as Done so that when I reopen the project
# I know I can skip it (with ability to reopen it if needed)"*. It lives in
# qa/review.yaml with the notes -- same sidecar, same reason: it is a record of
# what a human did during a review pass, not a musical fact about the song, and
# it must not go anywhere `drums restore` reads.
#
# Keyed by **position**, not by name. Two sections can share a name (CLAUDE.md:
# identical names are one part), so a name would tick both from one listen; and
# a section that moves is a section whose clips were re-cut, which is exactly
# when the mark should not follow it.


def test_marking_a_section_done_survives_a_reload(song):
    from rambass.review import load_done, set_section_done

    set_section_done(song, 9, 1.0, True)
    done = load_done(song.path("qa", "review.yaml"))

    assert [(d["bar"], d["beat"]) for d in done] == [(9, 1.0)]
    assert done[0]["section"] == "verse"


def test_a_section_can_be_reopened(song):
    from rambass.review import load_done, set_section_done

    set_section_done(song, 9, 1.0, True)
    set_section_done(song, 9, 1.0, False)
    assert load_done(song.path("qa", "review.yaml")) == []


def test_marking_the_same_section_twice_does_not_double_it(song):
    from rambass.review import load_done, set_section_done

    set_section_done(song, 9, 1.0, True)
    set_section_done(song, 9, 1.0, True)
    assert len(load_done(song.path("qa", "review.yaml"))) == 1


def test_two_sections_at_different_bars_are_marked_apart(song):
    """Position is the identity, so a repeated section name cannot tick a
    section nobody listened to."""
    from rambass.manifest import Section
    from rambass.review import load_done, set_section_done

    song.sections = [Section("chorus", 9), Section("chorus", 17)]
    set_section_done(song, 9, 1.0, True)
    done = load_done(song.path("qa", "review.yaml"))

    assert [(d["bar"], d["section"]) for d in done] == [(9, "chorus")]


def test_a_note_written_afterwards_keeps_the_done_marks(song):
    """`write_ledger` rewrites the whole file, so a note saved after a section
    was ticked would have wiped every tick -- the ledger is one file and both
    halves of it have to survive a write to the other."""
    from rambass.review import add_note, load_done, set_section_done

    set_section_done(song, 9, 1.0, True)
    add_note(song, _note(9, kind="timing", comment="late"))

    assert len(load_done(song.path("qa", "review.yaml"))) == 1


def test_a_done_mark_survives_a_promote(song):
    from rambass.review import add_note, load_done, promote_notes, set_section_done, write_ledger

    set_section_done(song, 9, 1.0, True)
    note = _note(9, kind="missing-hit", instrument="crash")
    add_note(song, note)
    promote_notes(song, [note])
    write_ledger(song, [note])

    assert len(load_done(song.path("qa", "review.yaml"))) == 1


def test_the_markdown_says_which_sections_are_reviewed(song):
    """qa/review.md is the file read next to the ruler, so "what can I skip"
    belongs in it — in Reaper numbers, like every other bar in that file."""
    from rambass.review import review_markdown

    text = review_markdown("X", [], count_in_bars=2,
                           done=[{"bar": 9, "beat": 1.0, "section": "verse"}])
    assert "verse" in text and "11.1" in text


# ── the three steps that are always one act ─────────────────────────────────
#
# Paolo: *"when I make changes in the review tool I generally add/remove/swap
# hits. Then I need to promote them, rebuild and re-render. It's three actions
# that are always in sequence ... one button that does everything and reloads
# the page on the same section so I can listen to the updated version"*.
#
# They stay available separately -- promote alone is how you read the git diff
# before anything touches the MIDI, rebuild alone is for a change that came
# from somewhere else (a section edit, a re-transcription), and re-render alone
# is for a candidate that is stale against a MIDI nobody has to rebuild. What
# was missing is the sequence itself, which is the one you run every time.


def _runner_log(monkeypatch, ran, code=0):
    from rambass import review as review_module

    def runner(cwd):
        def run(command):
            ran.append(command)
            return (code, f"did {command}")
        return run

    monkeypatch.setattr(review_module, "subprocess_runner", runner)


def test_the_one_act_promotes_then_rebuilds_then_renders_last(cwd_song,
                                                              monkeypatch):
    from rambass.manifest import load_song
    from rambass.review import add_note, load_review, promote_rebuild_render

    ran: list[str] = []
    _runner_log(monkeypatch, ran)
    add_note(cwd_song, _note(9, beat=3.0, kind="missing-hit",
                             instrument="crash"))
    result = promote_rebuild_render(cwd_song,
                                    project_root=cwd_song.directory.parent)

    assert result["promoted"] == 1
    # Promote first, and on disk: the rebuild runs `drums restore` as a
    # subprocess, which reads song.yaml -- an addition still only in memory
    # would be rebuilt away.
    assert load_song(cwd_song.directory).drum_additions
    assert load_review(cwd_song.path("qa", "review.yaml"))[1][0].status \
        == "promoted"
    # And the render is last, or it renders the part from before the rebuild.
    assert ran[-1] == f"rambass review render {cwd_song.slug}"
    assert len(ran) > 1


def test_a_failed_rebuild_does_not_go_on_to_render(cwd_song, monkeypatch):
    """Rendering the part that failed to rebuild is worse than not rendering:
    it produces audio that looks current and is not."""
    from rambass.review import add_note, promote_rebuild_render

    ran: list[str] = []
    _runner_log(monkeypatch, ran, code=1)
    add_note(cwd_song, _note(9, kind="missing-hit", instrument="crash"))
    result = promote_rebuild_render(cwd_song,
                                    project_root=cwd_song.directory.parent)

    assert result["failed"]
    assert f"rambass review render {cwd_song.slug}" not in ran


def test_nothing_to_rebuild_and_a_fresh_candidate_renders_nothing(cwd_song,
                                                                  monkeypatch):
    """A quarter of an hour of VST render for a click that changed nothing is
    not a no-op, so the sequence has to be able to say "already current"."""
    from rambass import review as review_module
    from rambass.review import promote_rebuild_render

    ran: list[str] = []
    _runner_log(monkeypatch, ran)
    monkeypatch.setattr(review_module, "rebuild_song",
                        lambda song, **kwargs: {
                            "song": song.slug, "dry_run": False, "commands": [],
                            "ran": 0, "failed": "", "outputs": {}, "backup": "",
                            "held": []})
    monkeypatch.setattr(review_module, "candidate_state",
                        lambda song: {"exists": True, "fresh": True, "why": "",
                                      "command": "rambass review render x"})
    result = promote_rebuild_render(cwd_song,
                                    project_root=cwd_song.directory.parent)

    assert ran == []
    assert result["rendered"] is False
    assert "current" in result["why"]


def test_the_one_act_still_rebuilds_when_there_was_nothing_to_promote(cwd_song,
                                                                      monkeypatch):
    """A hand edit typed into song.yaml is a perfectly good reason to press it,
    and an empty ledger must not turn the button into a no-op."""
    from rambass.review import promote_rebuild_render

    ran: list[str] = []
    _runner_log(monkeypatch, ran)
    result = promote_rebuild_render(cwd_song,
                                    project_root=cwd_song.directory.parent)

    assert result["promoted"] == 0
    assert ran and ran[-1] == f"rambass review render {cwd_song.slug}"


# ── a removal cannot delete a hit that drums.additions puts there ───────────
#
# Paolo, on Manlio: *"I have a promoted note 11.4.667 swap-hit crash →
# hihat_open but the midi now includes both the hihat_open and the crash"*.
#
# The crash at that position was never in the transcription: `drums missing
# --propose` had put it in `drums.additions`. `apply_edits` runs removals
# first — which is right when the hit is in the part it is given — so the swap's
# removal matched nothing, and then the addition list duly added both the crash
# and the hihat_open. The part came out with two hits where there should be one,
# and the promotion looked like it had worked.
#
# A removal cannot reach a declared hit, so the fix is not to write one: the
# swap edits the declaration it is about. That also makes the git diff say what
# happened — one line changing instrument, instead of a removal that
# contradicts an addition three screens further up the file.


def _added(song):
    return [(a.bar, a.beat, a.instrument, a.velocity) for a in song.drum_additions]


def _removed(song):
    return [(r.bar, r.beat, r.instrument) for r in song.drum_removals]


def test_swapping_a_declared_hit_rewrites_the_declaration(song):
    from rambass.manifest import Addition
    from rambass.review import promote_notes

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104,
                                    note="proposed by drums missing")]
    note = _note(9, beat=4.667, kind="swap-hit", instrument="crash",
                 swap_to="hihat_open")
    assert promote_notes(song, [note]) == (1, 0)

    assert _added(song) == [(9, 4.667, "hihat_open", 104)]
    assert _removed(song) == [], "a removal cannot delete a declared hit"
    assert "crash" in song.drum_additions[0].note, "the diff loses the story"


def test_swapping_a_declared_hit_leaves_one_hit_in_the_part(song):
    """The bug as Paolo met it, end to end: the crash was in `additions`, so
    the part came out with the crash *and* the hi-hat."""
    from rambass.manifest import Addition
    from rambass.restore import apply_edits
    from rambass.review import promote_notes

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104)]
    promote_notes(song, [_note(9, beat=4.667, kind="swap-hit",
                               instrument="crash", swap_to="hihat_open")])
    out, _ = apply_edits(_perf(song), additions=song.drum_additions,
                         removals=song.drum_removals)

    at = song.timeline().bar_beat_to_seconds(9, 4.667)
    assert sorted(h.instrument for h in out.hits
                  if abs(h.time - at) < 0.03) == ["hihat_open"]


def test_swapping_a_transcribed_hit_still_writes_the_pair(song):
    """The other half has not changed: a hit that came from the MIDI is not
    declared anywhere, so a removal is exactly the way to take it out."""
    from rambass.review import promote_notes

    promote_notes(song, [_note(9, beat=3.0, kind="swap-hit",
                               instrument="hihat_open", swap_to="crash")])

    assert _removed(song) == [(9, 3.0, "hihat_open")]
    assert _added(song) == [(9, 3.0, "crash", 0)]


def test_removing_a_declared_hit_retracts_the_declaration(song):
    """Same bug, the simpler half: "remove this crash" against a crash that
    `drums.additions` puts there wrote a removal that could never match, and
    the crash stayed in the part."""
    from rambass.manifest import Addition
    from rambass.review import promote_notes

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104)]
    assert promote_notes(song, [_note(9, beat=4.667, kind="extra-hit",
                                      instrument="crash")]) == (1, 0)

    assert _added(song) == [] and _removed(song) == []


def test_demoting_a_swap_of_a_declared_hit_puts_the_drum_back(song):
    """Exactly reversible, because the note carries both names: the swap moved
    one field and the demote moves it back, velocity and all."""
    from rambass.manifest import Addition
    from rambass.review import demote_note, note_key, promote_notes

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104)]
    note = _note(9, beat=4.667, kind="swap-hit", instrument="crash",
                 swap_to="hihat_open")
    promote_notes(song, [note])
    _, undone = demote_note(song, [note], note_key(note))

    assert undone == 1
    assert _added(song) == [(9, 4.667, "crash", 104)]
    assert note.status == "open"


def test_demoting_a_removed_declaration_restores_it(song):
    """The retracted addition comes back with what the note knows about it --
    which is why the grid menu files the hit's own velocity on an extra-hit."""
    from rambass.manifest import Addition
    from rambass.review import demote_note, note_key, promote_notes

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104)]
    note = _note(9, beat=4.667, kind="extra-hit", instrument="crash",
                 velocity=104)
    promote_notes(song, [note])
    _, undone = demote_note(song, [note], note_key(note))

    assert undone == 1
    assert _added(song) == [(9, 4.667, "crash", 104)]


def test_a_contradicting_pair_written_by_hand_is_reported(song):
    """Nothing promote writes can produce one any more, but a hand-edited
    manifest can -- and silently producing the hit anyway is what made this
    bug invisible for a whole review pass. Left working (a removal plus an
    addition of the same drum is how you re-voice a transcribed hit's
    velocity), but no longer silent."""
    from rambass.manifest import Addition, Removal
    from rambass.restore import apply_edits

    song.drum_additions = [Addition(bar=9, beat=4.667, instrument="crash",
                                    velocity=104)]
    song.drum_removals = [Removal(bar=9, beat=4.667, instrument="crash")]
    _, report = apply_edits(_perf(song), additions=song.drum_additions,
                            removals=song.drum_removals)

    assert report["contradicted"] == [(9, 4.667, "crash")]
    # And it is not also reported as stale: it matched something, just not a
    # hit, and two complaints about one line send the reader in two directions.
    assert report["stale_removals"] == []


# ── one observation, one note ───────────────────────────────────────────────
#
# Paolo: *"ensure we cannot create duplicated notes ... trying to remove the
# same hit twice should not yield a duplication in notes (silent de-duping per
# note/type/instrument/time)"*. Easy to do twice now that a note can be filed
# from the grid with two clicks and no typing, and a second copy says nothing
# the first did not: `promote` skips it as already done, and the ledger reads
# as two problems where there is one.
#
# The comment is deliberately NOT part of the identity. Two people looking at
# the same missing crash write two different sentences about it, and it is
# still one missing crash.


def test_the_same_observation_filed_twice_is_one_note(song):
    from rambass.review import add_note

    add_note(song, _note(9, beat=2.667, kind="extra-hit", instrument="snare",
                         comment="from the grid at 20.2.667"))
    notes = add_note(song, _note(9, beat=2.667, kind="extra-hit",
                                 instrument="snare",
                                 comment="from the grid at 20.2.667"))

    assert len(notes) == 1
    assert len(_ledger(song)) == 1


def test_a_different_comment_is_still_the_same_observation(song):
    from rambass.review import add_note

    add_note(song, _note(9, kind="missing-hit", instrument="crash",
                         comment="into the lift"))
    notes = add_note(song, _note(9, kind="missing-hit", instrument="crash",
                                 comment="crash missing here"))

    assert len(notes) == 1
    assert notes[0].comment == "into the lift", "the first wording stands"


def test_a_note_that_says_something_else_is_not_a_duplicate(song):
    """Everything the identity is made of, one at a time -- because a dedupe
    that is too eager silently eats a real second note."""
    from rambass.review import add_note

    add_note(song, _note(9, beat=2.0, kind="missing-hit", instrument="crash"))
    for other in (_note(9, beat=2.667, kind="missing-hit", instrument="crash"),
                  _note(10, beat=2.0, kind="missing-hit", instrument="crash"),
                  _note(9, beat=2.0, kind="extra-hit", instrument="crash"),
                  _note(9, beat=2.0, kind="missing-hit", instrument="splash"),
                  # A swap of a *different* drum: one whose source matched the
                  # crash above would be a correction to it, and `merge_notes`
                  # folds those -- which is a different rule, tested there.
                  _note(9, beat=2.0, kind="swap-hit", instrument="ride",
                        swap_to="china")):
        add_note(song, other)
    assert len(_ledger(song)) == 6


def test_two_swaps_of_one_hit_to_different_drums_are_both_kept(song):
    from rambass.review import add_note

    add_note(song, _note(9, kind="swap-hit", instrument="crash",
                         swap_to="china"))
    notes = add_note(song, _note(9, kind="swap-hit", instrument="crash",
                                 swap_to="splash"))
    assert len(notes) == 2


def test_filing_a_dismissed_observation_again_reopens_it(song):
    """"I listened and it was nothing" is a decision about a note, and filing
    the same thing again is a decision about the same note -- so it comes back
    rather than being swallowed by a chip the eye reads as struck out."""
    from rambass.review import add_note, note_key, set_note_status

    note = _note(9, kind="missing-hit", instrument="crash")
    add_note(song, note)
    set_note_status(song, note_key(note), "dismissed")
    notes = add_note(song, _note(9, kind="missing-hit", instrument="crash"))

    assert len(notes) == 1 and notes[0].status == "open"


def test_filing_a_promoted_observation_again_leaves_it_promoted(song):
    """Its edit is in song.yaml already; re-filing it is genuinely a no-op,
    and reopening it would offer to promote what is already promoted."""
    from rambass.review import add_note, promote_notes, write_ledger

    note = _note(9, kind="missing-hit", instrument="crash")
    add_note(song, note)
    promote_notes(song, [note])
    write_ledger(song, [note])
    notes = add_note(song, _note(9, kind="missing-hit", instrument="crash"))

    assert len(notes) == 1 and notes[0].status == "promoted"


def test_a_second_filing_fills_in_a_comment_the_first_one_lacked(song):
    """Two clicks in the grid file no words at all; if the same thing is later
    written down with a reason, the ledger should keep the reason."""
    from rambass.review import add_note

    add_note(song, _note(9, kind="missing-hit", instrument="crash"))
    notes = add_note(song, _note(9, kind="missing-hit", instrument="crash",
                                 comment="the section starts here"))

    assert len(notes) == 1
    assert notes[0].comment == "the section starts here"


def test_review_note_says_when_it_added_nothing(cwd_song, capsys):
    from rambass.cli import main
    from rambass.review import load_review

    args = ["review", "note", cwd_song.slug, "9.3", "crash here",
            "--kind", "missing-hit", "--instrument", "crash"]
    assert main(args) == 0
    assert main(args) == 0

    assert "already noted" in capsys.readouterr().out
    assert len(load_review(cwd_song.path("qa", "review.yaml"))[1]) == 1


# ── a note that only exists to correct an earlier one ───────────────────────
#
# Paolo: *"can notes be merged where appropriate ... First I have added a
# tom_mid, then swapped with tom_high, those two notes can be replaced with
# missing-hit tom_high"*. Right: the pair is not two observations, it is one
# observation and a correction to it, and what the part needs is the net effect.
#
# Only when both halves are in the same state, though. A promoted missing-hit
# whose swap is still open describes a manifest that says `tom_mid` -- collapsing
# them then would make the ledger claim `tom_high` was promoted when it was not.
# Once the swap is promoted too, the declaration has already been rewritten
# (`promote_notes` edits it in place), so the merged note is exactly true.


def test_a_swap_of_a_note_you_filed_collapses_into_it(song):
    from rambass.review import merge_notes

    added = _note(9, beat=2.667, kind="missing-hit", instrument="tom_mid")
    swap = _note(9, beat=2.667, kind="swap-hit", instrument="tom_mid",
                 swap_to="tom_high", comment="from the grid: tom_mid → tom_high")
    kept, merged = merge_notes([added, swap])

    assert merged == 1
    assert [(n.kind, n.instrument, n.swap_to) for n in kept] == [
        ("missing-hit", "tom_high", "")]


def test_the_merge_does_not_depend_on_the_order_in_the_file(song):
    """The ledger is sorted by position, so two notes at one position keep
    whatever order they were written in -- on Manlio the swap came first."""
    from rambass.review import merge_notes

    added = _note(9, beat=2.667, kind="missing-hit", instrument="tom_mid")
    swap = _note(9, beat=2.667, kind="swap-hit", instrument="tom_mid",
                 swap_to="tom_high")
    kept, merged = merge_notes([swap, added])

    assert merged == 1
    assert [(n.kind, n.instrument) for n in kept] == [
        ("missing-hit", "tom_high")]


def test_a_chain_of_swaps_collapses_to_where_it_ended_up(song):
    """Two goes at naming the same cymbal is one decision, not three."""
    from rambass.review import merge_notes

    kept, merged = merge_notes([
        _note(9, kind="missing-hit", instrument="crash"),
        _note(9, kind="swap-hit", instrument="crash", swap_to="crash_2"),
        _note(9, kind="swap-hit", instrument="crash_2", swap_to="china")])

    assert merged == 2
    assert [(n.kind, n.instrument) for n in kept] == [("missing-hit", "china")]


def test_swaps_of_a_transcribed_hit_chain_into_one_swap(song):
    """No missing-hit to fold into: the hit is the transcription's, and what
    the ledger should say is that it ends up as the last drum named."""
    from rambass.review import merge_notes

    kept, merged = merge_notes([
        _note(9, kind="swap-hit", instrument="hihat_open", swap_to="crash"),
        _note(9, kind="swap-hit", instrument="crash", swap_to="crash_2")])

    assert merged == 1
    assert [(n.kind, n.instrument, n.swap_to) for n in kept] == [
        ("swap-hit", "hihat_open", "crash_2")]


def test_a_promoted_note_and_an_open_swap_are_left_alone(song):
    """The manifest still says the old drum, so a merged note would lie about
    what has been promoted."""
    from rambass.review import merge_notes

    added = _note(9, kind="missing-hit", instrument="tom_mid")
    added.status = "promoted"
    swap = _note(9, kind="swap-hit", instrument="tom_mid", swap_to="tom_high")
    kept, merged = merge_notes([added, swap])

    assert merged == 0 and len(kept) == 2


def test_two_promoted_halves_do_collapse(song):
    """Which is Paolo's case: by then `promote` has rewritten the declaration
    in place, so `missing-hit tom_high (promoted)` is exactly what song.yaml
    says."""
    from rambass.review import merge_notes

    added = _note(9, beat=2.667, kind="missing-hit", instrument="tom_mid")
    swap = _note(9, beat=2.667, kind="swap-hit", instrument="tom_mid",
                 swap_to="tom_high")
    added.status = swap.status = "promoted"
    kept, merged = merge_notes([added, swap])

    assert merged == 1
    assert [(n.kind, n.instrument, n.status) for n in kept] == [
        ("missing-hit", "tom_high", "promoted")]


def test_nothing_merges_across_a_position_or_an_instrument(song):
    from rambass.review import merge_notes

    notes = [
        _note(9, beat=2.0, kind="missing-hit", instrument="tom_mid"),
        # different beat
        _note(9, beat=2.667, kind="swap-hit", instrument="tom_mid",
              swap_to="tom_high"),
        # different bar
        _note(10, beat=2.0, kind="swap-hit", instrument="tom_mid",
              swap_to="tom_high"),
        # a swap of something else entirely
        _note(9, beat=2.0, kind="swap-hit", instrument="crash",
              swap_to="china"),
    ]
    kept, merged = merge_notes(notes)
    assert merged == 0 and len(kept) == 4


def test_an_extra_hit_is_never_merged_away(song):
    """"There is a hit here that should not be" is its own observation, and
    folding it into anything would silently drop a decision."""
    from rambass.review import merge_notes

    kept, merged = merge_notes([
        _note(9, kind="missing-hit", instrument="crash"),
        _note(9, kind="extra-hit", instrument="crash")])
    assert merged == 0 and len(kept) == 2


def test_filing_a_swap_of_your_own_note_merges_it_on_the_spot(song):
    """The commonest way the pair appears: add the hit, hear it, swap it --
    both open, both from the grid, seconds apart."""
    from rambass.review import add_note

    add_note(song, _note(9, beat=2.667, kind="missing-hit",
                         instrument="tom_mid"))
    notes = add_note(song, _note(9, beat=2.667, kind="swap-hit",
                                 instrument="tom_mid", swap_to="tom_high"))

    assert [(n.kind, n.instrument) for n in notes] == [
        ("missing-hit", "tom_high")]
    assert len(_ledger(song)) == 1


def test_promote_tidies_the_pair_it_has_just_made_true(cwd_song):
    """The other door: the pair was promoted separately, so the merge happens
    when the second half lands -- and the manifest keeps the one declaration
    the swap rewrote."""
    from rambass.cli import main
    from rambass.manifest import load_song
    from rambass.review import add_note, load_review

    add_note(cwd_song, _note(9, beat=2.667, kind="missing-hit",
                             instrument="tom_mid", velocity=90))
    assert main(["review", "promote", cwd_song.slug]) == 0
    add_note(cwd_song, _note(9, beat=2.667, kind="swap-hit",
                             instrument="tom_mid", swap_to="tom_high"))
    assert main(["review", "promote", cwd_song.slug]) == 0

    _, notes = load_review(cwd_song.path("qa", "review.yaml"))
    assert [(n.kind, n.instrument, n.status) for n in notes] == [
        ("missing-hit", "tom_high", "promoted")]
    saved = load_song(cwd_song.directory)
    assert [(a.bar, a.beat, a.instrument, a.velocity)
            for a in saved.drum_additions] == [(9, 2.667, "tom_high", 90)]
    assert not saved.drum_removals
