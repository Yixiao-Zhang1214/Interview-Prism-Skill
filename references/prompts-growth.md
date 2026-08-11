# Growth Prompts: P08-P09

## Contents

- P08 growth tasks and knowledge candidates
- P09 multi-interview competency profile interpretation

Combine each stage with CORE-01. Inputs must already pass deterministic validation.

## P08 Growth Tasks and Knowledge Candidates

```text
Task: Convert high-priority, improvable risks into a small set of executable tasks and propose links to the existing question/knowledge library. Do not directly mutate task state or permanently merge knowledge items.

Input payload:
{
  "priority_risks": [],
  "answer_assessments": [],
  "existing_tasks": [],
  "existing_knowledge_items": [],
  "user_constraints": {}
}

Rules:
1. Each task addresses one clear root cause and cites its originating question chains.
2. Prefer link_existing when an open task already addresses the same root cause and validation condition.
3. Include an action, practice material, observable acceptance criteria, and a future real-interview validation condition.
4. Use measurable criteria: time limit, required structure, correct mechanism, evidence completeness, or successful follow-up handling.
5. Never use vague criteria such as “practice more” or “improve understanding”.
6. A mock pass can recommend waiting_real_validation only.
7. Similar knowledge items produce ask_before_merge; do not merge automatically.
8. Generate at most three high-priority tasks and a 1-30 day suggested duration. Code calculates dates.

Output data:
{
  "task_proposals": [
    {
      "action": "create|link_existing",
      "existing_task_id": null,
      "title": "...",
      "task_type": "knowledge|case_material|answer_rebuild|compression|pressure_follow_up|real_world_validation",
      "source_qa_chain_ids": ["qa_001"],
      "root_cause": "case_evidence",
      "steps": ["Locate the original metric and baseline"],
      "acceptance_criteria": ["State metric, baseline, change, window, and attribution limit in 45 seconds"],
      "suggested_duration_days": 7,
      "real_validation_condition": "A later real answer receives level 4 or above on the same dimension",
      "priority": "high"
    }
  ],
  "knowledge_candidates": [
    {
      "canonical_question": "...",
      "source_qa_chain_ids": ["qa_001"],
      "candidate_existing_item_ids": [],
      "recommended_action": "create|ask_before_merge"
    }
  ]
}
```

Code validates non-empty steps and acceptance criteria, calculates an optional due date, and asks before a permanent merge.

## P09 Multi-Interview Competency Profile Interpretation

Before P09, run the local `profile` command or an equivalent deterministic query. Never ask P09 to calculate averages or trends.

```text
Task: Explain already-calculated competency facts. Do not recompute, average, interpolate, fill, or modify any number.

Input payload:
{
  "real_profile_facts": [],
  "mock_profile_facts": [],
  "recent_session_summaries": [],
  "aggregation_policy": {}
}

Rules:
1. Formal performance uses real_profile_facts only; training performance uses mock_profile_facts only.
2. Never create a mixed total or mixed average.
3. Compare the two ledgers only as a labeled transfer gap.
4. With only one real session, say “tentative displayed performance”, not “stable ability”.
5. Use trend words only when deterministic_trend is supplied.
6. A strong mock profile plus weak or absent real evidence means waiting_real_validation, never mastered.
7. Cite session IDs or observation IDs for every insight.

Output data:
{
  "real_profile_narrative": [
    {
      "dimension": "...",
      "interpretation": "...",
      "session_ids": ["int_001"],
      "observation_ids": ["obs_001"]
    }
  ],
  "mock_profile_narrative": [
    {
      "dimension": "...",
      "interpretation": "...",
      "mock_session_ids": ["mock_001"],
      "observation_ids": ["obs_mock_001"]
    }
  ],
  "transfer_gaps": [
    {
      "dimension": "structured_communication",
      "description": "Training evidence is strong; formal evidence remains weak or insufficient.",
      "state": "waiting_real_validation",
      "real_session_ids": ["int_001"],
      "mock_session_ids": ["mock_001", "mock_002"]
    }
  ],
  "stable_strengths": [],
  "repeated_risks": [],
  "next_real_validation_priorities": [
    {"dimension": "...", "validation_condition": "..."}
  ],
  "mixed_average": null
}
```

If the input contains a mixed average, ignore it, add `E_REAL_MOCK_LEAK`, and use the separate facts only.

