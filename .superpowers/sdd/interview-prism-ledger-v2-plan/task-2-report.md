# Task 2 Report: Versioned Import State Machine and V1-to-V2 Migration

## Status

Implemented and test-green on Python 3.9.6 and Python 3.12.13.

## Changed Files

- `scripts/interview_store.py`
- `scripts/test_interview_store.py`
- `.superpowers/sdd/interview-prism-ledger-v2-plan/task-2-report.md`

## RED Evidence

Before production edits, the seven focused Task 2 tests were run with Python
3.9.6:

```text
python3 -m unittest <seven Task 2 test names>
Ran 7 tests in 0.054s
FAILED (failures=5, errors=2)
```

The failures matched the missing behavior:

- New imports returned the old contract and had no revision number.
- Duplicate source imports returned `duplicate`/`duplicate_of` instead of
  `duplicate_source`/`existing_session_id`.
- Same-package imports returned the old duplicate result instead of `unchanged`.
- Changed packages did not create revisions or replace projections.
- Changed source under the same session ID reached the session primary-key
  constraint instead of raising a source-conflict `ValueError`.
- V1 databases had no `sessions.current_revision` column.
- Revised observations did not update the current profile projection.

## GREEN Evidence

Focused Task 2 tests on Python 3.9.6:

```text
Ran 7 tests in 0.073s
OK
```

Complete store module on Python 3.9.6:

```text
python3 -m unittest scripts.test_interview_store
Ran 34 tests in 0.317s
OK
```

Focused Task 2 tests on the required Python 3.12.13 interpreter:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest <seven Task 2 test names>
Ran 7 tests in 0.064s
OK
```

Complete store module on Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest scripts.test_interview_store
Ran 34 tests in 0.249s
OK
```

Full suite on Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s scripts
Ran 56 tests in 0.723s
OK
```

## Migration Details

- Schema version is tracked with `PRAGMA user_version = 2`.
- New databases create `sessions.current_revision` and
  `session_revisions` directly.
- Existing databases with a `sessions` table and user version 0 or 1 are
  treated as V1 for backward compatibility.
- V1 migration runs under `BEGIN IMMEDIATE`; schema changes, revision
  backfill, and `user_version` advancement commit together or roll back
  together.
- Every V1 `sessions.raw_json` value is retained verbatim as revision 1.
- The migrated revision receives a canonical full-package SHA-256 hash and
  reuses the original session `imported_at` value as its creation timestamp.
- `session_revisions` uses `(session_id, revision_no)` as its primary key and
  enforces uniqueness on `(session_id, package_hash)`.
- `_import_fingerprint(package)` remains unchanged as immutable source
  identity. `_package_hash(package)` hashes canonical full-package JSON.

## Import State Machine

- New session ID and new source: `imported`, revision 1.
- Same session ID, source, and package: `unchanged`.
- Same session ID and source with changed package: `revised`, next revision.
- Different session ID with the same source: `duplicate_source` with
  `existing_session_id`.
- Same session ID with changed source: clear source-conflict `ValueError`.
- Revisions atomically update `sessions.raw_json`, session metadata, and the
  current segments, QA chains, observations, and session-local growth tasks.
- Historical `session_revisions.package_json` rows are never overwritten.
- Real/mock source isolation and Task 1 validation behavior remain intact.

## Commit

Implementation commit: `dcb9a8533064fa90b3a0a8ae087b2bf473c9a776`

The report is committed separately after replacing this marker because a
commit cannot truthfully contain its own final hash.

## Self-Review

- Confirmed source identity and full-package identity are separate hashes.
- Confirmed validation still runs before database creation or mutation.
- Confirmed source conflict is checked before duplicate-source handling for an
  existing session ID.
- Confirmed revision rows are inserted before current projections are replaced,
  within the same transaction.
- Confirmed session deletion state and original import timestamp are preserved
  across revisions.
- Confirmed profile aggregation reads only replacement observations, not
  historical revision JSON.
- Confirmed no third-party runtime dependency was added.
- Task 3 report bundling code and tests were not changed.

## Concerns

- Reimporting a package that already exists as a non-current historical
  revision returns `unchanged` for that historical revision and intentionally
  leaves the current projection unchanged. The brief does not define a restore
  operation for historical packages.
- The initial root-level unittest discovery command found zero tests because
  the suites live under `scripts`; the required full-suite gate was rerun with
  `discover -s scripts` and passed all 56 tests.

## Fix Round 1: Session-Scoped Task Projections and Rollback Proof

### Reviewer Findings Addressed

- `growth_tasks.task_id` remains the external task identifier, but persisted
  uniqueness is now scoped by the composite primary key
  `(session_id, task_id)`. Real and mock sessions can safely reuse the same
  external task ID.
- Revision replacement continues to delete and recreate projections only for
  the revised session, so a colliding task ID in another session is preserved.
- Initialization detects the legacy sole `task_id` primary key for both V1
  databases and V2 databases created before this fix. It renames the legacy
  table, creates the session-scoped table, copies every task row without
  changing external IDs or JSON, and drops the legacy table in the existing
  migration transaction.
- Real SQLite failure-path tests now prove rollback for migration and revision
  mutations without mocks.

### Fix-Round RED Evidence

The five focused tests were run under Python 3.9.6 before production edits:

```text
EEE..
Ran 5 tests in 0.064s
FAILED (errors=3)
```

All three errors were the intended defect:

```text
sqlite3.IntegrityError: UNIQUE constraint failed: growth_tasks.task_id
```

They covered direct real/mock collision, revision safety with a colliding task
in another session, and V1 migration followed by reuse of the legacy external
task ID. The two rollback tests passed in RED and serve as characterization
guards for the transaction boundaries changed by this fix.

### Fix-Round GREEN Evidence

Focused tests on Python 3.9.6:

```text
Ran 5 tests in 0.068s
OK
```

Focused tests on bundled Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest <five fix-round test names>
Ran 5 tests in 0.048s
OK
```

