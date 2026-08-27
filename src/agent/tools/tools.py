"""Tools for NetcoreCIAgent — the code the LLM can call.

This agent discovers .NET Core projects, generates GitHub Actions CI pipelines,
validates them, and opens PRs for human review.
"""
from agent_core import Tool
import json
import subprocess
import tempfile
import os
from pathlib import Path


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
                        if file.endswith('.csproj'):
                            discovery_result["project_files"].append(
                                os.path.relpath(os.path.join(root, file), clone_path)
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
            "runner_os": {
                "type": "string",
                "description": "Runner OS for the workflow",
                "enum": ["ubuntu-latest", "windows-latest"],
                "default": "ubuntu-latest"
            },
            "include_dast": {
                "type": "boolean",
                "description": "Include DAST stage in pipeline (default: true)",
                "default": True
            },
            "enable_docker_build": {
                "type": "boolean",
                "description": "Build Docker image as artifact (default: true)",
                "default": True
            },
            "enable_helm_update": {
                "type": "boolean",
                "description": "Update Helm chart after build (default: true)",
                "default": True
            }
        },
        "required": ["project_name", "target_framework"],
    }

    def run(self, project_name: str, target_framework: str, build_tool: str = ".NET CLI",
            runner_os: str = "ubuntu-latest", include_dast: bool = True,
            enable_docker_build: bool = True, enable_helm_update: bool = True) -> dict:
        """Generate GitHub Actions workflow YAML."""
        try:
            # Container job — pushes to GHCR using the workflow's built-in
            # GITHUB_TOKEN (no separate registry PAT needed). Built as a plain
            # string so GitHub's ${{ ... }} expressions need no f-string escaping.
            if enable_docker_build:
                container_job = (
                    "  container:\n"
                    "    needs: security\n"
                    "    runs-on: __RUNNER__\n"
                    "    permissions:\n"
                    "      contents: read\n"
                    "      packages: write\n"
                    "    steps:\n"
                    "      - name: Checkout code\n"
                    "        uses: actions/checkout@v4\n"
                    "      - name: Log in to GHCR\n"
                    "        uses: docker/login-action@v3\n"
                    "        with:\n"
                    "          registry: ghcr.io\n"
                    "          username: ${{ github.actor }}\n"
                    "          password: ${{ secrets.GITHUB_TOKEN }}\n"
                    "      - name: Build and push image\n"
                    "        run: |\n"
                    "          REPO=$(echo \"${{ github.repository }}\" | tr '[:upper:]' '[:lower:]')\n"
                    "          IMAGE=ghcr.io/$REPO:${{ github.sha }}\n"
                    "          docker build -t \"$IMAGE\" .\n"
                    "          docker push \"$IMAGE\"\n"
                    "      - name: Generate SBOM\n"
                    "        run: echo 'SBOM generation (placeholder — e.g. Trivy/Syft)'\n"
                ).replace("__RUNNER__", runner_os)
            else:
                container_job = "  # container build disabled in configuration\n"

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
  build:
    runs-on: {runner_os}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup .NET
        uses: actions/setup-dotnet@v3
        with:
          dotnet-version: '{self._get_dotnet_version(target_framework)}'

      - name: Restore & build
        run: |
          SLN=$(find . -name '*.sln' -print -quit)
          if [ -n "$SLN" ]; then
            dotnet build "$SLN" --configuration Release
          else
            for p in $(find . -name '*.csproj'); do
              echo "Building $p"
              dotnet build "$p" --configuration Release
            done
          fi

  test:
    needs: build
    runs-on: {runner_os}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup .NET
        uses: actions/setup-dotnet@v3
        with:
          dotnet-version: '{self._get_dotnet_version(target_framework)}'

      - name: Run Unit Tests
        run: |
          TESTS=$(find . -name '*.csproj' | grep -i test || true)
          if [ -z "$TESTS" ]; then echo "No test projects found"; exit 0; fi
          for p in $TESTS; do
            echo "Testing $p"
            dotnet test "$p" --configuration Release
          done

  sonarqube:
    needs: test
    runs-on: {runner_os}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup .NET
        uses: actions/setup-dotnet@v3
        with:
          dotnet-version: '{self._get_dotnet_version(target_framework)}'

      - name: Run SonarQube Analysis
        run: |
          echo "SonarQube coverage analysis placeholder"
          echo "Integration with SonarQube via SONAR_HOST_URL and SONAR_TOKEN"

  security:
    needs: test
    runs-on: {runner_os}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Run SAST (Fortify)
        run: echo "SAST analysis via Fortify integration (placeholder)"

      - name: Run SCA (Sonatype)
        run: echo "SCA analysis via Sonatype integration (placeholder)"

