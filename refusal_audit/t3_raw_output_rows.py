#!/usr/bin/env python3
"""Build the two raw-output row groups promised to reviewer zNTq (R3).

zNTq was promised, in the posted R3 response, "API-provider and text-fallback
raw-output rows to follow during discussion". The already-shipped
refusal_audit/raw_output_subset_20.json covers the open-weight pool stratified
by response format. This script emits the two missing groups on the *interface*
axis:

  raw_output_subset_api.json            closed-API vendors, native tool-calling
  raw_output_subset_text_fallback.json  vLLM runs launched with --tool-mode text

Schema is copied field-for-field from the shipped file:
    response_format, scenario_id, source_model, run_number,
    validator_behavior, judge_label_gpt_high, agent_response

Provenance rules enforced here:
  * validator_behavior is read from per_scenario_results_redacted.csv (the
    canonical, post-patch rescoring), never from results.jsonl, whose `success`
    and validation block are pre-patch.
  * every emitted agent_response is checked against the released
    agent_response_sha256 for that row, so the raw text provably belongs to the
    published record.
  * every emitted string is scanned for identifying material before it is
    written.

Usage:
    python t3_raw_output_rows.py              # full build (uses judge cache)
    python t3_raw_output_rows.py --dry-run    # select + scan, no judge calls
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
# 경로는 환경에서 받는다. 이 스크립트는 서플리먼트로 공개되므로 로컬 경로를 박지 않는다.
#   NLEB_RUNS       원본 실행 루트 (results/<timestamp>_<model>/results.jsonl 을 품는 디렉터리)
#   NLEB_SUPPLEMENT 릴리스된 서플리먼트 루트 (results/ 를 품는 디렉터리)
#   NLEB_SHIPPED    이미 공개된 raw_output_subset_20.json 경로
DEV = Path(os.environ.get("NLEB_RUNS", "."))
PAPER = Path(os.environ.get("NLEB_SUPPLEMENT", ".")) / "results"
SHIPPED = Path(os.environ.get("NLEB_SHIPPED", "refusal_audit/raw_output_subset_20.json"))
OUT_DIR = DEV / "claudedocs" / "rebuttal_2026"
RAW_ROOT = DEV / "results"

MANIFEST = PAPER / "run_manifest.csv"
PER_SCENARIO = PAPER / "per_scenario_results_redacted.csv"
AUDIT_LABELS = OUT_DIR / "t5_openai_labels.jsonl"

OUT_API = OUT_DIR / "raw_output_subset_api.json"
OUT_TF = OUT_DIR / "raw_output_subset_text_fallback.json"
OUT_README = OUT_DIR / "raw_output_rows_README.md"
JUDGE_CACHE = OUT_DIR / "t3_raw_output_rows_judge_cache.jsonl"
PROVENANCE = OUT_DIR / "t3_raw_output_rows_provenance.json"

AUDIT_SCRIPT = DEV / "scripts" / "run_openai_refusal_audit.py"

SCHEMA = [
    "response_format",
    "scenario_id",
    "source_model",
    "run_number",
    "validator_behavior",
    "judge_label_gpt_high",
    "agent_response",
]

EMPTY_SHA = hashlib.sha256(b"").hexdigest()

# --------------------------------------------------------------------------
# STEP 1 -- what "text-fallback" means in this codebase
# --------------------------------------------------------------------------
# The harness switch is --tool-mode {native,text,auto}, default "auto":
#   src/nlebench/__main__.py:99-107      CLI flag
#   src/nlebench/__main__.py:361         -> get_provider(tool_mode=...)
#   src/nlebench/providers/__init__.py:88  -> VLLMProvider(tool_mode=...)
#   src/nlebench/providers/vllm_provider.py:230-234
#       tool_mode == "text"  -> _generate_response_without_tools(...)
#   src/nlebench/providers/vllm_provider.py:337-425
#       drops `tools=` from the request, appends a prompt block asking for
#       ```json {"tool_calls":[{"name":...,"arguments":{...}}]}``` and parses it
#       back with _parse_tool_calls_from_text (line 429).
#
# Scope of the switch: providers/__init__.py passes tool_mode ONLY into the vLLM
# branch. OpenAIProvider / AnthropicProvider / GoogleProvider take no tool_mode
# parameter at all, so `--tool-mode text` on those runs (it is present in
# scripts/run_openai_responses_full800.sh:29, run_anthropic_paper_reruns.sh:75
# and quick10_api_cost_probe.sh:25) is an inert flag: those runs used native
# tool-calling regardless.
#
# The switch is NOT RECORDED. It appears in 0 of 165 results/*/config.yaml, is
# absent from summary.json (model/provider/track/total_runs/successful_runs/
# sr/timestamp only), from the results.jsonl record schema, from
# run_manifest.csv and from per_scenario_results_redacted.csv, and the harness
# stdout log does not print it.
#
# Operationalisation used here, in two layers:
#   (A) run-level, provenance-recovered. Each launcher writes its log to a
#       name it derives from the model slug, so the log file identifies the
#       launcher, and the launcher hardcodes --tool-mode:
#           logs/v3_1_paper_<slug>.log    run_v3_1_paper.sh:172,179    text
#           logs/retry_<slug>.log         run_v3_1_retry.sh:95,101     text
#           logs/thinking_rerun_<slug>.log run_qwen35_thinking_rerun.sh:80,84 text
#       plus an mtime agreement check against the manifest timestamp.
#   (B) provider-level, structural. provider in {openai,anthropic,google}
#       cannot be text-mode, from the code path above.
# Rows that match neither are reported as unrecovered, not guessed.

LAUNCHER_BY_LOG_PREFIX = {
    "v3_1_paper_": ("scripts/run_v3_1_paper.sh", "text"),
    "retry_": ("scripts/run_v3_1_retry.sh", "text"),
    "thinking_rerun_": ("scripts/run_qwen35_thinking_rerun.sh", "text"),
}
NATIVE_ONLY_PROVIDERS = {"openai", "anthropic", "google"}
MTIME_TOLERANCE_S = 900


def recover_tool_mode(manifest_rows: list[dict]) -> dict[str, dict]:
    """Map manifest row_id -> {protocol, basis, evidence}."""
    out: dict[str, dict] = {}
    for r in manifest_rows:
        rid = r["row_id"]
        provider = r["provider"]
        if provider in NATIVE_ONLY_PROVIDERS:
            out[rid] = {
                "protocol": "native",
                "basis": "structural",
                "evidence": (
                    "providers/__init__.py:get_provider passes tool_mode only to "
                    "the vllm branch; %s provider has no tool_mode parameter"
                    % provider
                ),
            }
            continue

        slug = r["model"].replace("/", "-")
        ts = dt.datetime.fromisoformat(r["timestamp"])
        hits = []
        for prefix, (script, mode) in LAUNCHER_BY_LOG_PREFIX.items():
            p = DEV / "logs" / f"{prefix}{slug}.log"
            if not p.exists():
                continue
            delta = abs(
                (dt.datetime.fromtimestamp(p.stat().st_mtime) - ts).total_seconds()
            )
            if delta <= MTIME_TOLERANCE_S:
                hits.append((delta, str(p.relative_to(DEV)), script, mode))
        hits.sort()
        if len(hits) == 1 or (len(hits) > 1 and hits[0][3] == hits[1][3]):
            delta, logrel, script, mode = hits[0]
            out[rid] = {
                "protocol": "text" if mode == "text" else mode,
                "basis": "launcher-provenance",
                "evidence": f"{logrel} (mtime delta {delta:.0f}s) written by {script}, which hardcodes --tool-mode {mode}",
            }
        else:
            out[rid] = {
                "protocol": "unrecovered",
                "basis": "none",
                "evidence": "no launcher script writes a log matching this model slug and timestamp; run appears to have been started by hand",
            }
    return out


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load_manifest() -> list[dict]:
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_canonical_rows() -> dict[str, dict[int, dict]]:
    """row_id -> source_jsonl_line -> canonical per-scenario row (track=canonical)."""
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    with open(PER_SCENARIO, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["track"] != "canonical":
                continue
            out[r["row_id"]][int(r["source_jsonl_line"])] = r
    return out


def load_raw_responses(row_id: str, relpath: str, wanted_lines: set[int]) -> dict[int, dict]:
    """Read only the requested lines of a results.jsonl."""
    path = RAW_ROOT / relpath
    out: dict[int, dict] = {}
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, start=1):
            if ln not in wanted_lines:
                continue
            try:
                out[ln] = json.loads(line)
            except json.JSONDecodeError:
                continue
    return out


def load_shipped_keys() -> set[tuple]:
    with open(SHIPPED, encoding="utf-8") as f:
        shipped = json.load(f)
    return {
        (r["source_model"], r["scenario_id"], int(r["run_number"]))
        for r in shipped
    }


def load_audit_labels() -> dict[tuple, str]:
    """(source_model, scenario_id, run_number) -> gpt-5.4 high judge label."""
    out = {}
    with open(AUDIT_LABELS, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("judge_setting") != "high" or d.get("judge_model") != "gpt-5.4":
                continue
            out[(d["source_model"], d["scenario_id"], int(d["run_number"]))] = d["judge_label"]
    return out


# --------------------------------------------------------------------------
# STEP 5 -- identifying-material scanner
# --------------------------------------------------------------------------
# Every emitted string passes through this. A hit disqualifies a candidate at
# selection time (we take the next-ranked candidate in the same stratum) so
# that nothing has to be mutilated; anything that still hits at write time is
# scrubbed and reported, and a record that cannot survive scrubbing is dropped.
def _identifier_pattern():
    """스캔할 신원 문자열을 환경에서 읽는다. 없으면 이 축은 비활성으로 두고 그 사실을 알린다.

    NLEB_SCAN_IDENTIFIERS 는 문자열 목록이거나, 그 목록을 담은 파일 경로다.
    """
    raw = os.environ.get("NLEB_SCAN_IDENTIFIERS", "")
    if raw and os.path.exists(raw):
        raw = open(raw).read()
    toks = [t.strip() for t in re.split(r"[,\n]", raw) if t.strip()]
    if not toks:
        sys.stderr.write(
            "note: NLEB_SCAN_IDENTIFIERS is unset, so the identifier axis of the scan "
            "did not run. The other patterns did.\n")
        return re.compile(r"(?!x)x")  # 절대 매치하지 않는다
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in toks) + r")\b", re.I)


SCAN_PATTERNS = [
    ("abs_home_path", re.compile(r"/(?:home|Users|root|Volumes)/[A-Za-z0-9._-]+")),
    ("abs_sys_path", re.compile(r"(?<![\w.])/(?:mnt|srv|opt|var|etc|usr/local)/[A-Za-z0-9._/-]{2,}")),
    ("windows_path", re.compile(r"[A-Za-z]:\\\\?(?:Users|Documents|Desktop)", re.I)),
    # 신원 문자열은 코드에 넣지 않는다. 이 스크립트 자체가 서플리먼트로 공개되므로,
    # 리터럴로 적으면 검사기가 곧 신원 공개가 된다.
    # NLEB_SCAN_IDENTIFIERS 에 개행/쉼표로 구분해 넘기거나, 같은 이름의 파일 경로를 준다.
    ("identifier", _identifier_pattern()),
    ("repo_path", re.compile(r"nle-bench-(?:dev|neurips2026|artifact-supplement)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("localhost", re.compile(r"\b(?:localhost|127\.0\.0\.1)(?::\d+)?\b", re.I)),
    ("hostname", re.compile(r"\b" + re.escape(os.uname().nodename) + r"\b", re.I)),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("bearer", re.compile(r"\b(?:Authorization|Bearer)\s*[:=]\s*\S+", re.I)),
]


# `/media/...` is the benchmark's own synthetic media namespace: it is the
# `path` field of the public fixtures in src/nlebench/dataset/fixtures/json/,
# and models sometimes invent further filenames inside it. Those strings name
# no real filesystem and carry no identifying material, so they are counted
# and reported but do not disqualify a record. Everything else in
# SCAN_PATTERNS is treated as identifying.
BENIGN_PATTERNS = [
    ("synthetic_media_path", re.compile(r"(?<![\w.])/media/[A-Za-z0-9._/-]{0,}")),
]


def scan_text(text: str) -> list[tuple[str, str]]:
    """Identifying hits only -- these disqualify or must be scrubbed."""
    hits = []
    for name, rx in SCAN_PATTERNS:
        for m in rx.finditer(text or ""):
            hits.append((name, m.group(0)))
    return hits


def scan_text_benign(text: str) -> list[tuple[str, str]]:
    hits = []
    for name, rx in BENIGN_PATTERNS:
        for m in rx.finditer(text or ""):
            hits.append((name, m.group(0)))
    return hits


def scan_record(rec: dict) -> list[tuple[str, str, str]]:
    hits = []
    for k, v in rec.items():
        if isinstance(v, str):
            for name, frag in scan_text(v):
                hits.append((k, name, frag))
    return hits


# --------------------------------------------------------------------------
# selection helpers
# --------------------------------------------------------------------------
def rank_key(cand: dict) -> str:
    """Deterministic, content-derived ordering; independent of file order."""
    s = f"{cand['run_id']}|{cand['scenario_id']}|{cand['run_number']}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def round_robin_pick(cands: list[dict], n: int, group_key, reject) -> list[dict]:
    """Take n candidates, cycling over group_key so no single model dominates.

    `reject(cand) -> reason|None` is consulted lazily; rejected candidates are
    skipped and the next-ranked candidate in the same group is taken.
    """
    groups: dict = defaultdict(list)
    for c in sorted(cands, key=rank_key):
        groups[group_key(c)].append(c)
    order = sorted(groups)
    picked: list[dict] = []
    cursor = {g: 0 for g in order}
    seen_resp: set[str] = set()
    exhausted: set = set()
    while len(picked) < n and len(exhausted) < len(order):
        progressed = False
        for g in order:
            if len(picked) >= n:
                break
            if g in exhausted:
                continue
            lst = groups[g]
            while cursor[g] < len(lst):
                c = lst[cursor[g]]
                cursor[g] += 1
                if c["agent_response_sha256"] in seen_resp:
                    continue
                why = reject(c)
                if why:
                    c["_rejected"] = why
                    continue
                seen_resp.add(c["agent_response_sha256"])
                picked.append(c)
                progressed = True
                break
            else:
                exhausted.add(g)
        if not progressed and len(exhausted) >= len(order):
            break
    return picked


# --------------------------------------------------------------------------
# candidate construction
# --------------------------------------------------------------------------
def build_candidates(
    manifest_rows: list[dict],
    canonical: dict[str, dict[int, dict]],
    row_ids: list[str],
    predicate,
) -> tuple[list[dict], list[str]]:
    """Materialise candidate records (canonical fields + raw agent_response)."""
    notes: list[str] = []
    cands: list[dict] = []
    by_id = {r["row_id"]: r for r in manifest_rows}
    for rid in row_ids:
        m = by_id[rid]
        rows = canonical.get(rid, {})
        if not rows:
            notes.append(
                f"row {rid} ({m['model']}, {m['provider']}): no canonical "
                f"per-scenario rows -- {m['recompute_status']}; excluded"
            )
            continue
        keep = {ln: r for ln, r in rows.items() if predicate(r)}
        if not keep:
            continue
        raw = load_raw_responses(rid, m["source_results_relpath"], set(keep))
        for ln, cr in keep.items():
            d = raw.get(ln)
            if d is None:
                notes.append(f"row {rid} line {ln}: raw line missing/malformed; skipped")
                continue
            resp = d.get("agent_response") or ""
            sha = hashlib.sha256(resp.encode("utf-8")).hexdigest()
            if sha != cr["agent_response_sha256"]:
                notes.append(
                    f"row {rid} line {ln}: agent_response sha mismatch vs released row; skipped"
                )
                continue
            if sha == EMPTY_SHA:
                continue
            cands.append({
                "row_id": rid,
                "run_id": cr["run_id"],
                "provider": m["provider"],
                "source_model": cr["model"],
                "scenario_id": cr["legacy_scenario_id"],
                "run_number": int(cr["run_number"]),
                "scenario_feasibility": cr["scenario_feasibility"],
                # canonical, post-patch verdict fields
                "validator_behavior": cr["behavior"],
                "canonical_success": cr["success"],
                "raw_success": str(d.get("success")),
                "raw_behavior": str((d.get("validation") or {}).get("behavior")),
                "tool_call_count": int(cr["tool_call_count"] or 0),
                "response_format": "mixed" if int(cr["tool_call_count"] or 0) > 0 else "text",
                "agent_response": resp,
                "agent_response_sha256": sha,
            })
    return cands, notes


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------
def load_audit_module():
    spec = importlib.util.spec_from_file_location("nleb_audit", AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nleb_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def judge_labels(cands: list[dict], dry_run: bool) -> dict[str, str]:
    """gpt-5.4 / reasoning effort high, same prompt builder as the shipped audit."""
    cache: dict[str, dict] = {}
    if JUDGE_CACHE.exists():
        with open(JUDGE_CACHE, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                cache[d["agent_response_sha256"]] = d

    todo = [c for c in cands if c["agent_response_sha256"] not in cache]
    if todo and dry_run:
        print(f"  [dry-run] {len(todo)} judge calls would be made", file=sys.stderr)
        return {c["agent_response_sha256"]: cache.get(c["agent_response_sha256"], {}).get("judge_label")
                for c in cands}

    if todo:
        mod = load_audit_module()
        from openai import OpenAI
        client = OpenAI()
        with open(JUDGE_CACHE, "a", encoding="utf-8") as out:
            for i, c in enumerate(todo, 1):
                payload = {
                    "validation": {
                        "feasibility": c["scenario_feasibility"],
                        "behavior": c["validator_behavior"],
                        "state_changed": None,
                    },
                    "tool_calls": [{"name": "x"}] * c["tool_call_count"],
                    "agent_response": c["agent_response"],
                }
                prompt = mod.build_prompt(payload)
                label, raw_hash = mod.call_openai_judge(
                    client=client, model="gpt-5.4", setting="high",
                    prompt=prompt, max_output_tokens=2000,
                )
                rec = {
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "judge_model": "gpt-5.4",
                    "judge_setting": "high",
                    "judge_label": label,
                    "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
                    "raw_judge_response_hash": raw_hash,
                    "agent_response_sha256": c["agent_response_sha256"],
                    "run_id": c["run_id"],
                    "scenario_id": c["scenario_id"],
                    "run_number": c["run_number"],
                    "source_model": c["source_model"],
                    "source_provider": c["provider"],
                    "validator_behavior": c["validator_behavior"],
                    "scenario_feasibility": c["scenario_feasibility"],
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                cache[c["agent_response_sha256"]] = rec
                print(f"  judged {i}/{len(todo)} -> {label}", file=sys.stderr)

    return {c["agent_response_sha256"]: cache[c["agent_response_sha256"]]["judge_label"]
            for c in cands if c["agent_response_sha256"] in cache}


# --------------------------------------------------------------------------
# emit
# --------------------------------------------------------------------------
def to_schema(c: dict, judge: dict[str, str]) -> dict:
    return {
        "response_format": c["response_format"],
        "scenario_id": c["scenario_id"],
        "source_model": c["source_model"],
        "run_number": c["run_number"],
        "validator_behavior": c["validator_behavior"],   # canonical, post-patch
        "judge_label_gpt_high": judge.get(c["agent_response_sha256"]),
        "agent_response": c["agent_response"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report: dict = {}
    manifest_rows = load_manifest()

    # ---------------- step 1: tool_mode recovery -------------------------
    tm = recover_tool_mode(manifest_rows)
    by_id = {r["row_id"]: r for r in manifest_rows}
    tm_counts = Counter(
        (by_id[k]["provider"], v["protocol"]) for k, v in tm.items()
    )
    print("== step 1: tool-protocol recovery ==", file=sys.stderr)
    for k, n in sorted(tm_counts.items()):
        print(f"   provider={k[0]:<10} protocol={k[1]:<12} runs={n}", file=sys.stderr)
    report["tool_protocol_recovery"] = {
        "switch": "--tool-mode {native,text,auto} (default auto)",
        "code_path": [
            "src/nlebench/__main__.py:99-107 (CLI)",
            "src/nlebench/__main__.py:361 -> get_provider(tool_mode=...)",
            "src/nlebench/providers/__init__.py:88 -> VLLMProvider(tool_mode=...)",
            "src/nlebench/providers/vllm_provider.py:230-234 (text -> _generate_response_without_tools)",
            "src/nlebench/providers/vllm_provider.py:337-425 (prompt-based JSON protocol)",
            "src/nlebench/providers/vllm_provider.py:429 (_parse_tool_calls_from_text)",
        ],
        "recorded_anywhere": False,
        "counts": {f"{k[0]}/{k[1]}": n for k, n in sorted(tm_counts.items())},
        "per_row": tm,
    }

    canonical = load_canonical_rows()
    shipped_keys = load_shipped_keys()
    audit_high = load_audit_labels()

    scan_rejects: Counter = Counter()
    scan_examples: list = []

    def reject_on_scan(c):
        hits = scan_text(c["agent_response"])
        if hits:
            scan_rejects[hits[0][0]] += 1
            if len(scan_examples) < 40:
                scan_examples.append({
                    "group": c.get("_group"),
                    "source_model": c["source_model"],
                    "scenario_id": c["scenario_id"],
                    "run_number": c["run_number"],
                    "pattern": hits[0][0],
                    "fragment": hits[0][1][:120],
                })
            return f"scan:{hits[0][0]}"
        return None

    # ---------------- group A: closed-API, native protocol ---------------
    api_ids = [r["row_id"] for r in manifest_rows
               if r["provider"] in NATIVE_ONLY_PROVIDERS and r["track"] == "canonical"]
    vendor_of = {r["row_id"]: r["provider"] for r in manifest_rows}

    api_cands, api_notes = build_candidates(
        manifest_rows, canonical, api_ids,
        predicate=lambda r: (
            r["scenario_feasibility"] in ("infeasible", "ambiguous")
            and r["behavior"] in ("refuse", "clarify")
        ),
    )
    for c in api_cands:
        c["_group"] = "api"
        c["vendor"] = vendor_of[c["row_id"]]

    # 3 vendors x 2 refuse-relevant behaviours (models.py:1209 -> the only two
    # values scenario.expected_behavior ever takes). 20 slots, refuse-weighted
    # because refuse is the scarcer arm.
    API_PLAN = {
        ("anthropic", "refuse"): 4, ("anthropic", "clarify"): 3,
        ("google", "refuse"): 4, ("google", "clarify"): 3,
        ("openai", "refuse"): 3, ("openai", "clarify"): 3,
    }
    api_picked: list[dict] = []
    api_short: dict = {}
    for (vendor, beh), n in sorted(API_PLAN.items()):
        pool = [c for c in api_cands
                if c["vendor"] == vendor and c["validator_behavior"] == beh]
        got = round_robin_pick(pool, n, lambda c: c["source_model"], reject_on_scan)
        if len(got) < n:
            api_short[f"{vendor}/{beh}"] = {"wanted": n, "got": len(got),
                                            "available": len(pool)}
        api_picked.extend(got)

    # ---------------- group B: vLLM text-fallback ------------------------
    tf_ids = [r["row_id"] for r in manifest_rows
              if tm[r["row_id"]]["protocol"] == "text" and r["track"] == "canonical"]
    # restrict to runs the shipped refusal audit already judged, so the label
    # column is the very same gpt-5.4/high label the supplement ships
    audited_models = {k[0] for k in audit_high}
    tf_ids = [rid for rid in tf_ids if by_id[rid]["model"] in audited_models]

    tf_cands, tf_notes = build_candidates(
        manifest_rows, canonical, tf_ids,
        predicate=lambda r: r["scenario_feasibility"] == "infeasible",
    )
    tf_cands = [c for c in tf_cands
                if (c["source_model"], c["scenario_id"], c["run_number"]) in audit_high]
    for c in tf_cands:
        c["_group"] = "text_fallback"

    before = len(tf_cands)
    tf_cands = [c for c in tf_cands
                if (c["source_model"], c["scenario_id"], c["run_number"]) not in shipped_keys]
    tf_excluded_shipped = before - len(tf_cands)

    # mirror the shipped file's 10 mixed / 10 text split so the three files are
    # directly comparable; within text, cover both refuse-relevant behaviours.
    TF_PLAN = [
        ("mixed", None, 10),
        ("text", "refuse", 6),
        ("text", "clarify", 4),
    ]
    tf_picked: list[dict] = []
    tf_short: dict = {}
    for rf, beh, n in TF_PLAN:
        pool = [c for c in tf_cands if c["response_format"] == rf
                and (beh is None or c["validator_behavior"] == beh)]
        got = round_robin_pick(pool, n, lambda c: c["source_model"], reject_on_scan)
        if len(got) < n:
            tf_short[f"{rf}/{beh or 'any'}"] = {"wanted": n, "got": len(got),
                                                "available": len(pool)}
        tf_picked.extend(got)

    # ---------------- judge ---------------------------------------------
    tf_judge = {c["agent_response_sha256"]:
                audit_high[(c["source_model"], c["scenario_id"], c["run_number"])]
                for c in tf_picked}
    print(f"== judge: {len(tf_picked)} text-fallback labels reused from "
          f"t5_openai_labels.jsonl, {len(api_picked)} API labels to compute ==",
          file=sys.stderr)
    api_judge = judge_labels(api_picked, args.dry_run)

    # ---------------- emit + final scan ----------------------------------
    def finalise(picked, judge, label):
        out, dropped = [], []
        for c in picked:
            rec = to_schema(c, judge)
            hits = scan_record(rec)
            if hits:
                dropped.append({"scenario_id": c["scenario_id"],
                                "source_model": c["source_model"],
                                "run_number": c["run_number"],
                                "hits": [{"field": h[0], "pattern": h[1],
                                          "fragment": h[2][:120]} for h in hits]})
                continue
            assert list(rec) == SCHEMA, "schema drift"
            out.append(rec)
        if dropped:
            print(f"  {label}: dropped {len(dropped)} at final scan", file=sys.stderr)
        return out, dropped

    api_out, api_dropped = finalise(api_picked, api_judge, "api")
    tf_out, tf_dropped = finalise(tf_picked, tf_judge, "text_fallback")

    if not args.dry_run:
        OUT_API.write_text(json.dumps(api_out, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        OUT_TF.write_text(json.dumps(tf_out, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    # ---------------- report ---------------------------------------------
    def strat(recs, keyfn):
        return {("/".join(map(str, k)) if isinstance(k, tuple) else str(k)): v
                for k, v in sorted(Counter(keyfn(r) for r in recs).items())}

    vendor_by_model = {c["source_model"]: c["vendor"] for c in api_cands}
    report["api"] = {
        "file": OUT_API.name,
        "pool_rows": api_ids,
        "excluded_rows": api_notes,
        "candidate_records": len(api_cands),
        "emitted": len(api_out),
        "by_vendor_behavior": strat(
            api_out, lambda r: (vendor_by_model[r["source_model"]], r["validator_behavior"])),
        "by_model": strat(api_out, lambda r: r["source_model"]),
        "by_response_format": strat(api_out, lambda r: r["response_format"]),
        "by_judge_label": strat(api_out, lambda r: str(r["judge_label_gpt_high"])),
        "shortfalls": api_short,
        "dropped_at_final_scan": api_dropped,
    }
    report["text_fallback"] = {
        "file": OUT_TF.name,
        "pool_rows": tf_ids,
        "notes": tf_notes,
        "candidate_records": len(tf_cands),
        "excluded_because_already_shipped": tf_excluded_shipped,
        "emitted": len(tf_out),
        "by_format_behavior": strat(
            tf_out, lambda r: (r["response_format"], r["validator_behavior"])),
        "by_model": strat(tf_out, lambda r: r["source_model"]),
        "by_judge_label": strat(tf_out, lambda r: str(r["judge_label_gpt_high"])),
        "shortfalls": tf_short,
        "dropped_at_final_scan": tf_dropped,
    }
    # verdict provenance check: prove validator_behavior is the canonical one
    allpick = api_picked + tf_picked
    report["verdict_provenance"] = {
        "validator_behavior_source": "per_scenario_results_redacted.csv:behavior (canonical, post-patch)",
        "raw_jsonl_behavior_differs_on_emitted": sum(
            1 for c in allpick if c["raw_behavior"] != c["validator_behavior"]),
        "raw_jsonl_success_differs_from_canonical_on_emitted": sum(
            1 for c in allpick if c["raw_success"] != c["canonical_success"]),
        "note": ("the shipped 20-record schema emits no success field, so no "
                 "verdict is written out; validator_behavior is nonetheless "
                 "read from the canonical rescoring, and the raw pre-patch "
                 "results.jsonl success field is never read"),
        "canonical_success_of_emitted": strat(allpick, lambda c: c["canonical_success"]),
    }
    # full-pool sweep: scan every candidate response either group could have
    # drawn, so the scrub report has a denominator rather than only the 40 rows
    # that happened to be sampled.
    pool_hits: Counter = Counter()
    pool_examples: list = []
    benign_hits: Counter = Counter()
    benign_frags: Counter = Counter()
    for c in api_cands + tf_cands:
        for name, frag in scan_text(c["agent_response"]):
            pool_hits[name] += 1
            if len(pool_examples) < 25:
                pool_examples.append({
                    "group": c["_group"], "source_model": c["source_model"],
                    "scenario_id": c["scenario_id"], "run_number": c["run_number"],
                    "pattern": name, "fragment": frag[:160],
                })
        for name, frag in scan_text_benign(c["agent_response"]):
            benign_hits[name] += 1
            benign_frags[frag[:80]] += 1

    emitted_benign = Counter()
    for rec in api_out + tf_out:
        for v in rec.values():
            if isinstance(v, str):
                for name, frag in scan_text_benign(v):
                    emitted_benign[frag[:80]] += 1

    report["scan"] = {
        "identifying_patterns": [p[0] for p in SCAN_PATTERNS],
        "candidate_pool_scanned": len(api_cands) + len(tf_cands),
        "candidate_pool_identifying_hits": dict(pool_hits),
        "candidate_pool_identifying_examples": pool_examples,
        "candidates_rejected_during_selection": dict(scan_rejects),
        "rejection_examples": scan_examples,
        "records_dropped_at_final_scan": len(api_dropped) + len(tf_dropped),
        "emitted_records_scanned": len(api_out) + len(tf_out),
        "emitted_strings_identifying_clean": (not api_dropped and not tf_dropped),
        "benign_reviewed_not_scrubbed": {
            "rule": ("/media/<name> is the benchmark's synthetic media namespace "
                     "(the `path` field of src/nlebench/dataset/fixtures/json/*.json, "
                     "already public in the supplement); models also invent further "
                     "filenames inside it. No real filesystem, user or host is named, "
                     "so these are left intact -- scrubbing them would corrupt the raw "
                     "output the reviewer asked to see."),
            "candidate_pool_hits": dict(benign_hits),
            "candidate_pool_distinct_strings": dict(benign_frags.most_common()),
            "emitted_hits_by_string": dict(emitted_benign.most_common()),
        },
    }

    if not args.dry_run:
        PROVENANCE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "tool_protocol_recovery"},
                     ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
