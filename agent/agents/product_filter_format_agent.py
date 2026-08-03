from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from .product_filter_contract import ProductFilterResponseSchema


logger = setup_logger("product_filter_format_agent", "agent.log")


def create_product_filter_format_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
You are product_filter_format_agent. Transform {product_filter_content_result_json} into one final JSON object matching the response schema.
Use {product_filter_intent} and {product_filter_format_correction}. Do not call tools, perform SQL or product selection, calculate new values, or add facts not present in the supplied content result.
Write the final user-facing message in Russian and return JSON only. For product_filter, use one count header followed by one product per line in ascending code order; encode line breaks as \\n in the JSON string.
"""
    prompt_file = "product_filter_format_agent_prompt.md"
    # Formatting stays tool-free so ADK schema control is isolated from tool calls.
    agent = LlmAgent(
        name="product_filter_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        output_key="product_filter_result_json",
        output_schema=ProductFilterResponseSchema,
        generate_content_config=GenerateContentConfig(temperature=0.0),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
