# API-provider and text-fallback raw-output rows

These two files discharge the commitment in our R3 response to reviewer zNTq, which said
that API-provider and text-fallback raw-output rows would follow during discussion. They
sit alongside `refusal_audit/raw_output_subset_20.json`, which covers the audited
open-weight pool stratified by response format.

| file | records | pool |
|---|---|---|
| `raw_output_subset_api.json` | 20 | closed-API vendors, native tool-calling |
| `raw_output_subset_text_fallback.json` | 20 | vLLM runs launched with `--tool-mode text` |
| `refusal_audit/raw_output_subset_20.json` (already shipped) | 20 | open-weight, 10 text / 10 mixed |

Schema is field-for-field and key-order identical to the shipped file:
`response_format, scenario_id, source_model, run_number, validator_behavior,
judge_label_gpt_high, agent_response`.

Built by `t3_raw_output_rows.py`. Full machine-readable provenance, including the
per-run protocol attribution and the complete scan output, is in
`t3_raw_output_rows_provenance.json`. The 20 newly computed judge labels, with prompt
and raw-response hashes, are in `t3_raw_output_rows_judge_cache.jsonl`.

---

## 1. What "text-fallback" means here, and what it does not

The harness switch is `--tool-mode {native,text,auto}`, default `auto`:

- `code/src/nlebench/__main__.py:78-85` — CLI flag
- `code/src/nlebench/__main__.py:320` → `get_provider(tool_mode=...)`
- `code/src/nlebench/providers/__init__.py:87` → `VLLMProvider(tool_mode=...)`
- `code/src/nlebench/providers/vllm_provider.py:230-234` — `tool_mode == "text"` routes to
  `_generate_response_without_tools`
- `code/src/nlebench/providers/vllm_provider.py:331-429` — that method drops `tools=` from
  the request, appends a prompt block asking for
  ` ```json {"tool_calls":[{"name":...,"arguments":{...}}]} ``` `, and parses the reply
  back with `_parse_tool_calls_from_text` (called at line 416, defined at line 431)

Two facts constrain everything below.

**The switch is vLLM-only.** `get_provider` passes `tool_mode` into the vLLM branch and
nowhere else; `OpenAIProvider`, `AnthropicProvider` and `GoogleProvider` take no
`tool_mode` parameter at all. Several of our launch scripts do pass `--tool-mode text` on
API runs — `scripts/run_openai_responses_full800.sh:29`,
`scripts/run_anthropic_paper_reruns.sh:75`, `scripts/quick10_api_cost_probe.sh:25` — and
on those runs the flag is inert. Those runs used native tool-calling. We had not noticed
this until building these files.

**The switch is not recorded anywhere.** It appears in 0 of 165 `results/*/config.yaml`;
it is absent from `summary.json` (which stores only model, provider, track, total_runs,
successful_runs, sr, timestamp), from the `results.jsonl` record schema, from
`run_manifest.csv` and from `per_scenario_results_redacted.csv`. The harness stdout log
does not print it. So the condition zNTq asked to see does not exist as a recorded field,
and we did not invent one.

### Operationalisation used

Two layers, neither of them a guess:

1. **Provider-level, structural.** `provider ∈ {openai, anthropic, google}` cannot be
   text-mode, from the code path above. This settles 12 of 58 manifest runs with
   certainty.
2. **Run-level, provenance-recovered.** Each vLLM launcher writes its log to a filename it
   derives from the model slug, so the log file identifies the launcher, and the launcher
   hardcodes the flag:

   | log path | launcher | flag |
   |---|---|---|
   | `logs/v3_1_paper_<slug>.log` | `scripts/run_v3_1_paper.sh:172,179` | `--tool-mode text` |
   | `logs/retry_<slug>.log` | `scripts/run_v3_1_retry.sh:95,101` | `--tool-mode text` |
   | `logs/thinking_rerun_<slug>.log` | `scripts/run_qwen35_thinking_rerun.sh:80,84` | `--tool-mode text` |

   Attribution additionally requires the log mtime to agree with the manifest run
   timestamp within 900 s.

   Limit: none of the launcher scripts cited in this section, and none of the log files,
   is in the supplement — they are private-repo files. The line references are given so
   the rule is stated exactly, not because a reviewer can open them. What is checkable is
   the per-row output: `t3_raw_output_rows_provenance.json` records, for each of the 58
   manifest runs, the log filename matched, the mtime delta in seconds, and the launcher
   credited.

