"""The progress board: what is done, what is not, across both albums."""

from __future__ import annotations

from .manifest import STAGES, Song

# Terminal marks are single ASCII characters so the columns stay aligned;
# markdown output can afford the emoji.
MARKS = {"done": "X", "wip": "~", "todo": ".", "n/a": "-"}
MARKS_MD = {"done": "✅", "wip": "🔧", "todo": "·", "n/a": "—"}
#: Short column headers so the table fits in a terminal.
SHORT = {
    "source": "src", "analyze": "anl", "stems": "stm", "drums_midi": "mid",
    "quantize": "qnt", "kit": "kit", "render": "rnd", "lyr": "lyr",
    "lyrics": "lyr", "video": "vid", "gx100": "gx", "rehearsed": "reh",
}


def board(songs: list[Song], *, markdown: bool = False) -> str:
    """Render a per-song / per-stage grid.

    Songs cut from the show are listed separately rather than shown as a row of
    dashes: they are not work, and leaving them in the grid makes the project
    look less finished than it is.
    """
    if not songs:
        return "no songs yet — start with `rambass new \"Titolo\" --album <album>`"

    cut = [s for s in songs if s.excluded]
    songs = [s for s in songs if not s.excluded]
    if not songs:
        return "every song is excluded from the set"

    width = max(len(f"{s.album}/{s.title}") for s in songs)
    headers = [SHORT.get(stage, stage[:3]) for stage in STAGES]

    if markdown:
        lines = [
            "| song | " + " | ".join(headers) + " | done |",
            "|:-----|" + "|".join([":--:"] * len(headers)) + "|-----:|",
        ]
    else:
        lines = [
            "song".ljust(width) + "  " + " ".join(h.rjust(3) for h in headers) + "   done",
            "-" * (width + 4 * len(headers) + 8),
        ]

    for song in songs:
        label = f"{song.album}/{song.title}"
        marks = MARKS_MD if markdown else MARKS
        cells = [marks.get(song.status.get(stage, "todo"), "?") for stage in STAGES]
        done, total = song.progress()
        if markdown:
            lines.append(f"| {label} | " + " | ".join(cells) + f" | {done}/{total} |")
        else:
            lines.append(
                label.ljust(width) + "  " + " ".join(c.rjust(3) for c in cells)
                + f"   {done}/{total}"
            )

    total_done = sum(s.progress()[0] for s in songs)
    total_all = sum(s.progress()[1] for s in songs)
    lines += ["", f"{len(songs)} songs in the set · {total_done}/{total_all} stages "
                  f"complete ({100 * total_done / max(total_all, 1):.0f}%)"]

    unaccompanied = [s for s in songs if s.drums_origin == "a-cappella"]
    if unaccompanied:
        lines.append(
            "a cappella (no backing track): "
            + ", ".join(s.title for s in unaccompanied)
        )
    if cut:
        lines.append("")
        lines.append(f"cut from the set ({len(cut)}):")
        for song in cut:
            reason = f" — {song.exclude_reason}" if song.exclude_reason else ""
            lines.append(f"    {song.album}/{song.title}{reason}")

    if not markdown:
        lines.append("")
        lines.append("legend: " + "  ".join(f"{v} = {k}" for k, v in MARKS.items()))
    return "\n".join(lines)


def next_actions(songs: list[Song], limit: int = 10) -> str:
    """What to work on next: the first unfinished stage of each song."""
    if not songs:
        return ""
    rows: list[tuple[str, str]] = []
    for song in (s for s in songs if not s.excluded):
        for stage in STAGES:
            state = song.status.get(stage, "todo")
            if state in ("todo", "wip"):
                rows.append((f"{song.album}/{song.slug}", stage))
                break
    if not rows:
        return "everything is done — go and play the gig"
    lines = ["next up:"]
    for reference, stage in rows[:limit]:
        lines.append(f"  {stage:<12} {reference}")
    if len(rows) > limit:
        lines.append(f"  ... and {len(rows) - limit} more")
    return "\n".join(lines)
