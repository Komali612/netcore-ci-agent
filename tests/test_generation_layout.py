"""Layout-aware generation — the MSB1003 regression suite.

A generated workflow must never run bare ``dotnet restore``/``build``/``test``:
those assume a root-level project or solution and fail with MSB1003 on any repo
whose .sln/.csproj live in subdirectories (e.g. netcore-sample-app, whose
projects sit under src/ and tests/). Generation must target the discovered
layout explicitly, or fall back to a find-based script when the layout is
unknown.
"""
import re

from unittest.mock import MagicMock

from agent.cookbook import resolve_build_targets, resolve_test_targets
from agent.pipeline import run_ci_pipeline
from agent.tools.tools import GenerateGitHubActionsWorkflow

SAMPLE_PROJECTS = [
    "src/SampleApi/SampleApi.csproj",
    "tests/SampleApi.Tests/SampleApi.Tests.csproj",
]


def _generate(**kwargs):
    res = GenerateGitHubActionsWorkflow().run(
        project_name="App", target_framework=".NET 8", **kwargs
    )
    assert res["status"] == "generated"
    assert res["generation_source"] == "cookbook"
    return res["workflow_yaml"]


def _assert_no_bare_dotnet(yaml_text: str):
    """No restore/build/test command may appear without an explicit target
    (a path argument or a shell variable from the find-based fallback)."""
    for line in yaml_text.splitlines():
        cmd = line.strip()
        m = re.match(r"dotnet (restore|build|test)(\s|$)", cmd)
        if m:
            rest = cmd[len("dotnet " + m.group(1)):].strip()
            assert rest.startswith(('"', "'")), f"bare command: {cmd}"


def test_targets_each_project_when_no_solution():
    yaml_text = _generate(project_files=SAMPLE_PROJECTS)
    assert 'dotnet restore "src/SampleApi/SampleApi.csproj"' in yaml_text
    assert 'dotnet restore "tests/SampleApi.Tests/SampleApi.Tests.csproj"' in yaml_text
    assert 'dotnet build "src/SampleApi/SampleApi.csproj" --configuration Release --no-restore' in yaml_text
    _assert_no_bare_dotnet(yaml_text)


def test_solution_takes_precedence():
    yaml_text = _generate(project_files=SAMPLE_PROJECTS, solution_file="DotnetService.sln")
    assert 'dotnet restore "DotnetService.sln"' in yaml_text
    assert 'dotnet build "DotnetService.sln" --configuration Release --no-restore' in yaml_text
    # Sonar's wrapped build must target the solution too.
    assert 'dotnet build "DotnetService.sln" --configuration Release\n' in yaml_text + "\n"
    _assert_no_bare_dotnet(yaml_text)


def test_test_job_targets_only_test_projects_and_never_no_build():
    yaml_text = _generate(project_files=SAMPLE_PROJECTS)
    assert 'dotnet test "tests/SampleApi.Tests/SampleApi.Tests.csproj"' in yaml_text
    assert 'dotnet test "src/SampleApi/SampleApi.csproj"' not in yaml_text
    # The test job runs on its own runner where nothing has been built.
    assert "--no-build" not in yaml_text


def test_no_test_projects_becomes_skip_notice():
    yaml_text = _generate(project_files=["src/SampleApi/SampleApi.csproj"])
    assert 'echo "No test projects found"' in yaml_text
    assert "dotnet test" not in yaml_text


def test_unknown_layout_falls_back_to_find_script():
    yaml_text = _generate()
    assert "SLN=$(find . -name '*.sln' -print -quit)" in yaml_text
    assert "TESTS=$(find . -name '*.csproj' | grep -i test || true)" in yaml_text
    _assert_no_bare_dotnet(yaml_text)


def test_resolvers():
    assert resolve_build_targets("a.sln", ["p.csproj"]) == ["a.sln"]
    assert resolve_build_targets(None, SAMPLE_PROJECTS) == SAMPLE_PROJECTS
    assert resolve_build_targets(None, None) == []
    assert resolve_test_targets(None, SAMPLE_PROJECTS) == [SAMPLE_PROJECTS[1]]
    assert resolve_test_targets("a.sln", None) == ["a.sln"]
    assert resolve_test_targets("a.sln", ["src/App/App.csproj"]) == []


def test_pipeline_threads_layout_into_generate():
    discover = MagicMock()
    discover.run.return_value = {
        "target_framework": ".NET 8",
        "build_tool": ".NET CLI",
        "project_files": SAMPLE_PROJECTS,
        "solution_file": None,
        "docker_support": True,
        "helm_support": True,
        "existing_ci_pipeline": False,
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
        repo_url="https://github.com/komali/netcore-sample-app.git",
        github_token="dummy",
        options={"open_pr": False, "set_secrets": False},
        repo=MagicMock(),
        discover_tool=discover, generate_tool=generate, validate_tool=validate,
    )
    kwargs = generate.run.call_args.kwargs
    assert kwargs["project_files"] == SAMPLE_PROJECTS
    assert kwargs["solution_file"] is None