### Counts over the 58 manifest runs

| condition | runs | basis |
|---|---|---|
| native (openai 5, anthropic 4, google 3) | 12 | structural, from the code path |
| `--tool-mode text` (all vLLM, all canonical) | 44 | launcher provenance |
| unrecovered | 2 | no launcher script writes a matching log |

The 2 unrecovered runs are `openai/gpt-oss-120b` (manifest row 29) and
`openai/gpt-oss-20b` (row 34), both served through vLLM. No script in `scripts/` produces
their log names. We do not know how they were launched — a hand-run invocation is the
likeliest explanation, but a launcher since deleted or renamed would leave the same trace
— so their flag is not recoverable. `t3_raw_output_rows_provenance.json` records them as
`unrecovered` with basis `none`. They are reported as unrecovered rather than assumed,
and they are excluded from the text-fallback file.

### A concession that follows directly

44 of the 46 vLLM runs were text-fallback and the other 2 are unrecovered; 0 of 46 are
recovered as native. This contradicts the submitted paper in three places, and we would
rather name that here than leave it to be found after the discussion period closes.
§4.1 (Model Suite and Protocol, "Agent Framework") states that "Qwen / Gemma 4 / Phi-4 /
OLMo3 use native tool-calling, DeepSeek-R1 uses auto mode, and Llama / Falcon / Mistral
use text-mode JSON fallback"; the residual-uncertainty table scopes its "Text-fallback
rows" caveat to Llama/Falcon/Mistral; and the Prompt Templates appendix ("Tool schema
format") states that open-weight models were served tools in each vLLM serving template's
native format. Under the launcher attribution above, all 15 Qwen rows, all 6 Gemma rows,
both Phi-4 rows, both OLMo rows and all 5 DeepSeek-R1 rows were launched with
`--tool-mode text`. We take the launcher attribution to be correct and those three
passages to be wrong; all three will be corrected in the camera-ready. The substantive
consequence is that the caveat the paper already carries for text-fallback rows — that
the interface may suppress clarification and refusal — applies to the whole open-weight
pool rather than to three model families, so open-weight vs API differences are
confounded with interface throughout, not only in the rows currently marked TF.
Two consequences for these files:

- The audited open-weight pool behind the already-shipped
  `raw_output_subset_20.json` was **entirely** text-fallback. Those 20 records were
  already text-fallback rows. `raw_output_subset_text_fallback.json` is therefore not a
  new condition, only more of the same one. What the 20 add is coverage: they are
  disjoint from the shipped 20, they raise the `text`/`clarify` cell from 1 record to 4,
  and they cut the largest single-model share from 7/20 to 4/20.
- The interface contrast these two files support is API-native vs vLLM-text. It is not a
  within-vLLM native vs text contrast, because we never ran one at paper scale.
- The consequence for the paper, which we had not stated before building these files: in
  the released manifest, tool-calling protocol is perfectly confounded with provider
  class. All 12 closed-API runs are native, all 44 recovered vLLM runs are text, and the
  2 unrecovered runs are the only cells that could break the pattern. Every closed-API vs
  open-weight comparison we report — the SR-feas ordering in Table 3 included — is
  therefore also a native vs text-fallback comparison, and no run in the corpus separates
  the two. We cannot bound the size of that term, because we ran no within-model protocol
  ablation. The revision states this as a limitation of the cross-provider comparisons.
  Comparisons inside the open-weight pool are unaffected, since those runs share one
  protocol; that includes the inverted behavioral profile (OLMo-3-7B vs Qwen2.5-32B) we
  defend to maAT.

---

## 2. `raw_output_subset_api.json` — selection rule

