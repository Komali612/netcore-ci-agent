"""Pluggable secret delivery — how a pipeline's secrets reach the CI runner.

The pipeline-authoring logic never hard-codes *where* secrets live; it calls a
``SecretsProvider``. Today we ship **Model A** (write GitHub Actions secrets);
a future ``VaultOIDCProvider`` would instead leave nothing in GitHub and have
the workflow fetch secrets at runtime via OIDC. Swapping providers must not
change the discover/generate/validate logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class SecretsProvider(ABC):
    """Delivers named secret values to wherever the CI runner will read them."""

    #: How generated workflows should reference a secret this provider delivers.
    #: Model A -> "${{ secrets.NAME }}". A pull-based provider would differ.
    def reference(self, name: str) -> str:
        return "${{ secrets." + name + " }}"

    @abstractmethod
    def set_secret(self, name: str, value: str) -> dict:
        """Deliver one secret. Returns a small status dict (never the value)."""

    def set_many(self, secrets: dict[str, str]) -> list[dict]:
        """Deliver several; blank values are skipped. A failure on one secret is
        recorded (status "failed") rather than raised, so the caller can still
        proceed (e.g. open the PR) and surface which secrets need attention —
        e.g. when the token lacks the 'Secrets' permission."""
        results = []
        for name, value in (secrets or {}).items():
            if value is None or value == "":
                results.append({"name": name, "status": "skipped_empty"})
                continue
            try:
                results.append(self.set_secret(name, value))
            except Exception as exc:
                results.append(
                    {"name": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                )
        return results


class GitHubActionsSecrets(SecretsProvider):
    """Model A — writes encrypted GitHub Actions secrets via the GitHub API.

    ``repo`` is a PyGithub ``Repository`` (injected, so this is unit-testable
    without network). PyGithub's ``create_secret`` fetches the repo public key
    and encrypts the value (libsodium sealed box) before it leaves the process.
    """

    def __init__(self, repo, scope: str = "repo") -> None:
        self._repo = repo
        self._scope = scope

    def set_secret(self, name: str, value: str) -> dict:
        # secret_type="actions" is the default; kept explicit for clarity.
        self._repo.create_secret(name, value, secret_type="actions")
        return {"name": name, "scope": self._scope, "status": "set"}