{"  dast:" if include_dast else "  # DAST stage disabled in configuration"}
{"    needs: build" if include_dast else "    # needs: build"}
{"    runs-on: " + runner_os if include_dast else "    # runs-on: " + runner_os}
{"    steps:" if include_dast else "    # steps:"}
{"      - name: Run DAST (Fortify WebInspect)" if include_dast else "      # - name: Run DAST (Fortify WebInspect)"}
{"        run: echo 'DAST analysis via Fortify WebInspect (placeholder)'" if include_dast else "        # run: echo 'DAST analysis via Fortify WebInspect (placeholder)'"}

{container_job}
{"  helm:" if enable_helm_update else "  # helm update disabled in configuration"}
{"    needs: container" if enable_helm_update else "    # needs: container"}
{"    runs-on: " + runner_os if enable_helm_update else "    # runs-on: " + runner_os}
{"    steps:" if enable_helm_update else "    # steps:"}
{"      - name: Update Helm Chart" if enable_helm_update else "      # - name: Update Helm Chart"}
{"        run: echo 'Update Helm chart (placeholder)'" if enable_helm_update else "        # run: echo 'Update Helm chart (placeholder)'"}

  notify:
    needs: [build, test, sonarqube, security]
    runs-on: {runner_os}
    if: always()
    steps:
      - name: Notify Dynatrace
        run: echo "Send build metrics to Dynatrace (placeholder)"

      - name: Notify Splunk
        run: echo "Send execution logs to Splunk (placeholder)"
"""

            return {
                "status": "generated",
                "project_name": project_name,
                "workflow_filename": f"{project_name}-ci.yml",
                "workflow_path": f".github/workflows/{project_name}-ci.yml",
                "workflow_yaml": workflow_yaml,
                "configuration": {
                    "target_framework": target_framework,
                    "build_tool": build_tool,
                    "runner_os": runner_os,
                    "dast_enabled": include_dast,
                    "docker_enabled": enable_docker_build,
                    "helm_enabled": enable_helm_update
                },
                "notes": [
                    "Workflow template generated successfully",
                    "Replace placeholders with actual tool integrations",
                    "Configure required secrets in GitHub repository settings",
                    "Required secrets: SONAR_HOST_URL, SONAR_TOKEN, DYNATRACE_ENVIRONMENT_ID, DYNATRACE_API_TOKEN, SPLUNK_HEC_URL, SPLUNK_HEC_TOKEN, NEXUS_REPO_URL, NEXUS_USERNAME, NEXUS_PASSWORD, REGISTRY_URL"
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
                jobs = workflow_dict["jobs"]
                required_jobs = ["build", "test", "sonarqube", "security"]

                validation_result["checks"]["required_jobs"] = {
                    "required": required_jobs,
                    "present": [j for j in required_jobs if j in jobs],
                    "missing": [j for j in required_jobs if j not in jobs]
                }

                if validation_result["checks"]["required_jobs"]["missing"]:
                    validation_result["warnings"].append(
                        f"Missing recommended jobs: {validation_result['checks']['required_jobs']['missing']}"
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