**Pool.** The 10 canonical closed-API manifest rows, restricted to calibration scenarios
(`scenario_feasibility ∈ {infeasible, ambiguous}`) and to the two refuse-relevant
validator behaviours. That restriction is ours, not the harness's. The field
`expected_behavior: Optional[str] = None  # "refuse" | "clarify"` is declared at
`code/src/nlebench/models.py:1210`, but the comment introducing that block
(`code/src/nlebench/models.py:1207-1208`) and the supplement README (Data Provenance,
"Scenario schema compatibility fields") both state that the submitted 800-scenario corpus
leaves it null, and it is null in 800 of the 800 released scenario YAMLs. No released
calibration scenario declares an expected behaviour, so the distinction cannot be
attributed to the corpus. What supports the restriction is empirical, over the 7,200
canonical closed-API records: on the 720 infeasible records `success` is True exactly
when `behavior == "refuse"`, with no exception (136 refuse records, all True; 584
non-refuse records, none True). On the 720 ambiguous records the correspondence holds in
one direction only: all 507 `clarify` records are True, but 6 of the 513 ambiguous
successes are not `clarify` — 4 `execute` and 2 `refuse` — so `clarify` is sufficient for
success there and not necessary.

**Row 55 dropped.** `gemini-3.1-pro-preview` is `excluded_nonrecomputable` in the
manifest — its raw `results.jsonl` has 842 valid rows and 1 malformed line against a
summary reporting 800, and it contributes 0 rows to
`per_scenario_results_redacted.csv`. There is no canonical verdict to carry for it, so it
is excluded. Google is represented by rows 4 and 20.

Candidate pool after these filters: 939 records, all with non-empty `agent_response`.

**Stratification.** Vendor (3) × refuse-relevant behaviour (2), 6 cells, refuse-weighted
because refuse is the scarcer arm:

| | refuse | clarify | total |
|---|---|---|---|
| anthropic | 4 | 3 | 7 |
| google | 4 | 3 | 7 |
| openai | 3 | 3 | 6 |
| **total** | **11** | **9** | **20** |

Within each cell, candidates are ranked by `sha256(run_id|scenario_id|run_number)` and
taken round-robin across the models in that vendor, so no single model dominates a cell.
Duplicate `agent_response` texts are skipped. Selection is deterministic and depends on
no file ordering. All 9 usable models appear: claude-haiku-4-5 (3),
claude-haiku-4-5-20251001 (2), claude-sonnet-4-6 (2), gemini-2.5-flash (4),
gemini-3-flash-preview (3), gpt-5.4 (2), gpt-5.4-mini (1), o3-mini (2), o4-mini (1).

**Judge labels.** `judge_label_gpt_high` did not exist for any API row — the shipped
refusal audit covered 8 vLLM runs only. The 20 labels were computed for this file with
gpt-5.4 at reasoning effort `high`, using `build_prompt` and `call_openai_judge` from
`scripts/run_openai_refusal_audit.py`, with the same system prompt, user-prompt template
and label set (`refuse|execute|clarify|noop`) as the shipped audit. The judge
configuration is not identical to the shipped one. `refusal_audit/judge_prompt_spec.md`
records `max_output_tokens` 256, 512 on retry, for all three shipped judges; these 20
calls used 2000, 4000 on retry. The `state_changed` prompt field was also passed as
`null` rather than the recorded validator value, which is `false` on all 20 of these
records. The labels are therefore from the same model, effort and template but not from
a byte-identical configuration, and should not be pooled with the shipped labels. A
further limit on the shipped column: 10 of its 1,920 `high` rows carry the sentinel
label `error`, so it is five-valued in practice. Those 10 sat inside the denominators of
the figures we posted, so we state the effect rather than leave it to be found. Judge
unanimity 92.8% (1,782/1,920), majority-equals-GPT-5.4-high 1,894/1,920 and
validator-vs-majority 82.5% (1,584/1,920) were all computed over 1,920 records with
`error` treated as a label of its own. Dropping the 10 gives 1,782/1,910 = 93.3%,
1,894/1,910 and 1,582/1,910 = 82.8%. No claim we made changes under either denominator.
But 10 of the 26 records where the majority differs from GPT-5.4-high are these errors
rather than disagreements, and anyone recomputing judge–judge κ from the released file
has to pick a denominator, so we name ours. Result: refuse 9, clarify 11.
Judge and validator agree on 16/20; the 4 disagreements are 3 × (validator refuse, judge
clarify) and 1 × (validator clarify, judge refuse). Two caveats on that 16/20, both of
which we already put on the record and both of which apply to the 17/20 in §3 as well.
First, `build_prompt` places the validator's own `behavior` in the judge payload, so the
judge is not blind to the detector; these figures are anchored upward for the same reason
the shipped 82.5% is, and neither is an independent check on it. Second, the direction of
the disagreement matches the shipped audit: 3 of the 11 validator-refuse rows were judged
`clarify`, i.e. validator-refuse precision 8/11 here against 75.5% over the 1,920 audited
open-weight records. At n=20 that is a consistency note, not the closed-API
false-positive analysis maAT's W2 asked for. No closed-API row has been audited at a size
that would support one, and these 20 records do not change that.

