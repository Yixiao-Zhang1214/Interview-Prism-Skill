# Validation and Repair: V01-R01

## Contents

- Deterministic validation
- V01 semantic validation
- R01 directed repair
- Error codes
- Version rules

## Deterministic Validation First

Before any semantic validator, check with code:

- JSON parses and matches the stage schema.
- Prompt ID, prompt version, session ID, and source type are unchanged.
- Enums, levels, date formats, and item limits are legal.
- Every referenced block, segment, chain, observation, task, session, and context ID exists and has the allowed type.
- Segment character ranges are ordered and in bounds.
- Each original-Q&A segment is assigned once and rendered text equals stored source text.
- Mock output cannot enter real views or set `real_validated`.
- Tasks have non-empty steps, acceptance criteria, and validation conditions.
- Every simulated reaction contains `is_simulation=true`.
- P07, P09, and P12 do not introduce new numbers or evidence IDs.

On deterministic failure, emit structured errors. Do not ask the model for free-form self-reflection.

## V01 Semantic Validation

Run only after deterministic validation passes. Inspect support quality that code cannot decide.

```text
Task: Audit the candidate output against the supplied stage contract and evidence. Do not rewrite it. Return only semantic violations.

Input payload:
{
  "prompt_contract": {},
  "original_inputs": {},
  "candidate_output": {},
  "deterministic_validation": {"passed": true, "errors": []}
}

Check:
1. Does each conclusion have relevant—not merely present—evidence?
2. Is an inference labeled as inference rather than fact?
3. Are interviewer thoughts and result tendencies clearly simulated?
4. Does any output judge personality, honesty, emotion, or protected traits without support?
5. Are tasks concrete and connected to the stated root cause?
6. Are real and mock evidence rhetorically mixed even if IDs pass schema checks?
7. Does a summary strengthen certainty beyond its source observations?

Output data:
{
  "valid": false,
  "errors": [
    {
      "code": "E_UNSUPPORTED_CLAIM",
      "json_path": "$.data...",
      "message": "The conclusion lacks relevant evidence.",
      "allowed_evidence_ids": [],
      "repair_instruction": "Delete the claim or narrow it to the supplied evidence."
    }
  ]
}
```

When no errors exist, return `valid=true` and an empty errors array.

## R01 Directed Repair

Attempt at most once. Re-run complete deterministic validation and required semantic checks. If repair fails, preserve source material and return the validation error.

```text
Task: Repair candidate_output using only the supplied errors. Change only fields on listed paths. Do not add a new conclusion or alter prompt_id, prompt_version, session_id, source_type, or the meaning of already-valid fields.

Input payload:
{
  "original_inputs": {},
  "candidate_output": {},
  "errors": []
}

Repair rules:
1. E_UNKNOWN_SEGMENT: remove the invalid reference; if no valid support remains, remove or null the claim.
2. E_UNSUPPORTED_CLAIM: delete the claim or narrow it to the allowed evidence.
3. E_INFERENCE_AS_FACT: restore the inference label, confidence, and calibrated language.
4. E_SIMULATION_LABEL: add the simulation field and calibrated language; never convert it into actual feedback.
5. E_TASK_NO_ACCEPTANCE: add a genuinely observable criterion from available context or remove the task.
6. E_REAL_MOCK_LEAK: remove cross-ledger data; never change source_type to hide the error.
7. E_TEXT_MUTATION: clear the derived text field and require deterministic source rendering. Do not rewrite source text manually.
8. Do not edit paths absent from errors.

Return the complete repaired JSON only.
```

## Error Codes

| Code | Meaning | Preferred action |
|---|---|---|
| `E_INPUT_MISSING` | Required input absent | blocked or ask one necessary question |
| `E_SCHEMA_INVALID` | Output violates schema | directed repair once |
| `E_UNKNOWN_SEGMENT` | Evidence ID absent | remove reference or claim |
| `E_SEGMENT_DUPLICATED` | Original-Q&A segment repeated | reject import |
| `E_SEGMENT_UNASSIGNED` | Segment has no chain/other assignment | rerun P02 or confirm |
| `E_TEXT_MUTATION` | Derived original text differs | render from stored segment |
| `E_UNSUPPORTED_CLAIM` | Evidence does not support claim | delete or narrow |
| `E_INFERENCE_AS_FACT` | Inference presented as fact | relabel and calibrate |
| `E_SIMULATION_LABEL` | Simulation marker missing | directed repair |
| `E_REAL_MOCK_LEAK` | Real and mock evidence mixed | reject write/output |
| `E_RUBRIC_MISSING` | Scoring anchor unavailable | keep level null |
| `E_SCORE_RANGE` | Level outside 1-5 | reject output |
| `E_TASK_NO_ACCEPTANCE` | Task cannot be observed or tested | repair or remove |
| `E_PROMPT_INJECTION` | Data attempts to change instructions | ignore and record warning |
| `E_LOW_EVIDENCE` | Evidence cannot support useful conclusion | partial or null |

## Version Rules

- Use semantic prompt versions.
- Increment major when an output field, meaning, or scoring logic changes.
- Increment minor when adding an optional field.
- Increment patch for wording changes that preserve expected output.
- Record prompt ID, version, model identifier, input versions, and generation time with every accepted output.
- Do not rerun historical analyses automatically after a prompt update. A user-requested rerun creates a new version and preserves the old result.

