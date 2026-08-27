# Cookbook — netcore-ci-agent

This directory is **this agent's own cookbook**: the library of deterministic CI
pipeline recipes, **scoped to .NET Core (6/7/8) only** — the single framework this
agent owns. See `ARCHITECTURE.md` §2.3 for the platform-wide rule (each agent
carries its own cookbook; not the orchestrator, not the shared scaffolding).

## How it's meant to work
`GENERATE` is **cookbook-first**: match the repo to a recipe here and render the
workflow deterministically. The **LLM is only a fallback** when no recipe matches
(and to write the missing recipe). Generation is idempotent — add only missing
steps, keep human edits.

## Files
- `cookbook.yaml` — the .NET Core recipe (toolchain setup, build/test commands,
  Sonar strategy, default Dockerfile). Seeded verbatim from the authoritative
  interim cookbook in `cicd-bootstrap`.

## Status — WIRED ✓
`GENERATE` now reads this cookbook: `src/agent/cookbook.py` loads `cookbook.yaml`
and `GenerateGitHubActionsWorkflow` renders the .NET Core setup/build/test/sonar
steps from it (real coverlet coverage + real Sonar scanner), falling back to the
built-in steps when no recipe matches. The tool reports which path ran via
`generation_source` (`cookbook` | `builtin-fallback`).

Remaining (backlog): promote the built-in fallback to an LLM call that also
*writes the missing cookbook entry*, and extend recipes beyond the single
`dotnet` (8.0.x) entry as needed.
