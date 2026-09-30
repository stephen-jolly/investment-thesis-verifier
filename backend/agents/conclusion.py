"""环节 3：综合全部子问题证据，形成可追溯结论；环节 4：流式撰写用户报告。"""
import json

from backend.agents.llm_client import chat_json, chat_stream
from backend.prompts.templates import (
    CONCLUSION_SYSTEM, CONCLUSION_USER, REPORT_SYSTEM, REPORT_USER,
)
from backend.config import settings


def _compact_sub_results(sub_results: list) -> list:
    """压缩子问题结果，供结论/报告模型使用。"""
    compact = []
    for sr in sub_results:
        analysis = sr.get("analysis", {})
        compact.append({
            "sub_question_id": analysis.get("sub_question_id")
                              or sr.get("sub_question", {}).get("id"),
            "question": sr.get("sub_question", {}).get("question", ""),
            "verdict": analysis.get("summary_verdict", "unverifiable"),
            "reason": analysis.get("summary_reason", ""),
            "evidences": analysis.get("evidences", []),
        })
    return compact


async def build_conclusion(thesis: str, sub_results: list) -> dict:
    """汇总各子问题的分析结果，调用 LLM 生成结构化综合结论（内部，用于溯源/测试）。

    sub_results: evidence.analyze_sub_question_group 返回的列表。
    """
    compact = _compact_sub_results(sub_results)

    user_prompt = CONCLUSION_USER.format(
        thesis=thesis,
        sub_results_json=json.dumps(compact, ensure_ascii=False, indent=2),
    )

    try:
        result = await chat_json(
            CONCLUSION_SYSTEM, user_prompt,
            model=settings.ARK_MODEL_MAIN, temperature=0.2,
        )
    except Exception as e:
        # 结论模型失败时的兜底
        result = {
            "verdict": "inconclusive",
            "confidence": "low",
            "confidence_reason": f"结论生成模型调用失败：{type(e).__name__}",
            "summary": "由于结论生成环节出现异常，暂无法给出综合判断，请稍后重试。",
            "conflicts": [],
            "flip_conditions": [],
            "next_steps": ["请稍后重试，或检查大模型服务状态。"],
        }

    # 字段兜底
    for key, default in [
        ("verdict", "inconclusive"), ("confidence", "low"),
        ("confidence_reason", ""), ("summary", ""),
        ("conflicts", []), ("flip_conditions", []), ("next_steps", []),
    ]:
        result.setdefault(key, default)
    return result


async def stream_report(thesis: str, target_name: str, thscode: str,
                        conclusion: dict, sub_results: list,
                        annual_report_links: str):
    """基于结构化结论与证据，流式 yield 面向用户的 Markdown 报告文本。"""
    compact = _compact_sub_results(sub_results)
    user_prompt = REPORT_USER.format(
        thesis=thesis,
        target_name=target_name or "未识别",
        thscode=thscode or "",
        conclusion_json=json.dumps(conclusion, ensure_ascii=False, indent=2),
        sub_results_json=json.dumps(compact, ensure_ascii=False, indent=2),
        annual_report_links=annual_report_links or "无",
    )
    messages = [
        {"role": "system", "content": REPORT_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    async for piece in chat_stream(
        messages, model=settings.ARK_MODEL_MAIN, temperature=0.3
    ):
        yield piece
