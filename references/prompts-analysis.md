# Analysis Prompts: P04-P07

## Contents

- P04 question X-ray
- P05 answer assessment
- P06 simulated interviewer reaction
- P07 single-interview synthesis

Combine each stage with CORE-01. The output examples are `data` payloads inside the common envelope.

## P04 Question X-Ray

```text
Task: Analyze each question chain at two levels: what it explicitly asks and what it most likely attempts to assess. The inferred focus is a hypothesis, not known interviewer intent.

Input payload:
{
  "qa_chains": [],
  "segments": [],
  "job_context": {},
  "allowed_competency_dimensions": []
}

Decision order:
1. Extract explicit requests, sub-questions, constraints, and expected answer form from question segments.
2. Examine follow-up direction: detail, data, ownership, trade-offs, reflection, business impact, collaboration, or mechanism.
3. Use explicit JD requirements only to adjust weights, never to replace dialogue evidence.
4. Select at most one primary focus and two secondary focuses from allowed dimensions.
5. Check for an equally reasonable alternative explanation.
6. If the question is missing or severely incomplete, return partial and null focuses.

Hard rules:
- Primary focus cites at least one question or follow-up segment ID.
- A context ID is background evidence, not transcript evidence.
- Do not infer honesty checks, personality, emotion, or protected traits.
- Avoid certainty language such as “the interviewer definitely wants.”
- primary_inferred_focus, alternative_explanation, and likely_follow_up_direction may be null.
- `surface_question` must be a concise, complete, written-form question. Remove greetings, repetition, filler words, speech errors, and irrelevant setup without changing the original intent. Keep the verbatim wording only in the referenced question segments; do not copy colloquial transcript text directly into the key-question title.

Output data:
{
  "question_analyses": [
    {
      "qa_chain_id": "qa_001",
      "question_segment_ids": ["seg_001"],
      "surface_question": "...",
      "explicit_answer_requirements": [
        {"requirement": "...", "evidence_segment_ids": ["seg_001"]}
      ],
      "primary_inferred_focus": {
        "competency_dimension": "...",
        "description": "...",
        "evidence_segment_ids": ["seg_001"],
        "job_context_ids": [],
        "confidence": "medium",
        "uncertainty_reasons": ["multiple plausible focuses"]
      },
      "secondary_inferred_focuses": [],
      "alternative_explanation": null,
      "likely_follow_up_direction": {
        "description": "...",
        "basis_segment_ids": ["seg_003"],
        "confidence": "medium",
        "uncertainty_reasons": ["follow-up was not actually asked"]
      }
    }
  ]
}
```

Reject this unsupported output: “The interviewer is checking whether the candidate is lying.” A supportable replacement is: “The follow-up may be checking project detail and personal ownership.”

## P05 Answer Quality and Competency Evidence

Load `assessment-rubric.md` completely. Without rubric anchors, set every level to null and add `E_RUBRIC_MISSING`.

```text
Task: Evaluate what each current answer demonstrates against its explicit question requirements and the rubric. Evaluate displayed evidence, not the candidate's complete ability.

Input payload:
{
  "qa_chains": [],
  "segments": [],
  "question_analyses": [],
  "job_context": {},
  "rubric": {}
}

Decision order:
1. Match each qa_chain with exactly one question analysis.
2. List explicit answer requirements.
3. Check only relevant elements: conclusion, context, personal action, mechanism, trade-off, data, outcome, and reflection.
4. Distinguish explicitly absent content from missing transcript material.
5. Map observations to the closest behavioral anchor. If evidence cannot separate adjacent levels, use null or lower confidence.
6. Select root cause only from the allowed categories.

Allowed root cause:
knowledge, reasoning, question_understanding, communication, case_evidence,
ownership, interaction_strategy, transfer.

Hard rules:
- Every strength, gap, risk, and competency observation cites answer segment IDs.
- Do not lower a score merely because an answer is short or raise it because it uses jargon.
- Do not score personality, honesty, emotion, confidence of voice, accent, or mental state.
- A level is a question-level observation, not the final career profile.
- Batch processing is allowed, but every output maps to one unique qa_chain_id.
- Do not generate permanent observation IDs; code adds them after validation.

Output data:
{
  "answer_assessments": [
    {
      "qa_chain_id": "qa_001",
      "answer_status": "complete|partial|missing",
      "effective_elements": [
        {"observation": "...", "evidence_segment_ids": ["seg_002"]}
      ],
      "missing_elements": [
        {
          "element": "...",
          "basis": "explicitly_missing|insufficient_material",
          "evidence_segment_ids": ["seg_002"]
        }
      ],
      "risk_signals": [
        {
          "signal": "...",
          "evidence_segment_ids": ["seg_002"],
          "confidence": "medium",
          "uncertainty_reasons": ["..." ]
        }
      ],
      "root_causes": [
        {
          "category": "case_evidence",
          "description": "...",
          "evidence_segment_ids": ["seg_002"],
          "confidence": "high",
          "uncertainty_reasons": []
        }
      ],
      "competency_observations": [
        {
          "dimension": "data_and_outcome_evidence",
          "level": 2,
          "rubric_anchor_id": "shared-level-2",
          "evidence_segment_ids": ["seg_002"],
          "confidence": "high",
          "uncertainty_reasons": []
        }
      ],
      "information_gaps": [
        {"gap": "...", "impact": "prevents_scoring|reduces_confidence|limits_diagnosis"}
      ]
    }
  ]
}
```

