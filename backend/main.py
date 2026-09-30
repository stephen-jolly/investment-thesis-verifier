"""FastAPI 入口：对话式（SSE）命题验证 API + 前端静态文件托管。"""
import os
import json
import uuid
import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.agents.pipeline import stream_pipeline
from backend.agents.llm_client import chat_json, chat_stream
from backend.prompts.templates import (
    FOLLOWUP_SYSTEM, FOLLOWUP_USER,
)
from backend.config import settings

app = FastAPI(title="投资命题与多证据验证（对话版）", version="2.0.0")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIR = os.path.join(_BASE_DIR, "frontend")

# 内存会话存储（Render 单实例足够；重启清空，前端 localStorage 另存列表）
SESSIONS: dict = {}


def _get_session(conv_id: str | None):
    if not conv_id or conv_id not in SESSIONS:
        conv_id = conv_id or str(uuid.uuid4())
        SESSIONS[conv_id] = {
            "messages": [],       # [{role:'user'|'assistant', content}]
            "last_result": None,  # 最近一次完整验证结果（内部溯源/测试）
            "last_markdown": None,  # 最近一次给用户看的 Markdown 报告
            "title": None,
        }
    return conv_id, SESSIONS[conv_id]


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


# ---------------------------------------------------------------------------
# 意图路由与上下文摘要
# ---------------------------------------------------------------------------
INTENT_SYSTEM = """你是对话意图分类器。判断用户这句话属于以下哪一类：
- new_thesis：要验证一个新的投资命题（对某公司/行业的财务、估值、行情、盈利能力等判断）；
- followup：针对上一次研究结果继续追问或讨论（仍与投资研究相关）；
- out_of_scope：与投资研究无关的内容，例如询问今天星期几/日期天气、闲聊、让写代码、通用常识问答等。
只输出 JSON：{"intent": "new_thesis / followup / out_of_scope"}"""


async def _classify_intent(message: str, has_last: bool) -> str:
    try:
        r = await chat_json(
            INTENT_SYSTEM, f"用户这句话：{message}",
            model=settings.ARK_MODEL_LITE, temperature=0.0,
        )
        intent = r.get("intent", "")
        if intent in ("new_thesis", "followup", "out_of_scope"):
            # 没有历史结果时，followup 不成立，按新命题兜底
            if intent == "followup" and not has_last:
                return "new_thesis"
            return intent
    except Exception:
        pass
    # 兜底
    return "followup" if has_last else "new_thesis"


def _summarize_prior(full: dict) -> str:
    """从完整结果提取追问所需的关键上下文（控制长度）。"""
    dec = full.get("decomposition", {})
    target = dec.get("target", {})
    sub_lines = []
    for sr in full.get("sub_results", []):
        an = sr.get("analysis", {})
        sq = sr.get("sub_question", {})
        sub_lines.append(
            f"- 子问题「{sq.get('question')}」结论：{an.get('summary_verdict')}"
            f"（{an.get('summary_reason')}）"
        )
    concl = full.get("conclusion", {})
    return json.dumps({
        "标的": f"{target.get('name')} {target.get('thscode')}",
        "核心主张": dec.get("core_claim"),
        "子问题验证": sub_lines,
        "总体判断": concl.get("verdict"),
        "置信度": concl.get("confidence"),
        "结论摘要": concl.get("summary"),
        "关键翻转条件": [
            f.get("condition") for f in concl.get("flip_conditions", [])
        ],
    }, ensure_ascii=False)[:6000]


# ---------------------------------------------------------------------------
# SSE 事件生产
# ---------------------------------------------------------------------------
OUT_OF_SCOPE_REPLY = (
    "我只专注于帮你**验证投资命题**，例如「贵州茅台2023年盈利增长主要来自主营业务」"
    "这类判断——我会把它拆开、调取真实数据，并给出可追溯的结论。\n\n"
    "你可以换一个与投资研究相关的问题，或直接在下方输入一个投资命题试试。"
)


