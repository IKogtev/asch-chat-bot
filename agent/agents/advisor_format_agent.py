from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger

from ..config import ADVISOR_MAX_OUTPUT_TOKENS, LLM_PRESENCE_PENALTY
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher


logger = setup_logger("advisor_format_agent", "agent.log")

ADVISOR_FORMAT_FALLBACK_PROMPT = """
You are advisor_format_agent. Transform {advisor_ranking_result_json} into exactly one valid JSON object and write the user-facing message in Russian.
Do not call tools, retrieve data, select a client type, filter products, calculate scores, or add facts. Preserve primary_client_type, client_age, and every top_products product's code, name, and is_active exactly and in the supplied order. Never insert, remove, replace, or reorder products. accepted_candidates outside top_products are internal data: never mention or display them. Mention the client's age only when client_age is not null, and explain age exclusions only from supplied excluded_candidates.
For recommendation mode, explain the verified fit and compromises, then present every product from top_products, and no other product, on its own line as one continuous numbered list. The displayed count must equal len(top_products) and therefore cannot exceed the configured TOP_N. Format each item exactly as "<list number>. <code> <name> (КВ <attributes.commission>%)" and preserve the supplied order. Use only the verified commission stored in the product attributes; do not omit the product code or KV. State that these are options for manager review. End with exactly: "Введи номер продукта, если хочешь посмотреть карточку или напиши если надо скачать комплект какого-то продукта." For needs_clarification, return only one short question. For no_data, clearly state the verified blocking reason without presenting a recommendation.
Return exactly these keys: mode, message, primary_client_type, products. Use null for an absent client type and [] when there are no products. Return raw JSON only, without Markdown fences, comments, or surrounding text.
"""


def create_advisor_format_agent(model: LiteLlm) -> LlmAgent:
    """Создает no-tool format agent с детерминированной температурой 0.0.

    Агент получает уже проверенный ranking result и может только объяснить его.
    Контракт после LLM дополнительно проверяет тип клиента, полные идентичности
    продуктов и исходный порядок итогового списка.
    """
    prompt_file = "advisor_format_agent_prompt.md"
    agent = LlmAgent(
        name="advisor_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, ADVISOR_FORMAT_FALLBACK_PROMPT),
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
