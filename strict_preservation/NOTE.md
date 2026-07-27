# Strict-preservation sensitivity (T2)

`t2_strict_rescore_results.json` carries BOTH variants for all 56 model/configuration rows:

- `delta_pp_preexisting` - median -1.51pp, worst -5.31pp (gemini-3-flash-preview).
  Restricts the added `unchanged_except` check to entities present in the initial state.
- `delta_pp_full` - median -4.74pp, worst -12.81pp (same model).
  Applies the check to every entity, which also penalizes intended additions, because
  `add`-type tools mint harness-generated IDs that cannot be listed in advance.

The author response reports the pre-existing-entity variant and names it as such; the
all-entity variant is reported alongside it as the harsher reading. Both are here.

Baseline note. `sr_feas` in this file is the raw archived scorer. `released_sr` in
`coverage_adjudication/t1_coverage_bound_prescreen.json` is the patched release scorer behind
Table 3. The two differ on 42 of 55 shared rows by up to 1.88pp, always in the same direction;
the per-model decomposition is in `direction_rescore/attribute_changed_direction_rescore_audit.md`.
Deltas within each file are internally consistent; do not mix baselines across files.