async def _produce_events(message: str, session: dict, queue: asyncio.Queue):
    """把一次对话的全部事件放入队列。"""
    put = queue.put
    try:
        intent = await _classify_intent(
            message, session.get("last_result") is not None)
        await put({"type": "meta", "intent": intent})

        if intent == "out_of_scope":
            # 无关问题：不调用数据，流式给出引导
            await put({"type": "status", "stage": "out_of_scope",
                       "label": "该问题超出我的服务范围"})
            for i in range(0, len(OUT_OF_SCOPE_REPLY), 3):
                await put({"type": "answer_delta",
                           "text": OUT_OF_SCOPE_REPLY[i:i + 3]})
                await asyncio.sleep(0.015)

        elif intent == "new_thesis":
            report_md = []
            async for ev in stream_pipeline(message):
                if ev["type"] == "answer_delta":
                    report_md.append(ev["text"])
                await put(ev)
                if ev["type"] == "done":
                    md = "".join(report_md)
                    ev["data"]["report_markdown"] = md
                    session["last_result"] = ev["data"]
                    session["last_markdown"] = md
                    if not session["title"]:
                        session["title"] = message[:24]

        else:
            # 追问：基于已有研究，真流式输出 Markdown
            full = session["last_result"]
            user_prompt = FOLLOWUP_USER.format(
                thesis=full.get("thesis"),
                prior_result=_summarize_prior(full),
                question=message,
            )
            await put({"type": "status", "stage": "followup",
                       "label": "正在结合已有研究结果思考…"})
            messages = [
                {"role": "system", "content": FOLLOWUP_SYSTEM},
                {"role": "user", "content": user_prompt},
            ]
            async for piece in chat_stream(
                messages, model=settings.ARK_MODEL_MAIN, temperature=0.3
            ):
                await put({"type": "answer_delta", "text": piece})
            await put({"type": "followup_done"})
    except Exception as e:
        await put({"type": "error",
                   "message": f"处理异常：{type(e).__name__}: {str(e)[:200]}"})
    finally:
        # 记录消息历史
        session["messages"].append({"role": "user", "content": message})
        await put({"type": "stream_end"})


@app.post("/api/chat")
async def chat(req: ChatRequest):
    message = (req.message or "").strip()
    if not message:
        async def empty():
            ev = {"type": "error", "message": "消息不能为空"}
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")
    if len(message) > 800:
        async def too_long():
            ev = {"type": "error", "message": "消息过长，请控制在 800 字以内"}
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        return StreamingResponse(too_long(), media_type="text/event-stream")

    conv_id, session = _get_session(req.conversation_id)
    queue: asyncio.Queue = asyncio.Queue()

    producer = asyncio.create_task(
        _produce_events(message, session, queue))

    async def event_stream():
        # 首帧下发会话 id
        first = {"type": "session", "conversation_id": conv_id}
        yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
        hb = 0
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                hb += 1
                yield f"data: {json.dumps({'type':'ping','n':hb}, ensure_ascii=False)}\n\n"
                continue
            if ev.get("type") == "stream_end":
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        await producer

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "fuyao_configured": bool(settings.FUYAO_API_KEY),
        "ark_configured": bool(settings.ARK_API_KEY),
        "model_main": settings.ARK_MODEL_MAIN,
    }


@app.get("/api/conversation/{conv_id}")
async def get_conversation(conv_id: str):
    """历史会话回放：返回该会话最近一次给用户看的 Markdown 报告。"""
    session = SESSIONS.get(conv_id)
    if not session or not session.get("last_markdown"):
        return {"ok": False, "markdown": None, "thesis": None}
    return {
        "ok": True,
        "markdown": session["last_markdown"],
        "thesis": session.get("title"),
    }


# 静态文件托管（放在 API 路由之后注册）
@app.get("/")
async def index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")
