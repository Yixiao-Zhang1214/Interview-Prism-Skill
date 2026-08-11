#!/usr/bin/env python3
"""Deterministic local storage for the interview-growth-coach Skill."""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('real', 'mock')),
    occurred_at TEXT,
    company TEXT,
    role TEXT,
    round_name TEXT,
    input_type TEXT,
    import_fingerprint TEXT NOT NULL UNIQUE,
    raw_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    speaker_role TEXT NOT NULL,
    speaker_label TEXT,
    event_type TEXT,
    text TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    confidence TEXT,
    PRIMARY KEY (session_id, segment_id),
    UNIQUE (session_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS qa_chains (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    qa_chain_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    direction TEXT NOT NULL,
    answer_status TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, qa_chain_id),
    UNIQUE (session_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS observations (
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    level INTEGER CHECK (level BETWEEN 1 AND 5),
    confidence TEXT,
    evidence_json TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, observation_id)
);

CREATE TABLE IF NOT EXISTS growth_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('real', 'mock')),
    title TEXT NOT NULL,
    task_type TEXT,
    status TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    data_json TEXT NOT NULL
);
"""

GROWTH_TASK_STATUSES = {
    "open",
    "in_progress",
    "training_passed",
    "waiting_real_validation",
    "real_validated",
    "archived",
}

GROWTH_TASK_TYPES = {
    "knowledge",
    "case_material",
    "answer_rebuild",
    "compression",
    "pressure_follow_up",
    "real_world_validation",
}


def initialize_library(data_dir):
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    db_path = data_path / "library.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
    return db_path


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _import_fingerprint(package):
    material = {
        "source_type": package["session"]["source_type"],
        "source": package.get("source", {}),
        "segments": sorted(
            package.get("segments", []), key=lambda item: item["sequence_no"]
        ),
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _observations(package):
    for assessment in package.get("assessments", []):
        for observation in assessment.get("competency_observations", []):
            yield observation


def _validate_semantic_references(value, qa_chain_ids, segment_ids):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "qa_chain_id",
                "best_answer_qa_chain_id",
                "highest_risk_qa_chain_id",
            }:
                if item not in qa_chain_ids:
                    raise ValueError(f"unknown qa_chain: {item}")
            elif key in {"qa_chain_ids", "source_qa_chain_ids"}:
                for chain_id in item:
                    if chain_id not in qa_chain_ids:
                        raise ValueError(f"unknown qa_chain: {chain_id}")
            elif key == "evidence_segment_ids":
                for segment_id in item:
                    if segment_id not in segment_ids:
                        raise ValueError(f"unknown segment: {segment_id}")
            else:
                _validate_semantic_references(item, qa_chain_ids, segment_ids)
    elif isinstance(value, list):
        for item in value:
            _validate_semantic_references(item, qa_chain_ids, segment_ids)


def _validate_qa_chain_semantic_records(
    records, record_type, qa_chain_ids, segment_ids, require_simulation=False
):
    seen_chain_ids = set()
    for record in records:
        chain_id = record.get("qa_chain_id")
        if chain_id not in qa_chain_ids:
            raise ValueError(f"unknown qa_chain: {chain_id}")
        if chain_id in seen_chain_ids:
            raise ValueError(f"duplicate {record_type}: {chain_id}")
        if require_simulation and record.get("is_simulation") is not True:
            raise ValueError("simulated_reaction is_simulation must be true")
        seen_chain_ids.add(chain_id)
        _validate_semantic_references(record, qa_chain_ids, segment_ids)


def validate_session_package(package):
    if package.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")

    session = package.get("session") or {}
    if not isinstance(session.get("id"), str) or not session["id"]:
        raise ValueError("session.id is required")
    if session.get("source_type") not in {"real", "mock"}:
        raise ValueError("source_type must be real or mock")

    segments = package.get("segments", [])
    source_block_items = package.get("source", {}).get("blocks", [])
    if segments and not source_block_items:
        raise ValueError("source.blocks are required when segments are present")

    source_blocks = {}
    for block in source_block_items:
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("source block_id is required")
        if block_id in source_blocks:
            raise ValueError(f"duplicate source block: {block_id}")
        if not isinstance(block.get("text"), str):
            raise ValueError(f"source block text must be a string: {block_id}")
        source_blocks[block_id] = block["text"]

    segment_ids = set()
    sequence_numbers = set()
    segment_sequence_numbers = {}
    ranges_by_block = {}
    for segment in segments:
        segment_id = segment.get("segment_id")
        sequence_no = segment.get("sequence_no")
        if not isinstance(segment_id, str) or not segment_id:
            raise ValueError("segment_id is required")
        if segment_id in segment_ids:
            raise ValueError(f"duplicate segment: {segment_id}")
        if not isinstance(sequence_no, int) or sequence_no < 1:
            raise ValueError(f"invalid segment sequence: {segment_id}")
        if sequence_no in sequence_numbers:
            raise ValueError(f"duplicate segment sequence: {sequence_no}")
        if not isinstance(segment.get("text"), str):
            raise ValueError(f"segment text must be a string: {segment_id}")
        if source_blocks:
            block_id = segment.get("source_block_id")
            start_char = segment.get("start_char")
            end_char = segment.get("end_char")
            if block_id not in source_blocks:
                raise ValueError(f"unknown source block: {block_id}")
            if not isinstance(start_char, int) or not isinstance(end_char, int):
                raise ValueError(f"segment character range is required: {segment_id}")
            block_text = source_blocks[block_id]
            if start_char < 0 or end_char <= start_char or end_char > len(block_text):
                raise ValueError(f"invalid segment character range: {segment_id}")
            if block_text[start_char:end_char] != segment["text"]:
                raise ValueError(f"segment text does not match source: {segment_id}")
            ranges_by_block.setdefault(block_id, []).append(
                (start_char, end_char, segment_id)
            )
        segment_ids.add(segment_id)
        sequence_numbers.add(sequence_no)
        segment_sequence_numbers[segment_id] = sequence_no

    for block_ranges in ranges_by_block.values():
        previous_end = -1
        for start_char, end_char, segment_id in sorted(block_ranges):
            if start_char < previous_end:
                raise ValueError(f"overlapping segment character range: {segment_id}")
            previous_end = end_char

    qa_chain_ids = set()
    qa_sequence_numbers = set()
    assigned_segment_ids = set()
    for chain in package.get("qa_chains", []):
        chain_id = chain.get("qa_chain_id")
        sequence_no = chain.get("sequence_no")
        if not isinstance(chain_id, str) or not chain_id:
            raise ValueError("qa_chain_id is required")
        if chain_id in qa_chain_ids:
            raise ValueError(f"duplicate qa_chain: {chain_id}")
        if not isinstance(sequence_no, int) or sequence_no < 1:
            raise ValueError(f"invalid qa_chain sequence: {chain_id}")
        if sequence_no in qa_sequence_numbers:
            raise ValueError(f"duplicate qa_chain sequence: {sequence_no}")
        qa_chain_ids.add(chain_id)
        qa_sequence_numbers.add(sequence_no)
        previous_segment_sequence = -1
        for turn in chain.get("turns", []):
            for segment_id in turn.get("segment_ids", []):
                if segment_id not in segment_ids:
                    raise ValueError(f"unknown segment: {segment_id}")
                if segment_sequence_numbers[segment_id] <= previous_segment_sequence:
                    raise ValueError(f"question chain is not chronological: {chain_id}")
                if segment_id in assigned_segment_ids:
                    raise ValueError(f"segment assigned more than once: {segment_id}")
                assigned_segment_ids.add(segment_id)
                previous_segment_sequence = segment_sequence_numbers[segment_id]

    for segment_id in package.get("other_dialogue_segment_ids", []):
        if segment_id not in segment_ids:
            raise ValueError(f"unknown segment: {segment_id}")
        if segment_id in assigned_segment_ids:
            raise ValueError(f"segment assigned more than once: {segment_id}")
        assigned_segment_ids.add(segment_id)

    unassigned_segment_ids = sorted(segment_ids - assigned_segment_ids)
    if unassigned_segment_ids:
        raise ValueError(f"unassigned segment: {unassigned_segment_ids[0]}")

    observation_ids = set()
    for observation in _observations(package):
        observation_id = observation.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("observation_id is required")
        if observation_id in observation_ids:
            raise ValueError(f"duplicate observation: {observation_id}")
        observation_ids.add(observation_id)
        level = observation.get("level")
        if level is not None and (not isinstance(level, int) or not 1 <= level <= 5):
            raise ValueError(f"invalid observation level: {observation_id}")
        for segment_id in observation.get("evidence_segment_ids", []):
            if segment_id not in segment_ids:
                raise ValueError(f"unknown segment: {segment_id}")

    _validate_qa_chain_semantic_records(
        package.get("question_analyses", []),
        "question_analysis",
        qa_chain_ids,
        segment_ids,
    )
    _validate_qa_chain_semantic_records(
        package.get("assessments", []), "assessment", qa_chain_ids, segment_ids
    )
    _validate_qa_chain_semantic_records(
        package.get("simulated_reactions", []),
        "simulated_reaction",
        qa_chain_ids,
        segment_ids,
        require_simulation=True,
    )
    _validate_semantic_references(
        package.get("session_review", {}), qa_chain_ids, segment_ids
    )

    for task in package.get("growth_tasks", []):
        _validate_semantic_references(task, qa_chain_ids, segment_ids)
        if task.get("source_type", session["source_type"]) != session["source_type"]:
            raise ValueError("growth task source_type mismatch")
        status = task.get("status", "open")
        if status not in GROWTH_TASK_STATUSES:
            raise ValueError(f"unknown task status: {status}")
        task_type = task.get("task_type")
        if task_type is not None and task_type not in GROWTH_TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type}")
        if session["source_type"] == "mock" and status == "real_validated":
            raise ValueError("mock task cannot be real_validated")

    for candidate in package.get("knowledge_candidates", []):
        _validate_semantic_references(candidate, qa_chain_ids, segment_ids)


def import_session(data_dir, package):
    db_path = initialize_library(data_dir)
    validate_session_package(package)
    session = package["session"]
    fingerprint = _import_fingerprint(package)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        duplicate = connection.execute(
            "SELECT session_id FROM sessions WHERE import_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if duplicate:
            return {
                "status": "duplicate",
                "session_id": duplicate[0],
                "duplicate_of": duplicate[0],
            }

        connection.execute(
            """
            INSERT INTO sessions (
                session_id, source_type, occurred_at, company, role, round_name,
                input_type, import_fingerprint, raw_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["id"],
                session["source_type"],
                session.get("occurred_at"),
                session.get("company"),
                session.get("role"),
                session.get("round"),
                package.get("source", {}).get("input_type"),
                fingerprint,
                _canonical_json(package),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        for segment in package.get("segments", []):
            connection.execute(
                """
                INSERT INTO segments (
                    session_id, segment_id, sequence_no, speaker_role,
                    speaker_label, event_type, text, start_time, end_time, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["id"],
                    segment["segment_id"],
                    segment["sequence_no"],
                    segment.get("speaker_role", "unknown"),
                    segment.get("speaker_label"),
                    segment.get("event_type"),
                    segment["text"],
                    segment.get("start_time"),
                    segment.get("end_time"),
                    segment.get("confidence"),
                ),
            )

        for chain in package.get("qa_chains", []):
            connection.execute(
                """
                INSERT INTO qa_chains (
                    session_id, qa_chain_id, sequence_no, direction,
                    answer_status, data_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session["id"],
                    chain["qa_chain_id"],
                    chain["sequence_no"],
                    chain.get("direction", "interviewer_to_candidate"),
                    chain.get("answer_status", "partial"),
                    _canonical_json(chain),
                ),
            )

        for observation in _observations(package):
            connection.execute(
                """
                INSERT INTO observations (
                    session_id, observation_id, dimension, level, confidence,
                    evidence_json, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["id"],
                    observation["observation_id"],
                    observation["dimension"],
                    observation.get("level"),
                    observation.get("confidence"),
                    _canonical_json(observation.get("evidence_segment_ids", [])),
                    _canonical_json(observation),
                ),
            )

        for index, task in enumerate(package.get("growth_tasks", []), start=1):
            task_id = task.get("task_id") or f"task_{session['id']}_{index:03d}"
            connection.execute(
                """
                INSERT INTO growth_tasks (
                    task_id, session_id, source_type, title, task_type, status,
                    acceptance_json, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    session["id"],
                    session["source_type"],
                    task["title"],
                    task.get("task_type"),
                    task.get("status", "open"),
                    _canonical_json(task.get("acceptance_criteria", [])),
                    _canonical_json(task),
                ),
            )

    return {
        "status": "imported",
        "session_id": session["id"],
        "duplicate_of": None,
    }


def render_original_qa(data_dir, session_id):
    db_path = initialize_library(data_dir)
    with sqlite3.connect(db_path) as connection:
        session_exists = connection.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not session_exists:
            raise ValueError(f"unknown session: {session_id}")
        segment_rows = connection.execute(
            """
            SELECT segment_id, text
            FROM segments
            WHERE session_id = ?
            ORDER BY sequence_no
            """,
            (session_id,),
        ).fetchall()
        chain_rows = connection.execute(
            """
            SELECT sequence_no, direction, answer_status, data_json
            FROM qa_chains
            WHERE session_id = ?
            ORDER BY sequence_no
            """,
            (session_id,),
        ).fetchall()

    segment_text = dict(segment_rows)
    blocks = ["# 原文问答稿"]
    candidate_question_no = 0
    for sequence_no, direction, answer_status, data_json in chain_rows:
        chain = json.loads(data_json)
        follow_up_no = 0
        has_answer = False
        for turn in chain.get("turns", []):
            turn_type = turn.get("turn_type")
            texts = [segment_text[item] for item in turn.get("segment_ids", [])]
            body = "\n".join(texts)
            if turn_type == "question":
                if direction == "candidate_to_interviewer":
                    candidate_question_no += 1
                    label = f"## 候选人反问 {candidate_question_no}："
                else:
                    label = f"## 问题 {sequence_no}："
            elif turn_type == "follow_up_question":
                follow_up_no += 1
                label = f"### 追问 {follow_up_no}："
            elif turn_type in {"answer", "follow_up_answer"}:
                has_answer = True
                label = "### 回答："
            else:
                label = "### 其他对话："
            blocks.append(f"{label}\n{body}")
        if answer_status == "missing" and not has_answer:
            blocks.append("### 回答：\n[未回答]")

    return "\n\n".join(blocks) + "\n"


def list_sessions(data_dir, include_deleted=False, source_type=None):
    if source_type not in {None, "real", "mock"}:
        raise ValueError("source_type must be real or mock")
    db_path = initialize_library(data_dir)
    clauses = []
    parameters = []
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if source_type:
        clauses.append("source_type = ?")
        parameters.append(source_type)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT session_id, source_type, occurred_at, company, role, round_name,
               imported_at, deleted_at
        FROM sessions
        {where_clause}
        ORDER BY occurred_at DESC, session_id
    """
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def soft_delete_session(data_dir, session_id):
    db_path = initialize_library(data_dir)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE sessions
            SET deleted_at = ?
            WHERE session_id = ? AND deleted_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), session_id),
        )
    return cursor.rowcount == 1


def restore_session(data_dir, session_id):
    db_path = initialize_library(data_dir)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE sessions
            SET deleted_at = NULL
            WHERE session_id = ? AND deleted_at IS NOT NULL
            """,
            (session_id,),
        )
    return cursor.rowcount == 1


