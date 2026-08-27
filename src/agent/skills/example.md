# Discover Repository Structure

When you receive a Git repository URL, call the `discover_project` tool to:
- Clone and analyze the repository
- Detect the .NET project structure and target framework version
- Identify existing build configuration and scripts
- Check for existing CI pipelines (`.github/workflows/` directory)
- Determine the appropriate build tool (.NET CLI, MSBuild, NuGet)
- Extract SDK version requirements

Use this information to guide the pipeline generation step.