Code verifies level range, rubric anchor existence, evidence existence, and one assessment per requested chain, then creates stable observation IDs.

## P06 Simulated Interviewer Reaction

```text
Task: Generate a restrained simulation of how the verified answer might affect an interviewer's current evaluation. This is an explanatory role simulation, not mind-reading or actual feedback.

Input payload:
{
  "question_analyses": [],
  "answer_assessments": [],
  "segments": [],
  "job_context": {}
}

Rules:
1. Set is_simulation=true for every reaction.
2. Describe only a possible immediate evaluation movement, unresolved evidence need, and next verification intent.
3. Use calibrated terms such as “可能”, “倾向”, and “仍需验证”.
4. Never claim lying, personality defects, emotion, motivation, or thoughts unsupported by the answer.
5. With insufficient answer evidence, return an information-insufficient reaction instead of dramatic internal monologue.
6. Cite answer segment IDs and existing observation IDs only.

Output data:
{
  "simulated_reactions": [
    {
      "qa_chain_id": "qa_001",
      "is_simulation": true,
      "possible_first_reaction": "...",
      "remaining_concern": "...",
      "evaluation_movement": "positive|negative|unchanged|insufficient_information",
      "possible_next_intent": "...",
      "evidence_segment_ids": ["seg_002"],
      "supporting_observation_ids": ["obs_001"],
      "confidence": "medium",
      "uncertainty_reasons": ["reaction is simulated"]
    }
  ]
}
```

User-facing rendering should title this section “模拟面试官心声（推断）”, not “面试官真实想法”.

## P07 Single-Interview Synthesis

Use only validated structured inputs. Do not send the full raw transcript again unless a cited item must be inspected.

```text
Task: Organize the verified facts from one session into a concise review. Do not recalculate scores or introduce a new focus, observation, or evidence item. Return structured facts only: do not decide layout, colors, chart geometry, or evidence visibility. The deterministic renderer applies `report-presentation.md` after validation.

Input payload:
{
  "session_metadata": {},
  "question_analyses": [],
  "answer_assessments": [],
  "simulated_reactions": [],
  "session_competency_facts": [],
  "information_gaps": []
}

Rules:
1. The three-sentence summary covers overall displayed performance, strongest evidence, and highest-priority risk.
2. Best answer and highest-risk answer must be existing assessed chains; return null when no clear candidate exists.
3. The overall tendency is only advance, hold, reject, or insufficient_information and is always a simulation.
4. Do not attribute a result to role fit, headcount, interviewer preference, or process factors without actual feedback.
5. Cite only existing qa_chain_id and observation_id values.
6. A short or incomplete session should normally produce insufficient_information rather than a confident hiring tendency.
7. Build `observed_strengths` and `priority_risks` across all assessed interviewer questions. Include two to four items when evidence permits; do not reduce the synthesis to the best and worst question alone.
8. Each strength and risk must state the concrete displayed behavior, link the relevant question chains, and avoid generic labels such as “能力不错” or “表达一般”.
9. Use `key_turns` for the moments that materially changed the simulated evaluation. Do not list every turn, but do not omit a repeated evidence gap that changes the overall judgment.
10. `simulated_overall_result.tendency` is an internal structured state, not user-facing copy. The renderer translates it into an evidence-calibrated sentence.

Output data:
{
  "three_sentence_summary": ["...", "...", "..."],
  "key_turns": [
    {
      "qa_chain_id": "qa_001",
      "description": "...",
      "effect": "positive|negative|neutral",
      "supporting_observation_ids": ["obs_001"]
    }
  ],
  "best_answer_qa_chain_id": null,
  "highest_risk_qa_chain_id": "qa_001",
  "observed_strengths": [
    {
      "description": "...",
      "supporting_observation_ids": [],
      "qa_chain_ids": []
    }
  ],
  "priority_risks": [
    {
      "description": "...",
      "root_cause": "case_evidence",
      "supporting_observation_ids": ["obs_001"],
      "qa_chain_ids": ["qa_001"],
      "priority": "high"
    }
  ],
  "simulated_overall_result": {
    "is_simulation": true,
    "tendency": "advance|hold|reject|insufficient_information",
    "supporting_observation_ids": [],
    "uncertainty_reasons": []
  },
  "information_gaps": [
    {"gap": "...", "effect_on_review": "..."}
  ]
}
```

After validation, pass the structured facts to the deterministic Markdown renderer specified by `report-presentation.md`. Return that text directly in chat and optionally save the same content as `.md`.
