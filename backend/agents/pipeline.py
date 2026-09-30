# -*- coding: utf-8 -*-
"""主链路编排：命题拆解 → 标的确认 → 分组批量取证 → 综合结论。

提供两种入口：
- stream_pipeline(thesis)：异步事件生成器，逐阶段 yield 事件（供 SSE）；
- run_pipeline(thesis)：收集完整结果一次性返回（供测试/兼容）。
"""
import re
import time
import asyncio
import httpx
from urllib.parse import quote

from backend.agents.decomposer import decompose
from backend.agents.evidence import analyze_sub_question_group
from backend.agents.conclusion import build_conclusion, stream_report
from backend.data_sources.fuyao import search_ticker
from backend.data_sources.ifind_mcp import IFindMCPClient

# 工具中文名（用于过程展示）
TOOL_CN = {
    "price_snapshot": "行情快照",
    "historical_prices": "历史K线",
    "income_statement": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow": "现金流量表",
    "financial_indicators": "财务指标",
    "ifind_notice": "公告原文",
    "ifind_indicators": "金融指标",
    "ifind_news": "财经新闻",
    "ifind_edb": "宏观行业数据",
}


async def _resolve_thscode(client: httpx.AsyncClient, target: dict) -> str:
    """确认标的完整 thscode；拆解结果缺失时用检索接口兜底。"""
    thscode = target.get("thscode")
    if thscode and target.get("confidence") in ("high", "medium"):
        return thscode
    name = target.get("name")
    if not name:
        return thscode or ""
    result = await search_ticker(client, name)
    if result.get("ok"):
        items = (result.get("data") or {}).get("item", [])
        for it in items:
            if it.get("asset_type") == "a-share":
                return it.get("thscode")
        if items:
            return items[0].get("thscode")
    return thscode or ""


def _group_by_tool(sub_questions: list):
    """按 tool 分组，保持组内原始顺序。返回 [(tool, [sq,...]), ...]。"""
    groups = {}
    order = []
    for sq in sub_questions:
        tool = sq.get("tool", "unknown")
        if tool not in groups:
            groups[tool] = []
            order.append(tool)
        groups[tool].append(sq)
    return [(tool, groups[tool]) for tool in order]


def _sources_section(target_name: str, time_scope: str,
                     sub_results: list) -> str:
    """构造报告文末「资料来源」：iFinD 公告/新闻标题(+日期) 与 巨潮年报链接。"""
    lines = []
    seen = set()
    for sr in sub_results:
        for r in sr.get("raw_data", []) or []:
            title = r.get("source_title")
            if not title:
                continue
            date = r.get("date") or ""
            key = (title, date)
            if key in seen:
                continue
            seen.add(key)
            suffix = f"（{date}，同花顺 iFinD）" if date else "（同花顺 iFinD）"
            lines.append(f"- 《{title}》{suffix}")

    # 巨潮年报原文链接：从时间范围与公告标题中提取年份
    blob = (time_scope or "") + " " + " ".join(t for (t, _) in seen)
    years = {int(y) for y in re.findall(r"20\d{2}", blob)}
    if target_name:
        for y in sorted(years, reverse=True):
            kw = quote(f"{target_name}{y}年年度报告")
            url = ("http://www.cninfo.com.cn/new/fulltextSearch"
                   f"?notautosubmit=&keyWord={kw}")
            lines.append(f"- [{y} 年年度报告原文（巨潮资讯网）]({url})")
    return "\n".join(lines)


