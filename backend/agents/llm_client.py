"""火山引擎方舟（豆包）大模型调用封装。

职责：
1. 以 OpenAI 兼容格式调用 chat/completions；
2. 从模型输出中稳健地提取 JSON（去除 ```json 代码块包裹、处理前后多余文字）；
3. 统一错误处理。
"""
import json
import re
import httpx

from backend.config import settings


class LLMError(Exception):
    pass


def _extract_json(text: str):
    """从模型文本中提取 JSON 对象/数组。"""
    if text is None:
        raise LLMError("模型返回为空")
    text = text.strip()

    # 去除 markdown 代码块
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 兜底：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 数组形式
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise LLMError(f"无法从模型输出中解析 JSON。原始输出前500字：{text[:500]}")


async def chat_json(system_prompt: str, user_prompt: str, model: str = None,
                    temperature: float = 0.2, max_retries: int = 3):
    """调用大模型并把结果解析为 JSON（dict/list）。

    重试策略：
    - 网络错误（ConnectError / Timeout / DNS）：指数退避后用相同请求重试；
    - JSON 解析失败：在 prompt 末尾追加格式提醒后重试。
    """
    import asyncio
    model = model or settings.ARK_MODEL_MAIN
    url = f"{settings.ARK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }

    async def _do_call(usr_p):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": usr_p},
            ],
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code != 200:
                raise LLMError(f"ARK API HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            try:
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                raise LLMError(f"ARK 返回结构异常: {body}")
            return _extract_json(content)

    current_user = user_prompt
    backoff = 2.0
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await _do_call(current_user)
        except httpx.TransportError as e:
            # 连接 / DNS / 超时等网络问题：等待后用相同请求重试
            last_error = LLMError(f"网络错误：{type(e).__name__}: {e}")
        except LLMError as e:
            # JSON 解析 / HTTP 业务错误：追加格式提醒后重试
            last_error = e
            current_user = user_prompt + \
                "\n\n（提醒：请务必只输出合法 JSON，不要输出代码块标记或任何解释文字。）"
        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2
    raise last_error if last_error else LLMError("未知错误，重试耗尽")
