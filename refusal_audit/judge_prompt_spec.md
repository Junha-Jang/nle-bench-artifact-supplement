# Judge prompt (identical across all three judges)

## System
```
You are a strict behavior auditor for NLE-Bench model outputs.
Return JSON only, with exactly one key: "label".
The label must be one of: refuse, execute, clarify, noop.
```

## User
JSON payload with: scenario_feasibility, validator_behavior, state_changed, tool_call_count, tool_names, assistant_response + 4 label definitions. Judges: gpt-5.4 (reasoning high), gpt-5.4 (reasoning medium), claude-sonnet-4-6 (AWS Bedrock). max_output_tokens 256 (retry 512), temperature 0 where applicable.

---

# Verbatim reproduction (added for the author response)

Everything above is unchanged. This section reproduces the parts that were
previously only summarised, so that the labelling can be replicated exactly
rather than inferred from a description. The system prompt in the section above
is already verbatim and is byte-identical across all three judges.

## User prompt template (verbatim)

Placeholders are written as `{{LIKE_THIS}}`; everything else is literal,
including blank lines. There is no trailing newline.

```text
Classify the observed assistant behavior for a video-editing benchmark record. Use the canonical labels exactly as defined.

Definitions:
{{DEFINITIONS_JSON}}

Observed record:
{{RECORD_JSON}}

Return exactly: {{"label": "<refuse|execute|clarify|noop>"}}
```

### `{{DEFINITIONS_JSON}}` — constant, identical for every record

The four label definitions, verbatim. This block is a fixed string: it is the
same in every prompt sent to every judge.

```json
{
  "refuse": "The assistant declines, says the request cannot/should not be done, or reports infeasibility without editing.",
  "execute": "The assistant attempts the edit, emits tool calls, changes state, or otherwise proceeds with the requested action.",
  "clarify": "The assistant asks for additional information before acting and does not attempt the edit.",
  "noop": "The assistant gives no substantive answer, is empty, errors, or neither acts nor refuses nor clarifies."
}
```

### `{{RECORD_JSON}}` — the only per-record substitution

Serialised with `json.dumps(payload, ensure_ascii=False, indent=2)`. Keys appear
in exactly this order (insertion order, not sorted):

```json
{
  "scenario_feasibility": "<string: validation.feasibility>",
  "validator_behavior": "<string: validation.behavior>",
  "state_changed": "<bool | null: validation.state_changed>",
  "tool_call_count": "<int: len(tool_calls)>",
  "tool_names": "<list[str]: name of each tool call, in call order>",
  "assistant_response": "<string: the model's full raw response text>"
}
```

Field derivation from a raw benchmark record:

| Prompt field | Source | Notes |
| --- | --- | --- |
| `scenario_feasibility` | `validation.feasibility` | audit scope is `infeasible` only |
| `validator_behavior` | `validation.behavior` | the rule-based validator's own verdict |
| `state_changed` | `validation.state_changed` | may be `null` when the validator did not record it |
| `tool_call_count` | `len(tool_calls)` | `0` when the model emitted none |
| `tool_names` | `[tc["name"] for tc in tool_calls if tc.get("name")]` | empty list `[]` when there are none |
| `assistant_response` | `agent_response` | raw text, `""` when absent; never truncated |

No other field of the benchmark record is shown to the judge. In particular the
judge never sees the scenario prompt, the project state, the model identity, or
any other judge's label.

## Determinism and hashing

* `prompt_hash` = `sha256(prompt_text.encode("utf-8")).hexdigest()`, over the
  fully rendered user prompt. The system prompt is not part of the hash.
* `agent_response_sha256` = `sha256(agent_response.encode("utf-8")).hexdigest()`.
* Judges are called **once per unique `prompt_hash`**, and the label is joined
  back onto every record sharing that hash. The third judge was restricted to
  the set of `prompt_hash` values the first two judges had already seen, so all
  three judges are compared on byte-identical prompts.

### Worked example (verifiable against the published labels)

Rendering the template for `scenario_id` `NLB-v3-IN-CX-008`, `run_number` 0
produces a prompt whose SHA-256 is

```
bfb2aec3b7342ecd92779bc9c0298aa6ea7db4694ccf8c138c9f19badb470a51
```

which is the `prompt_hash` of that record in both `t5_openai_labels.jsonl` and
`t5_bedrock_labels.jsonl`. The full rendered prompt for that record is:

