# Blinded re-rating (T3)

Collection is complete. All figures cited in the author response are computed from the files
in this directory; nothing here is pending.

Sampling rule. The 200-scenario sample was frozen before any re-rating. Its SHA-256 is in
`frozen_sample_manifest.sha256` and can be checked with
`sha256sum frozen_sample_manifest.json`. Composition: 104 feasible drawn proportionally over
the 16 perception x execution cells, plus 48 infeasible and 48 ambiguous drawn as 12 per
(class x information) cell out of 20 available in each, i.e. 96 of the 160 non-feasible
scenarios. "Eligible" in the manifest `design` field means exactly this 12-per-cell rule; it
is not a census of the class.

Raters. Arm A is four professional video editors recruited outside the original labeling pool
(A-1..A-4), blind to labels, hypotheses, and the paper. Arm B is two internal raters who took
no part in the original labeling (B-1, B-2), double-coding all 200 label-blind.

`per_item_ratings.csv` gives the full 200 x 6 judgment matrix so every aggregate in
`t3_results.json` can be recomputed independently.
