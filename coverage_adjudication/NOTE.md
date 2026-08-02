Author-adjudicated coverage sheet (173 flags) lands here after final adjudication pass.

t1_prescreen_summary.json holds the automated pre-screen verdicts over all 173 coverage flags (140 confirmed-gap / 31 covered / 2 ambiguous) with the per-flag-category breakdown. Author adjudication of the same 173 follows during discussion.

## Author adjudication (added for the closing comment, 2 August)

- `T1_author_verdicts.csv` — all 173 flagged scenarios with the machine pre-screen verdict, the
  author verdict where one was reached (60 rows), and the author's reasoning note. Notes were
  written in Korean and translated into English by the authors, preserving the original hedges.
  Eleven of the 60 verdicts were reached in a review session rather than typed into the sheet and
  are marked as such, so that the reported concordance (44/60, 73.3%) is reproducible from this
  file alone.
- `t1_final_verdicts.json` — merged outcome for all 173: `final` (COVERED / CONFIRMED_GAP /
  AMBIGUOUS / UNRESOLVED) and `source` (individual / class / unclassified).
- `t1_gap_class_rescore_v2.py`, `.json` — corpus-wide rescore of the five mechanically detectable
  gap classes, with within-class failure rates beside each ΔSR-feas. The script header documents
  six defects found in an earlier draft of this measurement by independent verification.
- `t1_where_rescore.py`, `.json` — the `entity_exists` where-clause rescore promised to the AC.
- `t1_coverage_bound.py` with `_prescreen`, `_final` and `_final21` tables — the pessimistic bound
  on the machine-labelled set (140 gaps), the adjudicated set (132), and the harshest reading that
  also counts the 21 unresolved flags as gaps (153).

Scripts resolve paths from `NLEB_SUPPLEMENT`, which should point at the released supplement root.
