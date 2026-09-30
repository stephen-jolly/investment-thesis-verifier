# -*- coding: utf-8 -*-
"""iFinD MCP 取数与解析。

把 MCP 工具返回的外层信封 `{"code":1,"msg":"success","data":...}` 解析为
统一结构，供证据分类 LLM 阅读、并保留可溯源信息（公告标题 + 日期）。

逻辑工具 → MCP 工具映射：
- ifind_notice     → search_notice（公告段落原文，法定披露）
- ifind_indicators → get_security_indicators（日频金融指标/财报科目/财务比率）
- ifind_news       → search_news（财经新闻片段）
- ifind_edb        → fetch_edb（宏观/行业/大宗商品指标）
"""
import re
import json
import datetime

IFIND_TOOLS = {"ifind_notice", "ifind_indicators", "ifind_news", "ifind_edb"}


# ----------------------------------------------------------------------
def _outer_json(text: str):
    """解析 MCP text 中的外层 JSON 信封（容错：必要时从首个 { 处截取）。"""
    try:
        return json.loads(text)
    except Exception:
        i = text.find("{")
        if i >= 0:
            try:
                return json.loads(text[i:])
            except Exception:
                return {}
    return {}


def _maybe_json(v):
    """data 可能是字符串化的 JSON，尝试二次解析。"""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _year_window(time_scope: str):
    """从时间范围推断公告/新闻的发布日期窗口（年报到次年才披露，故 +1 年）。"""
    today = datetime.date.today()
    years = [int(y) for y in re.findall(r"20\d{2}", time_scope or "")]
    if years:
        start = datetime.date(min(years), 1, 1)
        end_year = min(max(years) + 1, today.year)
        end = datetime.date(end_year, 12, 31)
        if end > today:
            end = today
        return start.isoformat(), end.isoformat()
    return (datetime.date(today.year - 6, 1, 1).isoformat(),
            today.isoformat())


def _market_of(thscode: str) -> str:
    t = (thscode or "").upper()
    if t.endswith(".HK"):
        return "港股"
    if t.endswith((".N", ".O", ".US")):
        return "美股"
    return "A股"


def _group_hint(sub_questions: list) -> str:
    """合并同组子问题的参数提示，去重、截断，作为 MCP 自然语言 query 一部分。"""
    parts = []
    for sq in sub_questions:
        h = (sq.get("params_hint") or sq.get("question") or "").strip()
        if h and h not in parts:
            parts.append(h)
    hint = "；".join(parts)
    return hint[:140]


def _assemble(tool, source, segments=None, answer=None, params=None,
              data_time=None):
    blocks = []
    raw = []
    for s in (segments or []):
        title = s.get("title")
        date = s.get("date")
        text = s.get("text") or ""
        blocks.append(f"【{title}；{date}】\n{text}")
        raw.append({"source_title": title, "date": date,
                    "snippet": text[:700]})
    if answer:
        blocks.append(answer)
    if not blocks:
        blocks.append("（该数据源本次未返回可用内容）")
    return {
        "ok": True, "tool": tool, "source": source,
        "data_time": data_time,
        "reading_text": "\n\n".join(blocks),
        "segments": raw,
        "answer": answer,
        "params": params,
    }


def _fallback(tool, source, data) -> dict:
    """结构化解析失败时的兜底：把原始 data 直接作为阅读文本。"""
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    return _assemble(tool, source, answer=text[:4000])


# ----------------------------------------------------------------------
async def fetch_ifind(mcp, tool: str, target_name: str, thscode: str,
                      time_scope: str, sub_questions: list) -> dict:
    """调用对应 iFinD MCP 工具并解析为统一结构。"""
    hint = _group_hint(sub_questions)
    name = target_name or ""
    scope = "" if (time_scope or "").strip() in ("", "无法判断") \
        else time_scope.strip()
    # 公告检索：未指定其他公告类型时，默认锁定对应年份的年度报告
    notice_type = ""
    if not re.search(r"预告|季报|临时公告|招股|问询|公告", hint):
        ym = re.search(r"20\d{2}", scope or hint or "")
        if ym:
            notice_type = f"{ym.group(0)}年年度报告"

    def _q(*parts):
        return " ".join(p for p in parts if p).strip()

    if tool == "ifind_notice":
        ts, te = _year_window(time_scope)
        r = await mcp.call_tool("search_notice", {
            "query": _q(name, scope, notice_type, hint),
            "size": 10, "time_start": ts, "time_end": te,
        })
        data = _maybe_json(_outer_json(r["text"]).get("data"))
        if isinstance(data, list):
            segs = [{
                "title": d.get("公告标题") or d.get("标题"),
                "date": d.get("日期") or d.get("发布日期"),
                "text": d.get("公告片段内容") or d.get("内容")
                        or d.get("片段"),
            } for d in data if isinstance(d, dict)]
            return _assemble(tool, "同花顺iFinD·公告原文", segs)
        return _fallback(tool, "同花顺iFinD·公告原文", data)

    if tool == "ifind_indicators":
        r = await mcp.call_tool("get_security_indicators", {
            "market": _market_of(thscode),
            "query": _q(name, scope, hint),
        })
        data = _maybe_json(_outer_json(r["text"]).get("data"))
        if isinstance(data, dict) and data.get("answer"):
            return _assemble(tool, "同花顺iFinD·金融指标",
                             answer=data.get("answer"),
                             params=data.get("indicators_params"))
        return _fallback(tool, "同花顺iFinD·金融指标", data)

    if tool == "ifind_news":
        ts, te = _year_window(time_scope)
        r = await mcp.call_tool("search_news", {
            "query": _q(name, scope, hint),
            "size": 10, "time_start": ts, "time_end": te,
        })
        data = _maybe_json(_outer_json(r["text"]).get("data"))
        if isinstance(data, list):
            segs = [{
                "title": d.get("新闻标题") or d.get("标题"),
                "date": d.get("日期") or d.get("发布时间"),
                "text": d.get("新闻片段内容") or d.get("内容")
                        or d.get("摘要"),
            } for d in data if isinstance(d, dict)]
            return _assemble(tool, "同花顺iFinD·财经新闻", segs)
        return _fallback(tool, "同花顺iFinD·财经新闻", data)

    if tool == "ifind_edb":
        r = await mcp.call_tool("fetch_edb", {
            "query": f"{hint}，时间范围：{time_scope}".strip(",时间范围：无法判断"),
        })
        data = _maybe_json(_outer_json(r["text"]).get("data"))
        return _fallback(tool, "同花顺iFinD·宏观行业数据", data)

    return _fallback(tool, tool, None)