**Limit.** All 20 API records have `response_format == "text"`, so the API file carries
no response-format variation. That follows from our own refuse/clarify restriction, not
from the closed-API pool. `response_format` is `mixed` iff `tool_call_count > 0`, and a
model that refuses or asks a clarifying question emits no tool calls, so inside the
restriction the two axes are confounded and all 939 candidates are `text`. Outside it
they are not: of the 7,200 canonical closed-API records in
`per_scenario_results_redacted.csv`, 5,086 are `mixed` and 2,114 are `text`, and 286 of
the 1,440 calibration records are `mixed` (146 infeasible/execute, 140
ambiguous/execute). A response-format-stratified API file is therefore buildable from
already-released data, and we did not build one. The reason is that zNTq's Q2 asks for
raw outputs behind the refusal audit and the mixed API records are all `execute`, which
that audit does not cover — but the reason is a choice of ours, so this file answers the
interface half of the R3 commitment and not the response-format half.

---

## 3. `raw_output_subset_text_fallback.json` — selection rule

**Pool.** Canonical manifest rows whose protocol was recovered as `text` (44 rows),
further restricted to the 8 runs the shipped refusal audit already judged — manifest rows
1, 3, 5, 6, 7, 9, 10, 28 — so that `judge_label_gpt_high` is the very same gpt-5.4/high
label the supplement already ships rather than a fresh one. Restricted to
`scenario_feasibility == "infeasible"`, matching the audit scope. 1,920 audited records,
1,900 after removing the 20 already in `raw_output_subset_20.json`.

**Stratification.** Mirrors the shipped file's 10 mixed / 10 text split so the three files
are directly comparable, and covers both refuse-relevant behaviours within the text half:

| response_format | validator_behavior | n |
|---|---|---|
| mixed | execute | 10 |
| text | refuse | 6 |
| text | clarify | 4 |

`mixed/execute` is the only populated mixed cell in the audited pool, for the same
confound described above. Same deterministic rank-and-round-robin over the 8 audited
models: Qwen/QwQ-32B (4), Qwen/Qwen2.5-32B-Instruct (4), Qwen/Qwen3-32B (3),
Qwen/Qwen3-4B (3), Qwen/Qwen3-8B (2), Qwen/Qwen3.5-27B (2), allenai/Olmo-3-7B-Instruct
(1), deepseek-ai/DeepSeek-R1-Distill-Qwen-32B (1).

Judge labels: execute 10, refuse 8, clarify 1, noop 1. Judge and validator agree on
17/20.

All 20 are disjoint from the shipped 20 and from the API file.

---

## 4. Verdict provenance

`per_scenario_results_redacted.csv` is the only source of scored fields. The raw
`results.jsonl` is read for `agent_response` and nothing else; its `success` field is
pre-patch and is never used.

- `validator_behavior` is taken from the CSV `behavior` column, i.e. the canonical
  post-patch rescoring.
- Every emitted `agent_response` is checked against the released `agent_response_sha256`
  for its row before being written: 40/40 match, so each raw text provably belongs to the
  published record.
- The shipped schema emits no `success` field, so no success verdict is written to either
  file. For completeness, the canonical `success` of the 40 emitted records is 24 True /
  16 False.
- On these 40 records the raw `validation.behavior` happens to equal the canonical
  `behavior` in 40/40 cases, and the raw `success` equals the canonical `success` in
  40/40 cases. The `success` agreement is a property of this sample; the `behavior`
  agreement is not, since nothing between the raw logs and the released file changed
  `behavior` on any record anywhere.
