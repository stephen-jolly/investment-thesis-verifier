"""FastAPI 入口：命题验证 API + 前端静态文件托管。"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.agents.pipeline import run_pipeline
from backend.agents.llm_client import chat_json
from backend.prompts.templates import FOLLOWUP_SYSTEM, FOLLOWUP_USER
from backend.config import settings

app = FastAPI(title="投资命题与多证据验证", version="1.0.0")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIR = os.path.join(_BASE_DIR, "frontend")


class AnalyzeRequest(BaseModel):
    thesis: str


class FollowupRequest(BaseModel):
    thesis: str
    prior_result: dict
    question: str


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "fuyao_configured": bool(settings.FUYAO_API_KEY),
        "ark_configured": bool(settings.ARK_API_KEY),
        "model_main": settings.ARK_MODEL_MAIN,
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    thesis = (req.thesis or "").strip()
    if not thesis:
        return {"error": "命题不能为空"}
    if len(thesis) > 500:
        return {"error": "命题过长，请控制在 500 字以内"}
    try:
        result = await run_pipeline(thesis)
        return {"ok": True, "result": result}
    except Exception as e:
        # 顶层兜底：任何未预期异常都返回结构化错误，不向前端抛 500 堆栈
        return {
            "ok": False,
            "error": f"分析链路异常：{type(e).__name__}: {str(e)[:300]}",
        }


@app.post("/api/followup")
async def followup(req: FollowupRequest):
    import json
    user_prompt = FOLLOWUP_USER.format(
        thesis=req.thesis,
        prior_result=json.dumps(req.prior_result, ensure_ascii=False)[:6000],
        question=req.question,
    )
    try:
        result = await chat_json(
            FOLLOWUP_SYSTEM, user_prompt,
            model=settings.ARK_MODEL_MAIN, temperature=0.3,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": f"追问处理失败：{type(e).__name__}: {str(e)[:200]}"}


# 静态文件托管（放在 API 路由之后注册）
@app.get("/")
async def index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")
