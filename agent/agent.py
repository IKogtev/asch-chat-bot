"""
Точка входа для Google ADK.
ADK требует наличия файла agent.py с root_agent.
"""

from .agent_v2 import root_agent

__all__ = ["root_agent"]