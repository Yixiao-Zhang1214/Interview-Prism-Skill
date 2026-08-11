# Final Review Fix Report

## Status

All six findings in `final-fix-brief.md` were addressed as one final fix wave. Existing import, migration, projection, report, source-identity, session-identity, and real/mock isolation behavior remains covered by the complete suites.

## Commit

- Implementation commit: `7cb83f920bebdad4a436d3a8b39ffd7d8263c637`
- The report itself is attached in a documentation-only follow-up commit because a file cannot contain the hash of the commit that first introduces that same file. The final task response identifies the resulting HEAD.

## RED Evidence

Focused command under system Python 3.9.6:

```text
/usr/bin/python3 -m unittest -v \
  scripts.test_interview_store.InterviewStoreTests.test_historical_replay_creates_new_current_revision_and_restores_projections \
  scripts.test_interview_store.InterviewStoreTests.test_initialize_library_migrates_v1_raw_json_to_revision_one \
  scripts.test_interview_store.InterviewStoreTests.test_initialize_library_migrates_v2_revision_schema_and_allows_replay \
  scripts.test_interview_report.SingleReportTests.test_task_status_labels_cover_exactly_the_canonical_statuses \
  scripts.test_interview_report.PublicPackageRenderValidationTests.test_radar_svg_rejects_invalid_package_at_public_boundary \
  scripts.test_interview_report.ComparisonReportTests.test_loader_uses_replayed_package_as_current_report_input \
  scripts.test_interview_report.ReportCliTests.test_bundle_interrupt_after_backup_retains_recovery_and_reraises_interrupt \
  scripts.test_interview_report.ReportCliTests.test_bundle_publication_failure_then_retry_converges_ledger_and_artifacts
```

Observed result before production edits: `FAILED (failures=4, errors=2)` across 8 focused tests.

Expected failures demonstrated:

- A -> B -> A returned historical revision 1 as `unchanged` instead of creating revision 3.
- V1 and V2 migration tests could not query the required `session_revisions.raw_json` column.
- `render_radar_svg` accepted an invalid semantic reference.
- Report loading continued to expose B after replaying A.
- `KeyboardInterrupt` bypassed rollback recovery surfacing after a backup had already moved.

The exact canonical-status equality test and publication-failure retry characterization already passed in RED; they protect behavior shared by the production changes.

## GREEN Evidence

Focused suite:

- Python 3.9.6: 8 tests passed in 0.116s.
- Python 3.12.13: 8 tests passed in 0.141s.

Complete suite command:

```text
python -m unittest discover -v -s scripts -p 'test_*.py'
```

Complete suite results:

- System Python 3.9.6: 74 tests passed in 2.711s.
- Bundled Python 3.12.13: 74 tests passed in 2.614s.

No third-party runtime dependency was added.

## Changes

- `scripts/interview_store.py`
  - Advanced the SQLite schema to version 3.
  - Renamed revision payload storage from `package_json` to `raw_json`.
  - Added transactional V2 table rebuilding while preserving V1 backfill behavior.
  - Removed historical package-hash uniqueness.
  - Compares an incoming package hash only with the current revision, so A -> B -> A creates revision 3 and refreshes all current projections.
- `scripts/interview_report.py`
  - Imports the canonical growth-task status set and asserts exact label-key equality.
  - Validates packages at the public `render_radar_svg` boundary.
  - Attempts publication rollback for every `BaseException`.
  - Re-raises interrupts while retaining and surfacing recovery directories and rollback errors when restoration is incomplete.
- `scripts/test_interview_store.py`
  - Added focused A -> B -> A projection coverage and V2-to-V3 migration/replay coverage.
  - Updated schema-version and revision-column assertions to the V3 contract.
- `scripts/test_interview_report.py`
  - Added radar-boundary, canonical-status, replayed-report-input, interrupted-publication recovery, and failure-then-retry convergence regressions.
- `.superpowers/sdd/interview-prism-ledger-v2-plan/final-fix-report.md`
  - Records this fix wave and its evidence.

## Self-Review

- Publication rollback now enters for ordinary failures and interrupts. If restoration completes, the original failure is re-raised. If restoration is incomplete, the temporary directory is retained; ordinary failures raise the existing recovery `OSError`, while interrupts remain interrupts with recovery details in both their message and attributes.
- Revision identity remains immutable: source-fingerprint conflicts still reject changed source material, and the session ID is unchanged. Replay changes only revision history and current derived projections.
- The V2 migration copies every revision number, hash, payload, and timestamp inside the existing transaction before dropping the legacy table. The V1 migration still derives revision 1 from `sessions.raw_json` and retains the original import timestamp.
- Report loaders continue to read the canonical current payload from `sessions.raw_json`; the replay regression proves they return A after A -> B -> A.
- Real and mock ledgers still use the existing source-type validation and filtering paths; complete-suite coverage remained green on both required Python versions.
- The retry regression proves the deliberate intermediate state: the ledger may already contain revised data while old artifacts are restored after publication failure, and retry converges artifacts to that committed current revision without adding a duplicate revision.

## Concerns

- No unresolved correctness concerns found.
- Operationally, an incomplete rollback intentionally leaves a recovery directory in the output directory. The raised error or interrupt includes its exact path; automatic deletion would risk destroying the only remaining backup.

## Additional Targeted Final Fix Round

### Finding

The backup move previously happened before its in-memory `replaced` entry was appended. A `KeyboardInterrupt` raised after `Path.replace` completed the filesystem move but before bookkeeping resumed left the only old artifact undiscoverable by rollback, and `finally` then deleted it with the temporary directory.

### RED Evidence

System Python 3.9.6 ran the new post-move interruption regression together with the existing pre-move interruption regression:

```text
/usr/bin/python3 -m unittest -v \
  scripts.test_interview_report.ReportCliTests.test_bundle_interrupt_after_backup_move_restores_unrecorded_backup \
  scripts.test_interview_report.ReportCliTests.test_bundle_interrupt_after_backup_retains_recovery_and_reraises_interrupt
```

Observed before the production fix: 2 tests ran; the new post-move test failed because the restored output bundle was missing the artifact moved before interruption, while the existing pre-move test passed.

### Fix

Rollback now discovers the backup files physically present in the temporary `previous/` directory and derives their final destinations from those files. This makes the filesystem the recovery source of truth:

- If replace completed and then raised, the moved backup is discovered and restored.
- If replace raised before moving, no backup is discovered and the untouched old final artifact remains in place.
- If restoration fails, the existing recovery-directory retention and surfacing behavior remains active.
- The original `KeyboardInterrupt` is re-raised after successful rollback.

### GREEN Evidence

Focused publication suite covering post-move interruption, pre-move interruption, ordinary publication rollback, incomplete rollback recovery, and failure-then-retry convergence:

- Python 3.9.6: 5 tests passed in 0.173s.
- Python 3.12.13: 5 tests passed in 0.229s.

Complete suites, superseding the earlier 74-test counts after adding this regression:

- System Python 3.9.6: 75 tests passed in 1.853s.
- Bundled Python 3.12.13: 75 tests passed in 1.836s.

### Additional Self-Review and Concerns

- The change is limited to publication rollback bookkeeping and its regression test; ledger import, migration, projection, and report rendering paths are unchanged.
- Successful publication behavior is unchanged because backup discovery runs only in the exception path.
- No unresolved correctness concerns found.
