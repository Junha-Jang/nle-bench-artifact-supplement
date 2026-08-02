#!/usr/bin/env python3
"""T1 gap-class rescore v2 — corrected after independent verification.

v1 대비 수정:
  (a) 성공 판정을 per_scenario_results_redacted.csv(패치 후 채점기)에서 가져온다.
      raw results.jsonl 의 success 는 패치 전 값이라 논문이 보고하는 집합이 아니다.
  (b) excluded_from_recompute 필터를 'yes'/'no' 인코딩에 맞춘다 (56 → 55 행).
  (c) G1: dB 언급이 여럿인 시나리오에서 첫 값을 모든 엔티티에 적용하던 버그 수정.
      개수가 안 맞으면 제외한다. 탐지 창을 넓히고 'to N dB'(절대값)는 계속 제외.
  (d) G4: '$' 접두사 때문에 이미 강제된 시나리오를 미강제로 분류하던 버그 수정.
      transition 문맥이 아닌 'for 3.0s'(클립 길이) 오탐 제거.
  (e) G5: 상대가 삭제된 레코드를 '아직 링크됨'으로 세던 버그 수정.
      has_link 로 연결을 요구하는 시나리오는 주입 자체가 모순이므로 제외.
  (f) G3: timeline_start 가 없는 타입(track/media/transition/sequence)을 대상으로
      잡던 버그 수정. clip/caption 만 대상으로 한다.
  (g) ΔSR-feas 와 함께 클래스 내부 실패율과 ΔSR 상한을 같이 보고한다.
      ΔSR 은 640건 전체로 나누므로, 작은 클래스에서는 0.00pp 가 '측정된 0'인지
      '측정 불가능한 0'인지 구분되지 않는다.
"""
# Paths are resolved relative to the released supplement root.
# Set NLEB_SUPPLEMENT to the directory containing code/ and results/.

import csv
import glob
import json
import os
import re
import sys
import statistics as st
from collections import defaultdict

