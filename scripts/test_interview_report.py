import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

REPORT_SCRIPT = Path(__file__).with_name("interview_report.py")
STORE_SCRIPT = Path(__file__).with_name("interview_store.py")

from interview_report import (
    artifact_stem,
    build_comparison_view,
    load_session_packages,
    render_ability_model_text,
    render_comparison_text,
    render_frequent_questions_text,
    render_radar_svg,
    render_single_text,
    write_artifact_bundle,
)
from interview_store import import_session, soft_delete_session


def sample_package(source_type="real", session_id="int_test"):
    return {
        "schema_version": 1,
        "session": {
            "id": session_id,
            "source_type": source_type,
            "occurred_at": "2026-07-02T13:30:00+08:00",
            "company": None,
            "role": "AI产品岗实习",
            "round": None,
        },
        "source": {
            "input_type": "text",
            "blocks": [
                {"block_id": "blk_001", "text": "项目解决了什么需求？"},
                {"block_id": "blk_002", "text": "它补充了推荐和搜索的边界。"},
            ],
        },
        "segments": [
            {"segment_id": "seg_001", "sequence_no": 1, "source_block_id": "blk_001", "start_char": 0, "end_char": len("项目解决了什么需求？"), "speaker_role": "interviewer", "event_type": "question", "text": "项目解决了什么需求？"},
            {"segment_id": "seg_002", "sequence_no": 2, "source_block_id": "blk_002", "start_char": 0, "end_char": len("它补充了推荐和搜索的边界。"), "speaker_role": "candidate", "event_type": "answer", "text": "它补充了推荐和搜索的边界。"},
        ],
        "qa_chains": [
            {"qa_chain_id": "qa_001", "sequence_no": 1, "direction": "interviewer_to_candidate", "turns": [{"turn_type": "question", "segment_ids": ["seg_001"]}, {"turn_type": "answer", "segment_ids": ["seg_002"]}], "answer_status": "complete"}
        ],
        "other_dialogue_segment_ids": [],
        "question_analyses": [
            {"qa_chain_id": "qa_001", "surface_question": "项目解决了什么需求？", "primary_inferred_focus": {"description": "观察需求定义是否清楚。"}}
        ],
        "assessments": [
            {
                "qa_chain_id": "qa_001",
                "effective_elements": [{"observation": "能区分推荐和搜索。", "evidence_segment_ids": ["seg_002"]}],
                "missing_elements": [{"element": "缺少采用率。", "basis": "explicitly_missing", "evidence_segment_ids": ["seg_002"]}],
                "risk_signals": [{"risk": "结论强于证据。", "evidence_segment_ids": ["seg_001"]}],
                "root_causes": [{"category": "case_evidence", "description": "缺少结果证据。", "evidence_segment_ids": ["seg_002"]}],
                "competency_observations": [
                    {"observation_id": "obs_001", "dimension": "problem_framing", "level": 4, "evidence_segment_ids": ["seg_002"], "confidence": "high"}
                ],
            }
        ],
        "simulated_reactions": [
            {"qa_chain_id": "qa_001", "is_simulation": True, "possible_first_reaction": "边界思考较清楚。", "remaining_concern": "仍缺少效果证据。"}
        ],
        "session_review": {
            "three_sentence_summary": ["需求边界清楚。", "效果证据不足。", "优先补齐数据。"],
            "key_turns": [{"qa_chain_id": "qa_001", "description": "需求边界回答加分。", "effect": "positive", "supporting_observation_ids": ["obs_001"]}],
            "best_answer_qa_chain_id": "qa_001",
            "highest_risk_qa_chain_id": "qa_001",
            "simulated_overall_result": {"is_simulation": True, "tendency": "hold", "uncertainty_reasons": ["没有真实结果。"]},
            "information_gaps": [{"gap": "缺少真实结果。", "effect_on_review": "只能模拟。"}],
        },
        "growth_tasks": [
            {"task_id": "task_001", "title": "补齐项目证据", "root_cause": "case_evidence", "steps": ["整理基线和对照。"], "acceptance_criteria": ["90 秒说清结果。"], "real_validation_condition": "后续真实面试达到 4 级。"}
        ],
        "knowledge_candidates": [{"canonical_question": "项目解决了什么需求？", "source_qa_chain_ids": ["qa_001"]}],
        "warnings": [],
    }


