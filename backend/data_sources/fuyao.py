"""扶摇金融数据 API 封装。

每个函数返回统一结构：
{
  "ok": bool,            # 是否成功取到数据
  "tool": str,           # 工具名
  "source": str,         # 人类可读来源
  "data_time": str,      # 数据时点（人类可读）
  "data": dict/list,     # 原始业务数据
  "error": str/None,     # 失败时的错误信息
  "request_id": str      # 扶摇返回的追踪ID
}
"""
import time
import asyncio
import httpx
from datetime import datetime, timezone, timedelta

from backend.config import settings

_CST = timezone(timedelta(hours=8))
_BASE = settings.FUYAO_BASE_URL
_HEADERS = {"X-api-key": settings.FUYAO_API_KEY}
_TIMEOUT = 20.0


def _ms_to_date(ms):
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=_CST).strftime("%Y-%m-%d")
    except Exception:
        return None


def _wrap(tool, source, resp_json, error=None):
    """把扶摇响应信封转成统一结构。"""
    if error is not None:
        return {
            "ok": False, "tool": tool, "source": source,
            "data_time": None, "data": None, "error": error, "request_id": None,
        }
    code = resp_json.get("code", -1)
    if code != 0:
        return {
            "ok": False, "tool": tool, "source": source,
            "data_time": None, "data": None,
            "error": f"扶摇业务错误 code={code}: {resp_json.get('message', '')}",
            "request_id": resp_json.get("request_id"),
        }
    data = resp_json.get("data") or {}
    return {
        "ok": True, "tool": tool, "source": source,
        "data_time": _ms_to_date(data.get("timestamp")),
        "data": data,
        "error": None,
        "request_id": resp_json.get("request_id"),
    }


async def _get(client, path, params, tool, source, max_retries=3):
    """发起 GET，遇到限流（HTTP 429 或业务 code=429/4001）时指数退避重试。"""
    backoff = 2.0
    last_result = None
    for attempt in range(max_retries + 1):
        try:
            r = await client.get(f"{_BASE}{path}", params=params, headers=_HEADERS)
            # HTTP 层限流
            if r.status_code == 429:
                last_result = _wrap(tool, source, None, error="HTTP 429 限流")
            else:
                result = _wrap(tool, source, r.json())
                code = r.json().get("code")
                # 业务层限流：429 / 4001
                if code in (429, 4001) and attempt < max_retries:
                    last_result = result
                else:
                    return result
        except httpx.TimeoutException:
            last_result = _wrap(tool, source, None, error=f"请求超时（{_TIMEOUT}s）")
            # 超时也重试
            if attempt >= max_retries:
                return last_result
        except Exception as e:
            return _wrap(tool, source, None, error=f"请求异常: {type(e).__name__}: {e}")
        # 退避等待
        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2
    return last_result if last_result else _wrap(tool, source, None, error="重试次数耗尽")


# ---------------------------------------------------------------------------
# 标的检索
# ---------------------------------------------------------------------------
async def search_ticker(client, keyword):
    return await _get(
        client, "/api/meta/tickers/search", {"q": keyword},
        "ticker_search", "扶摇-标的检索",
    )


# ---------------------------------------------------------------------------
# 行情快照
# ---------------------------------------------------------------------------
async def get_snapshot(client, thscodes):
    return await _get(
        client, "/api/a-share/prices/snapshot", {"thscodes": thscodes},
        "price_snapshot", "扶摇-行情快照",
    )


# ---------------------------------------------------------------------------
# 历史 K 线（默认近 1 年日线，前复权）
# ---------------------------------------------------------------------------
async def get_history(client, thscode, days=365):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    return await _get(
        client, "/api/a-share/prices/historical",
        {
            "thscode": thscode, "interval": "1d",
            "start": start_ms, "end": end_ms, "adjust": "forward",
        },
        "price_history", "扶摇-历史K线",
    )


# ---------------------------------------------------------------------------
# 三大报表
# ---------------------------------------------------------------------------
async def get_income_statement(client, thscode, period="annual", limit=4):
    return await _get(
        client, "/api/a-share/financials/income-statements",
        {"thscode": thscode, "period": period, "limit": limit},
        "income_statement", "扶摇-利润表",
    )


async def get_balance_sheet(client, thscode, period="annual", limit=4):
    return await _get(
        client, "/api/a-share/financials/balance-sheets",
        {"thscode": thscode, "period": period, "limit": limit},
        "balance_sheet", "扶摇-资产负债表",
    )


async def get_cash_flow(client, thscode, period="annual", limit=4):
    return await _get(
        client, "/api/a-share/financials/cash-flow-statements",
        {"thscode": thscode, "period": period, "limit": limit},
        "cash_flow", "扶摇-现金流量表",
    )


# ---------------------------------------------------------------------------
# 财务指标
# ---------------------------------------------------------------------------
async def get_financial_indicators(client, thscode, period="annual", limit=4):
    """财务指标接口契约为 report={yyyy}-{1|2|3|4}（按单期查询）。

    数据覆盖可能为空；此处查询最近一期年报，空数据交由上层自然处理。
    """
    current_year = datetime.now(tz=_CST).year
    return await _get(
        client, "/api/a-share/financials/indicators",
        {"thscode": thscode, "report": f"{current_year - 1}-4"},
        "financial_indicators", "扶摇-财务指标",
    )


# 工具名 -> 处理函数 的映射，供 Agent 按拆解结果动态调用
TOOL_REGISTRY = {
    "income_statement": get_income_statement,
    "balance_sheet": get_balance_sheet,
    "cash_flow": get_cash_flow,
    "price_snapshot": get_snapshot,
    "price_history": get_history,
    "financial_indicators": get_financial_indicators,
}