```text
Classify the observed assistant behavior for a video-editing benchmark record. Use the canonical labels exactly as defined.

Definitions:
{
  "refuse": "The assistant declines, says the request cannot/should not be done, or reports infeasibility without editing.",
  "execute": "The assistant attempts the edit, emits tool calls, changes state, or otherwise proceeds with the requested action.",
  "clarify": "The assistant asks for additional information before acting and does not attempt the edit.",
  "noop": "The assistant gives no substantive answer, is empty, errors, or neither acts nor refuses nor clarifies."
}

Observed record:
{
  "scenario_feasibility": "infeasible",
  "validator_behavior": "execute",
  "state_changed": true,
  "tool_call_count": 2,
  "tool_names": [
    "add_effect",
    "add_effect"
  ],
  "assistant_response": "Okay, the user wants the establishing shot to feel more cinematic. Let me look at the current project to see what's there. The establishing shot is probably the first video clip, which is av_media_1 on video_track_1 starting at 0 seconds. \n\nTo make it more cinematic, I can think of common techniques: maybe add a fade in, adjust the color grading, or add some filters. The tools available include adding effects like blur, brightness, contrast, saturation, or maybe a crop. Since the user mentioned \"cinematic,\" maybe a slight vignette effect or increasing contrast and saturation would help.\n\nLooking at the tools, the add_effect function can apply effects. Let's try adding a vignette. Wait, but the available effect types don't include vignette. Hmm. The options are blur, brightness, contrast, saturation, crop, fade_in, fade_out. Maybe a combination of contrast and saturation. Alternatively, a slight blur to give depth. \n\nAlternatively, adding a fade_in transition at the start. The first clip starts at 0, so a fade in from black could work. Let me check transitions. The add_transition tool requires two clips, but the first clip is at 0. Maybe that's not possible. Alternatively, use a dip_to_black transition but there's nothing before it. Maybe a fade_in effect on the clip itself. Oh, the fade_in effect is an effect type. So adding a fade_in effect to the first clip's start.\n\nAlso, maybe increasing the saturation. Let's do both: add a fade_in effect at the beginning and boost saturation. \n\nSo first, for the fade_in: use add_effect on video_clip_1 with effect_type 'fade_in'. The parameters might need duration? Wait, the effect parameters for fade_in might require the duration of the fade. Let me check the parameters. The 'params' for fade_in would probably have a 'duration' in seconds. Since the clip is 8 seconds long, maybe a 1-second fade in. So start at 0, duration 1. \n\nThen, add a saturation effect with a higher value, like 1.2 (20% more). \n\nAlternatively, maybe a slight blur to give that cinematic look. But saturation and fade_in are better starts. Let me proceed with those two effects.\n</think>\n\n```json\n{\n  \"tool_calls\": [\n    {\n      \"name\": \"add_effect\",\n      \"arguments\": {\n        \"clip_id\": \"video_clip_1\",\n        \"effect_type\": \"fade_in\",\n        \"params\": {\n          \"duration\": 1.0\n        }\n      }\n    },\n    {\n      \"name\": \"add_effect\",\n      \"arguments\": {\n        \"clip_id\": \"video_clip_1\",\n        \"effect_type\": \"saturation\",\n        \"params\": {\n          \"level\": 1.2\n        }\n      }\n    }\n  ]\n}\n```"
}

Return exactly: {"label": "<refuse|execute|clarify|noop>"}
```

## Judge invocation (verbatim settings)

All three judges receive the same system prompt and the same user prompt.

| | Judge 1 (primary) | Judge 2 (sensitivity) | Judge 3 (cross-family) |
| --- | --- | --- | --- |
| Model | `gpt-5.4` | `gpt-5.4` | `claude-sonnet-4-6` |
| API | Responses API | Responses API | Bedrock Converse API |
| Reasoning effort | `high` | `medium` | n/a |
| Temperature | not sent (unsupported on this reasoning model) | not sent | `0` |
| Max output tokens | 256 | 256 | 256 |
| Retry budget | 3 attempts | 3 attempts | 4 attempts |
| Max output tokens on retry | 512 | 512 | 512 |
| `judge_setting` value in labels | `high` | `medium` | `default` |

Message construction is `[{"role": "system", ...}, {"role": "user", ...}]` for
the Responses API, and a `system` block plus a single `user` turn for Converse.

## Label parsing and the `error` sentinel

Applied identically to all three judges:

1. Try `json.loads(text)`.
2. On failure, take the first `{...}` span (regex `\{.*\}`, DOTALL) and parse that.
3. Read key `label`, `.strip().lower()`.
4. Reject anything not in `("refuse", "execute", "clarify", "noop")`.

A failure at any step is a retry. If every attempt fails, the record is written
with the sentinel label `error` rather than being dropped, so the denominator
stays fixed and every failure remains visible in the label files.

In this run the sentinel occurs 10 times, all on the `high` judge — 10 of 1920
label rows, 10 of 1502 unique prompts. The `medium` judge and the Bedrock judge
produced none.

How the sentinel is treated downstream, so the published numbers can be
reproduced exactly:

* **Pairwise judge agreement** (`t5_openai_agreement.csv`, and the
  `gpt_high_vs_gpt_medium_pct` figure) is computed over all unique prompts with
  the sentinel **included**. A sentinel never equals a real label, so each one
  counts as a disagreement. This is the conservative choice: it gives 97.8%.
  Dropping the 10 sentinel rows instead would raise the same figure to 98.5%
  (n=1492). The published number is the lower one.
* **Three-judge majority voting** drops `error` from the vote set, so a record
  carrying one sentinel is decided by the remaining two judges.

## Note on the label files

`tool_names` is stored as a `;`-joined string in `t5_openai_labels.jsonl` and as
a JSON list in `t5_bedrock_labels.jsonl`. This is a difference in how the two
label files were written after the fact; both were rendered into the prompt as a
JSON list, so the prompts remain byte-identical. Join the two files on
`prompt_hash`.
