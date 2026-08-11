import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import sys
from xml.sax.saxutils import escape

from interview_store import import_session, render_original_qa, validate_session_package


DIMENSION_LABELS = {
    "problem_framing": "问题定义",
    "reasoning_and_tradeoffs": "产品推理",
    "execution_and_ownership": "执行与主导性",
    "data_and_outcome_evidence": "数据与结果",
    "structured_communication": "表达结构",
    "collaboration_and_influence": "协作与推动",
    "domain_or_technical_depth": "领域与技术理解",
    "reflection_and_transfer": "复盘与迁移",
}

ROOT_CAUSE_GUIDANCE = {
    "case_evidence": "补充一手事实、基线、对照或结果，再给结论。",
    "communication": "先说结论，再用两到三点支持。",
    "ownership": "明确说出你亲自做的决定、动作和结果。",
    "reasoning": "补齐目标用户、替代方案、取舍和反证条件。",
}

ROOT_CAUSE_LABELS = {
    "case_evidence": "案例与结果证据不足",
    "communication": "表达结构不够清楚",
    "ownership": "个人主导性不够明确",
    "reasoning": "推理与取舍不够完整",
}

RESULT_LABELS = {
    "strong_yes": "从现有材料看，正向信号明显",
    "lean_yes": "从现有材料看，正向信号较多",
    "hold": "现有证据不足，无法判断是否通过",
    "lean_no": "从现有材料看，风险信号较多",
    "strong_no": "从现有材料看，风险信号明显",
}

TASK_STATUS_LABELS = {
    "open": "待开始",
    "in_progress": "进行中",
    "waiting_real_validation": "等待真实面试验证",
    "real_validated": "已通过真实面试验证",
    "closed": "已结束",
}

def _text(value):
    return escape(str(value or ""))


def _plain_dimension_codes(value):
    text = str(value or "").replace(" 与 ", "、")
    for dimension, label in DIMENSION_LABELS.items():
        text = text.replace(dimension, label)
        text = text.replace(f" {label}", label).replace(f"{label} ", label)
    return text


def _evidence_ids(assessment):
    seen = set()
    evidence_ids = []
    for key in (
        "effective_elements",
        "missing_elements",
        "risk_signals",
        "root_causes",
        "competency_observations",
    ):
        for item in assessment.get(key, []):
            for segment_id in item.get("evidence_segment_ids", []):
                if segment_id not in seen:
                    seen.add(segment_id)
                    evidence_ids.append(segment_id)
    return "、".join(_text(segment_id) for segment_id in evidence_ids) or "无"


def _question_text(chain, segments):
    question_ids = [
        segment_id
        for turn in chain.get("turns", [])
        if "question" in turn.get("turn_type", "")
        for segment_id in turn.get("segment_ids", [])
    ]
    return " ".join(segments[item]["text"] for item in question_ids if item in segments)


def _improvement_sentence(assessment):
    missing = assessment.get("missing_elements", [])
    causes = assessment.get("root_causes", [])
    guidance = ROOT_CAUSE_GUIDANCE.get(causes[0].get("category"), "") if causes else ""
    element = str(missing[0].get("element", "")).rstrip("。；; ") if missing else ""
    if missing and guidance:
        return f"围绕「{element}」{guidance}"
    if missing:
        return f"补充「{element}」的具体证据，再给结论。"
    return guidance or "保留这次结构，再补充一个可验证的结果。"


def _session_ability_averages(package: dict) -> dict:
    levels = {dimension: [] for dimension in DIMENSION_LABELS}
    for assessment in package.get("assessments", []):
        for observation in assessment.get("competency_observations", []):
            dimension = observation.get("dimension")
            level = observation.get("level")
            if dimension in levels and level is not None:
                levels[dimension].append(level)
    return {
        dimension: round(sum(values) / len(values), 2) if values else None
        for dimension, values in levels.items()
    }


