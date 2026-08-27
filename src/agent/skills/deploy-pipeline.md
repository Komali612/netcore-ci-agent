# Commit and Open Pull Request

After validation succeeds, call the `create_pull_request` tool to:
- Commit the generated GitHub Actions workflow to a new branch
- Use standard folder structure: `.github/workflows/<workflow-name>.yml`
- Include any supporting configuration files (e.g., sonarqube config, dynatrace config)
- Open a pull request against the main branch with a clear description
- The PR description should explain:
  - What stages are included in the pipeline
  - How to configure or customize the pipeline
  - Any required environment variables or secrets
  - Next steps for the repository owner

**CRITICAL CONSTRAINT**: Never merge the PR yourself. The pipeline is ready for human review only.

Report the PR URL and status back to the user for manual approval and merge.
