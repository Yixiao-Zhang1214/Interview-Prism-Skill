# Interview Prism Answer Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a long-lived, explicitly confirmed interview answer notebook backed by SQLite and rendered as a precisely patched Markdown file with searchable indexes.

**Architecture:** A focused `answer_notebook.py` module owns schema migration, immutable revisions, pending-operation confirmation, exact Markdown block patching, integrity checks, search, and CLI commands. Existing interview report generation remains untouched; `SKILL.md` defines the conversational confirmation protocol and `README.md` documents the user-facing capability.

**Tech Stack:** Python 3 standard library, SQLite, Markdown, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-01-answer-notebook-design.md`

## Global Constraints

- Every add, update, delete, or restore requires explicit confirmation of a generated pending operation.
- Never auto-import best answers from interview reports.
- A write may alter only the target entry block and its own index line or necessary category heading.
- Non-target entry blocks and non-target index lines must remain byte-for-byte identical.
- Keep the existing five-report artifact contract unchanged.
- Use only Python standard-library dependencies.

---

### Task 1: Notebook storage and immutable revisions

**Files:**
- Create: `scripts/answer_notebook.py`
- Create: `scripts/test_answer_notebook.py`

**Interfaces:**
- Produces: `AnswerNotebook(data_dir: Path)`, `propose_add(payload)`, `propose_update(entry_id, changes)`, `propose_delete(entry_id)`, `propose_restore(entry_id)`, `confirm(operation_id)`, `search(...)`, and `get(entry_id, include_deleted=False)`.
- Stores data in `<data-dir>/library.db` and projects active entries to `<data-dir>/interview-answer-notebook.md`.

- [ ] **Step 1: Define tests for schema initialization and pending operations**

```python
def test_proposal_does_not_write_until_confirmed(tmp_path):
    notebook = AnswerNotebook(tmp_path)
    proposal = notebook.propose_add(sample_payload())
    assert notebook.search() == []
    assert not notebook.markdown_path.exists()
    created = notebook.confirm(proposal.operation_id)
    assert created.entry_id == "NB-0001"
```

- [ ] **Step 2: Implement schema creation**

Create `answer_notebook_entries`, `answer_notebook_revisions`, and `answer_notebook_pending_operations` with stable IDs, full JSON snapshots, content hashes, expiry, and consumed timestamps.

- [ ] **Step 3: Implement proposal creation without mutating entries**

Serialize a complete preview, bind updates to the target `content_hash`, and return a display-ready pending operation.

- [ ] **Step 4: Implement transactional confirmation**

Reject missing, expired, consumed, or stale operations. Add revisions for every confirmed mutation and use soft deletion for delete operations.

### Task 2: Precise Markdown projection and protection

**Files:**
- Modify: `scripts/answer_notebook.py`
- Modify: `scripts/test_answer_notebook.py`

**Interfaces:**
- Produces: `patch_markdown(original: str, operation: ConfirmedOperation) -> str` and `assert_untouched_regions(original: str, updated: str, target_entry_id: str) -> None`.
- Consumes: confirmed entry snapshots from Task 1.

- [ ] **Step 1: Define exact-update tests**

```python
def test_update_preserves_unrelated_entry_bytes(tmp_path):
    notebook = seeded_notebook(tmp_path, count=3)
    before = notebook.markdown_path.read_text()
    unrelated_before = entry_block(before, "NB-0002")
    proposal = notebook.propose_update("NB-0001", {"best_answer": "新版回答"})
    notebook.confirm(proposal.operation_id)
    after = notebook.markdown_path.read_text()
    assert entry_block(after, "NB-0002") == unrelated_before
```

- [ ] **Step 2: Implement stable index and entry delimiters**

Use `notebook-index` and `notebook-entry` markers, stable lowercase anchors, creation-order insertion, and no global sort or timestamp.

- [ ] **Step 3: Implement operation-scoped patches**

Append on add, replace one block on update, remove one block on delete, and restore one block on restore. Change the index only when the target title or category requires it.

- [ ] **Step 4: Implement pre-write invariants and rollback**

Compare non-target blocks and index lines before atomic file replacement. Restore the previous Markdown bytes if the SQLite commit fails, and roll back SQLite if Markdown staging or invariant checks fail.

### Task 3: Search and command-line interface

**Files:**
- Modify: `scripts/answer_notebook.py`
- Modify: `scripts/test_answer_notebook.py`

**Interfaces:**
- Produces CLI commands `propose-add`, `propose-update`, `propose-delete`, `propose-restore`, `confirm`, `get`, `search`, and `list`.
- All mutating proposal commands emit JSON previews; only `confirm` writes.

- [ ] **Step 1: Define search and CLI tests**

Cover keyword, role, ability type, scenario tag, source, validation status, ID, include-deleted, and malformed payload behavior.

- [ ] **Step 2: Implement structured search**

Use parameterized SQLite filters and in-memory tag matching. Default to active entries and creation order.

- [ ] **Step 3: Implement JSON CLI input and output**

Accept `--data-dir`, payload JSON, exact entry IDs, and pending operation IDs. Return machine-readable JSON with `ok`, `operation`, `preview`, `entry_id`, `version`, and `changed_regions` fields.

- [ ] **Step 4: Add duplicate-question suggestions**

On add proposals, include exact normalized-question matches as warnings without automatically merging or blocking the proposal.

### Task 4: Skill and public documentation

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Documents the CLI from Task 3 and the conversational confirmation rules from the design spec.

- [ ] **Step 1: Add the long-term notebook workflow to `SKILL.md`**

Document proposal, explicit confirmation, execution, cancellation, stale-operation handling, read-only search, and the prohibition on automatic collection.

- [ ] **Step 2: Add README usage examples**

Show natural-language commands for saving, updating, deleting, and searching. State that the notebook is independent from the five per-interview reports.

- [ ] **Step 3: Preserve existing onboarding and report descriptions**

Append focused notebook documentation without reintroducing the removed “第一次使用” README section or describing structured session data as a report artifact.

### Task 5: Contract regression coverage

**Files:**
- Modify: `scripts/test_answer_notebook.py`

**Interfaces:**
- Consumes all public interfaces from Tasks 1-3.

- [ ] **Step 1: Add a five-report non-interference assertion**

Assert the notebook module neither imports nor calls report bundle generation and writes only `library.db` notebook tables plus `interview-answer-notebook.md`.

- [ ] **Step 2: Add failure-path tests**

Cover stale confirmation, repeated confirmation, damaged Markdown markers, non-target mutation detection, database failure recovery, and missing entry IDs.

- [ ] **Step 3: Run the focused test module when validation is authorized**

```bash
python3 -m unittest scripts.test_answer_notebook -v
```

- [ ] **Step 4: Run the existing report tests when validation is authorized**

```bash
python3 -m unittest scripts.test_interview_report -v
```

Validation commands require explicit user permission under the collaboration rules for this workspace.
