from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..config import LLM_PRESENCE_PENALTY, PRODUCT_FORMATTER_MAX_OUTPUT_TOKENS
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher


logger = setup_logger("product_filter_format_agent", "agent.log")


def create_product_filter_format_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
You are product_filter_format_agent. Transform {product_filter_content_result_json} into exactly one valid JSON object.
Use {product_filter_intent}. Do not call tools, perform SQL or product selection, or add facts not present in the supplied content result. For product_compare, copy the two products, compare values[0] with values[1] for every shared property, and group their prepared values into different and common sections.
Return exactly these keys: mode, message, resolved_product, clarification_options, products, attribute_name, attribute_column, attribute_values.
Return raw JSON only: no Markdown, no code fences, no comments, and no text before or after the object.
Use null for an absent resolved_product, [] for unused lists, and "" for unused attribute_name and attribute_column.
Write the final user-facing message in Russian.
For needs_clarification, message must contain only a short clarification question. Never copy, enumerate, or describe clarification_options in message; RootAgent renders those structured options.
For product_filter and product_compare, encode every line break as \\n in the JSON string and never replace line breaks with spaces.
Put every product, product heading, and property on its own line.
For product_filter, use one count header followed by one product per line in ascending code order.
For product_compare, use \\n\\n between the introduction, product blocks, and common-properties block.
Before returning, verify that the JSON parses, contains every required key, and that every product in products has its own line in message.
"""
    prompt_file = "product_filter_format_agent_prompt.md"
    agent = LlmAgent(
        name="product_filter_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        include_contents="none",
        output_key="product_filter_result_json",
        generate_content_config=GenerateContentConfig(
            temperature=0.0,
            presence_penalty=LLM_PRESENCE_PENALTY,
            max_output_tokens=PRODUCT_FORMATTER_MAX_OUTPUT_TOKENS,
        ),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
