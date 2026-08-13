from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..config import LLM_MAX_OUTPUT_TOKENS, LLM_PRESENCE_PENALTY
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher


logger = setup_logger("product_info_format_agent", "agent.log")


def create_product_info_format_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
You are product_info_format_agent. Transform {product_info_content_result_json} into exactly one valid JSON object.
Use {product_info_intent}. Do not call tools, perform SQL or product selection, calculate new values, or add facts not present in the supplied content result.
Return exactly these keys: mode, message, resolved_product, clarification_options.
Return raw JSON only: no Markdown, no code fences, no comments, and no text before or after the object.
Use null for an absent resolved_product and [] for unused clarification_options.
Write a non-empty final user-facing message in Russian.
For product_card, put every field on its own line, encode every line break as \\n in the JSON string, and never replace line breaks with spaces.
For product_kit use: Комплект для продукта «<name>».
Before returning, verify that the JSON parses, contains every required key, and that product_card fields are separated by \\n.
"""
    prompt_file = "product_info_format_agent_prompt.md"
    agent = LlmAgent(
        name="product_info_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        include_contents="none",
        output_key="product_info_result_json",
        generate_content_config=GenerateContentConfig(
            temperature=0.0,
            presence_penalty=LLM_PRESENCE_PENALTY,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        ),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
