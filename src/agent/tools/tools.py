"""Tools for NetcoreCIAgent — the code the LLM can call.

This agent discovers .NET Core projects, generates GitHub Actions CI pipelines,
validates them, and opens PRs for human review.
"""
from agent_core import Tool
from ..cookbook import (
    TEST_TARGET,
    expand_step_targets,
    expand_targets,
    get_recipe,
    render_run,
    render_steps,
    resolve_build_targets,
    resolve_sonar_steps,
    resolve_test_targets,
)
import json
import re
import subprocess
import tempfile
import os
from pathlib import Path

# Layout-agnostic shell used whenever DISCOVER's layout is unavailable: locate
# the solution (else every project) at run time. Never emit a bare
# `dotnet restore`/`build` — it fails with MSB1003 on any repo whose
# .sln/.csproj live below the root.
FIND_BUILD_SH = [
    "SLN=$(find . -name '*.sln' -print -quit)",
    'if [ -n "$SLN" ]; then',
    '  dotnet build "$SLN" --configuration Release',
    "else",
    "  for p in $(find . -name '*.csproj'); do",
    '    echo "Building $p"',
    '    dotnet build "$p" --configuration Release',
    "  done",
    "fi",
]
FIND_TEST_SH = [
    "TESTS=$(find . -name '*.csproj' | grep -i test || true)",
    'if [ -z "$TESTS" ]; then echo "No test projects found"; exit 0; fi',
    "for p in $TESTS; do",
    '  echo "Testing $p"',
    '  dotnet test "$p" --configuration Release',
    "done",
]

# Optional CI capabilities, in the order their steps appear in the single CI job.
# 'coverage' is the Sonar block (rendered from the sonar strategy); the rest come
# from the cookbook's tool_steps. A capability that isn't selected in the UI is
# still emitted, but commented out.
OPTIONAL_CAPS = ["coverage", "sast", "sca", "dast", "imgscan", "artifact", "registry", "monitoring"]

# Fallback tool names used only when the caller passes no per-capability selection
# (e.g. the LLM/tool path or a unit test) — keeps behaviour close to the old
# always-on template. The real orchestrator run passes explicit selections.
DEFAULT_TOOL_NAME = {
    "coverage": "SonarQube", "sast": "Fortify", "sca": "Sonatype",
    "dast": "Fortify WebInspect", "imgscan": "Trivy", "artifact": "Nexus",
    "registry": "GHCR", "monitoring": "Dynatrace",
}


def comment_block(text: str) -> str:
    """Comment out a rendered YAML fragment, preserving each line's indentation.

    ``      - name: X``  ->  ``      # - name: X`` (matches the disabled-DAST look).
    Blank lines are left blank.
    """
    out = []
    for line in text.splitlines():
        if not line.strip():
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        out.append(f"{indent}# {line.lstrip()}")
    return "\n".join(out)


