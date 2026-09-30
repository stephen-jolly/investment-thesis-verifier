# -*- coding: utf-8 -*-
"""在线冒烟测试：对已部署的 Render 服务实跑，验证 MCP 证据与边界。

1. 新命题：流式收集 Markdown 报告，检查结论与 iFinD 来源；
2. 无关问题：应判 out_of_scope 并引导。
"""
import json
import sys
import httpx

BASE = "https://investment-thesis-verifier.onrender.com"


async def chat(client, message, conv_id=None):
    payload = {"message": message}
    if conv_id:
        payload["conversation_id"] = conv_id
    parts, intent, cid = [], None, conv_id
    statuses = []
    async with client.stream("POST", BASE + "/api/chat", json=payload) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[len("data:"):].strip())
            t = ev.get("type")
            if t == "session":
                cid = ev["conversation_id"]
            elif t == "meta":
                intent = ev.get("intent")
            elif t == "status":
                statuses.append(ev.get("label"))
            elif t == "answer_delta":
                parts.append(ev.get("text", ""))
            elif t == "done":
                meta = ev["data"]["meta"]
                print("  总耗时ms:", meta.get("total_elapsed_ms"))
    return cid, intent, "".join(parts), statuses


async def main():
    async with httpx.AsyncClient(timeout=400.0) as client:
        print("=== 1. 在线验证新命题 ===")
        cid, intent, md, statuses = await chat(
            client, "贵州茅台2023年盈利增长主要来自主营业务")
        print("intent:", intent)
        print("---- 报告 ----")
        print(md)
        assert intent == "new_thesis"
        assert "结论" in md and len(md) > 50, "报告不应为空"
        has_ifind = ("iFinD" in md or "同花顺" in md)
        print("\n>>> 是否包含 iFinD 来源:", has_ifind)

        print("\n=== 2. 无关问题（应拦截）===")
        _, intent2, md2, _ = await chat(client, "帮我写一首诗", cid)
        print("intent:", intent2)
        print(md2)
        assert intent2 == "out_of_scope", f"期望 out_of_scope，实际 {intent2}"

        print("\n在线冒烟测试通过 ✅  (MCP来源=%s)" % has_ifind)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
