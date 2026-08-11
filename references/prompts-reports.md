# Reporting and Calibration Prompts: P12-P13

## Contents

- P12 manual period report
- P13 real outcome feedback and calibration

Automatic weekly scheduling is not part of the first version. Run P12 only on an explicit request for a date range.

## P12 Manual Period Report

Before P12, calculate the date window, session counts, task changes, frequencies, and trends with code or deterministic queries.

```text
Task: Organize supplied period facts into a concise growth report. Do not query, calculate, estimate, or invent missing statistics.

Input payload:
{
  "period": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
  "real_facts": {},
  "mock_facts": {},
  "task_changes": {},
  "knowledge_facts": {},
  "profile_changes": {}
}

Rules:
1. Present real interviews and mock training in separate sections.
2. If no real interview occurred, say so; do not substitute mock results.
3. Explain only supplied deterministic trends and counts.
4. Recommend at most three next priorities and one mock theme.
5. Use a direct, concrete tone without manufacturing anxiety.
6. Set next_mock_theme=null when no supported theme exists.

Output data:
{
  "period_headline": "...",
  "real_section": {
    "session_count": 0,
    "session_ids": [],
    "outcome_facts": [],
    "narrative": "..."
  },
  "mock_section": {
    "session_count": 0,
    "mock_session_ids": [],
    "narrative": "..."
  },
  "profile_change_section": {
    "changes": [
      {
        "dimension": "...",
        "deterministic_trend": "up|down|flat|insufficient_data",
        "interpretation": "...",
        "observation_ids": []
      }
    ]
  },
  "repeated_focuses": [
    {
      "focus": "...",
      "real_count": 0,
      "mock_count": 0,
      "session_ids": []
    }
  ],
  "task_section": {
    "completed_task_ids": [],
    "overdue_task_ids": [],
    "new_task_ids": [],
    "narrative": "..."
  },
  "next_priorities": [
    {"title": "...", "basis_ids": [], "acceptance_criteria": []}
  ],
  "next_mock_theme": null,
  "one_sentence_theme": "..."
}
```

The final Markdown renderer preserves the real/mock section boundary and the supplied numbers.

## P13 Real Outcome Feedback and Calibration

```text
Task: Compare the original simulated result tendency with a user-supplied real outcome or actual interviewer feedback. Preserve the original review and avoid retrospective causal certainty.

Input payload:
{
  "session_id": "int_xxx",
  "original_review": {},
  "actual_outcome": {},
  "actual_feedback": []
}

Rules:
1. Preserve actual_outcome and actual_feedback as user-supplied facts with their original IDs and wording.
2. Determine only surface alignment and whether feedback supports or contradicts earlier observations.
3. A hiring result can depend on fit, headcount, process, competition, or unknown factors. Without explicit feedback, do not assign a cause.
4. Never rewrite historical scores, focuses, or tasks automatically.
5. Calibration suggestions require human approval and create a new prompt/rubric version if accepted.

Output data:
{
  "surface_alignment": "aligned|not_aligned|not_comparable",
  "supported_prior_observations": [
    {
      "observation_id": "obs_001",
      "actual_feedback_ids": ["feedback_001"],
      "explanation": "..."
    }
  ],
  "contradicted_prior_observations": [],
  "still_unverified_observations": [],
  "causal_attribution": "insufficient_information",
  "calibration_candidates": [
    {
      "target": "prompt|rubric|weighting_policy",
      "target_id": "...",
      "suggestion": "...",
      "basis_observation_ids": [],
      "actual_feedback_ids": [],
      "requires_human_approval": true
    }
  ],
  "history_rewrite_allowed": false
}
```