def _comparison_session_fact(package: dict) -> dict:
    session = package.get("session", {})
    ability_averages = _session_ability_averages(package)
    segments = {
        item.get("segment_id"): item for item in package.get("segments", [])
    }
    analyses = {
        item.get("qa_chain_id"): item
        for item in package.get("question_analyses", [])
    }
    chains = {
        item.get("qa_chain_id"): item for item in package.get("qa_chains", [])
    }
    evidence_by_dimension = {dimension: [] for dimension in DIMENSION_LABELS}
    for assessment in package.get("assessments", []):
        chain_id = assessment.get("qa_chain_id")
        question = (
            _question_text(chains.get(chain_id, {}), segments)
            or analyses.get(chain_id, {}).get("surface_question")
            or "问题未命名"
        )
        for observation in assessment.get("competency_observations", []):
            dimension = observation.get("dimension")
            if dimension not in evidence_by_dimension:
                continue
            evidence_by_dimension[dimension].append(
                {
                    "question": question,
                    "level": observation.get("level"),
                    "evidence": [
                        {
                            "segment_id": segment_id,
                            "text": segments.get(segment_id, {}).get("text", ""),
                        }
                        for segment_id in observation.get("evidence_segment_ids", [])
                    ],
                }
            )
    return {
        "session_id": session.get("id"),
        "occurred_at": session.get("occurred_at"),
        "role": session.get("role"),
        "ability_averages": ability_averages,
        "question_candidates": [
            item["canonical_question"]
            for item in package.get("knowledge_candidates", [])
            if isinstance(item.get("canonical_question"), str)
            and item["canonical_question"]
        ] or [
            item["surface_question"]
            for item in package.get("question_analyses", [])
            if isinstance(item.get("surface_question"), str)
            and item["surface_question"]
        ],
        "evidence_by_dimension": evidence_by_dimension,
        "risk_dimensions": [
            dimension
            for dimension, average in ability_averages.items()
            if average is not None and average <= 2
        ],
    }


def load_session_packages(
    data_dir: str, source_type: str, session_ids: list[str] | None = None
) -> list[dict]:
    if source_type not in {"real", "mock"}:
        raise ValueError("source_type must be real or mock")

    clauses = ["source_type = ?", "deleted_at IS NULL"]
    parameters = [source_type]
    if session_ids is not None:
        if not session_ids:
            return []
        clauses.append("session_id IN ({})".format(", ".join("?" for _ in session_ids)))
        parameters.extend(session_ids)
    query = """
        SELECT raw_json
        FROM sessions
        WHERE {}
        ORDER BY occurred_at, session_id
    """.format(" AND ".join(clauses))
    with sqlite3.connect(Path(data_dir) / "library.db") as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [json.loads(raw_json) for (raw_json,) in rows]


