"""Tests for the real CI pipeline — GitHub is mocked, so no network is used."""
from unittest.mock import MagicMock

from github import GithubException

from agent.github_client import open_pr, parse_repo_full_name
from agent.pipeline import run_ci_pipeline, to_actions_secrets
from agent.secrets_provider import GitHubActionsSecrets


def test_parse_repo_full_name_variants():
    assert parse_repo_full_name("https://github.com/o/r.git") == "o/r"
    assert parse_repo_full_name("git@github.com:o/r.git") == "o/r"
    assert parse_repo_full_name("o/r") == "o/r"


def test_to_actions_secrets_maps_and_drops_masked():
    selected = {
        "Code coverage & quality": {"tool": "SonarQube", "Host URL": "https://s", "Token": "realtok"},
        "Container registry": {"tool": "GHCR", "Owner": "komali", "PAT": "ghp_real"},
        "Artifact repository": {"tool": "Nexus", "Password": "[provided via UI]"},  # masked -> dropped
    }
    out = to_actions_secrets(selected)
    assert out["SONAR_HOST_URL"] == "https://s"
    assert out["SONAR_TOKEN"] == "realtok"
    assert out["REGISTRY_TOKEN"] == "ghp_real"
    assert "NEXUS_PASSWORD" not in out  # masked value must never be delivered


def test_github_actions_secrets_sets_and_skips_blank():
    repo = MagicMock()
    provider = GitHubActionsSecrets(repo)
    results = provider.set_many({"SONAR_TOKEN": "abc", "EMPTY": ""})
    repo.create_secret.assert_called_once_with("SONAR_TOKEN", "abc", secret_type="actions")
    statuses = {r["name"]: r["status"] for r in results}
    assert statuses["SONAR_TOKEN"] == "set"
    assert statuses["EMPTY"] == "skipped_empty"


def _mock_repo():
    repo = MagicMock()
    repo.default_branch = "main"
    repo.get_branch.return_value.commit.sha = "basesha"
    repo.owner.login = "komali"
    repo.get_contents.side_effect = GithubException(404, {}, None)  # file absent -> create
    pr = MagicMock(); pr.number = 7; pr.html_url = "https://github.com/komali/repo/pull/7"
    repo.create_pull.return_value = pr
    return repo


def test_open_pr_creates_branch_file_and_pr():
    repo = _mock_repo()
    res = open_pr(repo, workflow_path=".github/workflows/ci.yml", workflow_yaml="name: x",
                  branch="ci/netcore-app", title="t", body="b")
    repo.create_git_ref.assert_called_once()
    repo.create_file.assert_called_once()
    repo.create_pull.assert_called_once()
    assert res == {"pr_number": 7, "pr_url": "https://github.com/komali/repo/pull/7",
                   "branch": "ci/netcore-app", "base": "main"}


def test_run_ci_pipeline_end_to_end_with_mocks():
    discover = MagicMock()
    discover.run.return_value = {"target_framework": ".NET 8", "build_tool": ".NET CLI",
                                 "docker_support": True, "helm_support": True,
                                 "existing_ci_pipeline": False}
    generate = MagicMock()
    generate.run.return_value = {"status": "generated", "workflow_yaml": "name: 'App CI'\non:\n  push: {}\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: []\n  test: {}\n  sonarqube: {}\n  security: {}\n",
                                 "workflow_path": ".github/workflows/ci-pipeline.yml"}
    validate = MagicMock()
    validate.run.return_value = {"valid": True}

    repo = _mock_repo()
    result = run_ci_pipeline(
        repo_url="https://github.com/komali/netcore-sample-app.git",
        github_token="dummy",
        options={"open_pr": True, "set_secrets": True, "include_dast": True},
        pipeline_secrets={"SONAR_TOKEN": "abc", "REGISTRY_TOKEN": "ghp"},
        repo=repo,
        discover_tool=discover, generate_tool=generate, validate_tool=validate,
    )
    assert result["status"] == "opened"
    assert result["pr"]["pr_url"].endswith("/pull/7")
    assert {s["name"] for s in result["secrets"] if s["status"] == "set"} == {"SONAR_TOKEN", "REGISTRY_TOKEN"}
    assert result["repo"] == "komali/netcore-sample-app"


def test_pipeline_uses_custom_pr_branch_from_options():
    """The UI's 'PR branch' field (options.pr_branch) names the branch the agent
    creates and opens the PR from; blank falls back to ci/netcore-<repo>."""
    discover = MagicMock()
    discover.run.return_value = {"target_framework": ".NET 8", "build_tool": ".NET CLI",
                                 "project_files": [], "solution_file": None,
                                 "docker_support": True, "helm_support": False,
                                 "existing_ci_pipeline": False}
    generate = MagicMock()
    generate.run.return_value = {"status": "generated", "workflow_yaml": "name: x",
                                 "workflow_path": ".github/workflows/ci.yml"}
    validate = MagicMock(); validate.run.return_value = {"valid": True}

    repo = _mock_repo()
    result = run_ci_pipeline(
        repo_url="https://github.com/komali/netcore-sample-app.git",
        github_token="dummy",
        options={"open_pr": True, "set_secrets": False, "pr_branch": "feature/my-ci"},
        repo=repo,
        discover_tool=discover, generate_tool=generate, validate_tool=validate,
    )
    assert result["status"] == "opened"
    assert result["pr"]["branch"] == "feature/my-ci"
    assert repo.create_git_ref.call_args.kwargs["ref"] == "refs/heads/feature/my-ci"
    assert repo.create_pull.call_args.kwargs["head"] == "feature/my-ci"
