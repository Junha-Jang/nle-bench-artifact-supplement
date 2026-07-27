# NLE-Bench Author-Response Supplement (anonymized)

Supporting artifacts for the NeurIPS 2026 author response (submission 2775).

- refusal_audit/ — per-record labels from three judge configurations across two vendors over all 1,920
  infeasible-track records; judge prompt spec; validator-vs-majority table; 20 raw outputs stratified by response format (10 text-only, 10 mixed tool-call+text); human-audit per-record labels, rater-effect summary, a joined human/detector/judge table with boundary flags, and a judge-dependence + ranking-survival summary.
- strict_preservation/ — unchanged_except sensitivity rescore (273 scenarios, 56 model/configuration rows, both variants, with Spearman fields).
- rewrite_manifest/ — v3→v3.1 change decomposition (344 = 224 instruction + 106 constraints-only + 14 fixture-affecting),
  holdout SRs, borderline-overlap table.
- tool_usage/ — per-tool call counts, tool-conditioned SR, execution-axis decomposition.
- blinded_rerating/ — frozen sample manifest (SHA-256 recorded in frozen_sample_manifest.sha256 at freeze time, before any re-rating), aggregate re-rating results (t3_results), supplementary analyses, structured disputed-item table; full 200 x 6 per-item rating matrix included.
- coverage_adjudication/ — pre-screen pessimistic-bound table (per-model [bound, released], with Spearman); the author-adjudicated 173-flag sheet follows during discussion.
- direction_rescore/ — per-model effect of the 1341->1301 direction-enforcement rescore (also in the submitted supplementary material).
- correct_slot/ — external slot-category judgments (192 = 48 scenarios x 4 raters, with overlap flags) and per-model any-vs-correct-slot success rates over all 11,760 stored clarify judgments (median gap 10.4pp); the 500-rating human audit of the matcher follows during discussion.

No author-identifying information is included. Rater identities are pseudonymous codes (external re-rating: A-1..A-4; internal double-coding: B-1, B-2; refusal audit: R-1, R-2).

All files here are data artifacts released under CC BY 4.0, matching the data licensing of the main artifact.
