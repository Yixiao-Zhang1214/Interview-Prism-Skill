# Mock Interview Prompts: P10-P11

## Contents

- P10 dynamic interviewer turn
- P11 end-of-session training evaluation

Load CORE-01, the selected persona, the rubric when evaluating, and the data contracts.

## P10 Dynamic Interviewer Turn

```text
Task: Act as the selected mock interviewer and generate exactly one current question. Do not coach, teach, reveal the rubric, or evaluate during the interview.

Input payload:
{
  "source_type": "mock",
  "mock_session_id": "mock_xxx",
  "persona": {},
  "target_role": {},
  "training_objectives": [],
  "conversation_history": [],
  "turn_no": 1,
  "limits": {"max_turns": 8}
}

Rules:
1. If source_type is not mock, return blocked.
2. Echo the code-created mock_session_id exactly; never create or modify it.
3. Return one main question or one follow-up, never a question bank.
4. Base a follow-up on content in the user's latest actual answer.
5. Let persona change pace and angle only; keep respect and evidence standards fixed.
6. Do not reveal target weaknesses, ideal answers, scoring anchors, or an evaluation.
7. Set should_stop=true at the turn limit or when all objectives are sufficiently observed.
8. Do not assist a user covertly during a live hiring interview.

Output data:
{
  "mock_session_id": "mock_xxx",
  "turn_no": 1,
  "question_type": "opening|main|follow_up|challenge",
  "question": "...",
  "target_objective_ids": ["obj_001"],
  "should_stop": false,
  "stop_reason": null
}
```

Code verifies the session ID, turn number, maximum turns, and one-question limit before storing the turn.

## P11 End-of-Session Training Evaluation

```text
Task: Evaluate the completed mock session with the same behavioral anchors used for real interviews, but write every result to the training ledger only.

Input payload:
{
  "source_type": "mock",
  "mock_session_id": "mock_xxx",
  "training_objectives": [],
  "mock_qa_chains": [],
  "segments": [],
  "rubric": {},
  "pass_criteria": [],
  "existing_tasks": []
}

Rules:
1. Fix source_type=mock and is_training_evaluation=true.
2. Evaluate only this mock session; do not mix historical real scores into single-session levels.
3. Cite answer segment IDs for each objective and competency observation.
4. Propose training_pass_recommendation only. Code decides state after checking pass_criteria.
5. Never output real_validated, mastered, or a formal-profile update.
6. A passed objective may produce waiting_real_validation with a concrete real interview condition.
7. Generate at most three task proposals and preserve source_mock_session_id.
8. Use null level when the mock session did not expose enough evidence.

Output data:
{
  "mock_session_id": "mock_xxx",
  "source_type": "mock",
  "is_training_evaluation": true,
  "objective_results": [
    {
      "objective_id": "obj_001",
      "result": "met|partially_met|not_met|insufficient_information",
      "evidence_segment_ids": ["seg_mock_002"],
      "confidence": "high",
      "uncertainty_reasons": []
    }
  ],
  "competency_observations": [
    {
      "dimension": "structured_communication",
      "level": 4,
      "rubric_anchor_id": "shared-level-4",
      "evidence_segment_ids": ["seg_mock_002"],
      "confidence": "high",
      "uncertainty_reasons": []
    }
  ],
  "training_pass_recommendation": "pass|retry|insufficient_information",
  "waiting_real_validation": [
    {
      "dimension": "structured_communication",
      "validation_condition": "Reach level 4 in a later real interview answer"
    }
  ],
  "task_proposals": [
    {
      "action": "create|link_existing",
      "existing_task_id": null,
      "source_mock_session_id": "mock_xxx",
      "title": "...",
      "task_type": "pressure_follow_up",
      "source_qa_chain_ids": ["qa_mock_001"],
      "root_cause": "communication",
      "steps": ["..."],
      "acceptance_criteria": ["..."],
      "suggested_duration_days": 3,
      "real_validation_condition": "...",
      "priority": "high"
    }
  ],
  "information_gaps": []
}
```

Code rejects a result whose source type, mock session ID, evidence IDs, or task lineage differs from the input.

