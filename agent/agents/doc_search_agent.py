from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TOKEN, MCP_TIMEOUT_SEC
from ..helpers import load_prompt

logger = setup_logger("doc_search_agent", "agent.log")


def create_doc_search_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для поиска документов с подключением MCP tools.
    """
    tools = []

    if KBSEARCH_MCP_URL:
        try:
            headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else None

            kbsearch_toolset = McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=KBSEARCH_MCP_URL,
                    headers=headers,
                    timeout=MCP_TIMEOUT_SEC,
                ),
                tool_filter=["kb_search"],
            )

            tools.append(kbsearch_toolset)
            logger.info(f"✓ MCP kbsearch подключен к doc_search_agent: {KBSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(f"✗ Ошибка подключения MCP kbsearch: {e}", exc_info=True)
    else:
        logger.warning("⚠ KBSEARCH_MCP_URL не задан — MCP kbsearch не подключён к doc_search_agent")

    return LlmAgent(
        name="doc_search_agent",
        model=model,
        instruction=load_prompt(
            "doc_search_agent-prompt.md",
            "Ты — ИИ-ассистент для поиска по базе знаний.",
        ),
        tools=tools,
        output_key="doc_search_final_text",
    )