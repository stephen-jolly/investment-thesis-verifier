"""环节 3：综合全部子问题证据，形成可追溯结论。"""
import json

from backend.agents.llm_client import chat_json
from backend.prompts.templates import CONCLUSION_SYSTEM, CONCLUSION_USER
from backend.config import settings


async def build_conclusion(thesis: str, sub_results: list) -> dict:
    """汇总各子问题的分析结果，调用 LLM 生成综合结论。

    sub_results: evidence.analyze_sub_question 返回的列表。
    """
    # 只把 LLM 需要的精简信息传给结论模型
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
            "summary": "由于结论生成环节出现异常，暂无法给出综合判断，请查看各子问题的独立分析结果。",
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