SUP = os.environ.get("NLEB_SUPPLEMENT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = f"{SUP}/code/src"
sys.path.insert(0, SRC)
import yaml  # noqa: E402
from nlebench.models import EditProject  # noqa: E402
from nlebench.dataset.fixtures import load_base_fixture, apply_patch  # noqa: E402
from nlebench.runner.constraints import attribute_equals, _get_entity_list  # noqa: E402

SCEN = f"{SRC}/nlebench/dataset/scenarios_v3_1/feasible"
CANON = f"{SUP}/results/per_scenario_results_redacted.csv"
MANIFEST = f"{SUP}/results/run_manifest.csv"
RESULTS = "results"
TOL = {"time": 0.05, "default": 0.01}


def num(v):
    return v.n / v.d if hasattr(v, "n") else v


def load_initial(fx):
    s = load_base_fixture(fx) if isinstance(fx, str) else load_base_fixture(fx["base"])
    if isinstance(fx, dict):
        for pt in (fx.get("patch") or []):
            try:
                apply_patch(s, pt)
            except Exception:
                pass
    return s


def clips_of(state):
    for tl in state.timelines:
        for tr in tl.tracks:
            for c in (getattr(tr, "clips", None) or []):
                yield tr, c


# ── 탐지 ────────────────────────────────────────────────────────
DB_ALL = re.compile(r'\b(boost|raise|increase|lower|reduce|decrease|drop)\b'
                    r'[^.;/]{0,80}?\bby\s+(\d+(?:\.\d+)?)\s*dB', re.I)
DB_ABS = re.compile(r'\bto\s+[-+]?\d+(?:\.\d+)?\s*dB', re.I)
SPLIT = re.compile(r'\b(?:split|cut)\b[^.]{0,40}?\bat\s+(?:the\s+)?(\d+(?:\.\d+)?)\s*(?:-\s*)?s', re.I)
AT = re.compile(r'\b(?:at|starting at)\s+(\d+(?:\.\d+)?)\s*s(?:econds?)?\b', re.I)
TC = re.compile(r'\b(\d{2}):(\d{2}):(\d{2})[:;](\d{2})\b')
START_TL = re.compile(r'at the start of the timeline', re.I)
REL_MARK = re.compile(r'\b(?:overlay|clip|shot|half)\b[^.]{0,25}\bat the \d', re.I)
TDUR_T = re.compile(r'transition[^.]{0,60}?(?:duration|lasting)\s+(\d+(?:\.\d+)?)\s*s'
                    r'|(?:duration|lasting)\s+(\d+(?:\.\d+)?)\s*s[^.]{0,40}?transition', re.I)
PLACE_TYPES = {"clip", "caption"}          # timeline_start 를 갖는 타입만


def flat_constraints(d):
    out = []
    con = d.get("constraints") or {}
    for g in ("required", "specified"):
        for c in (con.get(g) or []):
            if isinstance(c, dict):
                for k, v in c.items():
                    out.append((k, v))
    return out


def build_specs():
    specs = defaultdict(dict)
    skipped = defaultdict(list)
    for p in glob.glob(f"{SCEN}/**/*.yaml", recursive=True):
        d = yaml.safe_load(open(p))
        if not isinstance(d, dict) or not d.get("id"):
            continue
        sid = d["id"]
        instr = " / ".join(t["instruction"] for t in d.get("turns", []))
        flat = flat_constraints(d)
        conj = json.dumps(d.get("constraints") or {})
        eq_fields = {v.get("field") for k, v in flat
                     if k == "attribute_equals" and isinstance(v, dict)}

        # ── G2 scale_y
        if "transform.scale_x" in eq_fields and "transform.scale_y" not in eq_fields:
            specs["G2"][sid] = [(v["entity"].lstrip("$"), "transform.scale_y", v["value"])
                                for k, v in flat if k == "attribute_equals"
                                and isinstance(v, dict) and v.get("field") == "transform.scale_x"]

        # ── G1 dB 크기
        if "audio.volume" not in eq_fields and not DB_ABS.search(instr):
            deltas = [(m.group(1), float(m.group(2))) for m in DB_ALL.finditer(instr)]
            ch = [v for k, v in flat if k == "attribute_changed" and isinstance(v, dict)
                  and v.get("field") == "audio.volume"]
            if deltas and ch:
                if len(deltas) != len(ch):
                    skipped["G1_target_count_mismatch"].append(sid)
                else:
                    init = load_initial(d["fixture"])
                    cur = {c.id: (num(getattr(getattr(c, "audio", None), "volume", None))
                                  if getattr(c, "audio", None) else None)
                           for _, c in clips_of(init)}
                    items = []
                    for (verb, mag), a in zip(deltas, ch):
                        ent = a["entity"].lstrip("$")
                        base = cur.get(ent)
                        if base is None:
                            items = []
                            break
                        sign = -1 if re.match(r'lower|reduce|decrease|drop', verb, re.I) else 1
                        items.append((ent, "audio.volume", base + sign * mag))
                    if items:
                        specs["G1"][sid] = items

        # ── G4 transition 길이
        if any(k == "has_transition" for k, _ in flat):
            t = TDUR_T.search(instr)
            enforced = any(k == "attribute_equals" and isinstance(v, dict)
                           and v.get("field") == "duration"
                           and str(v.get("entity", "")).lstrip("$").startswith(("transition", "vt_", "new_transition"))
                           for k, v in flat)
            if t and not enforced:
                specs["G4"][sid] = [("__transition__", "duration",
                                     float(t.group(1) or t.group(2)))]
            elif t and enforced:
                skipped["G4_already_enforced"].append(sid)

        # ── G5 not_linked 한쪽만
        nls = [v["entity"].lstrip("$") for k, v in flat
               if k == "not_linked" and isinstance(v, dict) and v.get("entity")]
        gone = {str(v.get("entity", "")).lstrip("$") for k, v in flat
                if k == "entity_not_exists" and isinstance(v, dict)}
        linked_req = set()
        for k, v in flat:
            if k == "has_link" and isinstance(v, dict):
                linked_req |= {str(x).lstrip("$") for x in (v.get("clip_ids") or [])}
        if len(nls) == 1 and nls[0] not in gone:
            init = load_initial(d["fixture"])
            lg = next((getattr(c, "link_group", None) for _, c in clips_of(init)
                       if c.id == nls[0]), None)
            partners = [c.id for _, c in clips_of(init)
                        if getattr(c, "link_group", None) == lg and c.id != nls[0]] if lg else []
            partners = [x for x in partners if x not in gone and x not in linked_req]
            if nls[0] in linked_req:
                skipped["G5_contradicts_has_link"].append(sid)
            elif partners:
                specs["G5"][sid] = [(x, "__unlinked__", None) for x in partners]

        # ── G3 명시된 위치
        if "position_equals" not in conj and '"field": "timeline_start"' not in conj:
            spec = None
            sp = SPLIT.search(instr)
            if sp and not REL_MARK.search(instr):
                spec = {"kind": "split", "time": float(sp.group(1))}
            elif not REL_MARK.search(instr):
                T = None
                tc = TC.search(instr)
                if tc:
                    h, m_, sec, fr = (int(x) for x in tc.groups())
                    T = h * 3600 + m_ * 60 + sec + fr / 24.0
                elif START_TL.search(instr):
                    T = 0.0
                else:
                    at = AT.search(instr)
                    if at:
                        T = float(at.group(1))
                if T is not None:
                    refs = re.findall(r'\$new_(\w+?)_(\d+)', conj)
                    tgt = next(((t_, int(i)) for t_, i in refs if t_ in PLACE_TYPES), None)
                    if tgt:
                        spec = {"kind": "at", "time": T, "type": tgt[0], "index": tgt[1]}
                    else:
                        skipped["G3_no_placeable_ref"].append(sid)
            if spec:
                specs["G3"][sid] = spec
    return specs, skipped


def check(state, ent, field, val):
    if field == "__unlinked__":
        c = state.get_clip_by_id(ent)
        if c is None:
            return True          # 상대가 삭제됨 → 링크는 남아있지 않다
        return getattr(c, "link_group", None) is None
    if ent == "__transition__":
        for tl in state.timelines:
            for tr in tl.tracks:
                for t in (getattr(tr, "transitions", None) or []):
                    if abs(num(getattr(t, "duration", 0)) - val) <= TOL["time"]:
                        return True
        return False
    return attribute_equals(state, {"entity": ent, "field": field, "value": val}, TOL)


def check_g3(state, initial_ids, spec):
    T = spec["time"]
    if spec["kind"] == "split":
        for _, c in clips_of(state):
            s = num(getattr(c, "timeline_start", None))
            dur = num(getattr(c, "duration", None))
            if s is None or dur is None:
                continue
            if abs(s - T) <= TOL["time"] or abs((s + dur) - T) <= TOL["time"]:
                return True
        return False
    ents = _get_entity_list(state, spec["type"])
    def eid(e):
        return getattr(e, "id", None) or (e.get("id") if isinstance(e, dict) else str(e))
    fresh = [e for e in ents if eid(e) not in initial_ids]
    if spec["index"] > len(fresh):
        return False
    s = num(getattr(fresh[spec["index"] - 1], "timeline_start", None))
    return s is not None and abs(s - T) <= TOL["time"]


def main():
    specs, skipped = build_specs()
    print("탐지 결과")
    for k in sorted(specs):
        print(f"  {k}: {len(specs[k])}건")
    for k, v in sorted(skipped.items()):
        print(f"  (제외) {k}: {len(v)}건  {v[:4]}")

    # 정본 성공 판정
    canon = {}
    for r in csv.DictReader(open(CANON)):
        if r["track"] != "canonical" or r["scenario_feasibility"] != "feasible":
            continue
        canon[(r["run_id"], r["legacy_scenario_id"], r["run_number"])] = r["success"] == "True"
    print(f"\n정본 성공 인덱스: {len(canon):,}건")

    runs = [(r["model"], r["source_results_relpath"]) for r in csv.DictReader(open(MANIFEST))
            if r.get("track") == "canonical"
            and (r.get("excluded_from_recompute") or "no").lower() not in ("yes", "true")
            and r.get("source_results_relpath")]
    print(f"모델 행: {len(runs)}")

    raw = {k: {s.replace("NLEB-", "NLB-v3-"): v for s, v in d.items()}
           for k, d in specs.items() if k != "G3"}
    g3 = {s.replace("NLEB-", "NLB-v3-"): v for s, v in specs["G3"].items()}

    init_ids = {}
    for p in glob.glob(f"{SCEN}/**/*.yaml", recursive=True):
        dd = yaml.safe_load(open(p))
        if isinstance(dd, dict) and dd.get("id") in specs["G3"]:
            s0 = load_initial(dd["fixture"])
            ids = {c.id for _, c in clips_of(s0)}
            for tl in s0.timelines:
                for tr in tl.tracks:
                    ids.add(tr.id)
            init_ids[dd["id"].replace("NLEB-", "NLB-v3-")] = ids

    agg = defaultdict(lambda: defaultdict(int))
    for model, rel in runs:
        path = os.path.join(RESULTS, rel)
        if not os.path.exists(path):
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
            if not canon[key]:
                continue
            agg[model]["succ"] += 1
            need = {k: raw[k][sid] for k in raw if sid in raw[k]}
            in3 = sid in g3
            if not need and not in3:
                continue
            try:
                fs = d.get("final_state_json")
                state = EditProject.from_dict(json.loads(fs) if isinstance(fs, str) else fs)
            except Exception:
                continue
            for k, items in need.items():
                agg[model][f"n_{k}"] += 1
                if not all(check(state, e, f, v) for e, f, v in items):
                    agg[model][f"fail_{k}"] += 1
            if in3:
                agg[model]["n_G3"] += 1
                try:
                    ok = check_g3(state, init_ids.get(sid, set()), g3[sid])
                except Exception:
                    ok = True
                if not ok:
                    agg[model]["fail_G3"] += 1

    out = {"per_class": {}, "skipped": {k: v for k, v in skipped.items()},
           "model_rows": len(agg)}
    print(f"\n{'유형':5s}{'시나리오':>7s}{'중앙값':>10s}{'최악':>10s}{'클래스내 실패':>13s}{'ΔSR 상한':>12s}")
    for k in sorted(specs):
        ds, ceil, n_app, n_fail = [], [], 0, 0
        for m, s in agg.items():
            if not s["tot"]:
                continue
            sr = 100 * s["succ"] / s["tot"]
            f = s[f"fail_{k}"]
            ds.append((100 * (s["succ"] - f) / s["tot"]) - sr)
            ceil.append(-100 * s[f"n_{k}"] / s["tot"])
            n_app += s[f"n_{k}"]
            n_fail += f
        ds.sort(); ceil.sort()
        rate = 100 * n_fail / n_app if n_app else 0.0
        out["per_class"][k] = {
            "scenarios": len(specs[k]), "applicable_records": n_app,
            "failing_records": n_fail, "within_class_fail_pct": round(rate, 1),
            "median_delta_pp": round(st.median(ds), 2) if ds else None,
            "worst_delta_pp": round(min(ds), 2) if ds else None,
            "ceiling_median_pp": round(st.median(ceil), 2) if ceil else None,
        }
        v = out["per_class"][k]
        print(f"{k:5s}{v['scenarios']:7d}{v['median_delta_pp']:+9.2f}pp{v['worst_delta_pp']:+9.2f}pp"
              f"{n_fail:7d}/{n_app:<5d}{v['within_class_fail_pct']:5.1f}%{v['ceiling_median_pp']:+9.2f}pp")
    json.dump(out, open("claudedocs/rebuttal_2026/t1_gap_class_rescore_v2.json", "w"),
              indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