Full suite on bundled Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s scripts
Ran 61 tests in 0.755s
OK
```

### Transaction Rollback Evidence

- Migration failure uses malformed V1 `raw_json`, causing real
  `json.JSONDecodeError` after schema work begins. After rollback, the test
  confirms `PRAGMA user_version` is still 1, `current_revision` is absent,
  `session_revisions` is absent, the legacy sole task primary key remains, and
  the malformed source row is unchanged.
- Revision failure installs a temporary SQLite `BEFORE INSERT` trigger using
  `RAISE(ABORT)` on the replacement task. After the real
  `sqlite3.IntegrityError`, the test confirms current revision 1, one historical
  revision, original `raw_json`, QA status, observation, and task projection.

### Fix-Round Self-Review and Concerns

- The table rebuild and row copy execute inside the same `BEGIN IMMEDIATE`
  transaction as schema creation, revision backfill, and `user_version` update.
- The migration runs even when `user_version` is already 2, repairing databases
  initialized by the earlier Task 2 implementation.
- Imported task projections always have a non-null session ID. Existing orphaned
  legacy rows with a null session ID remain representable under SQLite's
  composite-key semantics and are copied without data loss.
- No Task 3 report-bundling production code or tests were changed.

## Fix Round 2: Segment Projection Rollback Assertion

### Test Gap Addressed

- The failed-revision rollback test now snapshots the exact ordered segment
  projection before installing the `RAISE(ABORT)` trigger and compares it with
  the projection after the failed revision.
- The comparison covers `segment_id`, `sequence_no`, `speaker_role`,
  `speaker_label`, `event_type`, verbatim `text`, `start_time`, `end_time`, and
  `confidence` for every segment in the session.
- This extends the existing rollback proof for current revision, historical
  revision JSON, `sessions.raw_json`, QA chains, observations, and growth tasks.

### Fix-Round 2 Evidence

Focused rollback test on Python 3.9.6:

```text
Ran 1 test in 0.014s
OK
```

Focused rollback test on bundled Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest scripts.test_interview_store.InterviewStoreTests.test_revision_failure_rolls_back_history_and_current_projections
Ran 1 test in 0.016s
OK
```

Full suite on bundled Python 3.12.13:

```text
/Users/bytedance/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s scripts
Ran 61 tests in 0.765s
OK
```

### Outcome

- The new assertion passed immediately as a characterization of the existing
  transaction boundary; no production change was required.
- No Task 3 report-bundling production code or tests were changed.
