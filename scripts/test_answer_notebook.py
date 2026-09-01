import tempfile
import unittest
from pathlib import Path

from answer_notebook import (
    AnswerNotebook,
    ConflictError,
    NotebookError,
    extract_entry_block,
)


def sample_payload(question: str, answer: str, ability: str = "产品设计") -> dict:
    return {
        "canonical_question": question,
        "target_role": "产品经理",
        "ability_type": ability,
        "scenario_tags": ["业务面"],
        "original_problem_summary": "原回答缺少结构和结果证据。",
        "best_answer": answer,
        "answer_source": "co_created",
        "source_session_ids": ["IP-R-TEST"],
        "validation_status": "unvalidated",
    }


class AnswerNotebookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.notebook = AnswerNotebook(self.data_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add(self, question: str, answer: str, ability: str = "产品设计"):
        proposal = self.notebook.propose_add(sample_payload(question, answer, ability))
        return self.notebook.confirm(proposal.operation_id)

    def test_proposal_does_not_create_entry_or_markdown(self) -> None:
        proposal = self.notebook.propose_add(sample_payload("如何定义问题？", "先明确目标。"))
        self.assertTrue(proposal.preview["requires_confirmation"])
        self.assertEqual([], self.notebook.search())
        self.assertFalse(self.notebook.markdown_path.exists())

    def test_confirm_add_creates_structured_entry_and_markdown(self) -> None:
        result = self.add("如何定义问题？", "先明确目标，再验证用户痛点。")
        self.assertEqual("NB-0001", result.entry_id)
        self.assertEqual(1, result.version)
        self.assertIn("NB-0001 如何定义问题？", self.notebook.markdown_path.read_text())

    def test_update_changes_only_target_entry(self) -> None:
        self.add("问题一？", "回答一。")
        self.add("问题二？", "回答二。", "项目复盘")
        before = self.notebook.markdown_path.read_text()
        untouched = extract_entry_block(before, "NB-0002")
        proposal = self.notebook.propose_update("NB-0001", {"best_answer": "新版回答一。"})
        result = self.notebook.confirm(proposal.operation_id)
        after = self.notebook.markdown_path.read_text()
        self.assertEqual(2, result.version)
        self.assertEqual(untouched, extract_entry_block(after, "NB-0002"))
        self.assertIn("新版回答一。", extract_entry_block(after, "NB-0001"))

    def test_delete_and_restore_both_require_confirmation(self) -> None:
        self.add("如何复盘？", "从目标、过程和结果展开。")
        delete = self.notebook.propose_delete("NB-0001")
        self.assertEqual(1, len(self.notebook.search()))
        self.notebook.confirm(delete.operation_id)
        self.assertEqual([], self.notebook.search())
        self.assertNotIn("NB-0001", self.notebook.markdown_path.read_text())
        restore = self.notebook.propose_restore("NB-0001")
        self.assertEqual([], self.notebook.search())
        self.notebook.confirm(restore.operation_id)
        self.assertEqual(1, len(self.notebook.search()))

    def test_stale_confirmation_is_rejected(self) -> None:
        self.add("如何判断优先级？", "比较收益与成本。")
        first = self.notebook.propose_update("NB-0001", {"best_answer": "版本二。"})
        stale = self.notebook.propose_update("NB-0001", {"best_answer": "过期版本。"})
        self.notebook.confirm(first.operation_id)
        with self.assertRaises(ConflictError):
            self.notebook.confirm(stale.operation_id)

    def test_confirmation_cannot_be_reused(self) -> None:
        proposal = self.notebook.propose_add(sample_payload("如何做取舍？", "明确约束。"))
        self.notebook.confirm(proposal.operation_id)
        with self.assertRaises(NotebookError):
            self.notebook.confirm(proposal.operation_id)

    def test_search_filters_without_writing(self) -> None:
        self.add("如何设计推荐反馈？", "建立正负反馈闭环。", "产品设计")
        self.add("如何复盘失败项目？", "区分判断与执行问题。", "项目复盘")
        before = self.notebook.markdown_path.read_bytes()
        results = self.notebook.search(keyword="推荐", ability_type="产品设计")
        self.assertEqual(["NB-0001"], [entry.entry_id for entry in results])
        self.assertEqual(before, self.notebook.markdown_path.read_bytes())

    def test_duplicate_is_warning_not_automatic_merge(self) -> None:
        self.add("如何定义核心问题？", "先确定目标用户。")
        proposal = self.notebook.propose_add(
            sample_payload("如何定义核心问题？", "先识别关键矛盾。")
        )
        self.assertEqual(
            ["NB-0001"], proposal.preview["possible_duplicate_entry_ids"]
        )
        self.assertEqual(1, len(self.notebook.search()))


if __name__ == "__main__":
    unittest.main()
