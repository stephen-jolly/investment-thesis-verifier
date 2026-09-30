"""主链路编排：命题拆解 → 标的确认 → 分组批量取证 → 综合结论。

提供两种入口：
- stream_pipeline(thesis)：异步事件生成器，逐阶段 yield 结构化事件（供 SSE）；
- run_pipeline(thesis)：收集完整结果一次性返回（供测试/兼容）。
"""
import time
import httpx

from backend.agents.decomposer import decompose
from backend.agents.evidence import analyze_sub_question_group
from backend.agents.conclusion import build_conclusion
from backend.data_sources.fuyao import search_ticker

# 工具中文名（用于过程展示）
TOOL_CN = {
    "price_snapshot": "行情快照",
    "historical_prices": "历史K线",
    "income_statement": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow": "现金流量表",
    "financial_indicators": "财务指标",
    "ifind_announcements": "公告/研报",
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


async def stream_pipeline(thesis: str):
    """异步生成器：逐阶段 yield 事件字典。

    事件类型：
      {type:'status', stage, label}                 阶段状态（驱动时间线）
      {type:'decomposition', data}                 拆解完成
      {type:'sub_results', tool, data:[records]}   一组子问题取证完成
      {type:'conclusion', data}                    综合结论
      {type:'done', data: full_result}             完整结果
      {type:'error', message}                      致命错误
    """
    started = time.time()
    trace = []

    # 1. 拆解
    yield {"type": "status", "stage": "decompose",
           "label": "正在拆解投资命题、识别标的与核心主张…"}
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
    yield {"type": "decomposition", "data": decomposition}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 2. 确认标的
        yield {"type": "status", "stage": "resolve_target",
               "label": "正在确认研究标的与代码…"}
        t0 = time.time()
        thscode = await _resolve_thscode(client, decomposition.get("target", {}))
        trace.append({
            "step": "resolve_target",
            "status": "ok" if thscode else "warning",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "thscode": thscode,
        })

        # 3. 按工具分组，批量取证分析（逐组推送）
        records_by_id = {}
        groups = _group_by_tool(sub_questions)
        for tool, group in groups:
            cn = TOOL_CN.get(tool, tool)
            yield {"type": "tool_start", "tool": tool,
                   "label": f"正在调用「{cn}」取证并验证 {len(group)} 个子问题…"}
            t0 = time.time()
            try:
                recs = await analyze_sub_question_group(client, group, thscode)
                status = "ok"
            except Exception as e:
                status = f"error: {type(e).__name__}"
                from backend.agents.evidence import _build_record, _empty_analysis
                err_main = {"ok": False, "tool": tool,
                            "source": tool, "data_time": None,
                            "error": str(e), "request_id": None}
                recs = [
                    _build_record(sq, err_main, [err_main],
                                  _empty_analysis(sq, f"分组处理异常：{type(e).__name__}"))
                    for sq in group
                ]
            for rec in recs:
                records_by_id[rec["sub_question"].get("id")] = rec
            fetch_ok = any(r["tool_result_meta"].get("ok") for r in recs)
            trace.append({
                "step": f"evidence_group_{tool}",
                "status": status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "tool": tool,
                "sub_question_count": len(group),
                "fetch_ok": fetch_ok,
            })
            yield {"type": "sub_results", "tool": tool, "data": recs}

    # 按原始子问题顺序重组
    sub_results = [
        records_by_id[sq.get("id")]
        for sq in sub_questions
        if sq.get("id") in records_by_id
    ]

    # 4. 综合结论
    yield {"type": "status", "stage": "conclusion",
           "label": "正在汇总证据、处理冲突并生成结论…"}
    t0 = time.time()
    conclusion = await build_conclusion(thesis, sub_results)
    trace.append({
        "step": "conclusion", "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
    })
    yield {"type": "conclusion", "data": conclusion}

    total_ms = int((time.time() - started) * 1000)
    full = {
        "thesis": thesis,
        "thscode": thscode,
        "decomposition": decomposition,
        "sub_results": sub_results,
        "conclusion": conclusion,
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
