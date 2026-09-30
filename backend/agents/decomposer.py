"""环节 1：命题拆解 Agent。"""
from backend.agents.llm_client import chat_json
from backend.prompts.templates import DECOMPOSE_SYSTEM, DECOMPOSE_USER
from backend.config import settings


async def decompose(thesis: str) -> dict:
    """把投资命题拆解为可验证子问题。

    返回结构见 templates.DECOMPOSE_SYSTEM 中定义的 JSON。
    """
    user_prompt = DECOMPOSE_USER.format(thesis=thesis)
    result = await chat_json(
        DECOMPOSE_SYSTEM, user_prompt,
        model=settings.ARK_MODEL_MAIN, temperature=0.2,
    )
    # 基本校验与兜底
    if "sub_questions" not in result:
        result["sub_questions"] = []
    result.setdefault("clarifications", [])
    result.setdefault("target", {})
    result.setdefault("core_claim", thesis)
    result.setdefault("time_scope", "无法判断")
    return result
