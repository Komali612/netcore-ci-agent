# Validate Generated CI Pipeline

After generating the GitHub Actions workflow, call the `validate_workflow` tool to:
- Lint the YAML syntax for correctness
- Run a GitHub Actions test-agent dry-run to verify workflow structure
- Ensure all required stages are present and properly ordered
- Verify fail-fast criteria are configured correctly
- Check that pipeline will not overwrite existing steps if pipeline already exists

If validation fails, you will iterate:
1. Analyze the validation error
2. Call `generate_github_actions_workflow` to fix the issue
3. Call `validate_workflow` again

This cycle can repeat up to **3 total attempts**. If validation fails after 3 attempts, escalate to human review by creating an exception entry.

If validation succeeds, proceed to Deploy step.
