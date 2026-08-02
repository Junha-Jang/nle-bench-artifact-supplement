#!/usr/bin/env python3
"""T1 `where`-clause rescore — the gap class missing from t1_gap_class_rescore_v2.

배경
----
`nlebench/runner/constraints.py::entity_exists` 는 params 에서 type/track/index 만 읽고
`params["where"]` 를 절대 읽지 않는다. 따라서 `where` 가 붙은 제약은
"해당 타입 엔티티가 하나라도 있는가" 로 퇴화한다.

이 스크립트는
  (1) `where` 를 실제로 적용하는 교정 술어를 구현하고,
  (2) 저장된 canonical trajectory 의 final_state 를 교정 제약으로 재채점하고,
  (3) 모델 55행 × 640 feasible 시나리오 기준 ΔSR-feas 를 낸다.

v2 스크립트가 문서화한 7개 선행 오류를 그대로 승계한다:
  (a) 성공 판정은 per_scenario_results_redacted.csv (패치 후 채점기) 에서 가져온다.
      raw results.jsonl 의 success 는 패치 전 값이라 논문 보고 집합이 아니다.
  (b) excluded_from_recompute 는 'yes'/'no' 인코딩이다 (56 → 55 행).
  (c) YAML id 는 NLEB-*, 결과 아티팩트 id 는 NLB-v3-* 이다.
  (d) feasible 판정은 validation.feasibility == 'feasible' 로 건별 확인한다.

추가로 이 클래스 고유의 함정 두 가지를 처리한다:
  (e) caption 의 `text` 는 Clip 에 직접 없고 `clip.caption.text` 에 있다.
      `_resolve_field` 만 쓰면 모든 caption 제약이 항상 False 가 되어
      실패율이 허위로 부풀려진다. 부모 객체(caption/title) fallback 을 넣는다.
  (f) 교정 술어는 "생성되었는가" 가 아니라 "존재하는가" 다.
      초기 상태에서 이미 만족되는 제약은 교정 후에도 trivially 만족된다.
      따라서 초기 상태 만족 여부를 제약별로 따로 보고한다.

엄격/관대 두 가지 문자열 매칭을 모두 계산해 민감도를 보고한다.
"""
import csv
import glob
import json
import os
import sys
import statistics as st
from collections import defaultdict

SUP = os.environ.get("NLEB_SUPPLEMENT", ".")
SRC = f"{SUP}/code/src"
sys.path.insert(0, SRC)
import yaml  # noqa: E402
from nlebench.models import EditProject  # noqa: E402
from nlebench.dataset.fixtures import load_base_fixture, apply_patch  # noqa: E402
from nlebench.runner.constraints import (  # noqa: E402
    _get_entities_in_track, _resolve_field, _as_number, _field_tolerance,
    DEFAULT_TOLERANCE,
)

SCEN = f"{SRC}/nlebench/dataset/scenarios_v3_1/feasible"
CANON = f"{SUP}/results/per_scenario_results_redacted.csv"
MANIFEST = f"{SUP}/results/run_manifest.csv"
RESULTS = "${NLEB_SUPPLEMENT}"
OUT = "./t1_where_rescore.json"
TOL = dict(DEFAULT_TOLERANCE)


# ── 교정 술어 ──────────────────────────────────────────────────────
def where_field_value(entity, field):
    """`where.field` 를 엔티티에서 해석한다.

    `_resolve_field` 는 Clip 에 `text` 속성이 없어 caption 텍스트를 못 읽는다.
    caption/title 하위 객체를 fallback 으로 본다.
    """
    v = _resolve_field(entity, field)
    if v is not None:
        return v
    if "." not in field:
        for parent in ("caption", "title"):
            p = getattr(entity, parent, None)
            if p is not None:
                v = _resolve_field(p, field)
                if v is not None:
                    return v
    return None


def where_match(entity, where, lenient=False):
    field = where["field"]
    op = where.get("op", "==")
    expected = where["value"]
    actual = where_field_value(entity, field)
    if actual is None:
        return False
    an, en = _as_number(actual), _as_number(expected)
    if an is not None and en is not None:
        tol = _field_tolerance(field, TOL)
        if op in ("==", "eq"):
            return abs(an - en) <= tol
        if op == "!=":
            return abs(an - en) > tol
        if op == "<=":
            return an <= en + tol
        if op == "<":
            return an < en + tol
        if op == ">=":
            return an >= en - tol
        if op == ">":
            return an > en - tol
        return False
    sa, se = str(actual), str(expected)
    if lenient:
        sa, se = sa.strip().casefold(), se.strip().casefold()
    if op in ("==", "eq"):
        return sa == se
    if op == "!=":
        return sa != se
    return False