async def stream_pipeline(thesis: str):
    """异步生成器：逐阶段 yield 事件字典。

    面向用户的呈现（结论先行、简洁 Markdown）：
      {type:'status', stage, label}      轻量阶段状态（一行提示）
      {type:'report_start'}              报告开始（前端切换到正文）
      {type:'answer_delta', text}        Markdown 报告流式增量
      {type:'done', data: full_result}   完整结果（内部存储/溯源/测试）
      {type:'error', message}            致命错误
    """
    started = time.time()
    trace = []

    def status(stage, label):
        return {"type": "status", "stage": stage, "label": label}

    # 1. 拆解
    yield status("decompose", "正在理解命题、识别标的与核心主张…")
    t0 = time.time()
    try:
        decomposition = await decompose(thesis)
    except Exception as e:
        yield {"type": "error",
               "message": f"命题拆解失败：{type(e).__name__}"}
        return
    sub_questions = decomposition.get("sub_questions", [])
    trace.append({
        "step": "decompose", "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "sub_question_count": len(sub_questions),
    })

    target_name_q = decomposition.get("target", {}).get("name", "")
    time_scope = decomposition.get("time_scope", "")

    # MCP 客户端懒握手：仅当存在 ifind_* 子问题时才真正建立会话
    mcp = IFindMCPClient()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 2. 确认标的
            yield status("resolve_target", "正在确认研究标的与代码…")
            t0 = time.time()
            thscode = await _resolve_thscode(
                client, decomposition.get("target", {}))
            trace.append({
                "step": "resolve_target",
                "status": "ok" if thscode else "warning",
                "elapsed_ms": int((time.time() - t0) * 1000),
                "thscode": thscode,
            })

            # 3. 按工具分组，各组【并行】取证分析
            groups = _group_by_tool(sub_questions)
            for tool, group in groups:
                cn = TOOL_CN.get(tool, tool)
                yield status(
                    f"evidence_{tool}",
                    f"正在调取「{cn}」数据、验证 {len(group)} 个子问题…")

            async def _run_group(tool, group):
                t0 = time.time()
                try:
                    recs = await analyze_sub_question_group(
                        client, group, thscode,
                        mcp=mcp, target_name=target_name_q,
                        time_scope=time_scope)
                    return tool, recs, "ok", int((time.time() - t0) * 1000)
                except Exception as e:
                    from backend.agents.evidence import (
                        _build_record, _empty_analysis)
                    err_main = {"ok": False, "tool": tool,
                                "source": tool, "data_time": None,
                                "error": str(e), "request_id": None}
                    recs = [
                        _build_record(sq, err_main, [err_main],
                                      _empty_analysis(
                                          sq,
                                          f"分组处理异常：{type(e).__name__}"))
                        for sq in group
                    ]
                    return tool, recs, f"error: {type(e).__name__}", \
                        int((time.time() - t0) * 1000)

            group_results = await asyncio.gather(*[
                _run_group(t, g) for t, g in groups])

            records_by_id = {}
            group_sizes = dict(groups)
            for tool, recs, state, ms in group_results:
                for rec in recs:
                    records_by_id[rec["sub_question"].get("id")] = rec
                trace.append({
                    "step": f"evidence_group_{tool}",
                    "status": state, "elapsed_ms": ms, "tool": tool,
                    "sub_question_count": len(group_sizes.get(tool, [])),
                })
    finally:
        await mcp.aclose()

    # 按原始子问题顺序重组
    sub_results = [
        records_by_id[sq.get("id")]
        for sq in sub_questions
        if sq.get("id") in records_by_id
    ]

    # 4. 结构化综合结论
    yield status("conclusion", "正在汇总证据、处理冲突、形成结论…")
    t0 = time.time()
    conclusion = await build_conclusion(thesis, sub_results)
    trace.append({
        "step": "conclusion", "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
    })

    # 5. 流式撰写简洁 Markdown 报告
    target_name = decomposition.get("target", {}).get("name", "")
    sources = _sources_section(target_name, time_scope, sub_results)
    yield {"type": "report_start"}
    async for piece in stream_report(
        thesis, target_name, thscode, conclusion, sub_results, sources
    ):
        yield {"type": "answer_delta", "text": piece}

    total_ms = int((time.time() - started) * 1000)
    full = {
        "thesis": thesis,
        "thscode": thscode,
        "target_name": target_name,
        "decomposition": decomposition,
        "sub_results": sub_results,
        "conclusion": conclusion,
        "report_markdown": None,  # 由 main.py 流式结束后回填
        "meta": {
            "total_elapsed_ms": total_ms,
            "data_tool_groups": [g[0] for g in groups],
            "trace": trace,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    yield {"type": "done", "data": full}


async def run_pipeline(thesis: str) -> dict:
    """执行完整链路并返回最终结果（收集 stream_pipeline 事件）。"""
    final = None
    async for ev in stream_pipeline(thesis):
        if ev["type"] == "done":
            final = ev["data"]
        elif ev["type"] == "error":
            raise RuntimeError(ev["message"])
    if final is None:
        raise RuntimeError("验证链路未产出结果")
    return final
