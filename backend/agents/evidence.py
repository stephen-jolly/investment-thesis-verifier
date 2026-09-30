# -*- coding: utf-8 -*-
"""环节 2：调用数据工具并进行证据分类（分组批量 + 关联数据补充）。

支持两类数据源：
- 扶摇 REST（income_statement / balance_sheet / cash_flow / 行情等）；
- 同花顺 iFinD MCP（ifind_notice 公告原文 / ifind_indicators 金融指标 /
  ifind_news 新闻 / ifind_edb 宏观行业）。
"""
import asyncio
import json
import httpx

from backend.agents.llm_client import chat_json
from backend.data_sources.fuyao import TOOL_REGISTRY, get_snapshot
from backend.data_sources.ifind_data import IFIND_TOOLS, fetch_ifind
from backend.prompts.templates import (
    BATCH_EVIDENCE_SYSTEM, BATCH_EVIDENCE_USER,
)
from backend.config import settings

# 关联补充：某些工具的子问题天然需要另一张表的基准字段
SUPPLEMENT_TOOLS = {
    "cash_flow": "income_statement",
}


# ======================================================================
# 扶摇 REST 取数
# ======================================================================
async def _fetch_data(client: httpx.AsyncClient, tool: str, thscode: str):
    """根据工具名调用对应的扶摇函数，做参数适配。"""
    if tool and "," in tool:  # 兜底：模型误填逗号组合，取第一个工具
        tool = tool.split(",")[0].strip()
    if tool == "price_snapshot":
        return await get_snapshot(client, thscode)
    func = TOOL_REGISTRY.get(tool)
    if func is None:
        return {
            "ok": False, "tool": tool, "source": tool,
            "data_time": None, "data": None,
            "error": f"未知工具：{tool}", "request_id": None,
        }
    return await func(client, thscode)


def _format_results(tool_results: list) -> str:
    """把一份或多份扶摇取数结果格式化为给 LLM 阅读的文本，分别标注来源。"""
    blocks = []
    for tr in tool_results:
        if not tr.get("ok"):
            blocks.append(
                f"【{tr.get('source')} 接口调用失败】{tr.get('error', '未知错误')}")
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
            f"数据来源：{tr.get('source')}；时点：{tr.get('data_time')}\n{text}")
    return "\n\n".join(blocks)


# ======================================================================
# 通用：批量 LLM 证据分析与结果对齐
# ======================================================================
def _empty_analysis(sq, reason):
    return {
        "sub_question_id": sq.get("id"),
        "summary_verdict": "unverifiable",
        "summary_reason": reason,
        "evidences": [],
    }


async def _batch_llm_analyze(source: str, sub_questions: list,
                             reading_text: str) -> list:
    sq_compact = [{
        "sub_question_id": sq.get("id"),
        "question": sq.get("question", ""),
        "verify_logic": sq.get("verify_logic", ""),
        "params_hint": sq.get("params_hint", ""),
    } for sq in sub_questions]
    user_prompt = BATCH_EVIDENCE_USER.format(
        source=source,
        sub_questions_json=json.dumps(sq_compact, ensure_ascii=False, indent=2),
        raw_data=reading_text,
    )
    try:
        classified = await chat_json(
            BATCH_EVIDENCE_SYSTEM, user_prompt,
            model=settings.ARK_MODEL_MAIN, temperature=0.1,
        )
        return classified.get("results", [])
    except Exception as e:
        return [_empty_analysis(sq, f"证据分析模型调用失败：{type(e).__name__}")
                for sq in sub_questions]


def _align_analyses(sub_questions: list, results: list) -> list:
    by_id = {r.get("sub_question_id"): r for r in results}
    aligned = []
    for sq in sub_questions:
        a = by_id.get(sq.get("id"))
        if a is None:
            a = _empty_analysis(sq, "模型未返回该子问题的分析结果")
        else:
            a.setdefault("sub_question_id", sq.get("id"))
            a.setdefault("summary_verdict", "unverifiable")
            a.setdefault("summary_reason", "")
            a.setdefault("evidences", [])
        aligned.append(a)
    return aligned


