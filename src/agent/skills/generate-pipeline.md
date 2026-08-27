# Generate GitHub Actions CI Pipeline

When you have discovered the project structure, call the `generate_github_actions_workflow` tool to:
- Author a GitHub Actions workflow YAML file
- Include all required stages: Build, Unit Tests, SonarQube Coverage, SAST, SCA, DAST, Container Build, SBOM, Image Scan, and Artifact Storage
- Select the appropriate runner (Linux or Windows) based on project structure
- Use the correct build tool (.NET CLI, MSBuild, or NuGet) as discovered
- Make DAST configurable (can be disabled via configuration)
- Configure Dynatrace and Splunk monitoring integration
- Update Helm chart references for Kubernetes deployment
- Use standard folder structure (`.github/workflows/`)

The generated workflow should trigger on:
- Push to main/development branches
- Manual workflow_dispatch trigger
- Pull requests

Return the workflow YAML content and any supporting configuration files.