def build_profile(data_dir):
    db_path = initialize_library(data_dir)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT sessions.source_type, observations.dimension,
                   AVG(observations.level), COUNT(observations.observation_id),
                   COUNT(DISTINCT sessions.session_id)
            FROM observations
            JOIN sessions USING (session_id)
            WHERE sessions.deleted_at IS NULL AND observations.level IS NOT NULL
            GROUP BY sessions.source_type, observations.dimension
            ORDER BY sessions.source_type, observations.dimension
            """
        ).fetchall()

    result = {
        "formal_profile": {},
        "training_profile": {},
        "mixed_average": None,
    }
    for source_type, dimension, average, evidence_count, session_count in rows:
        target = "formal_profile" if source_type == "real" else "training_profile"
        result[target][dimension] = {
            "average": round(average, 2),
            "evidence_count": evidence_count,
            "session_count": session_count,
        }
    return result


def _print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init")
    init_parser.add_argument("--data-dir", required=True)

    import_parser = commands.add_parser("import")
    import_parser.add_argument("--data-dir", required=True)
    import_parser.add_argument("--file", required=True)

    list_parser = commands.add_parser("list")
    list_parser.add_argument("--data-dir", required=True)
    list_parser.add_argument("--source-type", choices=("real", "mock"))
    list_parser.add_argument("--include-deleted", action="store_true")

    render_parser = commands.add_parser("render-qa")
    render_parser.add_argument("--data-dir", required=True)
    render_parser.add_argument("--session-id", required=True)
    render_parser.add_argument("--output")

    delete_parser = commands.add_parser("delete")
    delete_parser.add_argument("--data-dir", required=True)
    delete_parser.add_argument("--session-id", required=True)

    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--data-dir", required=True)
    restore_parser.add_argument("--session-id", required=True)

    profile_parser = commands.add_parser("profile")
    profile_parser.add_argument("--data-dir", required=True)

    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "init":
            _print_json({"database": str(initialize_library(args.data_dir))})
        elif args.command == "import":
            package = json.loads(Path(args.file).read_text(encoding="utf-8"))
            _print_json(import_session(args.data_dir, package))
        elif args.command == "list":
            _print_json(
                list_sessions(
                    args.data_dir,
                    include_deleted=args.include_deleted,
                    source_type=args.source_type,
                )
            )
        elif args.command == "render-qa":
            rendered = render_original_qa(args.data_dir, args.session_id)
            if args.output:
                Path(args.output).write_text(rendered, encoding="utf-8")
                _print_json({"output": str(Path(args.output))})
            else:
                print(rendered, end="")
        elif args.command == "delete":
            _print_json(
                {"session_id": args.session_id, "deleted": soft_delete_session(args.data_dir, args.session_id)}
            )
        elif args.command == "restore":
            _print_json(
                {"session_id": args.session_id, "restored": restore_session(args.data_dir, args.session_id)}
            )
        elif args.command == "profile":
            _print_json(build_profile(args.data_dir))
    except (KeyError, OSError, sqlite3.Error, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