class DiscoverProject(Tool):
    """Analyze a .NET Core project repository to discover structure and configuration."""

    name = "discover_project"
    description = """Discover the .NET Core project structure, framework version, build configuration,
    and whether a CI pipeline already exists. Takes a Git repository URL and analyzes the repository."""
    parameters = {
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "Git repository URL (e.g., https://github.com/user/repo.git)"
            },
            "branch": {
                "type": "string",
                "description": "Branch to analyze (default: main)",
                "default": "main"
            }
        },
        "required": ["repo_url"],
    }

    def run(self, repo_url: str, branch: str = "main") -> dict:
        """Clone and analyze the repository."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Clone the repository
                clone_path = os.path.join(tmpdir, "repo")
                subprocess.run(
                    ["git", "clone", "--branch", branch, repo_url, clone_path],
                    check=True,
                    capture_output=True,
                    timeout=30
                )

                # Analyze project structure
                discovery_result = {
                    "repo_url": repo_url,
                    "branch": branch,
                    "project_files": [],
                    "solution_file": None,
                    "target_framework": None,
                    "build_tool": None,
                    "existing_ci_pipeline": False,
                    "sdk_version": None,
                    "docker_support": False,
                    "helm_support": False,
                    "analysis_status": "pending",
                    "errors": []
                }

                # Look for project files
                for root, dirs, files in os.walk(clone_path):
                    # Skip git and hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]

                    for file in files:
                        if file.endswith('.sln') and not discovery_result["solution_file"]:
                            discovery_result["solution_file"] = os.path.relpath(
                                os.path.join(root, file), clone_path
                            ).replace(os.sep, "/")

                        if file.endswith('.csproj'):
                            discovery_result["project_files"].append(
                                os.path.relpath(
                                    os.path.join(root, file), clone_path
                                ).replace(os.sep, "/")
                            )
                            # Parse for target framework (simplified)
                            try:
                                with open(os.path.join(root, file), 'r') as f:
                                    content = f.read()
                                    if 'net6' in content or 'net7' in content or 'net8' in content:
                                        if 'net8' in content:
                                            discovery_result["target_framework"] = ".NET 8"
                                        elif 'net7' in content:
                                            discovery_result["target_framework"] = ".NET 7"
                                        else:
                                            discovery_result["target_framework"] = ".NET 6"
                            except Exception as e:
                                discovery_result["errors"].append(f"Error reading {file}: {str(e)}")

                        if file == "Dockerfile":
                            discovery_result["docker_support"] = True

                        if file in ["Chart.yaml", "values.yaml"]:
                            discovery_result["helm_support"] = True

                # Check for existing CI pipeline
                workflows_path = os.path.join(clone_path, ".github", "workflows")
                if os.path.exists(workflows_path):
                    discovery_result["existing_ci_pipeline"] = True
                    discovery_result["existing_workflows"] = os.listdir(workflows_path)

                # Determine build tool (simplified logic)
                if discovery_result["project_files"]:
                    discovery_result["build_tool"] = ".NET CLI"

                discovery_result["analysis_status"] = "completed"
                return discovery_result

        except subprocess.CalledProcessError as e:
            return {
                "status": "error",
                "error": f"Failed to clone repository: {str(e)}",
                "repo_url": repo_url
            }
        except Exception as e:
            return {
                "status": "error",
                "error": f"Discovery failed: {str(e)}",
                "repo_url": repo_url
            }


class GenerateGitHubActionsWorkflow(Tool):
    """Generate a GitHub Actions workflow for .NET Core CI pipeline."""

    name = "generate_github_actions_workflow"
    description = """Generate a GitHub Actions CI workflow YAML for a .NET Core project.
    The workflow includes build, unit tests, SonarQube, SAST, SCA, DAST, container build,
    SBOM generation, image scanning, artifact storage, and Helm chart updates."""
    parameters = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the project for workflow identification"
            },
            "target_framework": {
                "type": "string",
                "description": "Target framework (.NET 6, 7, or 8)",
                "enum": [".NET 6", ".NET 7", ".NET 8"]
            },
            "build_tool": {
                "type": "string",
                "description": "Build tool to use",
                "enum": [".NET CLI", "MSBuild", "NuGet"],
                "default": ".NET CLI"
            },
            "project_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repo-relative .csproj paths from discovery; restore/build/test target these explicitly"
            },
            "solution_file": {
                "type": "string",
                "description": "Repo-relative .sln path from discovery (takes precedence over project_files)"
            },
            "sonar_project_key": {
                "type": "string",
                "description": "SonarQube/SonarCloud project key. Defaults to a repo-derived key; must be a real key, never a placeholder."
            },
            "sonar_org": {
                "type": "string",
                "description": "SonarCloud organization. When set it is inlined into the scan; when absent the /o: flag is omitted (self-hosted SonarQube)."
            },
            "runner_os": {
                "type": "string",
                "description": "GitHub Actions runner label for runs-on (e.g. ubuntu-latest, windows-latest, or a self-hosted label). Free text; taken from the orchestrator UI. Defaults to ubuntu-latest when blank."
            },
            "enabled_tools": {
                "type": "object",
                "description": "Which optional CI capabilities are selected in the UI, as {capability: tool_name}. Capabilities: coverage, sast, sca, dast, imgscan, artifact, registry, monitoring. A capability that is absent/empty is emitted commented-out. Omit the whole object to enable all with default tool names."
            }
        },
        "required": ["project_name", "target_framework"],
    }

    def run(self, project_name: str, target_framework: str, build_tool: str = ".NET CLI",
            runner_os: str = "ubuntu-latest", project_files: list = None,
            solution_file: str = None, sonar_project_key: str = None,
            sonar_org: str = None, enabled_tools: dict = None,
            # Accepted for backward-compat with older callers; no longer used
            # (Helm removed; DAST/registry are driven by enabled_tools now).
            include_dast=None, enable_docker_build=None, enable_helm_update=None) -> dict:
        """Generate a single-job GitHub Actions CI workflow (one checkout).

        The whole pipeline is ONE job: checkout once, then build/test plus the
        optional CI tool steps. Each optional capability is emitted only when the
        UI selected a tool for it; otherwise its steps are commented out. The
        runner label comes from the UI (defaults to ubuntu-latest).
        """
        try:
            runner_os = (runner_os or "ubuntu-latest").strip() or "ubuntu-latest"

            # Which optional capabilities are active, and with which tool name.
            # None => enable all with default names (legacy / LLM-tool path). A
            # dict (even empty) => only the listed, non-empty capabilities are
            # active; everything else is emitted commented-out.
            if enabled_tools is None:
                caps = dict(DEFAULT_TOOL_NAME)
            else:
                caps = {k: v for k, v in enabled_tools.items() if v}

            # GENERATE is cookbook-first (ARCHITECTURE.md §2.3): pull the .NET Core
            # setup/build/test/sonar step bodies from this agent's OWN cookbook, and
            # fall back to the built-in steps only when no recipe matches the stack.
            try:
                recipe = get_recipe("dotnet")
            except Exception:
                recipe = None
            if recipe:
                # Recipe commands target the discovered layout (.sln, else each
                # .csproj); without layout info, the find-based shell stands in.
                build_targets = resolve_build_targets(solution_file, project_files)
                test_targets = resolve_test_targets(solution_file, project_files)
                if build_targets:
                    build_cmds = expand_targets(list(recipe["build"]), build_targets)
                else:
                    build_cmds = FIND_BUILD_SH
                if test_targets:
                    test_cmds = expand_targets(
                        str(recipe["test"]).splitlines(), test_targets, placeholder=TEST_TARGET
                    )
                elif build_targets:
                    test_cmds = ['echo "No test projects found"']
                else:
                    test_cmds = FIND_TEST_SH
                setup_steps = render_steps(recipe["setup"])
                build_steps = render_run("Restore & build", build_cmds)
                test_steps = render_run("Run unit tests", test_cmds)
                # Resolve the Sonar project-key/org placeholders to real values
                # (a repo-derived key by default) BEFORE rendering, so an
                # unresolved __SONAR_ORG__ can never reach SonarCloud again.
                project_key = sonar_project_key or self._default_sonar_key(project_name)
                sonar_recipe = resolve_sonar_steps(recipe["sonar_steps"], project_key, sonar_org)
                sonar_rendered = render_steps(
                    expand_step_targets(sonar_recipe, build_targets, empty=FIND_BUILD_SH)
                )
                tool_templates = recipe.get("tool_steps") or {}
                generation_source = "cookbook"
            else:
                dv = self._get_dotnet_version(target_framework)
                setup_steps = (
                    "      - name: Setup .NET\n"
                    "        uses: actions/setup-dotnet@v3\n"
                    "        with:\n"
                    f"          dotnet-version: '{dv}'"
                )
                build_steps = render_run("Restore & build", FIND_BUILD_SH)
                test_steps = render_run("Run unit tests", FIND_TEST_SH)
                sonar_rendered = (
                    "      - name: Run SonarQube Analysis\n"
                    "        run: |\n"
                    "          echo \"SonarQube coverage analysis placeholder\""
                )
                tool_templates = {}
                generation_source = "builtin-fallback"

            # --- Assemble the SINGLE ci job's steps (one checkout for everything) ---
            checkout = ("      - name: Checkout code\n"
                        "        uses: actions/checkout@v4")

            def cap_block(cap):
                """Rendered steps for an optional capability — active, or, when the
                UI left it unselected, commented out (visible but inert)."""
                if cap == "coverage":
                    rendered, label = sonar_rendered, "COVERAGE (SonarQube/SonarCloud)"
                else:
                    steps = tool_templates.get(cap)
                    if not steps:
                        return None
                    rendered, label = render_steps(steps), cap.upper()
                tool = caps.get(cap)
                if tool:
                    return rendered.replace("__TOOL__", tool)
                header = f"      # {label} - not selected in the orchestrator UI"
                return header + "\n" + comment_block(rendered.replace("__TOOL__", "not selected"))

            step_blocks = [checkout, setup_steps, build_steps, test_steps]
            step_blocks += [cap_block(c) for c in OPTIONAL_CAPS]
            job_steps = "\n".join(b for b in step_blocks if b)

            workflow_yaml = f"""name: '{project_name} CI Pipeline'

