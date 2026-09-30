# -*- coding: utf-8 -*-
"""iFinD 数据 MCP 客户端（Streamable HTTP）。

后端作为 MCP 客户端连接同花顺 iFinD 数据服务：
- 握手：POST initialize → 记录 Mcp-Session-Id → POST notifications/initialized；
- 调用：POST tools/call，兼容 application/json 与 text/event-stream 两种响应；
- 会话单例复用（服务端对"频繁新建会话"限流，会间歇返回 nginx 405）；
- 单次"请求-响应"加锁原子化，支持多任务组并行取证而不串流；
- 遇限流保持会话退避重试；仅当会话真正失效时才重新握手。

鉴权：Authorization 头直接放个人令牌（无 Bearer 前缀，以官方配置为准）。
"""
import json
import asyncio
import itertools
import httpx

from backend.config import settings


class MCPError(Exception):
    pass


# 会话 / 协议类 JSON-RPC 错误码（出现时重连）
_RECONNECT_CODES = {-32000, -32001, -32011, -32600}
# 限流 / 瞬时 HTTP 状态（保持会话退避重试）
_RATE_LIMIT_HTTP = {405, 408, 429, 502, 503, 504}
# 可能是会话失效的 HTTP 状态（重新握手）
_BAD_SESSION_HTTP = {400, 404}

_INIT_DELAYS = [5, 10, 20, 30, 45]
_CALL_DELAYS = [3, 6, 10]


class IFindMCPClient:
    def __init__(self, base_url: str = None, token: str = None):
        self.url = base_url or settings.IFIND_MCP_URL
        self.token = token or settings.IFIND_MCP_TOKEN
        self._client: httpx.AsyncClient | None = None
        self._sid: str | None = None
        self._ready = False
        self._init_lock = asyncio.Lock()   # 防止并发重复握手
        self._io_lock = asyncio.Lock()     # 串行单次请求-响应
        self._ids = itertools.count(100)

    # ------------------------------------------------------------------
    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=20.0))
        return self._client

    def _headers(self) -> dict:
        h = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._sid:
            h["Mcp-Session-Id"] = self._sid
        return h

    @staticmethod
    def _decode(r: httpx.Response):
        ct = r.headers.get("content-type", "")
        if "text/event-stream" in ct:
            final = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    d = line[5:].strip()
                    if not d:
                        continue
                    try:
                        msg = json.loads(d)
                    except json.JSONDecodeError:
                        continue
                    if "id" in msg:  # 只取带 id 的响应，忽略 notification
                        final = msg
            return final
        try:
            return r.json()
        except Exception:
            return None

    async def _post(self, payload: dict) -> httpx.Response:
        """单次 POST，加锁保证同会话请求-响应不被并行结果串扰。"""
        async with self._io_lock:
            c = await self._ensure_http()
            return await c.post(self.url, json=payload,
                                headers=self._headers())

    # ------------------------------------------------------------------
    async def initialize(self) -> dict:
        if self._ready:
            return {}
        async with self._init_lock:
            if self._ready:
                return {}
            init = {
                "jsonrpc": "2.0", "id": next(self._ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "thesis-verifier", "version": "1.0"},
                },
            }
            last = "unknown"
            for attempt in range(5):
                try:
                    r = await self._post(init)
                    if r.status_code == 200:
                        self._sid = r.headers.get("mcp-session-id")
                        msg = self._decode(r)
                        await self._post({
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                        })
                        self._ready = True
                        return msg or {}
                    last = f"HTTP {r.status_code}"
                except httpx.TransportError as e:
                    last = type(e).__name__
                if attempt < 4:
                    await asyncio.sleep(_INIT_DELAYS[attempt])
            raise MCPError(f"initialize 重试仍失败：{last}")

    async def _request(self, payload: dict) -> dict:
        if not self._ready:
            await self.initialize()
        reconnected = False
        for attempt in range(4):
            try:
                r = await self._post(payload)
            except httpx.TransportError:
                if attempt < 3:
                    await asyncio.sleep(_CALL_DELAYS[min(attempt, 2)])
                    continue
                raise

            if r.status_code == 200:
                msg = self._decode(r)
                if msg is not None and not msg.get("error"):
                    return msg
                err = (msg or {}).get("error", {})
                # 会话失效 → 重连一次后重试
                if not reconnected and err.get("code") in _RECONNECT_CODES:
                    reconnected = True
                    self._ready = False
                    await self.initialize()
                    continue
                raise MCPError(
                    f"MCP error {err.get('code')}: {err.get('message')}")

            # 限流 / 瞬时错误：保持会话退避重试
            if r.status_code in _RATE_LIMIT_HTTP and attempt < 3:
                await asyncio.sleep(_CALL_DELAYS[min(attempt, 2)])
                continue
            # 疑似会话失效：重新握手一次
            if r.status_code in _BAD_SESSION_HTTP and not reconnected:
                reconnected = True
                self._ready = False
                await self.initialize()
                continue
            raise MCPError(f"MCP HTTP {r.status_code}: {r.text[:200]}")
        raise MCPError("MCP 请求重试耗尽")

    # ------------------------------------------------------------------
    async def list_tools(self) -> list:
        msg = await self._request({
            "jsonrpc": "2.0", "id": next(self._ids),
            "method": "tools/list",
        })
        return msg.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        msg = await self._request({
            "jsonrpc": "2.0", "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = msg.get("result", {})
        content = result.get("content", [])
        texts = [x.get("text", "") for x in content
                 if x.get("type") == "text"]
        return {
            "ok": not result.get("isError", False),
            "name": name,
            "content": content,
            "text": "\n".join(texts),
        }

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._ready = False
        self._sid = None
