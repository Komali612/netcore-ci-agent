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

## Status (backlog)
The agent currently generates in code and does **not yet read this cookbook**
(ARCHITECTURE.md §6, known drift). The remaining work is to wire `GENERATE` to
consume `cookbook.yaml` cookbook-first with the LLM as fallback.
