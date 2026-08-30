"""Single-job generation: one checkout, UI-driven runner, tool gating, no Helm.

Locks in the modifications requested for the demo:
- the whole pipeline is ONE job with exactly one actions/checkout;
- runs-on comes from the caller (orchestrator UI), defaulting to ubuntu-latest;
- a CI capability left unselected is emitted commented-out (visible but inert);
- Helm is gone entirely;
- no unresolved __PLACEHOLDER__ ever ships, in any selection state.
"""
import re

import yaml
from unittest.mock import MagicMock

from agent.pipeline import run_ci_pipeline, selected_caps
from agent.tools.tools import GenerateGitHubActionsWorkflow

PROJECTS = ["src/SampleApi/SampleApi.csproj", "tests/SampleApi.Tests/SampleApi.Tests.csproj"]


def _gen(**kwargs):
    res = GenerateGitHubActionsWorkflow().run(
        project_name="App", target_framework=".NET 8", project_files=PROJECTS, **kwargs
    )
    assert res["status"] == "generated", res
    return res["workflow_yaml"]


def test_single_job_single_checkout():
    wf = _gen(enabled_tools={"coverage": "SonarCloud", "registry": "GHCR"})
    doc = yaml.safe_load(wf)
    assert list(doc["jobs"].keys()) == ["ci"], "expected exactly one job named 'ci'"
    assert wf.count("actions/checkout") == 1, "expected exactly one checkout"


def test_runner_comes_from_caller():
    wf = _gen(runner_os="windows-latest", enabled_tools={})
    assert yaml.safe_load(wf)["jobs"]["ci"]["runs-on"] == "windows-latest"
    # a self-hosted label is free text and must pass through verbatim
    wf2 = _gen(runner_os="self-hosted-dotnet", enabled_tools={})
    assert yaml.safe_load(wf2)["jobs"]["ci"]["runs-on"] == "self-hosted-dotnet"


def test_runner_defaults_to_ubuntu_when_blank():
    for blank in ("", "   ", None):
        wf = _gen(runner_os=blank, enabled_tools={})
        assert yaml.safe_load(wf)["jobs"]["ci"]["runs-on"] == "ubuntu-latest"


def test_selected_tool_active_unselected_commented():
    wf = _gen(enabled_tools={"sast": "Fortify SSC"})
    # selected SAST is a real (uncommented) step
    assert "- name: 'Run SAST (Fortify SSC)'" in wf
    # unselected SCA appears only as a comment
    assert "# SCA - not selected in the orchestrator UI" in wf
    assert re.search(r"^\s+- name: 'Run SCA", wf, re.M) is None
    # and the active SAST step really is in the parsed step list
    names = [s.get("name") for s in yaml.safe_load(wf)["jobs"]["ci"]["steps"]]
    assert "Run SAST (Fortify SSC)" in names
    assert not any(n and n.startswith("Run SCA") for n in names)


def test_nothing_selected_leaves_only_core_steps():
    wf = _gen(enabled_tools={})
    names = [s.get("name") for s in yaml.safe_load(wf)["jobs"]["ci"]["steps"]]
    assert names == ["Checkout code", "Set up .NET", "Restore & build", "Run unit tests"]


def test_helm_is_gone():
    wf = _gen(enabled_tools={"coverage": "SonarCloud", "registry": "GHCR"})
    assert "helm" not in wf.lower()


def test_no_placeholder_ships_in_any_state():
    for et in ({}, {"sast": "Fortify"}, {"coverage": "SonarCloud", "registry": "GHCR"}, None):
        wf = _gen(enabled_tools=et)
        assert not re.search(r"__[A-Z0-9_]+__", wf), f"placeholder leaked with enabled_tools={et}"
        yaml.safe_load(wf)  # must always parse


def test_registry_block_active_only_when_selected():
    with_reg = _gen(enabled_tools={"registry": "GHCR"})
    # build+push via build-push-action with an SBOM attached by BuildKit
    assert "docker/build-push-action@v6" in with_reg
    assert "sbom: true" in with_reg
    assert re.search(r"^\s+- name: 'Build & push image", with_reg, re.M)
    without = _gen(enabled_tools={})
    assert re.search(r"^\s+- name: 'Build & push image", without, re.M) is None
    assert "# REGISTRY - not selected in the orchestrator UI" in without


def test_sbom_attached_via_buildkit_not_a_placeholder():
    """SBOM is generated+attached by BuildKit on push; the old echo placeholder
    and the manual docker build/push are gone."""
    wf = _gen(enabled_tools={"registry": "GHCR"})
    assert "sbom: true" in wf
    assert "docker/setup-buildx-action" in wf          # container driver for attestations
    assert "SBOM generation (placeholder" not in wf     # echo stub removed
    assert "docker push" not in wf                       # manual push replaced


def test_selected_caps_maps_ui_labels():
    ui = {
        "Code coverage & quality": {"tool": "SonarCloud", "Organization": "org"},
        "SAST (static analysis)": {"tool": "Fortify SSC"},
        "DAST (dynamic scan)": {"tool": ""},          # picked then blank -> ignored
        "Metrics / APM": {"tool": "Dynatrace"},
        "Log aggregation": {"tool": "Splunk"},         # same cap; first (metrics) wins
    }
    caps = selected_caps(ui)
    assert caps == {"coverage": "SonarCloud", "sast": "Fortify SSC", "monitoring": "Dynatrace"}
    assert selected_caps(None) == {} and selected_caps({}) == {}


def test_pipeline_threads_runner_and_tools_into_generate():
    discover = MagicMock()
    discover.run.return_value = {
        "target_framework": ".NET 8", "build_tool": ".NET CLI",
        "project_files": PROJECTS, "solution_file": None,
        "docker_support": True, "helm_support": True, "existing_ci_pipeline": False,
    }
    generate = MagicMock()
    generate.run.return_value = {
        "status": "generated",
        "workflow_yaml": "name: x\non: {}\njobs: {ci: {runs-on: ubuntu-latest, steps: [{name: a}]}}",
        "workflow_path": ".github/workflows/ci.yml",
    }
    validate = MagicMock(); validate.run.return_value = {"valid": True}

    run_ci_pipeline(
        repo_url="https://github.com/Komali612/netcore-sample-app.git",
        github_token="dummy",
        options={"open_pr": False, "set_secrets": False, "runner_os": "windows-latest"},
        selected_tools={"SAST (static analysis)": {"tool": "Fortify SSC"}},
        repo=MagicMock(),
        discover_tool=discover, generate_tool=generate, validate_tool=validate,
    )
    kwargs = generate.run.call_args.kwargs
    assert kwargs["runner_os"] == "windows-latest"
    assert kwargs["enabled_tools"] == {"sast": "Fortify SSC"}