on:
  push:
    branches:
      - main
      - develop
  pull_request:
    branches:
      - main
  workflow_dispatch:

env:
  SONAR_HOST_URL: ${{{{ secrets.SONAR_HOST_URL }}}}
  SONAR_TOKEN: ${{{{ secrets.SONAR_TOKEN }}}}
  DYNATRACE_ENVIRONMENT_ID: ${{{{ secrets.DYNATRACE_ENVIRONMENT_ID }}}}
  DYNATRACE_API_TOKEN: ${{{{ secrets.DYNATRACE_API_TOKEN }}}}
  SPLUNK_HEC_URL: ${{{{ secrets.SPLUNK_HEC_URL }}}}
  SPLUNK_HEC_TOKEN: ${{{{ secrets.SPLUNK_HEC_TOKEN }}}}
  NEXUS_REPO_URL: ${{{{ secrets.NEXUS_REPO_URL }}}}
  NEXUS_USERNAME: ${{{{ secrets.NEXUS_USERNAME }}}}
  NEXUS_PASSWORD: ${{{{ secrets.NEXUS_PASSWORD }}}}
  REGISTRY_URL: ${{{{ secrets.REGISTRY_URL }}}}

jobs:
  ci:
    runs-on: {runner_os}
    permissions:
      contents: read
      packages: write
    steps:
{job_steps}
"""

            # Safety net for the whole __TEMPLATE__ bug class: refuse to ship a
            # workflow that still carries any unresolved __PLACEHOLDER__ token.
            # A literal placeholder reaching a runner is always a generator bug
            # (e.g. __SONAR_ORG__ -> SonarCloud 404), never a valid value.
            leftover = sorted(set(re.findall(r"__[A-Z0-9_]+__", workflow_yaml)))
            if leftover:
                return {
                    "status": "error",
                    "error": (
                        "Refusing to emit a workflow with unresolved template "
                        "placeholders: " + ", ".join(leftover) + ". These must be "
                        "resolved at generation (provide the value, e.g. "
                        "sonar_project_key/sonar_org) or fixed in the cookbook recipe."
                    ),
                    "unresolved_placeholders": leftover,
                }

            return {
                "status": "generated",
                "project_name": project_name,
                "workflow_filename": f"{project_name}-ci.yml",
                "workflow_path": f".github/workflows/{project_name}-ci.yml",
                "workflow_yaml": workflow_yaml,
                "generation_source": generation_source,
                "configuration": {
                    "target_framework": target_framework,
                    "build_tool": build_tool,
                    "runner_os": runner_os,
                    "single_job": True,
                    "enabled_tools": caps,
                },
                "notes": [
                    "Single-job pipeline (one checkout for the whole run).",
                    "Runner (runs-on) is taken from the orchestrator UI; defaults to ubuntu-latest.",
                    "CI tools left '— not used —' in the UI are emitted commented-out.",
                    "Configure the GitHub repository secrets for the tools you selected.",
                ]
            }

        except Exception as e:
            return {
                "status": "error",
                "error": f"Failed to generate workflow: {str(e)}"
            }

    @staticmethod
    def _get_dotnet_version(target_framework: str) -> str:
        """Map target framework to dotnet version."""
        mapping = {
            ".NET 6": "6.0.x",
            ".NET 7": "7.0.x",
            ".NET 8": "8.0.x"
        }
        return mapping.get(target_framework, "8.0.x")

    @staticmethod
    def _default_sonar_key(project_name: str) -> str:
        """A safe, non-empty Sonar project key when the caller supplies none.

        The pipeline passes a repo-derived ``<owner>_<repo>`` key; this fallback
        only guards direct/LLM calls. Never returns an empty string (an empty key
        fails the scan just like a placeholder would)."""
        slug = re.sub(r"[^A-Za-z0-9_.:-]+", "_", project_name or "").strip("_")
        return slug or "app"


class ValidateWorkflow(Tool):
    """Validate generated GitHub Actions workflow YAML."""

    name = "validate_workflow"
    description = "Validate the generated GitHub Actions workflow for syntax errors and required stages."
    parameters = {
        "type": "object",
        "properties": {
            "workflow_yaml": {
                "type": "string",
                "description": "The workflow YAML content to validate"
            },
            "workflow_name": {
                "type": "string",
                "description": "Name/identifier of the workflow"
            }
        },
        "required": ["workflow_yaml", "workflow_name"],
    }

    def run(self, workflow_yaml: str, workflow_name: str) -> dict:
        """Validate workflow YAML."""
        try:
            import yaml

            # Try to parse the YAML
            workflow_dict = yaml.safe_load(workflow_yaml)

            validation_result = {
                "workflow_name": workflow_name,
                "valid": True,
                "errors": [],
                "warnings": [],
                "checks": {}
            }

            # Check required top-level fields.
            # NOTE: YAML 1.1 (PyYAML) parses the bare key `on:` as the boolean
            # True, so a valid GitHub Actions workflow shows up under the key
            # `True` rather than "on". Accept either so we don't false-negative a
            # perfectly valid trigger definition.
            if "on" not in workflow_dict and True not in workflow_dict:
                validation_result["errors"].append("Missing 'on' trigger definition")
                validation_result["valid"] = False

            if "jobs" not in workflow_dict:
                validation_result["errors"].append("Missing 'jobs' definition")
                validation_result["valid"] = False
            else:
                jobs = workflow_dict["jobs"] or {}
                validation_result["checks"]["jobs"] = list(jobs.keys())

                # The pipeline is generated as a single job; every job must carry
                # at least one step (a step-less job fails GitHub's own schema).
                if not any((j or {}).get("steps") for j in jobs.values()):
                    validation_result["errors"].append("No job defines any steps")
                    validation_result["valid"] = False

                # Soft check: the core build/test stages should be present as steps.
                all_step_names = " ".join(
                    str(st.get("name", ""))
                    for j in jobs.values()
                    for st in ((j or {}).get("steps") or [])
                    if isinstance(st, dict)
                ).lower()
                missing_stages = [s for s in ("build", "test") if s not in all_step_names]
                if missing_stages:
                    validation_result["warnings"].append(
                        f"Expected step(s) not found by name: {missing_stages}"
                    )

            validation_result["checks"]["yaml_syntax"] = "valid"

            return validation_result if validation_result["valid"] else {
                **validation_result,
                "status": "validation_failed"
            }

        except Exception as e:
            return {
                "workflow_name": workflow_name,
                "valid": False,
                "status": "validation_failed",
                "error": f"YAML validation error: {str(e)}"
            }


class CreatePullRequest(Tool):
    """Create a pull request with the generated CI pipeline."""

    name = "create_pull_request"
    description = "Commit the generated workflow to a new branch and create a pull request for human review."
    parameters = {
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": "Git repository URL"
            },
            "workflow_yaml": {
                "type": "string",
                "description": "The workflow YAML content to commit"
            },
            "workflow_filename": {
                "type": "string",
                "description": "Filename for the workflow (e.g., ci-pipeline.yml)"
            },
            "project_name": {
                "type": "string",
                "description": "Project name for PR title and branch name"
            },
            "branch_name": {
                "type": "string",
                "description": "Branch name for the PR (default: auto-generated)",
                "default": None
            }
        },
        "required": ["repo_url", "workflow_yaml", "workflow_filename", "project_name"],
    }

    def run(self, repo_url: str, workflow_yaml: str, workflow_filename: str,
            project_name: str, branch_name: str = None) -> dict:
        """Create a pull request with the workflow."""
        try:
            if branch_name is None:
                branch_name = f"feature/ci-pipeline-{project_name.lower()}"

            return {
                "status": "pr_created_placeholder",
                "repo_url": repo_url,
                "branch_name": branch_name,
                "workflow_file": f".github/workflows/{workflow_filename}",
                "pr_title": f"Add CI Pipeline for {project_name}",
                "pr_description": f"""This PR adds a GitHub Actions CI pipeline for {project_name}.

