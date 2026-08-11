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
            {"sessions", "segments", "qa_chains", "observations", "growth_tasks"}
            <= names
        )

    def test_duplicate_transcript_import_is_idempotent(self):
        first = store.import_session(self.data_dir, self.session_package())
        duplicate = store.import_session(
            self.data_dir, self.session_package(session_id="int_002")
        )

        self.assertEqual("imported", first["status"])
        self.assertEqual("duplicate", duplicate["status"])
        self.assertEqual("int_001", duplicate["duplicate_of"])
        with sqlite3.connect(self.data_dir / "library.db") as connection:
            count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(1, count)

    def test_unknown_segment_reference_rejects_whole_import(self):
        package = self.session_package()
        package["qa_chains"][0]["turns"][1]["segment_ids"] = ["seg_missing"]

        with self.assertRaisesRegex(ValueError, "unknown segment"):
            store.import_session(self.data_dir, package)

        with sqlite3.connect(self.data_dir / "library.db") as connection:
            session_count = connection.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            segment_count = connection.execute(
                "SELECT COUNT(*) FROM segments"
            ).fetchone()[0]
        self.assertEqual(0, session_count)
        self.assertEqual(0, segment_count)

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


if __name__ == "__main__":
    unittest.main()
