"""Thin GitHub helpers — resolve a repo and open a real pull request.

Uses the GitHub API (PyGithub) directly: create a branch ref, put the workflow
file on it, and open a PR. No local clone needed. The agent never merges its own
PR — human review is always required.
"""
from __future__ import annotations

import re
from typing import Optional


def parse_repo_full_name(repo_url: str) -> str:
    """'https://github.com/owner/repo(.git)' | 'git@github.com:owner/repo.git'
    | 'owner/repo'  ->  'owner/repo'."""
    s = repo_url.strip()
    s = re.sub(r"^git@github\.com:", "", s)
    s = re.sub(r"^https?://github\.com/", "", s)
    s = re.sub(r"\.git$", "", s)
    s = s.strip("/")
    parts = s.split("/")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse owner/repo from: {repo_url!r}")
    return f"{parts[0]}/{parts[1]}"


def get_repo(github_token: str, repo_full_name: str):
    """Return a PyGithub Repository for ``owner/repo`` (imported lazily)."""
    from github import Auth, Github

    gh = Github(auth=Auth.Token(github_token))
    return gh.get_repo(repo_full_name)


def open_pr(repo, *, workflow_path: str, workflow_yaml: str, branch: str,
            title: str, body: str, base: Optional[str] = None,
            commit_message: str = "ci: add generated CI pipeline") -> dict:
    """Create ``branch`` from the default branch, commit the workflow, open a PR.

    Idempotent-ish: if the branch or file already exists it updates in place.
    Returns {pr_number, pr_url, branch, base}. Never merges.
    """
    from github import GithubException

    base = base or repo.default_branch
    base_sha = repo.get_branch(base).commit.sha

    # Create the feature branch (reuse it if a prior run already made it).
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)
    except GithubException as exc:
        if exc.status != 422:  # 422 = ref already exists
            raise

    # Write the workflow file on the branch (create or update).
    try:
        existing = repo.get_contents(workflow_path, ref=branch)
        repo.update_file(workflow_path, commit_message, workflow_yaml,
                         existing.sha, branch=branch)
    except GithubException as exc:
        if exc.status != 404:
            raise
        repo.create_file(workflow_path, commit_message, workflow_yaml, branch=branch)

    # Open the PR (reuse an open one for this branch if present).
    try:
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
    except GithubException as exc:
        if exc.status != 422:  # 422 = a PR already exists for this head
            raise
        existing_prs = list(repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}"))
        if not existing_prs:
            raise
        pr = existing_prs[0]

    return {"pr_number": pr.number, "pr_url": pr.html_url, "branch": branch, "base": base}
