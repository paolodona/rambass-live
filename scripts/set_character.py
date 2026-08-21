#!/usr/bin/env python3
"""One-shot: write the band's own read of each song into its manifest.

Energy and heaviness are deliberately separate axes. Il Phurgone is high energy
and not heavy at all; Mandami un Faxe is neither; Orologiaio is both, for ninety
seconds. Collapsing them into one number loses exactly the information that
makes a running order work.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rambass.manifest import load_song, save_song  # noqa: E402
from rambass.project import Project  # noqa: E402

#        slug:                    (genre, energy, heaviness, standing, role, note)
CHARACTER = {
    # ── Tutti in Fila ────────────────────────────────────────────────────
    "i-puffi": ("rock, upbeat", 4, 3, "hit", "",
               "one of the big ones. Master file is the pun spelling 'I PoohFFI'"),
    "tutti-in-fila": ("metal", 5, 5, "hit", "", "one of the biggest"),
    "ampiamente-contestabile": ("rock, fun", 4, 2, "known", "", "less heavy"),
    "intro-vibratore": ("linking", 2, 1, "deep", "linking",
                        "runs straight into La Vera Storia — one backing track"),
    "la-vera-storia-del-vibratore": ("rock / ambient, offbeat", 3, 3, "deep", "",
                                     "the odd one out; not a crowd favourite"),
    "la-canzone-del-tonno": ("a cappella, comic", 2, 1, "known", "interlude",
                             "funny unaccompanied interlude"),
    "skizzo-sonovabic": ("jazzy rock, epic", 3, 3, "deep", "", "long"),
    "manlio": ("ballad", 1, 1, "known", "", ""),
    "l-esercito-del-surf": ("pop / light rock", 3, 1, "known", "", "lightweight"),
    # ── Diversamente Giovani ─────────────────────────────────────────────
    "intro": ("linking", 2, 1, "deep", "linking", "album intro; walk-on music"),
    "formaygrana": ("rock, upbeat", 5, 3, "hit", "", "very happy"),
    "mother-sacher": ("heavy metal, funny", 5, 5, "known", "",
                      "the heaviest song in the set"),
    "bambolina": ("rock / half ballad", 3, 3, "hit", "",
                  "intricate and elaborate; one of the famous ones"),
    "itturfiatrugoy": ("heavy metal, silly", 5, 5, "known", "", ""),
    "il-phurgone": ("60s-70s funky ballroom", 4, 1, "known", "",
                    "upbeat but not heavy — nothing to headbang to"),
    "orologiaio": ("death metal", 5, 5, "deep", "detour",
                   "very fast, very brutal, very short. Features a guest from a "
                   "death metal band — a fun detour"),
    "il-cellulare": ("nu metal / rap", 4, 4, "known", "", ""),
    "per-niente-stanca": ("Italian pop with a rock chorus", 3, 2, "known", "", ""),
    "superman": ("bluesy rock, upbeat", 4, 2, "known", "", ""),
    "se-sei-felice": ("a cappella", 2, 1, "known", "interlude",
                      "unaccompanied interlude"),
    "mandami-un-faxe": ("acoustic ballad", 1, 1, "deep", "",
                        "slow, repetitive and prolonged — the riskiest song to "
                        "place badly"),
    "la-ragazza-da-milano": ("rock", 4, 3, "hit", "closer",
                             "the ending song: long finale with a big chorus that "
                             "can repeat as long as it is working. The MIDI has two "
                             "fake stops before the chorus comes back"),
    "diversamente-giovani": ("ballad", 2, 2, "new", "",
                            "one of the newest, so the least known to the audience"),
}

RENAMES = {}  # I Puffi was corrected in place; keep this for future renames


def main() -> int:
    project = Project.discover()
    touched = 0
    for song_dir in project.song_dirs():
        song = load_song(song_dir)
        entry = CHARACTER.get(song.slug)
        if entry is None:
            print(f"  ?  no character for {song.album}/{song.slug}")
            continue
        genre, energy, heaviness, standing, role, note = entry
        song.character.genre = genre
        song.character.energy = energy
        song.character.heaviness = heaviness
        song.character.standing = standing
        song.character.role = role
        song.character.note = note
        if song.slug in RENAMES and song.title != RENAMES[song.slug]:
            print(f"  ->  retitled {song.title!r} -> {RENAMES[song.slug]!r}")
            song.title = RENAMES[song.slug]
        song.validate()
        save_song(song)
        touched += 1
        flag = " CUT" if song.excluded else ""
        print(f"  ok  {song.album}/{song.slug:<28} E{energy} H{heaviness} "
              f"{standing:<6}{role and ' ' + role}{flag}")
    print(f"{touched} song(s) characterised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