## Changes
- Added `.github/workflows/{workflow_filename}` - Complete CI/CD workflow
  - Build stage: Compiles the .NET Core application
  - Test stage: Runs all unit tests
  - SonarQube stage: Code coverage and quality analysis
  - Security stage: SAST and SCA scanning
  - Container stage: Builds Docker image and pushes to registry
  - Helm stage: Updates Helm chart references
  - Notification stage: Integrates with Dynatrace and Splunk

## Configuration
The workflow is parameterized via environment variables and secrets. Please configure the following secrets in your GitHub repository settings:
- SONAR_HOST_URL, SONAR_TOKEN
- DYNATRACE_ENVIRONMENT_ID, DYNATRACE_API_TOKEN
- SPLUNK_HEC_URL, SPLUNK_HEC_TOKEN
- NEXUS_REPO_URL, NEXUS_USERNAME, NEXUS_PASSWORD
- REGISTRY_URL

## Next Steps
1. Review this PR for any customizations needed for your project
2. Configure the required secrets in GitHub repository settings
3. Merge this PR when ready
4. The pipeline will run on next push to main or develop branches

---
Generated by NetcoreCIAgent - Always requires human review before merge.""",
                "notes": [
                    "PR created in draft mode - ready for review",
                    "No automatic merge - human approval required",
                    "All placeholders should be replaced with actual tool integrations",
                    "Secrets must be configured before first pipeline run"
                ],
                "placeholder_notice": "This is a placeholder response. In production, this tool would clone the repo, create a branch, commit the workflow file, and open a real GitHub PR via the GitHub API."
            }

        except Exception as e:
            return {
                "status": "error",
                "error": f"Failed to create PR: {str(e)}",
                "repo_url": repo_url
            }


# The framework loads this list. Add or remove tools here.
TOOLS = [
    DiscoverProject(),
    GenerateGitHubActionsWorkflow(),
    ValidateWorkflow(),
    CreatePullRequest(),
]
