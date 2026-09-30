# -*- coding: utf-8 -*-
"""测试 SSE /api/chat：逐事件打印类型与关键信息。"""
import json
import httpx

URL = "http://127.0.0.1:8000/api/chat"


async def run(message, conv_id=None):
    payload = {"message": message}
    if conv_id:
        payload["conversation_id"] = conv_id
    async with httpx.AsyncClient(timeout=400.0) as client:
        async with client.stream("POST", URL, json=payload) as resp:
            print("HTTP", resp.status_code)
            cid = conv_id
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[len("data:"):].strip())
                t = ev.get("type")
                if t == "session":
                    cid = ev["conversation_id"]
                    print("  conversation_id:", cid)
                elif t == "meta":
                    print("  intent:", ev.get("intent"))
                elif t == "status":
                    print("  ·", ev.get("label"))
                elif t == "tool_start":
                    print("  ·", ev.get("label"))
                elif t == "decomposition":
                    d = ev["data"]
                    tg = d.get("target", {})
                    print("  [拆解] 标的:", tg.get("name"), tg.get("thscode"),
                          "子问题:", len(d.get("sub_questions", [])))
                elif t == "sub_results":
                    recs = ev["data"]
                    verdicts = [r["analysis"].get("summary_verdict")
                                for r in recs]
                    print(f"  [子问题组 {ev.get('tool')}] 结论:", verdicts)
                elif t == "conclusion":
                    c = ev["data"]
                    print("  [结论]:", c.get("verdict"),
                          "置信度:", c.get("confidence"))
                elif t == "answer_delta":
                    print(ev.get("text"), end="", flush=True)
                elif t == "followup_done":
                    print("\n  [追问完成] suggested_tool:",
                          ev.get("suggested_tool"))
                elif t == "ping":
                    pass
                elif t == "error":
                    print("  [错误]", ev.get("message"))
                elif t == "done":
                    print("  [完成] 总耗时ms:",
                          ev["data"]["meta"]["total_elapsed_ms"])
            return cid


async def main():
    print("=== 第1轮：验证新命题 ===")
    cid = await run("贵州茅台2023年盈利增长主要来自主营业务")
    print("\n=== 第2轮：追问（同一会话）===")
    await run("接下来我应该重点监控哪些指标？", cid)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
