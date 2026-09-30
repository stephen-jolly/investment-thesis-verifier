"""环节 2：调用数据工具并进行证据分类（分组批量 + 关联数据补充）。"""
import asyncio
import json
import httpx

from backend.agents.llm_client import chat_json
from backend.data_sources.fuyao import TOOL_REGISTRY, get_snapshot
from backend.prompts.templates import (
    EVIDENCE_SYSTEM, EVIDENCE_USER,
    BATCH_EVIDENCE_SYSTEM, BATCH_EVIDENCE_USER,
)
from backend.config import settings

# 关联补充：某些工具的子问题天然需要另一张表的基准字段
# 例如现金流验证「经营现金流净额/归母净利润」需要利润表的净利润
SUPPLEMENT_TOOLS = {
    "cash_flow": "income_statement",
}


async def _fetch_data(client: httpx.AsyncClient, tool: str, thscode: str):
    """根据工具名调用对应的扶摇函数，做参数适配。"""
    # 兜底：模型若误填逗号组合，取第一个工具
    if tool and "," in tool:
        tool = tool.split(",")[0].strip()
    if tool == "price_snapshot":
        return await get_snapshot(client, thscode)
    if tool == "ifind_announcements":
        return {
            "ok": False, "tool": tool, "source": "iFinD-公告研报",
            "data_time": None, "data": None,
            "error": "iFinD MCP 文本数据源暂未接入，无法获取公告/研报原文",
            "request_id": None,
        }
    func = TOOL_REGISTRY.get(tool)
    if func is None:
        return {
            "ok": False, "tool": tool, "source": tool,
            "data_time": None, "data": None,
            "error": f"未知工具：{tool}", "request_id": None,
        }
    return await func(client, thscode)


def _format_results(tool_results: list) -> str:
    """把一份或多份取数结果格式化为给 LLM 阅读的文本，分别标注来源。"""
    blocks = []
    for tr in tool_results:
        if not tr.get("ok"):
            blocks.append(f"【{tr.get('source')} 接口调用失败】{tr.get('error', '未知错误')}")
            continue
        data = tr.get("data") or {}
        item = data.get("item", [])
        if not item:
            blocks.append(f"【{tr.get('source')}】调用成功但无记录。")
            continue
        text = json.dumps(item[:8], ensure_ascii=False, indent=2)
        if len(text) > 4000:
            text = text[:4000] + "\n...（已截断）"
        blocks.append(
            f"数据来源：{tr.get('source')}；时点：{tr.get('data_time')}\n{text}"
        )
    return "\n\n".join(blocks)


def _empty_analysis(sq, reason):
    return {
        "sub_question_id": sq.get("id"),
        "summary_verdict": "unverifiable",
        "summary_reason": reason,
        "evidences": [],
    }


def _build_record(sq, main_result, all_results, analysis):
    """组装单个子问题完整记录（含主+补充数据溯源）。"""
    raw = []
    for tr in all_results:
        raw.extend((tr.get("data") or {}).get("item", [])[:8])
    supplement = SUPPLEMENT_TOOLS.get(main_result.get("tool"))
    return {
        "sub_question": sq,
        "tool_result_meta": {
            "ok": main_result.get("ok"),
            "tool": main_result.get("tool"),
            "source": main_result.get("source"),
            "data_time": main_result.get("data_time"),
            "error": main_result.get("error"),
            "request_id": main_result.get("request_id"),
            "supplement_tool": supplement,
        },
        "raw_data": raw,
        "analysis": analysis,
    }


async def analyze_sub_question_group(client: httpx.AsyncClient,
                                     sub_questions: list, thscode: str) -> list:
    """对同一工具的一组子问题：取主数据（必要时补充关联表），批量 LLM 分析。"""
    if not sub_questions:
        return []
    tool = sub_questions[0].get("tool", "")
    if tool and "," in tool:
        tool = tool.split(",")[0].strip()

    # 1. 并行取主数据 + 关联补充数据
    fetch_tools = [tool]
    if tool in SUPPLEMENT_TOOLS:
        fetch_tools.append(SUPPLEMENT_TOOLS[tool])
    fetched = await asyncio.gather(*[
        _fetch_data(client, t, thscode) for t in fetch_tools
    ])
    main_result = fetched[0]
    raw_text = _format_results(fetched)

    # 2. 主数据失败：整组标记无法验证（不调用 LLM）
    if not main_result.get("ok"):
        return [
            _build_record(sq, main_result, fetched,
                          _empty_analysis(sq, f"数据源不可用：{main_result.get('error')}"))
            for sq in sub_questions
        ]

    # 3. 批量 LLM 分析
    sq_compact = [
        {
            "sub_question_id": sq.get("id"),
            "question": sq.get("question", ""),
            "verify_logic": sq.get("verify_logic", ""),
            "params_hint": sq.get("params_hint", ""),
        }
        for sq in sub_questions
    ]
    user_prompt = BATCH_EVIDENCE_USER.format(
        source=main_result.get("source"),
        sub_questions_json=json.dumps(sq_compact, ensure_ascii=False, indent=2),
        raw_data=raw_text,
    )

    try:
        classified = await chat_json(
            BATCH_EVIDENCE_SYSTEM, user_prompt,
            model=settings.ARK_MODEL_MAIN, temperature=0.1,
        )
        results = classified.get("results", [])
    except Exception as e:
        results = [
            _empty_analysis(sq, f"证据分析模型调用失败：{type(e).__name__}")
            for sq in sub_questions
        ]

    # 4. 按 id 对齐，防漏返/乱序
    by_id = {r.get("sub_question_id"): r for r in results}
    records = []
    for sq in sub_questions:
        analysis = by_id.get(sq.get("id"))
        if analysis is None:
            analysis = _empty_analysis(sq, "模型未返回该子问题的分析结果")
        else:
            analysis.setdefault("sub_question_id", sq.get("id"))
            analysis.setdefault("summary_verdict", "unverifiable")
            analysis.setdefault("summary_reason", "")
            analysis.setdefault("evidences", [])
        records.append(_build_record(sq, main_result, fetched, analysis))
    return records


async def analyze_sub_question(client: httpx.AsyncClient, sub_question: dict,
                               thscode: str) -> dict:
    """单个子问题分析（批量接口的单子问题包装）。"""
    records = await analyze_sub_question_group(client, [sub_question], thscode)
    return records[0]