# ======================================================================
# 记录组装
# ======================================================================
def _build_record(sq, main_result, all_results, analysis):
    """组装扶摇单个子问题完整记录（含主+补充数据溯源）。"""
    raw = []
    for tr in all_results:
        raw.extend((tr.get("data") or {}).get("item", [])[:8])
    return {
        "sub_question": sq,
        "tool_result_meta": {
            "ok": main_result.get("ok"),
            "tool": main_result.get("tool"),
            "source": main_result.get("source"),
            "data_time": main_result.get("data_time"),
            "error": main_result.get("error"),
            "request_id": main_result.get("request_id"),
            "supplement_tool": SUPPLEMENT_TOOLS.get(main_result.get("tool")),
        },
        "raw_data": raw,
        "analysis": analysis,
    }


def _mcp_record(sq, tool, source, analysis, segments, answer, data_time=None):
    return {
        "sub_question": sq,
        "tool_result_meta": {
            "ok": True, "tool": tool, "source": source,
            "data_time": data_time, "error": None,
            "request_id": None, "supplement_tool": None,
        },
        "raw_data": segments or ([{"answer": answer}] if answer else []),
        "analysis": analysis,
    }


def _mcp_error_record(sq, tool, reason):
    return {
        "sub_question": sq,
        "tool_result_meta": {
            "ok": False, "tool": tool, "source": "同花顺iFinD",
            "data_time": None, "error": reason,
            "request_id": None, "supplement_tool": None,
        },
        "raw_data": [],
        "analysis": _empty_analysis(sq, reason),
    }


# ======================================================================
# 主入口
# ======================================================================
async def analyze_sub_question_group(client: httpx.AsyncClient,
                                     sub_questions: list, thscode: str,
                                     mcp=None, target_name: str = "",
                                     time_scope: str = "") -> list:
    """对同一工具的一组子问题取数并批量 LLM 分析。"""
    if not sub_questions:
        return []
    tool = sub_questions[0].get("tool", "")
    if tool and "," in tool:
        tool = tool.split(",")[0].strip()

    # ------------------------------------------------------------------
    # iFinD MCP 分支
    # ------------------------------------------------------------------
    if tool in IFIND_TOOLS:
        try:
            parsed = await fetch_ifind(
                mcp, tool, target_name, thscode, time_scope, sub_questions)
        except Exception as e:
            return [_mcp_error_record(sq, tool,
                    f"iFinD 数据获取异常：{type(e).__name__}")
                    for sq in sub_questions]

        results = await _batch_llm_analyze(
            parsed["source"], sub_questions, parsed["reading_text"])
        analyses = _align_analyses(sub_questions, results)
        return [
            _mcp_record(sq, tool, parsed["source"], a,
                        parsed["segments"], parsed.get("answer"),
                        parsed.get("data_time"))
            for sq, a in zip(sub_questions, analyses)
        ]

    # ------------------------------------------------------------------
    # 扶摇 REST 分支
    # ------------------------------------------------------------------
    fetch_tools = [tool]
    if tool in SUPPLEMENT_TOOLS:
        fetch_tools.append(SUPPLEMENT_TOOLS[tool])
    fetched = await asyncio.gather(*[
        _fetch_data(client, t, thscode) for t in fetch_tools
    ])
    main_result = fetched[0]
    raw_text = _format_results(fetched)

    if not main_result.get("ok"):
        return [
            _build_record(sq, main_result, fetched,
                          _empty_analysis(sq,
                              f"数据源不可用：{main_result.get('error')}"))
            for sq in sub_questions
        ]

    results = await _batch_llm_analyze(
        main_result.get("source"), sub_questions, raw_text)
    analyses = _align_analyses(sub_questions, results)
    return [_build_record(sq, main_result, fetched, a)
            for sq, a in zip(sub_questions, analyses)]


async def analyze_sub_question(client: httpx.AsyncClient, sub_question: dict,
                               thscode: str, **kwargs) -> dict:
    """单个子问题分析（批量接口的单子问题包装）。"""
    records = await analyze_sub_question_group(
        client, [sub_question], thscode, **kwargs)
    return records[0]