def build_comparison_view(packages: list[dict]) -> dict:
    source_types = {
        package.get("session", {}).get("source_type") for package in packages
    }
    if len(source_types) > 1:
        raise ValueError("comparison packages must use the same source_type")
    if not packages or source_types - {"real", "mock"}:
        raise ValueError("comparison packages require source_type real or mock")

    ordered_packages = sorted(
        packages,
        key=lambda package: (
            package.get("session", {}).get("occurred_at") is None,
            package.get("session", {}).get("occurred_at") or "",
            package.get("session", {}).get("id") or "",
        ),
    )
    facts = [
        _comparison_session_fact(package) for package in ordered_packages
    ]
    source_type = source_types.pop()
    has_comparison = len(facts) >= 2
    empty_state = None
    if not has_comparison:
        ledger = "真实面试" if source_type == "real" else "模拟面试"
        empty_state = f"至少需要两场{ledger}才能进行交叉分析。"

    ability_differences = {}
    for dimension in DIMENSION_LABELS:
        first = facts[0]["ability_averages"][dimension]
        last = facts[-1]["ability_averages"][dimension]
        ability_differences[dimension] = {
            "first": first,
            "last": last,
            "difference": round(last - first, 2)
            if has_comparison and first is not None and last is not None
            else None,
        }

    question_counts = Counter(
        question for fact in facts for question in fact["question_candidates"]
    )
    root_cause_counts = Counter(
        cause["category"]
        for package in ordered_packages
        for assessment in package.get("assessments", [])
        for cause in assessment.get("root_causes", [])
        if isinstance(cause.get("category"), str) and cause["category"]
    )
    inferred_focus_counts = Counter()
    evidence_gap_counts = Counter()
    growth_task_states = []
    for package in ordered_packages:
        session_id = package.get("session", {}).get("id")
        for analysis in package.get("question_analyses", []):
            focus = analysis.get("primary_inferred_focus") or {}
            if isinstance(focus.get("description"), str) and focus["description"]:
                inferred_focus_counts[focus["description"]] += 1
            for secondary in analysis.get("secondary_inferred_focuses", []):
                if isinstance(secondary.get("description"), str) and secondary["description"]:
                    inferred_focus_counts[secondary["description"]] += 1
        for assessment in package.get("assessments", []):
            for missing in assessment.get("missing_elements", []):
                if isinstance(missing.get("element"), str) and missing["element"]:
                    evidence_gap_counts[missing["element"]] += 1
        for task in package.get("growth_tasks", []):
            growth_task_states.append(
                {
                    "session_id": session_id,
                    "title": task.get("title") or "未命名任务",
                    "status": task.get("status") or "open",
                    "real_validation_condition": task.get("real_validation_condition") or "未提供真实验证条件",
                }
            )
    return {
        "source_type": source_type,
        "sessions": facts,
        "has_comparison": has_comparison,
        "empty_state": empty_state,
        "ability_differences": ability_differences,
        "heatmap": {
            dimension: {
                fact["session_id"]: fact["ability_averages"][dimension]
                for fact in facts
            }
            for dimension in DIMENSION_LABELS
        },
        "heatmap_evidence": {
            dimension: {
                fact["session_id"]: fact["evidence_by_dimension"][dimension]
                for fact in facts
            }
            for dimension in DIMENSION_LABELS
        },
        "frequent_questions": [
            {"question": question, "count": count}
            for question, count in sorted(
                question_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "repeated_root_causes": [
            {
                "category": category,
                "label": ROOT_CAUSE_LABELS.get(category, "其他问题原因"),
                "count": count,
            }
            for category, count in sorted(
                root_cause_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "frequent_inferred_focuses": [
            {"focus": focus, "count": count}
            for focus, count in sorted(
                inferred_focus_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "repeated_evidence_gaps": [
            {"gap": gap, "count": count}
            for gap, count in sorted(
                evidence_gap_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "growth_task_states": growth_task_states,
        "mixed_average": None,
    }


def build_single_view(package: dict) -> dict:
    levels = {}
    for assessment in package.get("assessments", []):
        for observation in assessment.get("competency_observations", []):
            if observation.get("level") is not None:
                levels.setdefault(observation["dimension"], []).append(
                    observation["level"]
                )
    ability_rows = [
        {
            "dimension": dimension,
            "label": DIMENSION_LABELS[dimension],
            "average": round(sum(levels[dimension]) / len(levels[dimension]), 2),
            "evidence_count": len(levels[dimension]),
        }
        for dimension in DIMENSION_LABELS
        if levels.get(dimension)
    ]
    return {
        "session": package["session"],
        "review": package.get("session_review", {}),
        "ability_rows": ability_rows,
    }


def artifact_stem(session: dict) -> str:
    occurred_at = session.get("occurred_at")
    if not isinstance(occurred_at, str) or not occurred_at.strip():
        raise ValueError("interview occurred_at is required for artifact naming")
    try:
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("interview occurred_at must be ISO-8601") from error
    ledger = {"real": "R", "mock": "M"}.get(session.get("source_type"))
    if ledger is None:
        raise ValueError("source_type must be real or mock")
    return f"IP-{ledger}-{occurred:%Y%m%d-%H%M}"


def _point(center_x, center_y, radius, angle):
    return (
        center_x + radius * math.cos(angle),
        center_y + radius * math.sin(angle),
    )


def _svg_points(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def render_radar_svg(package: dict) -> str:
    rows = build_single_view(package)["ability_rows"]
    width, height = 760, 560
    if len(rows) < 3:
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="220" viewBox="0 0 {width} 220" role="img" aria-label="能力雷达图证据不足">
<rect width="100%" height="100%" rx="20" fill="#f5f5f7"/>
<text x="380" y="96" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif" font-size="22" font-weight="600" fill="#1d1d1f">能力雷达图</text>
<text x="380" y="136" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif" font-size="16" fill="#6e6e73">至少需要 3 个有证据的能力维度；缺失维度不会按 0 分处理。</text>
</svg>'''

    center_x, center_y, radius = 380, 270, 160
    angles = [(-math.pi / 2) + index * 2 * math.pi / len(rows) for index in range(len(rows))]
    rings = []
    for level in range(1, 6):
        ring_points = [
            _point(center_x, center_y, radius * level / 5, angle)
            for angle in angles
        ]
        rings.append(f'<polygon points="{_svg_points(ring_points)}"/>')
    axes = []
    labels = []
    for row, angle in zip(rows, angles):
        axis_x, axis_y = _point(center_x, center_y, radius, angle)
        label_x, label_y = _point(center_x, center_y, radius + 66, angle)
        anchor = "middle"
        if label_x < center_x - 20:
            anchor = "end"
        elif label_x > center_x + 20:
            anchor = "start"
        axes.append(f'<line x1="{center_x}" y1="{center_y}" x2="{axis_x:.1f}" y2="{axis_y:.1f}"/>')
        score_label = f'{row["average"]:.1f}'
        labels.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}">'
            f'{_text(row["label"])} {_text(score_label)}</text>'
        )
    score_points = [
        _point(center_x, center_y, radius * row["average"] / 5, angle)
        for row, angle in zip(rows, angles)
    ]
    score_dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in score_points
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="本次面试能力雷达图">
<style>
text {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; font-size: 15px; font-weight: 600; fill: #3a3a3c; }}
.grid polygon, .axes line {{ fill: none; stroke: #c7c7cc; stroke-width: 1; }}
.score-polygon {{ fill: #5e5ce6; fill-opacity: .22; stroke: #5e5ce6; stroke-width: 3; }}
.score-dots circle {{ fill: #5e5ce6; }}
@media (prefers-color-scheme: dark) {{ text {{ fill: #f2f2f7; }} .grid polygon, .axes line {{ stroke: #636366; }} .score-polygon {{ fill: #8e8cff; stroke: #a09eff; }} .score-dots circle {{ fill: #a09eff; }} }}
</style>
<g class="grid">{''.join(rings)}</g>
<g class="axes">{''.join(axes)}</g>
<polygon class="score-polygon" points="{_svg_points(score_points)}"/>
<g class="score-dots">{score_dots}</g>
<g class="labels">{''.join(labels)}</g>
<text x="380" y="538" text-anchor="middle" font-size="13" font-weight="400">仅展示有证据的维度 · 满分 5 分</text>
</svg>'''


def _overall_interviewer_thought(review: dict, reactions: dict) -> list[str]:
    summaries = [
        str(item).strip()
        for item in review.get("three_sentence_summary", [])
        if str(item).strip()
    ]
    result = review.get("simulated_overall_result", {})
    tendency = RESULT_LABELS.get(result.get("tendency"), result.get("tendency"))
    parts = []
    if summaries:
        parts.append(f'整体印象：{summaries[0].rstrip("。；; ")}')

    strengths = [
        str(item.get("description", "")).rstrip("。；; ")
        for item in review.get("observed_strengths", [])
        if item.get("description")
    ]
    if not strengths and len(summaries) > 1:
        strengths = [summaries[1].rstrip("。；; ")]
    if strengths:
        parts.append(f"让我加分的地方是：{'；'.join(strengths)}")

    risks = [
        item
        for item in review.get("priority_risks", [])
        if item.get("description")
    ]
    risk_descriptions = [
        str(item["description"]).rstrip("。；; ") for item in risks
    ]
    if not risk_descriptions and len(summaries) > 2:
        risk_descriptions = [summaries[2].rstrip("。；; ")]
    if risk_descriptions:
        parts.append(f"让我犹豫的是：{'；'.join(risk_descriptions)}")

    next_intents = []
    for risk in risks:
        next_intent = None
        for chain_id in risk.get("qa_chain_ids", []):
            candidate = reactions.get(chain_id, {}).get("possible_next_intent")
            if candidate and "结束" not in str(candidate):
                next_intent = str(candidate).rstrip("。；; ")
                break
        if next_intent is None:
            next_intent = f'继续核验「{str(risk["description"]).rstrip("。；; ")}」'
        if next_intent not in next_intents:
            next_intents.append(next_intent)
    if next_intents:
        parts.append(f"如果进入下一轮，我会继续追问：{'；'.join(next_intents)}")

    if tendency:
        parts.append(f"综合判断：{tendency}")
    if not parts:
        return ["现有信息不足，暂时无法形成整体模拟评价"]
    return parts


def render_single_text(package: dict, radar_image: str | None = None) -> str:
    view = build_single_view(package)
    review = view["review"]
    chains = sorted(
        package.get("qa_chains", []), key=lambda item: item.get("sequence_no", 0)
    )
    chain_by_id = {item.get("qa_chain_id"): item for item in chains}
    segments = {item.get("segment_id"): item for item in package.get("segments", [])}
    analyses = {
        item.get("qa_chain_id"): item
        for item in package.get("question_analyses", [])
    }
    assessments = {
        item.get("qa_chain_id"): item for item in package.get("assessments", [])
    }
    reactions = {
        item.get("qa_chain_id"): item
        for item in package.get("simulated_reactions", [])
    }

    lines = ["# 面试复盘", "", "## 30 秒结论", ""]
    lines.extend(
        f"- {sentence}" for sentence in review.get("three_sentence_summary", [])
    )
    result = review.get("simulated_overall_result", {})
    tendency = result.get("tendency", "未提供")
    for label, chain_id in (
        ("最佳回答", review.get("best_answer_qa_chain_id")),
        ("最高风险", review.get("highest_risk_qa_chain_id")),
    ):
        question = (
            analyses.get(chain_id, {}).get("surface_question")
            or _question_text(chain_by_id.get(chain_id, {}), segments)
            or "未指定"
        )
        lines.append(f"- {label}：{question}")
    overall_thoughts = _overall_interviewer_thought(review, reactions)
    lines.extend(["", "## 模拟面试官总评", ""])
    for index, thought in enumerate(overall_thoughts):
        prefix = "模拟面试官总评：" if index == 0 else ""
        lines.extend([f"*{prefix}{thought}。*", ""])
    lines.append(
        "> 这是基于逐字稿的模拟解读，不代表真实面试官意见或实际面试结果。"
    )

    lines.extend(["", "## 能力快照", ""])
    if radar_image:
        lines.extend([f"![能力雷达图]({radar_image})", ""])
    if view["ability_rows"]:
        lines.extend(["| 能力 | 分数 | 证据 |", "|---|---:|---:|"])
        lines.extend(
            f'| {row["label"]} | {row["average"]:.1f} / 5 | {row["evidence_count"]} 条 |'
            for row in view["ability_rows"]
        )
    else:
        lines.append("- 暂无可汇总的能力证据。")

    lines.extend(["", "## 关键问题", ""])
    all_assessed_chains = [
        chain
        for chain in chains
        if chain.get("qa_chain_id") in assessments
        and chain.get("direction") == "interviewer_to_candidate"
    ]
    assessed_chains = all_assessed_chains
    for chain in assessed_chains:
        chain_id = chain.get("qa_chain_id")
        assessment = assessments[chain_id]
        focus = analyses.get(chain_id, {}).get("primary_inferred_focus") or {}
        reaction = reactions.get(chain_id, {})
        lines.extend(
            [
                f'### 第{chain.get("sequence_no")}题：{analyses.get(chain_id, {}).get("surface_question") or _question_text(chain, segments)}',
                "",
                f'> **真正考察点（推断）**：{focus.get("description") or "暂无推断。"}',
                "",
            ]
        )
        effective = assessment.get("effective_elements", [])
        missing = assessment.get("missing_elements", [])
        effective_text = "；".join(
            str(item.get("observation", "")).rstrip("。；; ") for item in effective
        )
        missing_text = "；".join(
            str(item.get("element", "")).rstrip("。；; ") for item in missing
        )
        lines.extend(
            [
                "**答得好的地方**",
                "",
                f"{effective_text}。" if effective_text else "暂无明确加分点。",
                "",
                "**没有答够**",
                "",
                f"{missing_text}。" if missing_text else "暂无明确缺口。",
                "",
                "**下次怎么答**",
                "",
                _improvement_sentence(assessment),
                "",
            ]
        )
        if reaction:
            first_reaction = str(
                reaction.get("possible_first_reaction") or ""
            ).rstrip("。；; ")
            lines.append(
                "*模拟面试官心声："
                f"{first_reaction}；仍有顾虑：{reaction.get('remaining_concern') or ''}*"
            )
        lines.append("")

    lines.extend(["## 成长任务", ""])
    tasks = package.get("growth_tasks", [])[:3]
    if not tasks:
        lines.append("暂无成长任务。")
    for index, task in enumerate(tasks, start=1):
        lines.extend([f'### {index}. {task.get("title") or "未命名任务"}', ""])
        lines.append("动作：")
        lines.extend(f'- {step}' for step in task.get("steps", []))
        lines.append("")
        lines.append("完成标准：")
        lines.extend(
            f'- {criterion}' for criterion in task.get("acceptance_criteria", [])
        )
        lines.extend(
            [
                "",
                f'真实面试验证条件：{_plain_dimension_codes(task.get("real_validation_condition") or "未提供。")}',
                "",
            ]
        )

    lines.extend(["## 证据说明", ""])
    notices = []
    for warning in package.get("warnings", []):
        notices.append(
            warning.get("message", "未提供警告说明。")
            if isinstance(warning, dict)
            else str(warning)
        )
    notices.extend(
        item.get("gap", "") for item in review.get("information_gaps", [])
    )
    lines.extend(f"- {item}" for item in dict.fromkeys(item for item in notices if item))
    for chain in assessed_chains:
        assessment = assessments[chain["qa_chain_id"]]
        lines.append(
            f'- 第{chain.get("sequence_no")}题证据片段：{_evidence_ids(assessment)}'
        )
    lines.append("- 完整原文问答见独立的原文整理文件；需要时可在对话中展开。")
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_text(packages: list[dict]) -> str:
    view = build_comparison_view(packages)
    ledger = "真实面试账本" if view["source_type"] == "real" else "模拟训练账本"
    lines = ["# 面试交叉分析", "", "## 数据范围", "", f"- {ledger}"]
    lines.extend(
        f'- {session["occurred_at"] or "时间未提供"} · {session["role"] or "岗位未提供"}（{session["session_id"]}）'
        for session in view["sessions"]
    )
    if not view["has_comparison"]:
        lines.extend(["", view["empty_state"], "", "没有计算趋势或差值。"])
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            "",
            "## 能力变化",
            "",
            "首末差值仅描述记录变化，不说明原因。",
            "",
        ]
    )
    for dimension, label in DIMENSION_LABELS.items():
        item = view["ability_differences"][dimension]
        first = f'{item["first"]:.1f}' if item["first"] is not None else "无证据"
        last = f'{item["last"]:.1f}' if item["last"] is not None else "无证据"
        difference = (
            f'{item["difference"]:+.1f}'
            if item["difference"] is not None
            else "无法计算"
        )
        lines.append(f"- {label}：{first} → {last}；差值 {difference}")

    sections = (
        ("高频问题", view["frequent_questions"], "question"),
        ("高频真正考察点（推断）", view["frequent_inferred_focuses"], "focus"),
        ("重复证据缺口", view["repeated_evidence_gaps"], "gap"),
        ("重复问题原因", view["repeated_root_causes"], "label"),
    )
    for title, items, key in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(f'- {item[key]}：{item["count"]} 次' for item in items)
        if not items:
            lines.append("- 暂无可汇总信息。")

    lines.extend(["", "## 成长任务状态", ""])
    for task in view["growth_task_states"]:
        status = TASK_STATUS_LABELS.get(task["status"], task["status"])
        lines.append(f'- {task["title"]}：{status}；真实验证：{task["real_validation_condition"]}')
    if not view["growth_task_states"]:
        lines.append("- 暂无成长任务。")
    return "\n".join(lines).rstrip() + "\n"


def render_ability_model_text(packages: list[dict]) -> str:
    view = build_comparison_view(packages)
    ledger = "真实面试" if view["source_type"] == "real" else "模拟训练"
    title = "初始能力快照" if len(packages) == 1 else "累计能力模型"
    levels = {dimension: [] for dimension in DIMENSION_LABELS}
    session_ids = {dimension: set() for dimension in DIMENSION_LABELS}
    for package in packages:
        session_id = package.get("session", {}).get("id")
        for assessment in package.get("assessments", []):
            for observation in assessment.get("competency_observations", []):
                dimension = observation.get("dimension")
                level = observation.get("level")
                if dimension in levels and level is not None:
                    levels[dimension].append(level)
                    session_ids[dimension].add(session_id)

    lines = [
        "# 职业能力模型",
        "",
        "## 数据范围",
        "",
        f"- {ledger}账本：{len(packages)} 场",
        "- 只汇总有原文证据的能力观察；缺失维度不按 0 分处理。",
        "",
        f"## {title}",
        "",
        "| 能力 | 当前记录均值 | 证据数 | 覆盖面试 |",
        "|---|---:|---:|---:|",
    ]
    for dimension, label in DIMENSION_LABELS.items():
        if not levels[dimension]:
            continue
        average = sum(levels[dimension]) / len(levels[dimension])
        lines.append(
            f"| {label} | {average:.2f} / 5 | {len(levels[dimension])} | {len(session_ids[dimension])} |"
        )
    if not any(levels.values()):
        lines.append("| 暂无能力证据 | — | 0 | 0 |")

    lines.extend(["", "## 记录变化", ""])
    if len(packages) == 1:
        lines.append("仅有 1 场，不计算趋势；这是后续对比的初始能力快照。")
    else:
        lines.append("首末差值仅描述记录变化，不解释原因，也不等于稳定趋势。")
        lines.append("")
        for dimension, label in DIMENSION_LABELS.items():
            item = view["ability_differences"][dimension]
            if item["difference"] is None:
                continue
            lines.append(
                f'- {label}：{item["first"]:.2f} → {item["last"]:.2f}（{item["difference"]:+.2f}）'
            )
        if not any(
            item["difference"] is not None
            for item in view["ability_differences"].values()
        ):
            lines.append("- 暂无可直接比较的首末同维度证据。")
    return "\n".join(lines).rstrip() + "\n"


def render_frequent_questions_text(packages: list[dict]) -> str:
    view = build_comparison_view(packages)
    ledger = "真实面试" if view["source_type"] == "real" else "模拟训练"
    first_session = len(packages) == 1
    lines = [
        "# 高频提问与知识点管理",
        "",
        "## 数据范围",
        "",
        f"- {ledger}账本：{len(packages)} 场",
        "- 真实面试与模拟训练严格分开计数。",
        "",
        "## 首次问题记录" if first_session else "## 高频问题",
        "",
    ]
    if first_session:
        lines.append("当前只有 1 场；以下均为首次出现，尚不能称为高频。")
        lines.append("")
    repeated = [item for item in view["frequent_questions"] if item["count"] >= 2]
    singles = [item for item in view["frequent_questions"] if item["count"] == 1]
    shown = view["frequent_questions"] if first_session else repeated
    for item in shown:
        suffix = "（首次出现）" if first_session else ""
        lines.append(f'- {item["question"]}：{item["count"]} 次{suffix}')
    if not shown:
        lines.append("- 暂无重复出现的问题。")
    if not first_session:
        lines.extend(["", "## 单次出现的问题", ""])
        lines.extend(f'- {item["question"]}' for item in singles)
        if not singles:
            lines.append("- 暂无。")

    sections = (
        ("真正考察点（推断）", view["frequent_inferred_focuses"], "focus"),
        ("待补知识与证据", view["repeated_evidence_gaps"], "gap"),
        ("重复问题原因", view["repeated_root_causes"], "label"),
    )
    for title, items, key in sections:
        lines.extend(["", f"## {title}", ""])
        for item in items:
            suffix = "（首次记录）" if first_session and item["count"] == 1 else ""
            lines.append(f'- {item[key]}：{item["count"]} 次{suffix}')
        if not items:
            lines.append("- 暂无可汇总信息。")

    lines.extend(["", "## 成长任务关联", ""])
    for task in view["growth_task_states"]:
        status = TASK_STATUS_LABELS.get(task["status"], task["status"])
        lines.append(f'- {task["title"]}：{status}')
    if not view["growth_task_states"]:
        lines.append("- 暂无成长任务。")
    return "\n".join(lines).rstrip() + "\n"


def _resolve_artifact_stem(output_dir: Path, package: dict) -> str:
    base = artifact_stem(package["session"])
    session_id = package["session"].get("id")
    for index in range(100):
        candidate = base if index == 0 else f"{base}-{index:02d}"
        paths = list(output_dir.glob(f"{candidate}-*"))
        if not paths:
            return candidate
        session_path = output_dir / f"{candidate}-session.json"
        if session_path.is_file():
            try:
                existing = json.loads(session_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
            if existing.get("session", {}).get("id") == session_id:
                return candidate
    raise ValueError("too many interviews share the same minute")


def write_artifact_bundle(file_path: str, data_dir: str, output_dir: str) -> list[Path]:
    package = json.loads(Path(file_path).read_text(encoding="utf-8"))
    validate_session_package(package)
    import_session(data_dir, package)
    packages = load_session_packages(data_dir, package["session"]["source_type"])
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = _resolve_artifact_stem(destination, package)
    radar_name = f"{stem}-ability-radar.svg"
    contents = {
        f"{stem}-analysis.md": render_single_text(package, radar_name),
        f"{stem}-qa-original.md": render_original_qa(data_dir, package["session"]["id"]),
        f"{stem}-session.json": json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        f"{stem}-ability-model.md": render_ability_model_text(packages),
        f"{stem}-frequent-questions.md": render_frequent_questions_text(packages),
        radar_name: render_radar_svg(package),
    }
    written = []
    for name, content in contents.items():
        path = destination / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def _build_parser():
    parser = argparse.ArgumentParser(description="Render interview growth reports.")
    commands = parser.add_subparsers(dest="command", required=True)

    single_parser = commands.add_parser("single")
    single_parser.add_argument("--file", required=True)
    single_parser.add_argument("--output", required=True)

    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--data-dir", required=True)
    compare_parser.add_argument("--source-type", choices=("real", "mock"), required=True)
    compare_parser.add_argument("--session-id", action="append")
    compare_parser.add_argument("--output", required=True)

    bundle_parser = commands.add_parser("bundle")
    bundle_parser.add_argument("--file", required=True)
    bundle_parser.add_argument("--data-dir", required=True)
    bundle_parser.add_argument("--output-dir", required=True)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "bundle":
            written = write_artifact_bundle(
                args.file, args.data_dir, args.output_dir
            )
            print(json.dumps([str(path) for path in written], ensure_ascii=False))
            return 0
        if args.command == "single":
            package = json.loads(Path(args.file).read_text(encoding="utf-8"))
            validate_session_package(package)
            output = render_single_text(package)
        else:
            database_path = Path(args.data_dir) / "library.db"
            if not database_path.is_file():
                raise ValueError(f"library database does not exist: {database_path}")
            packages = load_session_packages(
                args.data_dir, args.source_type, args.session_id
            )
            for package in packages:
                validate_session_package(package)
            output = render_comparison_text(packages)
        Path(args.output).write_text(output, encoding="utf-8")
    except (
        AttributeError,
        KeyError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
