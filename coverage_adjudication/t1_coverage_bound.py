#!/usr/bin/env python3
"""Coverage pessimistic-bound calculator (sol-converged design, 2026-07-26).

Not a new scorer. For each model: report [pessimistic bound, released SR]
where the bound demotes every SUCCESS on a confirmed-gap feasible scenario
to failure (worst case: assume the unchecked aspect was violated whenever
it was not checked). Input gap list defaults to the CL pre-screen
CONFIRMED_GAP set; rerun with --final after the author adjudication pass
(uses the `final` column of the adjudication sheet).

  --fake N   sanity mode: use N random feasible scenarios as the "gap" set
             and verify bound <= released for every model, equality iff no
             successes on the set.
"""
import argparse
import csv
import json
import os
import random
from collections import defaultdict

BASE = "."
PSR = (os.environ.get("NLEB_SUPPLEMENT",".") + "/"
       "results/per_scenario_results_redacted.csv")


def gap_ids(mode: str):
    """확정 갭 시나리오 집합.

    prescreen  기계 사전선별의 CONFIRMED_GAP (시트의 claude_verdict)
    final      저자 판정을 반영한 병합 결과 (t1_final_verdicts.json)
    final+21   미해결 21건까지 갭으로 치는 가장 가혹한 읽기

    final 을 시트의 `final` 컬럼에서 읽던 이전 판은 그 컬럼이 비어 있어
    항상 0건을 냈다. 판정의 정본은 병합 파일이다.
    """
    if mode == "prescreen":
        return {r["scenario_id"]
                for r in csv.DictReader(open(f"{BASE}/T1_author_verdicts.csv"))
                if r.get("claude_verdict") == "CONFIRMED_GAP"}

    verdicts = json.load(open(f"{BASE}/t1_final_verdicts.json"))["final"]
    want = {"CONFIRMED_GAP"} | ({"UNRESOLVED"} if mode == "final+21" else set())
    return {sid for sid, v in verdicts.items() if v in want}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true",
                    help="저자 판정 병합 결과(132 confirmed gaps)를 쓴다")
    ap.add_argument("--final21", action="store_true",
                    help="미해결 21건까지 갭으로 치는 가장 가혹한 읽기(153건)")
    ap.add_argument("--fake", type=int, default=None)
    args = ap.parse_args()

    # canonical track, feasible records
    per_model = defaultdict(lambda: {"n": 0, "succ": 0, "succ_gap": 0})
    feas_ids = set()
    rows = []
    for r in csv.DictReader(open(PSR)):
        if r["track"] != "canonical" or r["scenario_feasibility"] != "feasible":
            continue
        rows.append((r["model"], r["scenario_id"], r["success"] == "True"))
        feas_ids.add(r["scenario_id"])

    if args.fake is not None:
        rng = random.Random(3)
        gaps = set(rng.sample(sorted(feas_ids), args.fake))
        mode = f"fake({args.fake})"
    else:
        mode = "final+21" if args.final21 else ("final" if args.final else "prescreen")
        gaps = gap_ids(mode)
    gap_feas = gaps & feas_ids

    for model, sid, succ in rows:
        m = per_model[model]
        m["n"] += 1
        if succ:
            m["succ"] += 1
            if sid in gap_feas:
                m["succ_gap"] += 1

    out = {"_mode": mode, "gap_scenarios_total": len(gaps),
           "gap_scenarios_feasible": len(gap_feas), "per_model": {}}
    viol = 0
    deltas = []
    for model, m in sorted(per_model.items()):
        if m["n"] == 0:
            continue
        released = 100 * m["succ"] / m["n"]
        bound = 100 * (m["succ"] - m["succ_gap"]) / m["n"]
        if bound > released + 1e-9:
            viol += 1
        deltas.append(released - bound)
        out["per_model"][model] = {
            "n": m["n"], "released_sr": round(released, 2),
            "pessimistic_bound": round(bound, 2),
            "delta_pp": round(released - bound, 2)}
    deltas.sort()
    out["summary"] = {
        "models": len(deltas),
        "median_delta_pp": round(deltas[len(deltas) // 2], 2) if deltas else None,
        "max_delta_pp": round(max(deltas), 2) if deltas else None,
        "bound_gt_released_violations": viol}
    path = f"{BASE}/t1_coverage_bound{'_FAKE' if args.fake is not None else ('_final21' if args.final21 else ('_final' if args.final else '_prescreen'))}.json"
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "per_model"},
                     indent=1))
    top = sorted(out["per_model"].items(),
                 key=lambda kv: -kv[1]["released_sr"])[:5]
    for k, v in top:
        print(k, v)


if __name__ == "__main__":
    main()
