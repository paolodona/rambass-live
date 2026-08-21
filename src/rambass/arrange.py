"""Checking a running order against how set lists are actually built.

The craft here is well documented and fairly consistent across sources: open
with a statement of intent that the audience already knows, build in runs of
three or four, drop to one real breather, climb again, and finish on the biggest
thing you have. Put the crowd-pleasers at the front and the tail; put unfamiliar
material in the middle where familiar songs either side carry it. Never let three
songs of the same tempo and feel sit together, or the set starts to sound like
one long song.

None of that is arithmetic, so this module does not try to *generate* an order.
It takes the order a human wrote and says which of those principles it breaks —
which is the part a human is bad at spotting in their own list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .manifest import Song

#: Sparkline glyphs for energy 1-5.
BARS = " ▁▃▅▆█"

#: Three songs at the same energy is the documented limit, and only when the
#: feels differ. Four is never right.
MAX_SAME_ENERGY_RUN = 3

#: How far into the set a song may be and still count as "the front" or "the
#: tail". Floors matter: a fifth of a short set is one song, and a one-song
#: window makes the front/tail checks meaningless.
FRONT_FRACTION = 0.2
TAIL_FRACTION = 0.15
MIN_FRONT = 3
MIN_TAIL = 2


@dataclass
class Finding:
    """One thing the order gets wrong, or is worth a second look."""

    severity: str        # "problem" | "watch"
    where: str
    what: str


def sparkline(songs: list[Song]) -> str:
    return "".join(BARS[max(1, min(5, s.character.energy))] for s in songs)


def review(songs: list[Song]) -> list[Finding]:
    """Check a running order. Returns findings, worst first."""
    out: list[Finding] = []
    if not songs:
        return [Finding("problem", "—", "the set is empty")]

    total = len(songs)
    front = min(total, max(MIN_FRONT, int(total * FRONT_FRACTION)))
    tail = min(total, max(MIN_TAIL, int(total * TAIL_FRACTION)))

    # ── the two positions that matter most ───────────────────────────────
    opener = songs[0]
    if opener.character.role == "linking":
        opener = songs[1] if total > 1 else opener
    if opener.character.standing in ("deep", "new"):
        out.append(Finding(
            "problem", f"1. {opener.title}",
            f"opening with a {opener.character.standing} song. The first minute "
            "sets what the audience expects of the whole night — open with "
            "something they already know",
        ))
    if opener.character.energy <= 2:
        out.append(Finding(
            "problem", f"1. {opener.title}",
            f"opening at energy {opener.character.energy}. An opener is a "
            "statement of intent, not a warm-up",
        ))

    closer = songs[-1]
    if closer.character.standing in ("deep", "new"):
        out.append(Finding(
            "problem", f"{total}. {closer.title}",
            f"closing on a {closer.character.standing} song. The last song is the "
            "peak — finish on something they will leave singing",
        ))
    declared_closer = [s for s in songs if s.character.role == "closer"]
    for song in declared_closer:
        if song is not closer:
            out.append(Finding(
                "problem", f"{songs.index(song) + 1}. {song.title}",
                "marked as the closer but is not last",
            ))

    # ── monotony ─────────────────────────────────────────────────────────
    run_start = 0
    for index in range(1, total + 1):
        same = (
            index < total
            and songs[index].character.energy == songs[run_start].character.energy
        )
        if same:
            continue
        length = index - run_start
        if length > MAX_SAME_ENERGY_RUN:
            titles = ", ".join(s.title for s in songs[run_start:index])
            out.append(Finding(
                "problem", f"{run_start + 1}-{index}",
                f"{length} songs at energy {songs[run_start].character.energy} in a "
                f"row ({titles}) — the set will start to sound like one long song",
            ))
        elif length == MAX_SAME_ENERGY_RUN:
            genres = {s.character.genre for s in songs[run_start:index]}
            if len(genres) < MAX_SAME_ENERGY_RUN:
                out.append(Finding(
                    "watch", f"{run_start + 1}-{index}",
                    f"three songs at energy {songs[run_start].character.energy} with "
                    "only "
                    f"{len(genres)} distinct feel(s) between them — three is the "
                    "limit, and only when they feel different",
                ))
        run_start = index

    # ── ballads ──────────────────────────────────────────────────────────
    for index in range(total - 1):
        first, second = songs[index], songs[index + 1]
        if first.character.energy <= 1 and second.character.energy <= 1:
            out.append(Finding(
                "problem", f"{index + 1}-{index + 2}",
                f"two ballads back to back ({first.title}, {second.title})",
            ))

    # ── where the crowd-pleasers sit ─────────────────────────────────────
    # A linking piece at the front is a walk-on, not a song that has to carry
    # the opening — judge the stretch on the real songs in it.
    hits = [i for i, s in enumerate(songs) if s.character.standing == "hit"]
    if hits:
        if not any(i < front for i in hits):
            out.append(Finding(
                "problem", f"1-{front}",
                "no hit in the opening stretch — the front of the set is where "
                "you buy the audience's attention for everything after it",
            ))
        if not any(i >= total - tail for i in hits):
            out.append(Finding(
                "watch", f"{total - tail + 1}-{total}",
                "no hit in the closing stretch",
            ))
    unfamiliar = [
        (i, s) for i, s in enumerate(songs)
        if s.character.standing in ("deep", "new")
        and (i < front or i >= total - tail)
        and s.character.role not in ("linking", "detour")
    ]
    for index, song in unfamiliar:
        out.append(Finding(
            "watch", f"{index + 1}. {song.title}",
            f"a {song.character.standing} song near the {'front' if index < front else 'tail'} "
            "— unfamiliar material does better in the middle, carried by the songs "
            "either side",
        ))

    # ── does it actually have a shape? ───────────────────────────────────
    energies = [s.character.energy for s in songs]
    if max(energies) - min(energies) < 3:
        out.append(Finding(
            "problem", "whole set",
            f"energy only ranges {min(energies)}-{max(energies)} — there is no arc, "
            "just a plateau",
        ))
    troughs = [i for i, e in enumerate(energies) if e <= 2]
    if not troughs:
        out.append(Finding(
            "problem", "whole set",
            "nothing below energy 3 anywhere — the audience never gets to breathe, "
            "and a set with no trough has no peak either",
        ))
    # Several songs usually share the top energy, so what matters is whether the
    # set ever gets back up there — not where the first one happens to sit.
    peaks = [i for i, e in enumerate(energies) if e == max(energies)]
    if peaks and max(peaks) < total * 0.5:
        last = max(peaks)
        out.append(Finding(
            "watch", f"{last + 1}. {songs[last].title}",
            "the set never returns to its highest energy after the halfway point — "
            "everything from here is downhill",
        ))

    # ── linking pieces must stay attached ────────────────────────────────
    for index, song in enumerate(songs):
        if song.character.role != "linking" or not song.character.note:
            continue
        if "runs straight into" in song.character.note and index == total - 1:
            out.append(Finding(
                "problem", f"{index + 1}. {song.title}",
                "a linking piece cannot be last — it runs into the next song",
            ))

    order = {"problem": 0, "watch": 1}
    return sorted(out, key=lambda f: order[f.severity])


def table(songs: list[Song]) -> str:
    """The running order with its shape visible."""
    lines = [
        f"{'#':>3}  {'song':<32} {'E':<6} {'H':<3} {'standing':<9} {'role':<10} genre",
        "-" * 104,
    ]
    for index, song in enumerate(songs, start=1):
        char = song.character
        meter = BARS[max(1, min(5, char.energy))] * char.energy
        lines.append(
            f"{index:>3}  {song.title:<32} {meter:<6} {char.heaviness:<3} "
            f"{char.standing:<9} {char.role or '-':<10} {char.genre}"
        )
    lines.append("")
    lines.append(f"     energy  {sparkline(songs)}")
    return "\n".join(lines)
