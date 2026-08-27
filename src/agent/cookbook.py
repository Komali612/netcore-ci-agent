"""Cookbook loader + renderers for netcore-ci-agent (see ARCHITECTURE.md §2.3).

This agent carries its OWN cookbook (``cookbook/cookbook.yaml`` at the repo root),
scoped to .NET Core only. GENERATE is cookbook-first: it pulls the per-stack step
bodies (setup / build / test / sonar) from the cookbook and renders them into the
workflow skeleton. The LLM / built-in path is used only as a fallback when no
cookbook recipe matches the discovered stack.

Deliberately has NO ``agent_core`` dependency so the rendering can be unit-tested
offline.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml


def find_cookbook() -> Optional[Path]:
    """Locate ``cookbook/cookbook.yaml``.

    Honours ``COOKBOOK_PATH`` if set; otherwise walks up from this module looking
    for a ``cookbook/cookbook.yaml`` (works from a src/ layout and when run in place).
    """
    override = os.getenv("COOKBOOK_PATH")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "cookbook" / "cookbook.yaml"
        if candidate.is_file():
            return candidate
    return None


def load_cookbook() -> Optional[dict]:
    """Load and parse the cookbook, or return None if it isn't present."""
    path = find_cookbook()
    if not path:
        return None
    with path.open() as fh:
        return yaml.safe_load(fh)


def get_recipe(build_system: str = "dotnet") -> Optional[dict]:
    """Return the recipe for a build system with its Sonar strategy resolved.

    Adds a ``sonar_steps`` key (the concrete steps for the recipe's ``sonar``
    strategy). Returns None when there is no cookbook or no matching recipe — the
    caller then falls back to the built-in path.
    """
    cb = load_cookbook()
    if not cb:
        return None
    recipe = (cb.get("cookbooks") or {}).get(build_system)
    if not recipe:
        return None
    recipe = dict(recipe)
    sonar_name = recipe.get("sonar")
    recipe["sonar_steps"] = (cb.get("sonar_strategies") or {}).get(sonar_name, []) if sonar_name else []
    return recipe


# --- YAML fragment renderers (GitHub Actions steps, 6-space step indent) --------

def _scalar(v: Any) -> str:
    """Render a YAML scalar; single-quote strings so versions like 8.0.x stay strings."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if "'" in s:
        return '"' + s.replace('"', '\\"') + '"'
    return f"'{s}'"


def render_steps(steps: list[dict], base: int = 6) -> str:
    """Render a list of step dicts (``name`` + either ``uses``/``with`` or ``run``)."""
    pad = " " * base
    out: list[str] = []
    for step in steps:
        out.append(f"{pad}- name: {_scalar(step['name'])}")
        if "uses" in step:
            out.append(f"{pad}  uses: {step['uses']}")
            if step.get("with"):
                out.append(f"{pad}  with:")
                for k, v in step["with"].items():
                    out.append(f"{pad}    {k}: {_scalar(v)}")
        if "run" in step:
            out.append(f"{pad}  run: |")
            for line in str(step["run"]).splitlines():
                out.append(f"{pad}    {line}")
    return "\n".join(out)


def render_run(name: str, cmds: Any, base: int = 6) -> str:
    """Render a single ``run:`` step from a command string or list of shell lines."""
    pad = " " * base
    lines = cmds if isinstance(cmds, list) else str(cmds).splitlines()
    body = "\n".join(f"{pad}    {ln}" for ln in lines)
    return f"{pad}- name: {_scalar(name)}\n{pad}  run: |\n{body}"
