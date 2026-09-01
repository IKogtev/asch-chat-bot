from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger

from ..config import ADVISOR_MAX_OUTPUT_TOKENS, LLM_PRESENCE_PENALTY
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher


logger = setup_logger("advisor_format_agent", "agent.log")


def create_advisor_format_agent(model: LiteLlm) -> LlmAgent:
    """Создает no-tool format agent с детерминированной температурой 0.0.

    Агент получает уже проверенный ranking result и может только объяснить его.
    Контракт после LLM дополнительно проверяет тип клиента, полные идентичности
    продуктов и исходный порядок TOP.
    """
    fallback = """
You are advisor_format_agent. Transform {advisor_ranking_result_json} into exactly one valid JSON object and write the user-facing message in Russian.
Do not call tools, retrieve data, select a client type, filter products, calculate scores, or add facts. Preserve primary_client_type and every TOP product's code, name, and is_active exactly and in the supplied order. Never insert, remove, replace, or reorder products.
For recommendation mode, explain why each option fits, include verified score components and compromises when present, and state that these are options for manager review. For needs_clarification, return only one short question. For no_data, clearly state the verified blocking reason without presenting a recommendation.
Return exactly these keys: mode, message, primary_client_type, products. Use null for an absent client type and [] when there are no products. Return raw JSON only, without Markdown fences, comments, or surrounding text.
"""
    prompt_file = "advisor_format_agent_prompt.md"
    agent = LlmAgent(
        name="advisor_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        include_contents="none",
        output_key="advisor_result_json",
        generate_content_config=GenerateContentConfig(
            temperature=0.0,
            presence_penalty=LLM_PRESENCE_PENALTY,
            max_output_tokens=ADVISOR_MAX_OUTPUT_TOKENS,
        ),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
