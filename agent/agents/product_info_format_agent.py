from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from .product_info_contract import ProductInfoResponseSchema


logger = setup_logger("product_info_format_agent", "agent.log")


def create_product_info_format_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
You are product_info_format_agent. Transform {product_info_content_result_json} into one final JSON object matching the response schema.
Use {product_info_intent} and {product_info_format_correction}. Do not call tools, perform SQL or product selection, calculate new values, or add facts not present in the supplied content result.
Write a non-empty final user-facing message in Russian and return JSON only. For product_kit use: Комплект для продукта «<name>».
"""
    prompt_file = "product_info_format_agent_prompt.md"
    # Formatting stays tool-free so ADK schema control is isolated from tool calls.
    agent = LlmAgent(
        name="product_info_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        output_key="product_info_result_json",
        output_schema=ProductInfoResponseSchema,
        generate_content_config=GenerateContentConfig(temperature=0.0),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