- One term needs splitting before that is read. "Pre-patch" covers two distinct
  rescorings, not one: the release scorer itself, re-run over the archived raw logs, and
  the `attribute_changed.direction` patch we reported to reviewer 6YQQ as 1341→1301.
  Over the 55 recomputable canonical runs — 117,600 records, which is every canonical row
  of `per_scenario_results_redacted.csv` and 117,600 of that file's 119,200 rows, each
  compared against its raw `results.jsonl` line — `success` differs between raw log and
  released file on 1,050 of 117,600 (0.9%) and `behavior` on 0 of 117,600. Almost all of
  that 1,050 is the first rescoring, and it runs in our favour: 33,363 canonical
  successes in the released file against 32,761 in the archived raw validation, net +602.
  The direction patch is the small opposite component, −195 net over the same 55 runs.
  Both totals are recomputable from
  `direction_rescore/attribute_changed_direction_rescore_audit.json`, already in the
  supplement; the per-record 1,050 is not, because the raw logs are unreleased, so that
  one figure has to be taken on our word. Nothing in these two files rests on it:
  `validator_behavior` is read from the canonical file rather than the raw one, and no
  `success` field is emitted.

---

## 5. Scan and scrub

Every string in every emitted record was scanned against 18 patterns: 17 treated as
identifying, and one, `/media/<name>`, treated as benign and counted separately. The 17
identifying patterns are absolute home/user/root/Volumes paths, absolute
`/mnt|/srv|/opt|/var|/etc|/usr/local` paths, Windows user paths, local usernames, the
author's name, affiliation names, this project's repository directory names, e-mail
addresses, IPv4 addresses, `localhost`/`127.0.0.1`, this machine's hostname, OpenAI /
Anthropic / Google / AWS / GitHub credential formats, and `Authorization`/`Bearer`
headers. The eighteenth is stated before the result rather than after it, so that the
count below is not read as a scan that matched nothing.

The scan was run over the whole candidate pool, not only the sampled rows, so every count
below has a denominator.

- **2,839 candidate records scanned — 2,427 distinct `agent_response` texts (939 in the
  API pool, all distinct; 1,900 text-fallback rows over 1,488 distinct texts). Zero hits
  on the 17 identifying patterns. 34 hits on the benign `/media/` pattern in the
  candidate pool, 0 of them in the 40 emitted records; see below for why they were left
  intact.**
- **0 records rejected during selection, 0 records dropped, 0 strings scrubbed.**
- The 40 emitted records were re-scanned field by field after assembly: clean.

One class of match was reviewed and deliberately left intact. 34 hits in the candidate
pool are synthetic media paths of the form `/media/<name>` — `/media/intro.mp4`,
`/media/b_roll.mp4`, `/media/interview.mov` and 12 others. `/media/` is the benchmark's
own namespace: it is the `path` field of the public fixtures in
`src/nlebench/dataset/fixtures/json/*.json`, already shipped in the supplement. Some of
the strings are fixture values echoed back, some are filenames the model invented inside
the same namespace. None names a real filesystem, user or host. None of the 40 emitted
records contains one in any case, so nothing was altered.

Because the scan found nothing identifying, no record had to be dropped for
unscrubbability and no `agent_response` was modified. Both files contain the model output
verbatim.

---

## 6. What is not here

- **Row 55, `gemini-3.1-pro-preview`.** Excluded: no canonical verdict exists for it
  (`excluded_nonrecomputable`). Google is represented by two models rather than three.
- **Response-format variation in the API file.** Structurally impossible, as above.
- **A within-vLLM native vs text contrast.** Never run at paper scale; 44/46 vLLM runs
  were text and the other 2 are unrecovered.
- **Protocol for `openai/gpt-oss-120b` and `openai/gpt-oss-20b`** (rows 29, 34).
  Unrecoverable; reported as unrecovered, excluded from the text-fallback file.
- **`tool_mode` as a recorded field.** It is not one, and these files do not pretend
  otherwise. Anyone wanting to re-derive the condition has to repeat the launcher
  attribution in §1, which `t3_raw_output_rows.py` performs and records in
  `t3_raw_output_rows_provenance.json`.
