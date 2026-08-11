import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("interview_store.py")


def load_store_module():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("interview_store", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_store_module()


class InterviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def session_package(self, session_id="int_001", source_type="real"):
        return {
            "schema_version": 1,
            "session": {
                "id": session_id,
                "source_type": source_type,
                "occurred_at": "2026-08-10T10:00:00+08:00",
                "company": "Example",
                "role": "Product Manager",
                "round": "Hiring Manager",
            },
            "source": {
                "input_type": "text",
                "blocks": [
                    {"block_id": "blk_001", "text": "讲一个你推动复杂项目的例子。"},
                    {"block_id": "blk_002", "text": "我们做了推荐页改版。"},
                ],
            },
            "segments": [
                {
                    "segment_id": "seg_001",
                    "sequence_no": 1,
                    "source_block_id": "blk_001",
                    "start_char": 0,
                    "end_char": 14,
                    "speaker_role": "interviewer",
                    "event_type": "question",
                    "text": "讲一个你推动复杂项目的例子。",
                    "confidence": "high",
                },
                {
                    "segment_id": "seg_002",
                    "sequence_no": 2,
                    "source_block_id": "blk_002",
                    "start_char": 0,
                    "end_char": 10,
                    "speaker_role": "candidate",
                    "event_type": "answer",
                    "text": "我们做了推荐页改版。",
                    "confidence": "high",
                },
            ],
            "qa_chains": [
                {
                    "qa_chain_id": "qa_001",
                    "sequence_no": 1,
                    "direction": "interviewer_to_candidate",
                    "answer_status": "complete",
                    "turns": [
                        {"turn_type": "question", "segment_ids": ["seg_001"]},
                        {"turn_type": "answer", "segment_ids": ["seg_002"]},
                    ],
                }
            ],
            "assessments": [],
            "growth_tasks": [],
        }

    def add_observation(self, package, observation_id, level):
        package["assessments"] = [
            {
                "qa_chain_id": "qa_001",
                "competency_observations": [
                    {
                        "observation_id": observation_id,
                        "dimension": "structured_communication",
                        "level": level,
                        "confidence": "high",
                        "evidence_segment_ids": ["seg_002"],
                    }
                ],
            }
        ]

    def test_initialize_library_creates_database_and_schema(self):
        self.assertIsNotNone(store, "interview_store.py is missing")
        db_path = store.initialize_library(self.data_dir)
        self.assertTrue(db_path.exists())
        with sqlite3.connect(db_path) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(
            {
                "sessions",
                "session_revisions",
                "segments",
                "qa_chains",
                "observations",
                "growth_tasks",
            }
            <= names
        )

    def test_new_source_import_creates_revision_one(self):
        result = store.import_session(self.data_dir, self.session_package())

        self.assertEqual(
            {"status": "imported", "session_id": "int_001", "revision_no": 1},
            result,
        )
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            session_revision = connection.execute(
                "SELECT current_revision FROM sessions WHERE session_id = 'int_001'"
            ).fetchone()[0]
            revisions = connection.execute(
                "SELECT revision_no FROM session_revisions WHERE session_id = 'int_001'"
            ).fetchall()
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(1, session_revision)
        self.assertEqual([(1,)], revisions)
        self.assertEqual(2, user_version)

    def test_different_session_id_with_same_source_is_duplicate_source(self):
        first = store.import_session(self.data_dir, self.session_package())
        duplicate = store.import_session(
            self.data_dir, self.session_package(session_id="int_002")
        )

        self.assertEqual("imported", first["status"])
        self.assertEqual(
            {
                "status": "duplicate_source",
                "session_id": "int_002",
                "existing_session_id": "int_001",
            },
            duplicate,
        )
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(1, count)

    def test_same_session_source_and_package_is_unchanged(self):
        package = self.session_package()
        store.import_session(self.data_dir, package)

        result = store.import_session(self.data_dir, package)

        self.assertEqual(
            {"status": "unchanged", "session_id": "int_001", "revision_no": 1},
            result,
        )
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            revision_count = connection.execute(
                "SELECT COUNT(*) FROM session_revisions"
            ).fetchone()[0]
        self.assertEqual(1, revision_count)

    def test_same_session_and_source_with_changed_package_creates_revision(self):
        original = self.session_package()
        original["growth_tasks"] = [
            {"task_id": "task_old", "title": "Old task", "status": "open"}
        ]
        store.import_session(self.data_dir, original)
        revised = self.session_package()
        revised["session"]["role"] = "Senior Product Manager"
        revised["qa_chains"][0]["answer_status"] = "partial"
        revised["growth_tasks"] = [
            {"task_id": "task_new", "title": "New task", "status": "in_progress"}
        ]

        result = store.import_session(self.data_dir, revised)

        self.assertEqual(
            {"status": "revised", "session_id": "int_001", "revision_no": 2},
            result,
        )
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            current_revision, current_json = connection.execute(
                "SELECT current_revision, raw_json FROM sessions WHERE session_id = 'int_001'"
            ).fetchone()
            revision_json = connection.execute(
                "SELECT package_json FROM session_revisions WHERE session_id = 'int_001' ORDER BY revision_no"
            ).fetchall()
            qa_status = connection.execute(
                "SELECT answer_status FROM qa_chains WHERE session_id = 'int_001'"
            ).fetchone()[0]
            tasks = connection.execute(
                "SELECT task_id FROM growth_tasks WHERE session_id = 'int_001'"
            ).fetchall()
        self.assertEqual(2, current_revision)
        self.assertEqual("Senior Product Manager", json.loads(current_json)["session"]["role"])
        self.assertEqual(
            ["Product Manager", "Senior Product Manager"],
            [json.loads(row[0])["session"]["role"] for row in revision_json],
        )
        self.assertEqual("partial", qa_status)
        self.assertEqual([("task_new",)], tasks)

    def test_same_session_id_with_changed_source_raises_source_conflict(self):
        store.import_session(self.data_dir, self.session_package())
        changed_source = self.session_package()
        changed_source["source"]["blocks"][1]["text"] = "new answer"
        changed_source["segments"][1]["text"] = "new answer"
        changed_source["segments"][1]["end_char"] = 10

        with self.assertRaisesRegex(ValueError, "source conflict.*int_001"):
            store.import_session(self.data_dir, changed_source)

        with sqlite3.connect(self.data_dir / "library.db") as connection:
            revision_count = connection.execute(
                "SELECT COUNT(*) FROM session_revisions"
            ).fetchone()[0]
        self.assertEqual(1, revision_count)

    def test_initialize_library_migrates_v1_raw_json_to_revision_one(self):
        db_path = self.data_dir / "library.db"
        package = self.session_package()
        raw_json = json.dumps(
            package, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with sqlite3.connect(db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
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
                PRAGMA user_version = 1;
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, source_type, import_fingerprint, raw_json, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("int_001", "real", "legacy-source", raw_json, "2026-08-10T02:00:00+00:00"),
            )

        store.initialize_library(self.data_dir)

        with sqlite3.connect(db_path) as connection:
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            current_revision = connection.execute(
                "SELECT current_revision FROM sessions WHERE session_id = 'int_001'"
            ).fetchone()[0]
            revision = connection.execute(
                "SELECT revision_no, package_json, created_at FROM session_revisions"
            ).fetchone()
        self.assertEqual(2, user_version)
        self.assertEqual(1, current_revision)
        self.assertEqual((1, raw_json, "2026-08-10T02:00:00+00:00"), revision)

    def test_revision_replaces_observations_used_by_profile(self):
        original = self.session_package()
        self.add_observation(original, "obs_original", 2)
        store.import_session(self.data_dir, original)
        revised = self.session_package()
        self.add_observation(revised, "obs_revised", 5)

        store.import_session(self.data_dir, revised)
        profile = store.build_profile(self.data_dir)

        self.assertEqual(
            {"average": 5.0, "evidence_count": 1, "session_count": 1},
            profile["formal_profile"]["structured_communication"],
        )

    def test_same_external_task_id_is_scoped_across_real_and_mock_sessions(self):
        real_package = self.session_package()
        real_package["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Real task", "status": "open"}
        ]
        mock_package = self.session_package(session_id="mock_001", source_type="mock")
        mock_package["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Mock task", "status": "open"}
        ]

        store.import_session(self.data_dir, real_package)
        store.import_session(self.data_dir, mock_package)

        with sqlite3.connect(self.data_dir / "library.db") as connection:
            tasks = connection.execute(
                """
                SELECT session_id, task_id, title
                FROM growth_tasks
                ORDER BY session_id
                """
            ).fetchall()
        self.assertEqual(
            [
                ("int_001", "task_shared", "Real task"),
                ("mock_001", "task_shared", "Mock task"),
            ],
            tasks,
        )

    def test_revision_replaces_only_its_session_scoped_task_projection(self):
        real_package = self.session_package()
        real_package["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Real task", "status": "open"}
        ]
        mock_package = self.session_package(session_id="mock_001", source_type="mock")
        mock_package["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Mock task", "status": "open"}
        ]
        store.import_session(self.data_dir, real_package)
        store.import_session(self.data_dir, mock_package)
        revised_real = self.session_package()
        revised_real["session"]["role"] = "Senior Product Manager"
        revised_real["growth_tasks"] = [
            {
                "task_id": "task_shared",
                "title": "Revised real task",
                "status": "in_progress",
            }
        ]

        result = store.import_session(self.data_dir, revised_real)

        self.assertEqual(2, result["revision_no"])
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            tasks = connection.execute(
                """
                SELECT session_id, task_id, title
                FROM growth_tasks
                ORDER BY session_id
                """
            ).fetchall()
        self.assertEqual(
            [
                ("int_001", "task_shared", "Revised real task"),
                ("mock_001", "task_shared", "Mock task"),
            ],
            tasks,
        )

    def test_v1_task_projection_migration_allows_reused_external_task_id(self):
        db_path = self.data_dir / "library.db"
        legacy_package = self.session_package()
        raw_json = json.dumps(
            legacy_package, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with sqlite3.connect(db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
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
                CREATE TABLE growth_tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    task_type TEXT,
                    status TEXT NOT NULL,
                    acceptance_json TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, source_type, import_fingerprint, raw_json, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("int_001", "real", "legacy-source", raw_json, "2026-08-10T02:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO growth_tasks (
                    task_id, session_id, source_type, title, status,
                    acceptance_json, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task_shared",
                    "int_001",
                    "real",
                    "Legacy task",
                    "open",
                    "[]",
                    '{"task_id":"task_shared","title":"Legacy task"}',
                ),
            )

        store.initialize_library(self.data_dir)
        mock_package = self.session_package(session_id="mock_001", source_type="mock")
        mock_package["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Mock task", "status": "open"}
        ]
        store.import_session(self.data_dir, mock_package)

        with sqlite3.connect(db_path) as connection:
            tasks = connection.execute(
                """
                SELECT session_id, task_id, title
                FROM growth_tasks
                ORDER BY session_id
                """
            ).fetchall()
        self.assertEqual(
            [
                ("int_001", "task_shared", "Legacy task"),
                ("mock_001", "task_shared", "Mock task"),
            ],
            tasks,
        )

    def test_v1_migration_failure_rolls_back_schema_version_and_data(self):
        db_path = self.data_dir / "library.db"
        malformed_json = "{not valid json"
        with sqlite3.connect(db_path) as connection:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
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
                CREATE TABLE growth_tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    task_type TEXT,
                    status TEXT NOT NULL,
                    acceptance_json TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, source_type, import_fingerprint, raw_json, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("int_001", "real", "legacy-source", malformed_json, "2026-08-10T02:00:00+00:00"),
            )

        with self.assertRaises(json.JSONDecodeError):
            store.initialize_library(self.data_dir)

        with sqlite3.connect(db_path) as connection:
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            session_columns = [
                row[1] for row in connection.execute("PRAGMA table_info(sessions)")
            ]
            revision_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'session_revisions'
                """
            ).fetchone()
            task_primary_key = [
                row[1]
                for row in connection.execute("PRAGMA table_info(growth_tasks)")
                if row[5]
            ]
            stored_json = connection.execute(
                "SELECT raw_json FROM sessions WHERE session_id = 'int_001'"
            ).fetchone()[0]
        self.assertEqual(1, user_version)
        self.assertNotIn("current_revision", session_columns)
        self.assertIsNone(revision_table)
        self.assertEqual(["task_id"], task_primary_key)
        self.assertEqual(malformed_json, stored_json)

    def test_revision_failure_rolls_back_history_and_current_projections(self):
        original = self.session_package()
        self.add_observation(original, "obs_original", 2)
        original["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Original task", "status": "open"}
        ]
        store.import_session(self.data_dir, original)
        db_path = self.data_dir / "library.db"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER abort_failed_revision
                BEFORE INSERT ON growth_tasks
                WHEN NEW.title = 'Fail revision'
                BEGIN
                    SELECT RAISE(ABORT, 'forced revision failure');
                END
                """
            )
        revised = self.session_package()
        revised["session"]["role"] = "Senior Product Manager"
        revised["qa_chains"][0]["answer_status"] = "partial"
        self.add_observation(revised, "obs_revised", 5)
        revised["growth_tasks"] = [
            {"task_id": "task_shared", "title": "Fail revision", "status": "open"}
        ]

        with self.assertRaisesRegex(sqlite3.IntegrityError, "forced revision failure"):
            store.import_session(self.data_dir, revised)

        with sqlite3.connect(db_path) as connection:
            current_revision, raw_json = connection.execute(
                "SELECT current_revision, raw_json FROM sessions WHERE session_id = 'int_001'"
            ).fetchone()
            revisions = connection.execute(
                "SELECT revision_no FROM session_revisions WHERE session_id = 'int_001'"
            ).fetchall()
            qa_status = connection.execute(
                "SELECT answer_status FROM qa_chains WHERE session_id = 'int_001'"
            ).fetchone()[0]
            observations = connection.execute(
                "SELECT observation_id FROM observations WHERE session_id = 'int_001'"
            ).fetchall()
            tasks = connection.execute(
                "SELECT task_id, title FROM growth_tasks WHERE session_id = 'int_001'"
            ).fetchall()
        self.assertEqual(1, current_revision)
        self.assertEqual("Product Manager", json.loads(raw_json)["session"]["role"])
        self.assertEqual([(1,)], revisions)
        self.assertEqual("complete", qa_status)
        self.assertEqual([("obs_original",)], observations)
        self.assertEqual([("task_shared", "Original task")], tasks)

    def test_unknown_segment_reference_rejects_whole_import(self):
        package = self.session_package()
        package["qa_chains"][0]["turns"][1]["segment_ids"] = ["seg_missing"]

        with self.assertRaisesRegex(ValueError, "unknown segment"):
            store.import_session(self.data_dir, package)

        self.assertFalse((self.data_dir / "library.db").exists())

    def test_segment_cannot_be_assigned_to_multiple_question_chains(self):
        package = self.session_package()
        package["qa_chains"].append(
            {
                "qa_chain_id": "qa_002",
                "sequence_no": 2,
                "direction": "interviewer_to_candidate",
                "answer_status": "missing",
                "turns": [
                    {"turn_type": "question", "segment_ids": ["seg_001"]}
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "segment assigned more than once"):
            store.import_session(self.data_dir, package)

    def test_unassigned_segment_is_rejected(self):
        package = self.session_package()
        package["source"]["blocks"].append(
            {"block_id": "blk_003", "text": "谢谢，今天就到这里。"}
        )
        package["segments"].append(
            {
                "segment_id": "seg_003",
                "sequence_no": 3,
                "source_block_id": "blk_003",
                "start_char": 0,
                "end_char": 10,
                "speaker_role": "interviewer",
                "event_type": "closing",
                "text": "谢谢，今天就到这里。",
                "confidence": "high",
            }
        )

        with self.assertRaisesRegex(ValueError, "unassigned segment"):
            store.import_session(self.data_dir, package)

    def test_question_chain_segment_order_must_be_chronological(self):
        package = self.session_package()
        package["qa_chains"][0]["turns"] = [
            {"turn_type": "answer", "segment_ids": ["seg_002"]},
            {"turn_type": "question", "segment_ids": ["seg_001"]},
        ]

        with self.assertRaisesRegex(ValueError, "question chain is not chronological"):
            store.import_session(self.data_dir, package)

    def test_segment_text_must_match_source_character_range(self):
        package = self.session_package()
        package["segments"][1]["text"] = "我们完成了推荐页改版。"

        with self.assertRaisesRegex(ValueError, "segment text does not match source"):
            store.import_session(self.data_dir, package)

    def test_segments_require_source_blocks_for_mechanical_verification(self):
        package = self.session_package()
        package["source"].pop("blocks")

        with self.assertRaisesRegex(ValueError, "source.blocks are required"):
            store.import_session(self.data_dir, package)

    def test_render_original_qa_preserves_text_and_question_chain(self):
        package = self.session_package()
        package["segments"].extend(
            [
                {
                    "segment_id": "seg_003",
                    "sequence_no": 3,
                    "source_block_id": "blk_003",
                    "start_char": 0,
                    "end_char": 8,
                    "speaker_role": "interviewer",
                    "event_type": "follow_up_question",
                    "text": "你个人做了什么？",
                    "confidence": "high",
                },
                {
                    "segment_id": "seg_004",
                    "sequence_no": 4,
                    "source_block_id": "blk_004",
                    "start_char": 0,
                    "end_char": 11,
                    "speaker_role": "candidate",
                    "event_type": "follow_up_answer",
                    "text": "嗯，我主要协调，协调。",
                    "confidence": "high",
                },
                {
                    "segment_id": "seg_005",
                    "sequence_no": 5,
                    "source_block_id": "blk_005",
                    "start_char": 0,
                    "end_char": 7,
                    "speaker_role": "interviewer",
                    "event_type": "question",
                    "text": "结果怎么证明？",
                    "confidence": "high",
                },
            ]
        )
        package["source"]["blocks"].extend(
            [
                {"block_id": "blk_003", "text": "你个人做了什么？"},
                {"block_id": "blk_004", "text": "嗯，我主要协调，协调。"},
                {"block_id": "blk_005", "text": "结果怎么证明？"},
            ]
        )
        package["qa_chains"][0]["turns"].extend(
            [
                {"turn_type": "follow_up_question", "segment_ids": ["seg_003"]},
                {"turn_type": "follow_up_answer", "segment_ids": ["seg_004"]},
            ]
        )
        package["qa_chains"].append(
            {
                "qa_chain_id": "qa_002",
                "sequence_no": 2,
                "direction": "interviewer_to_candidate",
                "answer_status": "missing",
                "turns": [
                    {"turn_type": "question", "segment_ids": ["seg_005"]}
                ],
            }
        )
        store.import_session(self.data_dir, package)

        rendered = store.render_original_qa(self.data_dir, "int_001")

        self.assertEqual(
            "# 原文问答稿\n\n"
            "## 问题 1：\n讲一个你推动复杂项目的例子。\n\n"
            "### 回答：\n我们做了推荐页改版。\n\n"
            "### 追问 1：\n你个人做了什么？\n\n"
            "### 回答：\n嗯，我主要协调，协调。\n\n"
            "## 问题 2：\n结果怎么证明？\n\n"
            "### 回答：\n[未回答]\n",
            rendered,
        )

    def test_list_sessions_filters_by_source_type(self):
        store.import_session(self.data_dir, self.session_package())
        mock_package = self.session_package(session_id="mock_001", source_type="mock")
        mock_package["segments"][1]["text"] = "这是模拟回答。"
        mock_package["segments"][1]["end_char"] = 7
        mock_package["source"]["blocks"][1]["text"] = "这是模拟回答。"
        store.import_session(self.data_dir, mock_package)

        real_sessions = store.list_sessions(self.data_dir, source_type="real")
        mock_sessions = store.list_sessions(self.data_dir, source_type="mock")

        self.assertEqual(["int_001"], [item["session_id"] for item in real_sessions])
        self.assertEqual(
            ["mock_001"], [item["session_id"] for item in mock_sessions]
        )

    def test_soft_delete_hides_session_and_restore_recovers_it(self):
        store.import_session(self.data_dir, self.session_package())

        self.assertTrue(store.soft_delete_session(self.data_dir, "int_001"))
        self.assertEqual([], store.list_sessions(self.data_dir))
        deleted = store.list_sessions(self.data_dir, include_deleted=True)
        self.assertIsNotNone(deleted[0]["deleted_at"])

        self.assertTrue(store.restore_session(self.data_dir, "int_001"))
        restored = store.list_sessions(self.data_dir)
        self.assertEqual(["int_001"], [item["session_id"] for item in restored])

    def test_profile_never_mixes_real_and_mock_scores(self):
        real_package = self.session_package()
        self.add_observation(real_package, "obs_real_001", 2)
        store.import_session(self.data_dir, real_package)

        first_mock = self.session_package(session_id="mock_001", source_type="mock")
        first_mock["segments"][1]["text"] = "第一次模拟回答。"
        first_mock["segments"][1]["end_char"] = 8
        first_mock["source"]["blocks"][1]["text"] = "第一次模拟回答。"
        self.add_observation(first_mock, "obs_mock_001", 5)
        store.import_session(self.data_dir, first_mock)

        second_mock = self.session_package(session_id="mock_002", source_type="mock")
        second_mock["segments"][1]["text"] = "第二次模拟回答。"
        second_mock["segments"][1]["end_char"] = 8
        second_mock["source"]["blocks"][1]["text"] = "第二次模拟回答。"
        self.add_observation(second_mock, "obs_mock_002", 5)
        store.import_session(self.data_dir, second_mock)

        profile = store.build_profile(self.data_dir)

        self.assertEqual(
            {"average": 2.0, "evidence_count": 1, "session_count": 1},
            profile["formal_profile"]["structured_communication"],
        )
        self.assertEqual(
            {"average": 5.0, "evidence_count": 2, "session_count": 2},
            profile["training_profile"]["structured_communication"],
        )
        self.assertIsNone(profile["mixed_average"])

    def test_cli_import_list_and_render(self):
        package_path = self.data_dir / "session.json"
        package_path.write_text(
            json.dumps(self.session_package(), ensure_ascii=False), encoding="utf-8"
        )
        archive_path = self.data_dir / "archive"

        imported = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "import",
                "--data-dir",
                str(archive_path),
                "--file",
                str(package_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, imported.returncode, imported.stderr)
        self.assertEqual("imported", json.loads(imported.stdout)["status"])

        listed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "list",
                "--data-dir",
                str(archive_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, listed.returncode, listed.stderr)
        self.assertEqual("int_001", json.loads(listed.stdout)[0]["session_id"])

        rendered = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "render-qa",
                "--data-dir",
                str(archive_path),
                "--session-id",
                "int_001",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, rendered.returncode, rendered.stderr)
        self.assertIn("讲一个你推动复杂项目的例子。", rendered.stdout)
        self.assertIn("我们做了推荐页改版。", rendered.stdout)


class SemanticValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def package(self):
        return InterviewStoreTests().session_package()

    def assert_unknown_qa_chain_is_rejected(self, key, value):
        package = self.package()
        package[key] = value

        with self.assertRaisesRegex(ValueError, "unknown qa_chain"):
            store.validate_session_package(package)

    def test_question_analyses_reject_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "question_analyses", [{"qa_chain_id": "qa_missing"}]
        )

    def test_assessments_reject_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "assessments", [{"qa_chain_id": "qa_missing"}]
        )

    def test_simulated_reactions_reject_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "simulated_reactions",
            [{"qa_chain_id": "qa_missing", "is_simulation": True}],
        )

    def test_session_review_rejects_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "session_review", {"best_answer_qa_chain_id": "qa_missing"}
        )

    def test_growth_tasks_reject_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "growth_tasks",
            [{"task_id": "task_001", "source_qa_chain_ids": ["qa_missing"]}],
        )

    def test_knowledge_candidates_reject_unknown_qa_chain_references(self):
        self.assert_unknown_qa_chain_is_rejected(
            "knowledge_candidates", [{"source_qa_chain_ids": ["qa_missing"]}]
        )

    def test_duplicate_question_analysis_for_a_qa_chain_is_rejected(self):
        package = self.package()
        package["question_analyses"] = [
            {"qa_chain_id": "qa_001"},
            {"qa_chain_id": "qa_001"},
        ]

        with self.assertRaisesRegex(ValueError, "duplicate question_analysis"):
            store.validate_session_package(package)

    def test_duplicate_assessment_for_a_qa_chain_is_rejected(self):
        package = self.package()
        package["assessments"] = [
            {"qa_chain_id": "qa_001", "competency_observations": []},
            {"qa_chain_id": "qa_001", "competency_observations": []},
        ]

        with self.assertRaisesRegex(ValueError, "duplicate assessment"):
            store.validate_session_package(package)

    def test_duplicate_simulated_reaction_for_a_qa_chain_is_rejected(self):
        package = self.package()
        package["simulated_reactions"] = [
            {"qa_chain_id": "qa_001", "is_simulation": True},
            {"qa_chain_id": "qa_001", "is_simulation": True},
        ]

        with self.assertRaisesRegex(ValueError, "duplicate simulated_reaction"):
            store.validate_session_package(package)

    def test_simulated_reactions_require_explicit_true_simulation_marker(self):
        for marker in (None, False):
            with self.subTest(marker=marker):
                package = self.package()
                reaction = {"qa_chain_id": "qa_001"}
                if marker is not None:
                    reaction["is_simulation"] = marker
                package["simulated_reactions"] = [reaction]

                with self.assertRaisesRegex(ValueError, "is_simulation"):
                    store.validate_session_package(package)

    def test_semantic_records_reject_unknown_evidence_segments(self):
        cases = (
            ("question_analyses", [{"qa_chain_id": "qa_001", "evidence_segment_ids": ["seg_missing"]}]),
            ("assessments", [{"qa_chain_id": "qa_001", "evidence_segment_ids": ["seg_missing"], "competency_observations": []}]),
            ("simulated_reactions", [{"qa_chain_id": "qa_001", "is_simulation": True, "evidence_segment_ids": ["seg_missing"]}]),
            ("session_review", {"key_turns": [{"qa_chain_id": "qa_001", "evidence_segment_ids": ["seg_missing"]}]}),
            ("growth_tasks", [{"task_id": "task_001", "source_qa_chain_ids": ["qa_001"], "evidence_segment_ids": ["seg_missing"]}]),
            ("knowledge_candidates", [{"source_qa_chain_ids": ["qa_001"], "evidence_segment_ids": ["seg_missing"]}]),
        )
        for key, value in cases:
            with self.subTest(record_type=key):
                package = self.package()
                package[key] = value

                with self.assertRaisesRegex(ValueError, "unknown segment"):
                    store.validate_session_package(package)

    def test_growth_tasks_accept_every_canonical_status(self):
        for status in (
            "open",
            "in_progress",
            "training_passed",
            "waiting_real_validation",
            "real_validated",
            "archived",
        ):
            with self.subTest(status=status):
                package = self.package()
                package["growth_tasks"] = [{"task_id": "task_001", "status": status}]
                store.validate_session_package(package)

    def test_growth_tasks_reject_unknown_status_and_type(self):
        for task in (
            {"task_id": "task_001", "status": "closed"},
            {"task_id": "task_001", "task_type": "unknown_type"},
        ):
            with self.subTest(task=task):
                package = self.package()
                package["growth_tasks"] = [task]

                with self.assertRaisesRegex(ValueError, "unknown task"):
                    store.validate_session_package(package)

    def test_import_rejects_unknown_qa_chain_before_creating_database(self):
        package = self.package()
        package["question_analyses"] = [{"qa_chain_id": "qa_missing"}]

        with self.assertRaisesRegex(ValueError, "unknown qa_chain"):
            store.import_session(self.data_dir, package)

        self.assertFalse((self.data_dir / "library.db").exists())

    def test_import_rejects_unknown_evidence_before_creating_database(self):
        package = self.package()
        package["question_analyses"] = [
            {"qa_chain_id": "qa_001", "evidence_segment_ids": ["seg_missing"]}
        ]

        with self.assertRaisesRegex(ValueError, "unknown segment"):
            store.import_session(self.data_dir, package)

        self.assertFalse((self.data_dir / "library.db").exists())


if __name__ == "__main__":
    unittest.main()
