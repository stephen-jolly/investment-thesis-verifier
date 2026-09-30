"""主链路编排：命题拆解 → 标的确认 → 分组批量取证 → 综合结论。"""
import time
import httpx

from backend.agents.decomposer import decompose
from backend.agents.evidence import analyze_sub_question_group
from backend.agents.conclusion import build_conclusion
from backend.data_sources.fuyao import search_ticker


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


async def run_pipeline(thesis: str) -> dict:
    """执行完整的命题验证链路。"""
    started = time.time()
    trace = []

    # 1. 拆解
    t0 = time.time()
    decomposition = await decompose(thesis)
    sub_questions = decomposition.get("sub_questions", [])
    trace.append({
        "step": "decompose", "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "sub_question_count": len(sub_questions),
    })

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 2. 确认标的
        t0 = time.time()
        thscode = await _resolve_thscode(client, decomposition.get("target", {}))
        trace.append({
            "step": "resolve_target",
            "status": "ok" if thscode else "warning",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "thscode": thscode,
        })

        # 3. 按工具分组，批量取证分析
        records_by_id = {}
        groups = _group_by_tool(sub_questions)
        for tool, group in groups:
            t0 = time.time()
            try:
                recs = await analyze_sub_question_group(client, group, thscode)
                status = "ok"
            except Exception as e:
                status = f"error: {type(e).__name__}"
                from backend.agents.evidence import _build_record, _empty_analysis
                recs = [
                    _build_record(sq, {"ok": False, "tool": tool,
                                       "source": tool, "data_time": None,
                                       "error": str(e), "request_id": None},
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

    # 按原始子问题顺序重组
    sub_results = [
        records_by_id[sq.get("id")]
        for sq in sub_questions
        if sq.get("id") in records_by_id
    ]

    # 4. 综合结论
    t0 = time.time()
    conclusion = await build_conclusion(thesis, sub_results)
    trace.append({
        "step": "conclusion", "status": "ok",
        "elapsed_ms": int((time.time() - t0) * 1000),
    })

    total_ms = int((time.time() - started) * 1000)
    return {
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
