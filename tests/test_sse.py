# -*- coding: utf-8 -*-
"""测试 SSE /api/chat（简洁对话版）。

三轮：
1. 新命题：轻量 status → report_start → answer_delta(Markdown 流式) → done
2. 追问：status → answer_delta(Markdown 真流式) → followup_done
3. 无关问题：intent=out_of_scope，仅给引导，不调用数据
"""
import json
import httpx

URL = "http://127.0.0.1:8000/api/chat"


async def run(message, conv_id=None, tag=""):
    payload = {"message": message}
    if conv_id:
        payload["conversation_id"] = conv_id
    parts, intent, cid = [], None, conv_id

    async with httpx.AsyncClient(timeout=400.0) as client:
        async with client.stream("POST", URL, json=payload) as resp:
            print(f"[{tag}] HTTP", resp.status_code)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[len("data:"):].strip())
                t = ev.get("type")
                if t == "session":
                    cid = ev["conversation_id"]
                    print("  conversation_id:", cid)
                elif t == "meta":
                    intent = ev.get("intent")
                    print("  intent:", intent)
                elif t == "status":
                    print("  ·", ev.get("label"))
                elif t == "report_start":
                    print("  ·（开始撰写报告）")
                elif t == "answer_delta":
                    parts.append(ev.get("text", ""))
                elif t == "followup_done":
                    print("  ·（追问完成）")
                elif t == "error":
                    print("  [错误]", ev.get("message"))
                elif t == "done":
                    print("  [完成] 总耗时ms:",
                          ev["data"]["meta"]["total_elapsed_ms"])

    md = "".join(parts)
    print("  ---- 回答正文 ----")
    print(md)
    print()
    return cid, intent, md


async def main():
    print("=== 第1轮：验证新命题 ===")
    cid, intent, md = await run(
        "贵州茅台2023年盈利增长主要来自主营业务", tag="新命题")
    assert intent == "new_thesis"
    assert len(md) > 50, "报告不应为空"

    print("=== 第2轮：追问（同一会话）===")
    cid, intent2, md2 = await run(
        "接下来我应该重点监控哪些指标？", cid, tag="追问")
    assert intent2 == "followup"
    assert len(md2) > 20

    print("=== 第3轮：无关问题（应识别为 out_of_scope）===")
    cid, intent3, md3 = await run("今天星期几？", cid, tag="无关问题")
    assert intent3 == "out_of_scope", f"期望 out_of_scope，实际 {intent3}"
    assert "投资命题" in md3, "应引导回投资命题"

    print("全部断言通过 ✅")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
