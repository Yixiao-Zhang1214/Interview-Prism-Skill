#!/usr/bin/env python3
"""Long-lived, explicitly confirmed answer notebook for Interview Prism."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


INDEX_START = "<!-- notebook-index:start -->"
INDEX_END = "<!-- notebook-index:end -->"
ENTRIES_START = "<!-- notebook-entries:start -->"
ENTRIES_END = "<!-- notebook-entries:end -->"
ENTRY_PATTERN = re.compile(
    r'(?m)^<a id="nb-\d+"></a>\n'
    r'<!-- notebook-entry:start (NB-\d+) -->\n.*?'
    r'^<!-- notebook-entry:end \1 -->\n?',
    re.DOTALL,
)
INDEX_ITEM_PATTERN = re.compile(
    r"(?m)^- \[(NB-\d+) .+?\]\(#(nb-\d+)\)$"
)

ALLOWED_SOURCES = {"user", "co_created"}
ALLOWED_VALIDATION = {
    "unvalidated",
    "used_in_real_interview",
    "real_validated",
}
MUTABLE_FIELDS = {
    "canonical_question",
    "target_role",
    "ability_type",
    "scenario_tags",
    "original_problem_summary",
    "best_answer",
    "answer_source",
    "source_session_ids",
    "validation_status",
}


class NotebookError(RuntimeError):
    """Base class for safe, user-facing notebook failures."""


class ConflictError(NotebookError):
    """The confirmed proposal no longer matches current notebook state."""


class MarkdownIntegrityError(NotebookError):
    """The Markdown projection cannot be patched without collateral edits."""


@dataclass(frozen=True)
class NotebookEntry:
    entry_id: str
    canonical_question: str
    target_role: str
    ability_type: str
    scenario_tags: list[str]
    original_problem_summary: str
    best_answer: str
    answer_source: str
    source_session_ids: list[str]
    validation_status: str
    version: int
    created_at: str
    updated_at: str
    deleted_at: str | None
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PendingOperation:
    operation_id: str
    operation_type: str
    entry_id: str | None
    preview: dict[str, Any]
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfirmationResult:
    operation_id: str
    operation_type: str
    entry_id: str
    version: int
    changed_regions: list[str]
    entry: NotebookEntry

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["ok"] = True
        return result


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _single_line(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise NotebookError("标签和关联面试必须使用 JSON 数组。")
    result: list[str] = []
    for item in value:
        normalized = _single_line(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _normalize_payload(
    payload: dict[str, Any], base: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise NotebookError("错题内容必须是 JSON 对象。")
    unknown = set(payload) - MUTABLE_FIELDS
    if unknown:
        raise NotebookError(f"不支持的字段：{', '.join(sorted(unknown))}")
    merged = dict(base or {})
    merged.update(payload)
    normalized = {
        "canonical_question": _single_line(merged.get("canonical_question")),
        "target_role": _single_line(merged.get("target_role"), "未指定"),
        "ability_type": _single_line(merged.get("ability_type"), "未分类"),
        "scenario_tags": _string_list(merged.get("scenario_tags")),
        "original_problem_summary": str(
            merged.get("original_problem_summary") or ""
        ).strip(),
        "best_answer": str(merged.get("best_answer") or "").strip(),
        "answer_source": _single_line(merged.get("answer_source"), "co_created"),
        "source_session_ids": _string_list(merged.get("source_session_ids")),
        "validation_status": _single_line(
            merged.get("validation_status"), "unvalidated"
        ),
    }
    if not normalized["canonical_question"]:
        raise NotebookError("提炼后的问题不能为空。")
    if not normalized["best_answer"]:
        raise NotebookError("最优回答不能为空。")
    if normalized["answer_source"] not in ALLOWED_SOURCES:
        raise NotebookError("答案来源只能是 user 或 co_created。")
    if normalized["validation_status"] not in ALLOWED_VALIDATION:
        raise NotebookError("验证状态不受支持。")
    return normalized


def _snapshot_hash(entry: dict[str, Any]) -> str:
    content = {key: entry.get(key) for key in MUTABLE_FIELDS}
    content.update(
        version=entry["version"],
        deleted_at=entry.get("deleted_at"),
    )
    return _hash_payload(content)


def _empty_markdown() -> str:
    return (
        "# 面试错题本\n\n"
        f"{INDEX_START}\n"
        "## 错题目录\n"
        f"{INDEX_END}\n\n"
        f"{ENTRIES_START}\n"
        f"{ENTRIES_END}\n"
    )


def _render_entry(entry: NotebookEntry) -> str:
    source = "用户提供" if entry.answer_source == "user" else "共同整理"
    validation = {
        "unvalidated": "尚未验证",
        "used_in_real_interview": "已用于真实面试",
        "real_validated": "真实面试验证有效",
    }[entry.validation_status]
    tags = "、".join(entry.scenario_tags) or "无"
    sessions = "、".join(entry.source_session_ids) or "无"
    anchor = entry.entry_id.lower()
    return (
        f'<a id="{anchor}"></a>\n'
        f"<!-- notebook-entry:start {entry.entry_id} -->\n"
        f"## {entry.canonical_question}\n\n"
        f"- 条目 ID：{entry.entry_id}\n"
        f"- 适用岗位：{entry.target_role}\n"
        f"- 能力类型：{entry.ability_type}\n"
        f"- 场景标签：{tags}\n"
        f"- 来源：{source}\n"
        f"- 关联面试：{sessions}\n"
        f"- 当前版本：v{entry.version}\n"
        f"- 验证状态：{validation}\n\n"
        "### 原回答的主要问题\n\n"
        f"{entry.original_problem_summary or '未记录'}\n\n"
        "### 当前最优回答\n\n"
        f"{entry.best_answer}\n"
        f"<!-- notebook-entry:end {entry.entry_id} -->\n"
    )


def _entry_blocks(markdown: str) -> dict[str, str]:
    return {match.group(1): match.group(0) for match in ENTRY_PATTERN.finditer(markdown)}


def _index_items(markdown: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in INDEX_ITEM_PATTERN.finditer(markdown):
        entry_id = match.group(1)
        if match.group(2) != entry_id.lower():
            raise MarkdownIntegrityError(f"目录锚点与条目 ID 不一致：{entry_id}")
        result[entry_id] = match.group(0)
    return result


def _validate_markdown(markdown: str) -> None:
    for marker in (INDEX_START, INDEX_END, ENTRIES_START, ENTRIES_END):
        if markdown.count(marker) != 1:
            raise MarkdownIntegrityError(f"Markdown 标记缺失或重复：{marker}")
    starts = re.findall(r"<!-- notebook-entry:start (NB-\d+) -->", markdown)
    ends = re.findall(r"<!-- notebook-entry:end (NB-\d+) -->", markdown)
    blocks = _entry_blocks(markdown)
    if starts != ends or len(starts) != len(blocks) or len(starts) != len(set(starts)):
        raise MarkdownIntegrityError("错题条目边界损坏，已停止写入。")
    if set(_index_items(markdown)) != set(blocks):
        raise MarkdownIntegrityError("目录与错题条目不一致，已停止写入。")


def extract_entry_block(markdown: str, entry_id: str) -> str:
    try:
        return _entry_blocks(markdown)[entry_id]
    except KeyError as exc:
        raise MarkdownIntegrityError(f"Markdown 中不存在条目 {entry_id}。") from exc


def _index_content(markdown: str) -> tuple[str, int, int]:
    start = markdown.index(INDEX_START) + len(INDEX_START)
    end = markdown.index(INDEX_END)
    return markdown[start:end], start, end


def _replace_index_content(markdown: str, content: str) -> str:
    _, start, end = _index_content(markdown)
    return markdown[:start] + content + markdown[end:]


def _add_index_item(markdown: str, entry: NotebookEntry) -> str:
    content, _, _ = _index_content(markdown)
    lines = content.splitlines()
    item = f"- [{entry.entry_id} {entry.canonical_question}](#{entry.entry_id.lower()})"
    heading = f"### {entry.ability_type}"
    if heading in lines:
        heading_at = lines.index(heading)
        insert_at = len(lines)
        for index in range(heading_at + 1, len(lines)):
            if lines[index].startswith("### "):
                insert_at = index
                break
        while insert_at > heading_at + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, item)
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend(["", heading, item])
    return _replace_index_content(markdown, "\n" + "\n".join(lines).strip("\n") + "\n")


def _remove_index_item(markdown: str, entry_id: str) -> str:
    content, _, _ = _index_content(markdown)
    lines = content.splitlines()
    anchor = f"](#{entry_id.lower()})"
    positions = [index for index, line in enumerate(lines) if anchor in line]
    if len(positions) != 1:
        raise MarkdownIntegrityError(f"目录中无法唯一定位条目 {entry_id}。")
    position = positions[0]
    heading_at = next(
        (index for index in range(position - 1, -1, -1) if lines[index].startswith("### ")),
        None,
    )
    lines.pop(position)
    if heading_at is not None:
        next_heading = next(
            (index for index in range(heading_at + 1, len(lines)) if lines[index].startswith("### ")),
            len(lines),
        )
        if not any(line.startswith("- [") for line in lines[heading_at + 1 : next_heading]):
            lines.pop(heading_at)
            if heading_at < len(lines) and not lines[heading_at].strip():
                lines.pop(heading_at)
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    return _replace_index_content(markdown, "\n" + "\n".join(lines).strip("\n") + "\n")


def _replace_index_item(markdown: str, entry: NotebookEntry) -> str:
    content, _, _ = _index_content(markdown)
    lines = content.splitlines()
    anchor = f"](#{entry.entry_id.lower()})"
    positions = [index for index, line in enumerate(lines) if anchor in line]
    if len(positions) != 1:
        raise MarkdownIntegrityError(f"目录中无法唯一定位条目 {entry.entry_id}。")
    lines[positions[0]] = (
        f"- [{entry.entry_id} {entry.canonical_question}](#{entry.entry_id.lower()})"
    )
    return _replace_index_content(markdown, "\n" + "\n".join(lines).strip("\n") + "\n")


def assert_untouched_regions(before: str, after: str, target_entry_id: str) -> None:
    before_blocks = _entry_blocks(before)
    after_blocks = _entry_blocks(after)
    for entry_id in set(before_blocks) | set(after_blocks):
        if entry_id != target_entry_id and before_blocks.get(entry_id) != after_blocks.get(entry_id):
            raise MarkdownIntegrityError(f"检测到非目标错题被修改：{entry_id}")
    before_items = _index_items(before)
    after_items = _index_items(after)
    for entry_id in set(before_items) | set(after_items):
        if entry_id != target_entry_id and before_items.get(entry_id) != after_items.get(entry_id):
            raise MarkdownIntegrityError(f"检测到非目标目录项被修改：{entry_id}")


def patch_markdown(
    original: str,
    operation_type: str,
    entry: NotebookEntry,
    previous: NotebookEntry | None = None,
) -> str:
    before = original or _empty_markdown()
    _validate_markdown(before)
    updated = before
    if operation_type in {"add", "restore"}:
        updated = _add_index_item(updated, entry)
        updated = updated.replace(ENTRIES_END, _render_entry(entry) + ENTRIES_END, 1)
    elif operation_type == "update":
        if previous is None:
            raise NotebookError("更新操作缺少旧版本快照。")
        old_block = extract_entry_block(updated, entry.entry_id)
        updated = updated.replace(old_block, _render_entry(entry), 1)
        if previous.ability_type == entry.ability_type:
            if previous.canonical_question != entry.canonical_question:
                updated = _replace_index_item(updated, entry)
        else:
            updated = _remove_index_item(updated, entry.entry_id)
            updated = _add_index_item(updated, entry)
    elif operation_type == "delete":
        updated = _remove_index_item(updated, entry.entry_id)
        updated = updated.replace(extract_entry_block(updated, entry.entry_id), "", 1)
    else:
        raise NotebookError(f"未知操作类型：{operation_type}")
    _validate_markdown(updated)
    assert_untouched_regions(before, updated, entry.entry_id)
    return updated


class AnswerNotebook:
    """SQLite-backed notebook whose mutating operations require confirmation."""

    def __init__(self, data_dir: str | Path, confirmation_hours: int = 24):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "library.db"
        self.markdown_path = self.data_dir / "interview-answer-notebook.md"
        self.confirmation_hours = confirmation_hours
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS answer_notebook_entries (
                    entry_id TEXT PRIMARY KEY,
                    canonical_question TEXT NOT NULL,
                    target_role TEXT NOT NULL,
                    ability_type TEXT NOT NULL,
                    scenario_tags_json TEXT NOT NULL,
                    original_problem_summary TEXT NOT NULL,
                    best_answer TEXT NOT NULL,
                    answer_source TEXT NOT NULL,
                    source_session_ids_json TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_notebook_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    operation_type TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_notebook_pending_operations (
                    operation_id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    entry_id TEXT,
                    expected_hash TEXT,
                    payload_json TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                """
            )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> NotebookEntry:
        return NotebookEntry(
            entry_id=row["entry_id"],
            canonical_question=row["canonical_question"],
            target_role=row["target_role"],
            ability_type=row["ability_type"],
            scenario_tags=json.loads(row["scenario_tags_json"]),
            original_problem_summary=row["original_problem_summary"],
            best_answer=row["best_answer"],
            answer_source=row["answer_source"],
            source_session_ids=json.loads(row["source_session_ids_json"]),
            validation_status=row["validation_status"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
            content_hash=row["content_hash"],
        )

    def _get_with_connection(
        self, connection: sqlite3.Connection, entry_id: str
    ) -> NotebookEntry:
        row = connection.execute(
            "SELECT * FROM answer_notebook_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            raise NotebookError(f"错题不存在：{entry_id}")
        return self._entry_from_row(row)

    def get(self, entry_id: str, include_deleted: bool = False) -> NotebookEntry:
        with self._connect() as connection:
            entry = self._get_with_connection(connection, entry_id)
        if entry.deleted_at and not include_deleted:
            raise NotebookError(f"错题已删除：{entry_id}")
        return entry

    def _save_pending(
        self,
        operation_type: str,
        payload: dict[str, Any],
        preview: dict[str, Any],
        entry_id: str | None = None,
        expected_hash: str | None = None,
    ) -> PendingOperation:
        operation_id = f"OP-{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = created_at + timedelta(hours=self.confirmation_hours)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO answer_notebook_pending_operations
                (operation_id, operation_type, entry_id, expected_hash, payload_json,
                 preview_json, created_at, expires_at, consumed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    operation_id,
                    operation_type,
                    entry_id,
                    expected_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(preview, ensure_ascii=False, sort_keys=True),
                    created_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return PendingOperation(
            operation_id=operation_id,
            operation_type=operation_type,
            entry_id=entry_id,
            preview=preview,
            expires_at=expires_at.isoformat(),
        )

    def propose_add(self, payload: dict[str, Any]) -> PendingOperation:
        normalized = _normalize_payload(payload)
        duplicate_ids = [
            entry.entry_id
            for entry in self.search(keyword=normalized["canonical_question"])
            if entry.canonical_question.casefold()
            == normalized["canonical_question"].casefold()
        ]
        preview = {
            "action": "收录新错题",
            "entry": normalized,
            "possible_duplicate_entry_ids": duplicate_ids,
            "requires_confirmation": True,
        }
        return self._save_pending("add", normalized, preview)

    def propose_update(
        self, entry_id: str, changes: dict[str, Any]
    ) -> PendingOperation:
        current = self.get(entry_id)
        normalized = _normalize_payload(changes, current.to_dict())
        changed_fields = [
            key for key in MUTABLE_FIELDS if normalized[key] != getattr(current, key)
        ]
        if not changed_fields:
            raise NotebookError("更新内容与当前版本相同。")
        preview = {
            "action": "更新错题",
            "entry_id": entry_id,
            "from_version": current.version,
            "changed_fields": sorted(changed_fields),
            "entry_after": normalized,
            "requires_confirmation": True,
        }
        return self._save_pending(
            "update", normalized, preview, entry_id, current.content_hash
        )

    def propose_delete(self, entry_id: str) -> PendingOperation:
        current = self.get(entry_id)
        preview = {
            "action": "删除错题",
            "entry_id": entry_id,
            "canonical_question": current.canonical_question,
            "current_version": current.version,
            "requires_confirmation": True,
        }
        return self._save_pending(
            "delete", {}, preview, entry_id, current.content_hash
        )

    def propose_restore(self, entry_id: str) -> PendingOperation:
        current = self.get(entry_id, include_deleted=True)
        if not current.deleted_at:
            raise NotebookError(f"错题未删除，无需恢复：{entry_id}")
        preview = {
            "action": "恢复错题",
            "entry_id": entry_id,
            "canonical_question": current.canonical_question,
            "current_version": current.version,
            "requires_confirmation": True,
        }
        return self._save_pending(
            "restore", {}, preview, entry_id, current.content_hash
        )

    @staticmethod
    def _next_entry_id(connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            "SELECT entry_id FROM answer_notebook_entries"
        ).fetchall()
        highest = max((int(row[0].split("-")[1]) for row in rows), default=0)
        return f"NB-{highest + 1:04d}"

    @staticmethod
    def _entry_values(entry: dict[str, Any]) -> tuple[Any, ...]:
        return (
            entry["entry_id"],
            entry["canonical_question"],
            entry["target_role"],
            entry["ability_type"],
            json.dumps(entry["scenario_tags"], ensure_ascii=False),
            entry["original_problem_summary"],
            entry["best_answer"],
            entry["answer_source"],
            json.dumps(entry["source_session_ids"], ensure_ascii=False),
            entry["validation_status"],
            entry["version"],
            entry["created_at"],
            entry["updated_at"],
            entry.get("deleted_at"),
            entry["content_hash"],
        )

    @staticmethod
    def _write_temp(path: Path, content: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        temp_path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return temp_path

    def _restore_markdown(self, existed: bool, content: str) -> None:
        if existed:
            temp_path = self._write_temp(self.markdown_path, content)
            os.replace(temp_path, self.markdown_path)
        else:
            self.markdown_path.unlink(missing_ok=True)

    def confirm(self, operation_id: str) -> ConfirmationResult:
        connection = self._connect()
        original_exists = self.markdown_path.exists()
        original_markdown = (
            self.markdown_path.read_text(encoding="utf-8") if original_exists else ""
        )
        markdown_replaced = False
        temp_path: Path | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT * FROM answer_notebook_pending_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if pending is None:
                raise NotebookError(f"确认操作不存在：{operation_id}")
            if pending["consumed_at"]:
                raise NotebookError("该确认操作已经执行，不能重复使用。")
            if datetime.fromisoformat(pending["expires_at"]) < datetime.now(timezone.utc):
                raise ConflictError("确认已过期，请重新生成操作预览。")

            operation_type = pending["operation_type"]
            payload = json.loads(pending["payload_json"])
            previous: NotebookEntry | None = None
            now = _now()
            if operation_type == "add":
                entry_id = self._next_entry_id(connection)
                data = dict(payload)
                data.update(
                    entry_id=entry_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            else:
                entry_id = pending["entry_id"]
                previous = self._get_with_connection(connection, entry_id)
                if previous.content_hash != pending["expected_hash"]:
                    raise ConflictError("目标错题已经变化，请基于最新版本重新确认。")
                data = previous.to_dict()
                if operation_type == "update":
                    data.update(payload)
                elif operation_type == "delete":
                    if previous.deleted_at:
                        raise ConflictError("目标错题已经删除。")
                    data["deleted_at"] = now
                elif operation_type == "restore":
                    if not previous.deleted_at:
                        raise ConflictError("目标错题已经恢复。")
                    data["deleted_at"] = None
                else:
                    raise NotebookError(f"未知操作类型：{operation_type}")
                data["version"] = previous.version + 1
                data["updated_at"] = now

            data["content_hash"] = _snapshot_hash(data)
            if operation_type == "add":
                connection.execute(
                    """
                    INSERT INTO answer_notebook_entries VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._entry_values(data),
                )
            else:
                connection.execute(
                    """
                    UPDATE answer_notebook_entries SET
                    canonical_question=?, target_role=?, ability_type=?,
                    scenario_tags_json=?, original_problem_summary=?, best_answer=?,
                    answer_source=?, source_session_ids_json=?, validation_status=?,
                    version=?, created_at=?, updated_at=?, deleted_at=?, content_hash=?
                    WHERE entry_id=?
                    """,
                    self._entry_values(data)[1:] + (entry_id,),
                )

            entry = self._entry_from_row(
                connection.execute(
                    "SELECT * FROM answer_notebook_entries WHERE entry_id = ?", (entry_id,)
                ).fetchone()
            )
            markdown_operation = operation_type
            updated_markdown = patch_markdown(
                original_markdown, markdown_operation, entry, previous
            )
            temp_path = self._write_temp(self.markdown_path, updated_markdown)
            os.replace(temp_path, self.markdown_path)
            temp_path = None
            markdown_replaced = True

            connection.execute(
                """
                INSERT INTO answer_notebook_revisions
                (entry_id, version, operation_type, snapshot_json, confirmed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.version,
                    operation_type,
                    json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            connection.execute(
                "UPDATE answer_notebook_pending_operations SET consumed_at = ? WHERE operation_id = ?",
                (now, operation_id),
            )
            connection.commit()
            changed_regions = [f"entry:{entry_id}", f"index:{entry_id}"]
            if operation_type == "update" and previous:
                if (
                    previous.canonical_question == entry.canonical_question
                    and previous.ability_type == entry.ability_type
                ):
                    changed_regions = [f"entry:{entry_id}"]
            return ConfirmationResult(
                operation_id=operation_id,
                operation_type=operation_type,
                entry_id=entry_id,
                version=entry.version,
                changed_regions=changed_regions,
                entry=entry,
            )
        except Exception:
            connection.rollback()
            if markdown_replaced:
                self._restore_markdown(original_exists, original_markdown)
            raise
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            connection.close()

    def search(
        self,
        keyword: str | None = None,
        target_role: str | None = None,
        ability_type: str | None = None,
        scenario_tag: str | None = None,
        answer_source: str | None = None,
        validation_status: str | None = None,
        entry_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[NotebookEntry]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        for column, value in (
            ("target_role", target_role),
            ("ability_type", ability_type),
            ("answer_source", answer_source),
            ("validation_status", validation_status),
            ("entry_id", entry_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        sql = "SELECT * FROM answer_notebook_entries"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY CAST(SUBSTR(entry_id, 4) AS INTEGER)"
        with self._connect() as connection:
            entries = [
                self._entry_from_row(row)
                for row in connection.execute(sql, parameters).fetchall()
            ]
        if keyword:
            needle = keyword.casefold()
            entries = [
                entry
                for entry in entries
                if needle in entry.canonical_question.casefold()
                or needle in entry.best_answer.casefold()
            ]
        if scenario_tag:
            entries = [entry for entry in entries if scenario_tag in entry.scenario_tags]
        return entries


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"JSON 格式错误：{exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("参数必须是 JSON 对象。")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interview Prism 面试错题本")
    parser.add_argument("--data-dir", required=True, help="包含 library.db 的长期数据目录")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("propose-add")
    add.add_argument("--payload", required=True, type=_json_object)
    update = commands.add_parser("propose-update")
    update.add_argument("--entry-id", required=True)
    update.add_argument("--changes", required=True, type=_json_object)
    for name in ("propose-delete", "propose-restore", "get"):
        command = commands.add_parser(name)
        command.add_argument("--entry-id", required=True)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--operation-id", required=True)
    search = commands.add_parser("search", aliases=["list"])
    search.add_argument("--keyword")
    search.add_argument("--target-role")
    search.add_argument("--ability-type")
    search.add_argument("--scenario-tag")
    search.add_argument("--answer-source", choices=sorted(ALLOWED_SOURCES))
    search.add_argument("--validation-status", choices=sorted(ALLOWED_VALIDATION))
    search.add_argument("--entry-id")
    search.add_argument("--include-deleted", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    notebook = AnswerNotebook(args.data_dir)
    try:
        if args.command == "propose-add":
            result: Any = notebook.propose_add(args.payload)
        elif args.command == "propose-update":
            result = notebook.propose_update(args.entry_id, args.changes)
        elif args.command == "propose-delete":
            result = notebook.propose_delete(args.entry_id)
        elif args.command == "propose-restore":
            result = notebook.propose_restore(args.entry_id)
        elif args.command == "confirm":
            result = notebook.confirm(args.operation_id)
        elif args.command == "get":
            result = notebook.get(args.entry_id, include_deleted=True)
        else:
            result = notebook.search(
                keyword=args.keyword,
                target_role=args.target_role,
                ability_type=args.ability_type,
                scenario_tag=args.scenario_tag,
                answer_source=args.answer_source,
                validation_status=args.validation_status,
                entry_id=args.entry_id,
                include_deleted=args.include_deleted,
            )
        if isinstance(result, list):
            payload = {"ok": True, "entries": [item.to_dict() for item in result]}
        else:
            payload = result.to_dict()
            payload["ok"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except NotebookError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
