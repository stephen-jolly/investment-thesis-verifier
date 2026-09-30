"""应用配置：从环境变量读取 API Key 与模型设置。"""
import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))


class Settings:
    # 扶摇
    FUYAO_API_KEY: str = os.getenv("FUYAO_API_KEY", "")
    FUYAO_BASE_URL: str = "https://fuyao.aicubes.cn"

    # 火山引擎方舟
    ARK_API_KEY: str = os.getenv("ARK_API_KEY", "")
    ARK_BASE_URL: str = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    ARK_MODEL_MAIN: str = os.getenv("ARK_MODEL_MAIN", "doubao-seed-2-0-pro-260215")
    ARK_MODEL_LITE: str = os.getenv("ARK_MODEL_LITE", "doubao-seed-2-0-lite-260215")

    # iFinD MCP
    IFIND_MCP_TOKEN: str = os.getenv("IFIND_MCP_TOKEN", "")
    IFIND_MCP_URL: str = os.getenv(
        "IFIND_MCP_URL",
        "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-mcp")


settings = Settings()