class SingleReportTests(unittest.TestCase):
    def test_single_text_rejects_invalid_package_at_public_boundary(self):
        package = sample_package()
        package["question_analyses"][0]["qa_chain_id"] = "qa_missing"

        with self.assertRaisesRegex(ValueError, "unknown qa_chain"):
            render_single_text(package)

    def test_single_text_uses_chinese_labels_for_every_canonical_task_status(self):
        package = sample_package()
        package["growth_tasks"] = [
            {
                "task_id": f"task_{status}",
                "title": status,
                "status": status,
                "acceptance_criteria": [],
            }
            for status in (
                "open",
                "in_progress",
                "training_passed",
                "waiting_real_validation",
                "real_validated",
                "archived",
            )
        ]

        text = render_single_text(package)

        for label in (
            "待开始",
            "进行中",
            "训练已通过",
            "等待真实面试验证",
            "已通过真实面试验证",
            "已归档",
        ):
            self.assertIn(label, text)

    def test_single_text_report_is_plain_readable_markdown(self):
        package = sample_package()
        package["growth_tasks"][0]["real_validation_condition"] = (
            "后续真实面试中 problem_framing 达到 4 级。"
        )
        text = render_single_text(package, "IP-R-20260702-1330-ability-radar.svg")

        labels = [
            "# 面试复盘",
            "## 30 秒结论",
            "## 能力快照",
            "## 关键问题",
            "## 成长任务",
            "## 证据说明",
        ]
        positions = [text.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("## 模拟面试官总评", text)
        self.assertIn("现有证据不足，无法判断是否通过", text)
        self.assertNotIn("模拟结果，不是实际面试结果", text)
        self.assertNotIn("暂时保留", text)
        self.assertIn("![能力雷达图](IP-R-20260702-1330-ability-radar.svg)", text)
        self.assertIn("**真正考察点（推断）**", text)
        self.assertIn("**答得好的地方**", text)
        self.assertIn("**没有答够**", text)
        self.assertIn("**下次怎么答**", text)
        self.assertIn("*模拟面试官心声：", text)
        self.assertIn("*模拟面试官总评：", text)
        self.assertIn("整理基线和对照。", text)
        self.assertIn("问题定义达到 4 级", text)
        self.assertNotIn("problem_framing", text)
        self.assertNotIn("case_evidence", text)
        self.assertNotIn("<section", text)

    def test_radar_svg_uses_only_observed_dimensions(self):
        package = sample_package()
        observations = package["assessments"][0]["competency_observations"]
        observations.extend(
            [
                {"observation_id": "obs_002", "dimension": "structured_communication", "level": 3, "evidence_segment_ids": ["seg_002"], "confidence": "high"},
                {"observation_id": "obs_003", "dimension": "data_and_outcome_evidence", "level": 2, "evidence_segment_ids": ["seg_002"], "confidence": "high"},
            ]
        )

        svg = render_radar_svg(package)

        self.assertIn("<svg", svg)
        self.assertIn("问题定义 4.0", svg)
        self.assertIn("表达结构 3.0", svg)
        self.assertIn("数据与结果 2.0", svg)
        self.assertNotIn("协作与推动", svg)

    def test_radar_svg_refuses_misleading_polygon_with_too_few_dimensions(self):
        svg = render_radar_svg(sample_package())

        self.assertIn("至少需要 3 个有证据的能力维度", svg)
        self.assertNotIn("class=\"score-polygon\"", svg)

    def test_first_session_history_documents_are_honest(self):
        package = sample_package()

        ability = render_ability_model_text([package])
        questions = render_frequent_questions_text([package])

        self.assertIn("初始能力快照", ability)
        self.assertIn("仅有 1 场，不计算趋势", ability)
        self.assertIn("首次问题记录", questions)
        self.assertIn("尚不能称为高频", questions)
        self.assertIn("项目解决了什么需求？：1 次（首次出现）", questions)

    def test_artifact_stem_uses_interview_time_and_ledger(self):
        self.assertEqual(
            artifact_stem(sample_package()["session"]), "IP-R-20260702-1330"
        )
        self.assertEqual(
            artifact_stem(sample_package("mock")["session"]),
            "IP-M-20260702-1330",
        )

    def test_single_text_renders_every_assessed_interviewer_question(self):
        package = sample_package()
        for number in range(2, 5):
            chain_id = f"qa_{number:03d}"
            question_id = f"seg_q{number}"
            answer_id = f"seg_a{number}"
            question = f"第{number}个完整问题？"
            answer = f"第{number}个回答。"
            package["source"]["blocks"].extend(
                [
                    {"block_id": f"blk_q{number}", "text": question},
                    {"block_id": f"blk_a{number}", "text": answer},
                ]
            )
            package["segments"].extend(
                [
                    {"segment_id": question_id, "sequence_no": number * 2 - 1, "source_block_id": f"blk_q{number}", "start_char": 0, "end_char": len(question), "speaker_role": "interviewer", "event_type": "question", "text": question},
                    {"segment_id": answer_id, "sequence_no": number * 2, "source_block_id": f"blk_a{number}", "start_char": 0, "end_char": len(answer), "speaker_role": "candidate", "event_type": "answer", "text": answer},
                ]
            )
            package["qa_chains"].append(
                {"qa_chain_id": chain_id, "sequence_no": number, "direction": "interviewer_to_candidate", "turns": [{"turn_type": "question", "segment_ids": [question_id]}, {"turn_type": "answer", "segment_ids": [answer_id]}], "answer_status": "complete"}
            )
            package["question_analyses"].append(
                {"qa_chain_id": chain_id, "surface_question": question, "primary_inferred_focus": {"description": f"考察点{number}。"}}
            )
            package["assessments"].append(
                {"qa_chain_id": chain_id, "effective_elements": [], "missing_elements": [], "risk_signals": [], "root_causes": [], "competency_observations": []}
            )
            package["simulated_reactions"].append(
                {"qa_chain_id": chain_id, "is_simulation": True, "possible_first_reaction": f"反应{number}。", "remaining_concern": f"顾虑{number}。"}
            )

        text = render_single_text(package)

        for number in range(1, 5):
            self.assertIn(f"### 第{number}题：", text)
        self.assertEqual(text.count("**真正考察点（推断）**"), 4)

    def test_overall_interviewer_review_synthesizes_strength_risk_and_follow_up(self):
        package = sample_package()
        package["session_review"]["observed_strengths"] = [
            {"description": "能清楚解释产品边界。", "qa_chain_ids": ["qa_001"]}
        ]
        package["session_review"]["priority_risks"] = [
            {"description": "结果证据没有闭环。", "qa_chain_ids": ["qa_001"], "priority": "high"}
        ]
        package["simulated_reactions"][0]["possible_next_intent"] = "继续追问基线和对照。"

        text = render_single_text(package)

        self.assertIn("让我加分的地方是：能清楚解释产品边界", text)
        self.assertIn("让我犹豫的是：结果证据没有闭环", text)
        self.assertIn("如果进入下一轮，我会继续追问：继续追问基线和对照", text)


class PublicPackageRenderValidationTests(unittest.TestCase):
    def assert_invalid_package_is_rejected(self, renderer):
        package = sample_package()
        package["question_analyses"][0]["qa_chain_id"] = "qa_missing"

        with self.assertRaisesRegex(ValueError, "unknown qa_chain"):
            renderer([package])

    def test_comparison_text_rejects_invalid_package_at_public_boundary(self):
        self.assert_invalid_package_is_rejected(render_comparison_text)

    def test_ability_model_rejects_invalid_package_at_public_boundary(self):
        self.assert_invalid_package_is_rejected(render_ability_model_text)

    def test_frequent_questions_rejects_invalid_package_at_public_boundary(self):
        self.assert_invalid_package_is_rejected(render_frequent_questions_text)


class ComparisonReportTests(unittest.TestCase):
    def test_comparison_text_uses_deterministic_same_ledger_facts(self):
        packages = [
            sample_package("real", "int_one"),
            sample_package("real", "int_two"),
        ]
        packages[1]["assessments"][0]["competency_observations"][0]["level"] = 2

        text = render_comparison_text(packages)

        self.assertIn("# 面试交叉分析", text)
        self.assertIn("真实面试账本", text)
        self.assertIn("项目解决了什么需求？：2 次", text)
        self.assertIn("观察需求定义是否清楚。：2 次", text)
        self.assertIn("缺少采用率。：2 次", text)
        self.assertIn("案例与结果证据不足：2 次", text)
        self.assertIn("首末差值仅描述记录变化，不说明原因", text)
        self.assertNotIn("mixed_average", text)

    def test_one_session_text_reports_insufficient_sample(self):
        text = render_comparison_text([sample_package("real", "int_one")])

        self.assertIn("至少需要两场真实面试才能进行交叉分析。", text)
        self.assertNotIn("上升趋势", text)

    def test_comparison_rejects_mixed_ledgers(self):
        real = sample_package("real", "int_real")
        mock = sample_package("mock", "int_mock")

        with self.assertRaisesRegex(ValueError, "same source_type"):
            build_comparison_view([real, mock])

    def test_one_session_has_no_trend_claim(self):
        view = build_comparison_view([sample_package("real", "int_one")])

        self.assertFalse(view["has_comparison"])
        self.assertEqual(
            view["empty_state"], "至少需要两场真实面试才能进行交叉分析。"
        )

    def test_comparison_counts_questions_and_preserves_missing_cells(self):
        packages = [
            sample_package("real", "int_one"),
            sample_package("real", "int_two"),
            sample_package("real", "int_three"),
        ]
        for package, level in ((packages[0], 4), (packages[2], 2)):
            package["assessments"][0]["competency_observations"].append(
                {
                    "observation_id": f"obs_{package['session']['id']}",
                    "dimension": "collaboration_and_influence",
                    "level": level,
                    "evidence_segment_ids": ["seg_002"],
                    "confidence": "high",
                }
            )

        view = build_comparison_view(packages)

        self.assertEqual(view["frequent_questions"][0]["count"], 3)
        self.assertEqual(view["repeated_root_causes"][0]["category"], "case_evidence")
        self.assertEqual(view["repeated_root_causes"][0]["count"], 3)
        self.assertIsNone(
            view["heatmap"]["collaboration_and_influence"]["int_two"]
        )
        self.assertIsNone(view["mixed_average"])

    def test_loader_uses_selected_non_deleted_ledger_sessions(self):
        visible = sample_package("real", "int_visible")
        deleted = sample_package("real", "int_deleted")
        deleted["source"]["blocks"][0]["text"] = "你如何衡量项目效果？"
        deleted["segments"][0]["text"] = "你如何衡量项目效果？"
        deleted["segments"][0]["end_char"] = len("你如何衡量项目效果？")
        deleted["growth_tasks"][0]["task_id"] = "task_deleted"

        with tempfile.TemporaryDirectory() as data_dir:
            import_session(data_dir, visible)
            import_session(data_dir, deleted)
            soft_delete_session(data_dir, "int_deleted")

            loaded = load_session_packages(data_dir, "real")

        self.assertEqual([item["session"]["id"] for item in loaded], ["int_visible"])
        self.assertEqual(loaded[0]["session"]["source_type"], "real")

class ReportCliTests(unittest.TestCase):
    def test_bundle_cli_writes_timestamped_complete_artifact_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = sample_package()
            package_path = root / "source.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            package_path.write_text(
                json.dumps(package, ensure_ascii=False), encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPORT_SCRIPT),
                    "bundle",
                    "--file",
                    str(package_path),
                    "--data-dir",
                    str(data_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = {
                "IP-R-20260702-1330-analysis.md",
                "IP-R-20260702-1330-qa-original.md",
                "IP-R-20260702-1330-session.json",
                "IP-R-20260702-1330-ability-model.md",
                "IP-R-20260702-1330-frequent-questions.md",
                "IP-R-20260702-1330-ability-radar.svg",
            }
            self.assertEqual({item.name for item in output_dir.iterdir()}, expected)
            analysis = (output_dir / "IP-R-20260702-1330-analysis.md").read_text(encoding="utf-8")
            self.assertIn("![能力雷达图](IP-R-20260702-1330-ability-radar.svg)", analysis)
            self.assertEqual(
                json.loads((output_dir / "IP-R-20260702-1330-session.json").read_text(encoding="utf-8")),
                package,
            )
            self.assertIn(
                "项目解决了什么需求？",
                (output_dir / "IP-R-20260702-1330-qa-original.md").read_text(encoding="utf-8"),
            )

    def test_bundle_rejects_duplicate_source_without_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "library"
            output_dir = root / "outputs"
            existing = sample_package(session_id="existing_session")
            rejected = copy.deepcopy(existing)
            rejected["session"]["id"] = "rejected_session"
            rejected["growth_tasks"][0]["task_id"] = "task_rejected_session"
            package_path = root / "rejected.json"
            package_path.write_text(
                json.dumps(rejected, ensure_ascii=False), encoding="utf-8"
            )
            import_session(data_dir, existing)

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPORT_SCRIPT),
                    "bundle",
                    "--file",
                    str(package_path),
                    "--data-dir",
                    str(data_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("existing_session", result.stderr)
            self.assertFalse(output_dir.exists())

    def test_bundle_staging_failure_preserves_completed_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = sample_package()
            package_path = root / "source.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            output_dir.mkdir()
            completed = output_dir / "completed-report.md"
            completed.write_text("previous complete report\n", encoding="utf-8")
            package_path.write_text(
                json.dumps(package, ensure_ascii=False), encoding="utf-8"
            )
            original_write_text = Path.write_text

            def fail_qa_write(path, content, *args, **kwargs):
                if path.name.endswith("-qa-original.md"):
                    raise OSError("simulated staging failure")
                return original_write_text(path, content, *args, **kwargs)

            with patch.object(Path, "write_text", new=fail_qa_write):
                with self.assertRaisesRegex(OSError, "simulated staging failure"):
                    write_artifact_bundle(
                        str(package_path), str(data_dir), str(output_dir)
                    )

            self.assertEqual(
                {item.name for item in output_dir.iterdir()},
                {"completed-report.md"},
            )
            self.assertEqual(
                completed.read_text(encoding="utf-8"), "previous complete report\n"
            )

    def test_bundle_publication_failure_restores_complete_previous_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = sample_package(session_id="rollback_session")
            revised = copy.deepcopy(package)
            revised["growth_tasks"][0]["title"] = "修订后的成长任务"
            package_path = root / "source.json"
            revised_path = root / "revised.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            package_path.write_text(
                json.dumps(package, ensure_ascii=False), encoding="utf-8"
            )
            revised_path.write_text(
                json.dumps(revised, ensure_ascii=False), encoding="utf-8"
            )
            write_artifact_bundle(str(package_path), str(data_dir), str(output_dir))
            previous_contents = {
                path.name: path.read_bytes() for path in output_dir.iterdir()
            }
            original_replace = Path.replace

            def fail_final_publication(path, target):
                if (
                    path.parent.name.startswith(".interview-report-")
                    and path.parent.name != "previous"
                    and path.name.endswith("-ability-model.md")
                ):
                    raise OSError("simulated final publication failure")
                return original_replace(path, target)

            with patch.object(Path, "replace", new=fail_final_publication):
                with self.assertRaisesRegex(
                    OSError, "simulated final publication failure"
                ):
                    write_artifact_bundle(
                        str(revised_path), str(data_dir), str(output_dir)
                    )

            self.assertEqual(
                {path.name: path.read_bytes() for path in output_dir.iterdir()},
                previous_contents,
            )

    def test_bundle_rollback_failure_retains_recovery_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = sample_package(session_id="recovery_session")
            revised = copy.deepcopy(package)
            revised["growth_tasks"][0]["title"] = "修订后的成长任务"
            package_path = root / "source.json"
            revised_path = root / "revised.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            package_path.write_text(
                json.dumps(package, ensure_ascii=False), encoding="utf-8"
            )
            revised_path.write_text(
                json.dumps(revised, ensure_ascii=False), encoding="utf-8"
            )
            write_artifact_bundle(str(package_path), str(data_dir), str(output_dir))
            previous_contents = {
                path.name: path.read_bytes() for path in output_dir.iterdir()
            }
            original_replace = Path.replace
            retained_name = "IP-R-20260702-1330-qa-original.md"

            def fail_publication_and_restore(path, target):
                if (
                    path.parent.name.startswith(".interview-report-")
                    and path.parent.name != "previous"
                    and path.name.endswith("-ability-model.md")
                ):
                    raise OSError("simulated final publication failure")
                if path.parent.name == "previous" and path.name == retained_name:
                    raise OSError("simulated rollback restoration failure")
                return original_replace(path, target)

            with patch.object(Path, "replace", new=fail_publication_and_restore):
                with self.assertRaisesRegex(
                    OSError, "recovery directory:"
                ) as caught:
                    write_artifact_bundle(
                        str(revised_path), str(data_dir), str(output_dir)
                    )

            recovery_path = Path(
                str(caught.exception)
                .split("recovery directory: ", 1)[1]
                .split("; errors:", 1)[0]
            )
            self.assertTrue(recovery_path.is_dir())
            self.assertEqual(
                (recovery_path / "previous" / retained_name).read_bytes(),
                previous_contents[retained_name],
            )
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in output_dir.iterdir()
                    if path.name not in {recovery_path.name, retained_name}
                },
                {
                    name: content
                    for name, content in previous_contents.items()
                    if name != retained_name
                },
            )

    def test_bundle_revision_uses_persisted_current_cumulative_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = sample_package(session_id="revised_session")
            revised = copy.deepcopy(original)
            revised["assessments"][0]["competency_observations"][0]["level"] = 5
            revised["question_analyses"][0]["surface_question"] = "修订后的问题"
            revised["knowledge_candidates"][0]["canonical_question"] = "修订后的问题"
            other = sample_package(session_id="other_session")
            other_question = "另一个同账本问题"
            other["source"]["blocks"][0]["text"] = other_question
            other["segments"][0]["text"] = other_question
            other["segments"][0]["end_char"] = len(other_question)
            other["question_analyses"][0]["surface_question"] = other_question
            other["knowledge_candidates"][0]["canonical_question"] = other_question
            other["growth_tasks"][0]["task_id"] = "task_other_session"
            other["assessments"][0]["competency_observations"][0]["level"] = 2
            revised_path = root / "revised.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            revised_path.write_text(
                json.dumps(revised, ensure_ascii=False), encoding="utf-8"
            )
            import_session(data_dir, original)
            import_session(data_dir, other)
            write_artifact_bundle(str(revised_path), str(data_dir), str(output_dir))

            self.assertEqual(
                json.loads(
                    (output_dir / "IP-R-20260702-1330-session.json").read_text(
                        encoding="utf-8"
                    )
                ),
                revised,
            )
            self.assertIn(
                "| 问题定义 | 3.50 / 5 | 2 | 2 |",
                (output_dir / "IP-R-20260702-1330-ability-model.md").read_text(
                    encoding="utf-8"
                ),
            )
            frequent_questions = (
                output_dir / "IP-R-20260702-1330-frequent-questions.md"
            ).read_text(encoding="utf-8")
            self.assertIn("修订后的问题", frequent_questions)
            self.assertNotIn("项目解决了什么需求？", frequent_questions)
            self.assertIn(other_question, frequent_questions)

    def test_bundle_same_minute_collision_and_rerun_keep_compatible_stems(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = sample_package(session_id="same_minute_first")
            second = copy.deepcopy(first)
            second_question = "同一分钟的另一道问题"
            second["session"]["id"] = "same_minute_second"
            second["growth_tasks"][0]["task_id"] = "task_same_minute_second"
            second["source"]["blocks"][0]["text"] = second_question
            second["segments"][0]["text"] = second_question
            second["segments"][0]["end_char"] = len(second_question)
            second["question_analyses"][0]["surface_question"] = second_question
            second["knowledge_candidates"][0]["canonical_question"] = second_question
            first_path = root / "first.json"
            second_path = root / "second.json"
            data_dir = root / "library"
            output_dir = root / "outputs"
            first_path.write_text(
                json.dumps(first, ensure_ascii=False), encoding="utf-8"
            )
            second_path.write_text(
                json.dumps(second, ensure_ascii=False), encoding="utf-8"
            )

            write_artifact_bundle(str(first_path), str(data_dir), str(output_dir))
            write_artifact_bundle(str(second_path), str(data_dir), str(output_dir))
            write_artifact_bundle(str(first_path), str(data_dir), str(output_dir))

            names = {path.name for path in output_dir.iterdir()}
            self.assertIn("IP-R-20260702-1330-analysis.md", names)
            self.assertIn("IP-R-20260702-1330-01-analysis.md", names)
            self.assertFalse(any("-02-" in name for name in names))

    def test_single_cli_defaults_to_markdown_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "session.json"
            output_path = Path(temp_dir) / "report.md"
            package_path.write_text(
                json.dumps(sample_package(), ensure_ascii=False), encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPORT_SCRIPT),
                    "single",
                    "--file",
                    str(package_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )

            text = output_path.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(text.startswith("# 面试复盘"))
            self.assertNotIn("<!doctype html>", text)

    def test_single_cli_rejects_removed_html_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_path = Path(temp_dir) / "session.json"
            output_path = Path(temp_dir) / "report.html"
            package_path.write_text(
                json.dumps(sample_package(), ensure_ascii=False), encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPORT_SCRIPT),
                    "single",
                    "--file",
                    str(package_path),
                    "--output",
                    str(output_path),
                    "--format",
                    "html",
                ],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output_path.exists())

    def test_compare_cli_filters_deleted_and_source_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "library"
            output_path = Path(temp_dir) / "comparison.md"
            deleted_package = sample_package("real", "deleted_real_session")
            deleted_package["source"]["blocks"][0]["text"] = "你如何衡量项目效果？"
            deleted_package["segments"][0]["text"] = "你如何衡量项目效果？"
            deleted_package["segments"][0]["end_char"] = len("你如何衡量项目效果？")
            for package in (
                sample_package("real", "real_session"),
                deleted_package,
                sample_package("mock", "mock_session"),
            ):
                package["growth_tasks"][0]["task_id"] = (
                    f'task_{package["session"]["id"]}'
                )
                package_path = Path(temp_dir) / f'{package["session"]["id"]}.json'
                package_path.write_text(
                    json.dumps(package, ensure_ascii=False), encoding="utf-8"
                )
                imported = subprocess.run(
                    [
                        sys.executable,
                        str(STORE_SCRIPT),
                        "import",
                        "--data-dir",
                        str(data_dir),
                        "--file",
                        str(package_path),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(imported.returncode, 0, imported.stderr)

            deleted = subprocess.run(
                [
                    sys.executable,
                    str(STORE_SCRIPT),
                    "delete",
                    "--data-dir",
                    str(data_dir),
                    "--session-id",
                    "deleted_real_session",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(deleted.returncode, 0, deleted.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPORT_SCRIPT),
                    "compare",
                    "--data-dir",
                    str(data_dir),
                    "--source-type",
                    "real",
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            text = output_path.read_text(encoding="utf-8")
            self.assertIn("real_session", text)
            self.assertNotIn("deleted_real_session", text)
            self.assertNotIn("mock_session", text)
