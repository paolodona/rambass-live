# Backlog

Out-of-scope items discovered while planning or implementing. Format:
`- [ ] **[Plan <NNN>]** <description>`

## High
## Medium

- [ ] **[No plan]** The stems step records no provenance inputs, so `rambass stale` can never
  report `stems/drums.wav` stale when the source audio changes. `provenance.PIPELINE` declares
  its input as the template `"source/{source_audio}"`, but `stamp()` filters with
  `if name in known.inputs`, comparing the *resolved* path (`source/09 Manlio.wav`) against that
  unexpanded template — it never matches, so `inputs` is written empty. Visible in every stamp
  written by the Aug 2026 bulk run, and it dropped the source hash Manlio's stamp already had.
  Only the stems step is affected; every other step declares literal input paths. Fix needs a
  failing test first (expand `{source_audio}` against the manifest before the membership check).

## Low
