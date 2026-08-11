# Deterministic Text Report Presentation

Use this contract after P01–P07 outputs pass deterministic and semantic validation. Return readable Markdown directly in chat. When local writes are available, save the same content as `.md`.

## Render

```bash
python3 scripts/interview_report.py single --file SESSION.json --output REPORT.md
python3 scripts/interview_report.py compare --data-dir PATH --source-type real --output COMPARISON.md
python3 scripts/interview_report.py bundle --file SESSION.json --data-dir PATH --output-dir OUTPUTS
```

The report layer is Markdown-only. The radar chart is the only separate visual asset.

## Single-interview order

1. 30-second conclusion: summary, best answer, and highest risk in plain language.
2. Dedicated simulated interviewer review: one italicized synthesis of the recorded overall impression, strongest evidence, main risk, and conclusion boundary. Render `hold` as `现有证据不足，无法判断是否通过`.
3. Ability snapshot: embed a deterministic radar SVG and keep a score table below it. Plot only dimensions with evidence; if fewer than three exist, show an insufficient-evidence message instead of a polygon.
4. All assessed interviewer questions in transcript order: use a question heading, a blockquoted bold `真正考察点（推断）`, separate bold labels for strengths, gaps, and next answer, then an italicized simulated interviewer thought. Never sample only best/risk/neutral questions.
5. Up to three growth tasks: problem, actions, completion criteria, and real-interview validation condition.
6. Evidence notes: information limits and evidence IDs. Keep the mechanically rendered original Q&A as a separate artifact; include it inline only when requested.

Keep raw IDs and English dimension codes out of the main conclusions. The renderer, not P07, decides text order and evidence placement.

## Per-interview artifact bundle

Every accepted interview, including the first, produces one shared timestamp stem and five report/data files:

- `IP-{R|M}-YYYYMMDD-HHMM-analysis.md`
- `IP-{R|M}-YYYYMMDD-HHMM-qa-original.md`
- `IP-{R|M}-YYYYMMDD-HHMM-session.json`
- `IP-{R|M}-YYYYMMDD-HHMM-ability-model.md`
- `IP-{R|M}-YYYYMMDD-HHMM-frequent-questions.md`

The analysis embeds `IP-{R|M}-YYYYMMDD-HHMM-ability-radar.svg`. Use interview occurrence time, never processing time. A first-session ability model explicitly has no trend; a first-session question record explicitly has no high-frequency claim. Later bundles rebuild the cumulative documents from all non-deleted sessions in the same ledger.

## Comparison order

1. Name the selected real or mock ledger and included sessions.
2. Show recorded ability facts and first-to-last differences only with at least two same-ledger sessions.
3. Count canonical questions, inferred assessment focuses, repeated evidence gaps, repeated root causes, and growth-task states in code.
4. Keep missing evidence as missing, never zero. Never mix real and mock averages or infer a trend from one session.

## Claim boundaries

- Label all inferred assessment focuses as inference.
- Label interviewer reactions and outcome tendencies as simulations.
- Do not fabricate evidence, outcomes, scores, hiring causes, or trends.
