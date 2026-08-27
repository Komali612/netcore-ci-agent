"""FROZEN — identical in every agent repo. Do not edit.

Assembles the agent from its prompt, its markdown skills, and its code tools.
Edit prompts/system.md, skills/*.md, and tools/tools.py instead.
"""
from pathlib import Path

from agent_core import Agent, load_skills

from .config import settings
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def build_agent() -> Agent:
    return Agent(
        name=settings.name,
        prompt=SYSTEM_PROMPT,
        skills=load_skills(_SKILLS_DIR),   # markdown docs
        tools=TOOLS,                        # code the LLM can call
        model=settings.model,               # set AGENT_MODEL to enable the brain
    )
