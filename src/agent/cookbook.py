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
import re
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


# --- Repo-layout targeting -------------------------------------------------------
#
# Recipe commands never assume a root-level project (that fails with MSB1003 on
# any repo whose .sln/.csproj live in subdirectories). Instead they carry the
# placeholders below, which the generator expands from DISCOVER's results.

BUILD_TARGET = "__TARGET__"
TEST_TARGET = "__TEST_TARGET__"


def resolve_build_targets(solution_file: Optional[str] = None,
                          project_files: Optional[list[str]] = None) -> list[str]:
    """The solution when there is one, else every discovered project.

    Empty means the layout is unknown — the caller must fall back to a
    find-based script, never to bare commands.
    """
    if solution_file:
        return [solution_file]
    return list(project_files or [])


def resolve_test_targets(solution_file: Optional[str] = None,
                         project_files: Optional[list[str]] = None) -> list[str]:
    """Test projects only; a lone solution serves when projects weren't listed."""
    tests = [p for p in (project_files or []) if "test" in p.lower()]
    if tests:
        return tests
    return [solution_file] if solution_file and not project_files else []


def expand_targets(lines: list[str], targets: list[str], placeholder: str = BUILD_TARGET,
                   empty: Optional[list[str]] = None) -> list[str]:
    """Expand each line containing ``placeholder`` once per target.

    With no targets, such lines are replaced by ``empty`` (or dropped when
    ``empty`` is None). Lines without the placeholder pass through untouched.
    """
    out: list[str] = []
    for line in lines:
        if placeholder in line:
            if targets:
                out.extend(line.replace(placeholder, t) for t in targets)
            elif empty is not None:
                out.extend(empty)
        else:
            out.append(line)
    return out


def expand_step_targets(steps: list[dict], targets: list[str],
                        empty: Optional[list[str]] = None) -> list[dict]:
    """Expand ``BUILD_TARGET`` inside the ``run`` bodies of a step list."""
    out: list[dict] = []
    for step in steps:
        if "run" in step and BUILD_TARGET in str(step["run"]):
            step = dict(step)
            step["run"] = "\n".join(
                expand_targets(str(step["run"]).splitlines(), targets, empty=empty)
            )
        out.append(step)
    return out


# --- Sonar targeting -------------------------------------------------------------
#
# The Sonar recipe carries __SONAR_PROJECT_KEY__ / __SONAR_ORG__ markers that MUST
# be resolved before the workflow ships — an unresolved __SONAR_ORG__ reaches
# SonarCloud verbatim and 404s the quality-profile lookup (pre-processing fails).

SONAR_PROJECT_KEY = "__SONAR_PROJECT_KEY__"
SONAR_ORG = "__SONAR_ORG__"


def resolve_sonar_steps(steps: list[dict], project_key: str,
                        org: Optional[str] = None) -> list[dict]:
    """Fill the Sonar recipe's project-key / organization placeholders.

    - ``__SONAR_PROJECT_KEY__`` -> ``project_key`` (always required; the generator
      derives it per-repo so it is never blank).
    - ``/o:"__SONAR_ORG__"`` -> ``/o:"<org>"`` when an organization is known
      (SonarCloud). When it isn't (self-hosted SonarQube has no organization) the
      whole ``/o:`` argument is dropped rather than passed empty.

    Returns new step dicts; the inputs are not mutated.
    """
    out: list[dict] = []
    for step in steps:
        run = step.get("run")
        if run is not None and ("__SONAR_" in str(run)):
            step = dict(step)
            run = str(run).replace(SONAR_PROJECT_KEY, project_key)
            if org:
                run = run.replace(SONAR_ORG, org)
            else:
                # Drop the /o: flag (and its leading whitespace) entirely.
                run = re.sub(r'\s*/o:"' + re.escape(SONAR_ORG) + r'"', "", run)
            step["run"] = run
        out.append(step)
    return out


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
