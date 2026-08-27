"""Sonar targeting — the __SONAR_ORG__ 404 regression suite.

The Sonar recipe carries __SONAR_PROJECT_KEY__ / __SONAR_ORG__ markers. If they
ship unresolved, `dotnet-sonarscanner begin` sends the literal `__SONAR_ORG__`
to SonarCloud and pre-processing fails (quality-profile lookup 404s). Generation
must resolve them to real values, and refuse to emit anything that still holds a
`__…__` placeholder.
"""
import re

from unittest.mock import MagicMock

import agent.tools.tools as tools_mod
from agent.cookbook import resolve_sonar_steps
from agent.pipeline import run_ci_pipeline
from agent.tools.tools import GenerateGitHubActionsWorkflow

PROJECTS = ["src/SampleApi/SampleApi.csproj", "tests/SampleApi.Tests/SampleApi.Tests.csproj"]


def _generate(**kwargs):
    res = GenerateGitHubActionsWorkflow().run(
        project_name="App", target_framework=".NET 8", project_files=PROJECTS, **kwargs
    )
    assert res["status"] == "generated", res
    return res["workflow_yaml"]


def test_no_unresolved_placeholder_ever_ships():
    """The whole point: a generated workflow carries no __…__ token."""
    yaml_text = _generate(sonar_org="my-org")
    assert not re.search(r"__[A-Z0-9_]+__", yaml_text), "unresolved placeholder in output"


def test_org_inlined_when_known():
    yaml_text = _generate(sonar_project_key="my-org_app", sonar_org="my-org")
    assert '/k:"my-org_app"' in yaml_text
    assert '/o:"my-org"' in yaml_text
    assert "__SONAR_ORG__" not in yaml_text


def test_org_flag_dropped_when_unknown():
    """Self-hosted SonarQube has no organization — /o: must be omitted, not empty."""
    yaml_text = _generate(sonar_project_key="app", sonar_org=None)
    assert "/o:" not in yaml_text
    assert '/k:"app"' in yaml_text


def test_project_key_defaulted_from_name_when_absent():
    yaml_text = _generate()  # no sonar_project_key
    assert '/k:"App"' in yaml_text


def test_host_url_comes_from_secret_with_sonarcloud_default():
    yaml_text = _generate(sonar_org="my-org")
    assert '/d:sonar.host.url="${SONAR_HOST_URL:-https://sonarcloud.io}"' in yaml_text


def test_guard_refuses_stray_placeholder(monkeypatch):
    """If any recipe step still holds a placeholder the resolver doesn't cover,
    generation errors out instead of shipping it."""
    recipe = tools_mod.get_recipe("dotnet")
    recipe["sonar_steps"] = recipe["sonar_steps"] + [
        {"name": "Bad step", "run": 'echo "__MYSTERY_TOKEN__"'}
    ]
    monkeypatch.setattr(tools_mod, "get_recipe", lambda *a, **k: recipe)
    res = GenerateGitHubActionsWorkflow().run(
        project_name="App", target_framework=".NET 8",
        project_files=PROJECTS, sonar_org="my-org",
    )
    assert res["status"] == "error"
    assert res["unresolved_placeholders"] == ["__MYSTERY_TOKEN__"]


def test_resolve_sonar_steps_unit():
    steps = [
        {"name": "begin", "run": 'x begin /k:"__SONAR_PROJECT_KEY__" /o:"__SONAR_ORG__" /d:a'},
        {"name": "build", "run": "dotnet build"},
    ]
    with_org = resolve_sonar_steps(steps, "the-key", "the-org")
    assert '/k:"the-key"' in with_org[0]["run"]
    assert '/o:"the-org"' in with_org[0]["run"]
    assert with_org[1]["run"] == "dotnet build"  # untouched
    # inputs not mutated
    assert "__SONAR_ORG__" in steps[0]["run"]

    without_org = resolve_sonar_steps(steps, "the-key", None)
    assert '/o:' not in without_org[0]["run"]
    assert without_org[0]["run"] == 'x begin /k:"the-key" /d:a'


def test_pipeline_derives_key_and_passes_org():
    discover = MagicMock()
    discover.run.return_value = {
        "target_framework": ".NET 8", "build_tool": ".NET CLI",
        "project_files": PROJECTS, "solution_file": None,
        "docker_support": True, "helm_support": True, "existing_ci_pipeline": False,
    }
    generate = MagicMock()
    generate.run.return_value = {
        "status": "generated",
        "workflow_yaml": "name: x\non: {}\njobs: {build: {}, test: {}, sonarqube: {}, security: {}}",
        "workflow_path": ".github/workflows/ci.yml",
    }
    validate = MagicMock()
    validate.run.return_value = {"valid": True}

    run_ci_pipeline(
        repo_url="https://github.com/Komali612/netcore-sample-app.git",
        github_token="dummy",
        options={"open_pr": False, "set_secrets": False},
        pipeline_secrets={"SONAR_ORG": "komali-org", "SONAR_TOKEN": "t"},
        repo=MagicMock(),
        discover_tool=discover, generate_tool=generate, validate_tool=validate,
    )
    kwargs = generate.run.call_args.kwargs
    assert kwargs["sonar_project_key"] == "Komali612_netcore-sample-app"
    assert kwargs["sonar_org"] == "komali-org"