def entity_exists_corrected(state, params, lenient=False):
    """`where` 로 엔티티 목록을 거른 뒤 index 를 적용한다."""
    if "entity" in params:                     # id 모드는 버그 대상이 아니다
        raise ValueError("id-mode entity_exists has no where clause")
    entities = _get_entities_in_track(state, params["type"], params.get("track"))
    where = params.get("where")
    if where:
        entities = [e for e in entities if where_match(e, where, lenient)]
    return params.get("index", 0) < len(entities)


def entity_exists_released(state, params):
    """출시된(버그 있는) 술어 — where 를 무시한다."""
    entities = _get_entities_in_track(state, params["type"], params.get("track"))
    return params.get("index", 0) < len(entities)


# ── 시나리오 수집 ───────────────────────────────────────────────────
def flat_constraints(d):
    out = []
    con = d.get("constraints") or {}
    for g in ("required", "specified"):
        for c in (con.get(g) or []):
            if isinstance(c, dict):
                for k, v in c.items():
                    out.append((g, k, v))
    return out


def load_initial(fx):
    s = load_base_fixture(fx) if isinstance(fx, str) else load_base_fixture(fx["base"])
    if isinstance(fx, dict):
        for pt in (fx.get("patch") or []):
            try:
                apply_patch(s, pt)
            except Exception:
                pass
    return s


def collect():
    """where 절이 붙은 entity_exists 제약을 가진 feasible 시나리오를 모은다."""
    scen = {}
    for p in sorted(glob.glob(f"{SCEN}/**/*.yaml", recursive=True)):
        d = yaml.safe_load(open(p))
        if not isinstance(d, dict) or not d.get("id"):
            continue
        cons = [(g, v) for g, k, v in flat_constraints(d)
                if k == "entity_exists" and isinstance(v, dict) and "where" in v]
        if not cons:
            continue
        sid = d["id"]
        legacy = d.get("legacy_id") or sid.replace("NLEB-", "NLB-v3-")
        scen[legacy] = {
            "yaml_id": sid,
            "legacy_id": legacy,
            "path": os.path.relpath(p, SRC),
            "instruction": " / ".join(t["instruction"] for t in d.get("turns", [])),
            "scale": (d.get("taxonomy") or {}).get("scale"),
            "cell": (d.get("taxonomy") or {}).get("information", "") + "/" +
                    (d.get("taxonomy") or {}).get("action", ""),
            "constraints": [c for _, c in cons],
            "groups": [g for g, _ in cons],
            "fixture": d["fixture"],
        }
    return scen


def main():
    scen = collect()
    n_scen = len(scen)
    n_con = sum(len(s["constraints"]) for s in scen.values())
    print(f"where 절 entity_exists: 시나리오 {n_scen}건 / 제약 {n_con}개")

    # ── 초기 상태 만족 여부 ─────────────────────────────────────────
    for legacy, s in scen.items():
        init = load_initial(s["fixture"])
        s["initial"] = []
        for c in s["constraints"]:
            s["initial"].append({
                "params": c,
                "released_holds_initial": entity_exists_released(init, c),
                "corrected_holds_initial": entity_exists_corrected(init, c),
                "corrected_holds_initial_lenient": entity_exists_corrected(init, c, True),
            })

    rel_init = sum(1 for s in scen.values() for i in s["initial"] if i["released_holds_initial"])
    cor_init = sum(1 for s in scen.values() for i in s["initial"] if i["corrected_holds_initial"])
    print(f"초기 상태에서 만족: released {rel_init}/{n_con}, corrected {cor_init}/{n_con}")

    # ── 정본 성공 판정 ──────────────────────────────────────────────
    canon = {}
    with open(CANON) as f:
        for r in csv.DictReader(f):
            if r["track"] != "canonical" or r["scenario_feasibility"] != "feasible":
                continue
            canon[(r["run_id"], r["legacy_scenario_id"], r["run_number"])] = r["success"] == "True"
    print(f"정본 성공 인덱스: {len(canon):,}건")

    runs = [(r["model"], r["source_results_relpath"])
            for r in csv.DictReader(open(MANIFEST))
            if r.get("track") == "canonical"
            and (r.get("excluded_from_recompute") or "no").lower() not in ("yes", "true")
            and r.get("source_results_relpath")]
    print(f"모델 행: {len(runs)}")

    # ── 재채점 ──────────────────────────────────────────────────────
    agg = defaultdict(lambda: defaultdict(int))
    per_scen = defaultdict(lambda: defaultdict(int))
    per_con_fail = defaultdict(lambda: defaultdict(int))
    missing = defaultdict(int)

    for model, rel in runs:
        path = os.path.join(RESULTS, rel)
        if not os.path.exists(path):
            missing["results_file"] += 1
            continue
        run_id = rel.split("/")[0]
        for line in open(path):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if ((d.get("validation") or {}).get("feasibility")) != "feasible":
                continue
            sid = d.get("scenario_id", "")
            key = (run_id, sid, str(d.get("run_number")))
            if key not in canon:
                continue
            agg[model]["tot"] += 1
            ok = canon[key]
            if ok:
                agg[model]["succ"] += 1
            if sid not in scen:
                continue

            per_scen[sid]["records"] += 1
            if ok:
                per_scen[sid]["released_succ"] += 1

            # 진단용: released 성공 여부와 무관하게 교정 술어 만족 건수를 센다.
            # 이 값이 0 이면 "해당 시나리오는 교정 제약을 아무도 만족시킨 적이 없다"는 뜻이고,
            # 그때는 모델 실패가 아니라 시나리오/도구 쪽 결함을 의심해야 한다.
            fs = d.get("final_state_json")
            state = None
            if fs:
                try:
                    state = EditProject.from_dict(
                        json.loads(fs) if isinstance(fs, str) else fs)
                except Exception:
                    state = None
            if state is None:
                per_scen[sid]["no_final_state"] += 1
                if ok:
                    missing["unparseable_final_state_on_success"] += 1
                    per_scen[sid]["released_succ_unjudgeable"] += 1
                    per_scen[sid]["corrected_succ"] += 1   # 판정 불가 → 성공 유지 (보수적)
                    agg[model]["n_where"] += 1
                continue
            if all(entity_exists_corrected(state, c) for c in scen[sid]["constraints"]):
                per_scen[sid]["corrected_satisfiable_any"] += 1

            if not ok:
                continue
            agg[model]["n_where"] += 1

            cons = scen[sid]["constraints"]
            strict_ok, lenient_ok = True, True
            for ci, c in enumerate(cons):
                s_ok = entity_exists_corrected(state, c)
                l_ok = s_ok or entity_exists_corrected(state, c, True)
                if not s_ok:
                    strict_ok = False
                    per_con_fail[sid][ci] += 1
                if not l_ok:
                    lenient_ok = False
            if strict_ok:
                per_scen[sid]["corrected_succ"] += 1
            else:
                agg[model]["fail_where"] += 1
                per_scen[sid]["corrected_fail"] += 1
            if lenient_ok:
                per_scen[sid]["corrected_succ_lenient"] += 1
            else:
                agg[model]["fail_where_lenient"] += 1

    # ── 집계 ────────────────────────────────────────────────────────
    deltas, deltas_len, ceilings, srs = [], [], [], []
    per_model = {}
    for m, s in agg.items():
        if not s["tot"]:
            continue
        sr = 100 * s["succ"] / s["tot"]
        d_strict = (100 * (s["succ"] - s["fail_where"]) / s["tot"]) - sr
        d_len = (100 * (s["succ"] - s["fail_where_lenient"]) / s["tot"]) - sr
        deltas.append(d_strict)
        deltas_len.append(d_len)
        ceilings.append(-100 * s["n_where"] / s["tot"])
        srs.append(sr)
        per_model[m] = {
            "feasible_records": s["tot"], "released_succ": s["succ"],
            "released_sr_pct": round(sr, 4),
            "class_applicable": s["n_where"], "class_failing": s["fail_where"],
            "corrected_sr_pct": round(100 * (s["succ"] - s["fail_where"]) / s["tot"], 4),
            "delta_pp": round(d_strict, 4),
            "delta_pp_lenient": round(d_len, 4),
        }

    n_app = sum(s["n_where"] for s in agg.values())
    n_fail = sum(s["fail_where"] for s in agg.values())
    n_fail_len = sum(s["fail_where_lenient"] for s in agg.values())

    scen_out = {}
    for legacy, s in sorted(scen.items()):
        ps = per_scen[legacy]
        scen_out[legacy] = {
            "yaml_id": s["yaml_id"], "path": s["path"], "scale": s["scale"],
            "cell": s["cell"], "instruction": s["instruction"],
            "n_where_constraints": len(s["constraints"]),
            "scored_records": ps["records"],
            "released_success": ps["released_succ"],
            "corrected_success": ps["corrected_succ"],
            "corrected_success_lenient": ps["corrected_succ_lenient"],
            "newly_failing": ps["corrected_fail"],
            "records_without_final_state": ps["no_final_state"],
            "released_success_unjudgeable": ps["released_succ_unjudgeable"],
            # released 성공 여부와 무관하게 교정 제약을 만족한 기록 수.
            # 0 이면 그 시나리오는 아무도 만족시킨 적이 없다 → 시나리오 결함 의심.
            "corrected_satisfiable_any_record": ps["corrected_satisfiable_any"],
            "constraints": [
                {
                    "params": i["params"],
                    "released_holds_initial": i["released_holds_initial"],
                    "corrected_holds_initial": i["corrected_holds_initial"],
                    "corrected_holds_initial_lenient": i["corrected_holds_initial_lenient"],
                    "failing_records_strict": per_con_fail[legacy].get(ci, 0),
                }
                for ci, i in enumerate(s["initial"])
            ],
        }

    out = {
        "meta": {
            "generated_by": "./t1_where_rescore.py",
            "canonical_verdicts": os.path.relpath(CANON, SUP),
            "manifest": os.path.relpath(MANIFEST, SUP),
            "scenario_dir": os.path.relpath(SCEN, SRC),
            "model_rows": len(per_model),
            "feasible_records_per_model": st.median([s["tot"] for s in agg.values()]),
            "string_match": "strict = exact str equality; lenient = strip+casefold",
        },
        "class_size": {
            "scenarios": n_scen,
            "where_constraints": n_con,
            "constraints_holding_in_initial_state_released": rel_init,
            "constraints_holding_in_initial_state_corrected": cor_init,
        },
        "within_class": {
            "released_successful_runs": n_app,
            "failing_under_corrected_strict": n_fail,
            "failing_under_corrected_lenient": n_fail_len,
            "fail_rate_pct_strict": round(100 * n_fail / n_app, 2) if n_app else None,
            "fail_rate_pct_lenient": round(100 * n_fail_len / n_app, 2) if n_app else None,
        },
        "delta_sr_feas_pp": {
            # 주의: 두 통계량은 다르다. AC 에 약속한 "-0.47pp" 는 아래 strict.median,
            # 즉 모델별 ΔSR 의 중앙값이다. 중앙값 SR 의 차이(difference of medians)는
            # -0.31pp 로 값이 다르다. split-family 쪽 "+0.45pp" 는 difference of medians
            # 이므로, 두 수치를 나란히 쓸 때는 반드시 같은 통계량으로 맞춰야 한다.
            "statistic_note": ("strict.median = median over models of (corrected SR - released SR). "
                               "difference_of_medians = median(corrected SR) - median(released SR). "
                               "AC 에 보고된 -0.47pp 는 전자다."),
            "difference_of_medians": None,   # main() 에서 채운다
            "strict": {
                "median": round(st.median(deltas), 4),
                "min": round(min(deltas), 4),
                "max": round(max(deltas), 4),
                "mean": round(st.mean(deltas), 4),
            },
            "lenient": {
                "median": round(st.median(deltas_len), 4),
                "min": round(min(deltas_len), 4),
                "max": round(max(deltas_len), 4),
            },
            "ceiling_median_pp": round(st.median(ceilings), 4),
            "released_median_sr_pct": round(st.median(srs), 4),
            "corrected_median_sr_pct": round(
                st.median([v["corrected_sr_pct"] for v in per_model.values()]), 4),
        },
        "per_scenario": scen_out,
        "per_model": per_model,
        "anomalies": dict(missing),
        "caveats": [
            {
                "id": "CO-028-unsatisfiable-as-written",
                "scenario": "NLB-v3-EX-CO-028",
                "contributes_to_397": 84,
                "detail": (
                    "instruction 은 complex_sequence 에 \"Add a caption 'Title Card' at 0.0s\" "
                    "를 요구하지만 이 fixture 의 caption_1 이 0.5~3.0s 를 점유하고 있고 "
                    "add_caption 은 caption 간 겹침을 거부한다. 따라서 0.0s 캡션은 길이 "
                    "0.5s 이하일 때만 생성 가능하며, 147개 기록 중 이를 만족한 사례는 0건이다. "
                    "즉 이 시나리오의 84건은 모델 실패가 아니라 시나리오 자체 결함에 가깝다. "
                    "397건의 21%를 차지하므로 AC 에 함께 고지해야 한다."
                ),
            },
            {
                "id": "media-entity-list-excludes-image",
                "scenario": "NLB-v3-EX-CO-029",
                "contributes_to_397": 0,
                "detail": (
                    "constraints._get_entity_list('media') 는 av_medias+video_medias+audio_medias "
                    "인데 셋 다 type in ('video','audio') 로 필터된다. import_media('overlay.png') "
                    "는 type='image' 를 만들므로 이 목록에 절대 들어가지 않는다. 따라서 "
                    "entity_exists{type: media, where:{name: overlay.png}} 는 교정해도 만족 불가능하다. "
                    "CO-029 는 released 성공이 0건이라 397/-0.47pp 에는 영향이 없지만 "
                    "교정 술어의 한계로 명시해야 한다. "
                    "부수적으로 av_medias 와 video_medias 가 동일 조건이라 video media 는 "
                    "'media' 목록에 중복 계상된다(존재 검사에는 무해)."
                ),
            },
            {
                "id": "released-success-anticorrelated-with-doing-the-task",
                "scenario": "NLB-v3-EX-CO-002",
                "detail": (
                    "CO-002 에서 released 성공 61건 중 교정 술어를 만족한 건은 0건인 반면, "
                    "실제로 'Chapter 1' 캡션을 만든 33건은 전부 released 실패로 채점됐다 "
                    "(unchanged_except 가 신규 캡션을 위반으로 잡는다). "
                    "where 버그가 단독 결함이 아니라 unchanged_except 와 상호작용해 "
                    "'과제를 수행하면 실패하는' 채점을 만들고 있다."
                ),
            },
        ],
    }
    out["delta_sr_feas_pp"]["difference_of_medians"] = round(
        out["delta_sr_feas_pp"]["corrected_median_sr_pct"]
        - out["delta_sr_feas_pp"]["released_median_sr_pct"], 4)
    json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)

    # ── 콘솔 요약 ───────────────────────────────────────────────────
    print(f"\n클래스 내부: released 성공 {n_app}건 중 교정 후 실패 "
          f"{n_fail}건 ({100*n_fail/n_app:.1f}%)  [관대 매칭 {n_fail_len}건]")
    print(f"ΔSR-feas: median {st.median(deltas):+.3f}pp  "
          f"min {min(deltas):+.3f}pp  max {max(deltas):+.3f}pp")
    print(f"  (관대) median {st.median(deltas_len):+.3f}pp")
    print(f"  released median SR {st.median(srs):.3f}%  → corrected "
          f"{st.median([v['corrected_sr_pct'] for v in per_model.values()]):.3f}%")
    print(f"\n{'scenario':22s}{'rec':>5s}{'rel':>5s}{'cor':>5s}{'newfail':>8s}{'satAny':>8s}"
          f"  init rel/cor")
    for legacy, v in scen_out.items():
        ri = sum(1 for c in v["constraints"] if c["released_holds_initial"])
        ci = sum(1 for c in v["constraints"] if c["corrected_holds_initial"])
        flag = "  <-- 아무도 만족 못함" if v["corrected_satisfiable_any_record"] == 0 else ""
        print(f"{legacy:22s}{v['scored_records']:5d}{v['released_success']:5d}"
              f"{v['corrected_success']:5d}{v['newly_failing']:8d}"
              f"{v['corrected_satisfiable_any_record']:8d}"
              f"  {ri}/{len(v['constraints'])}  {ci}/{len(v['constraints'])}{flag}")
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
