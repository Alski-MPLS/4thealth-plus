# Contributing

Thanks for your interest in contributing to 4THealth.

## Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install uv (macOS)
brew install uv

# Clone and install dependencies
git clone <repo-url>
cd 4thealth
uv sync

# Copy and configure the environment
cp .env.example .env
# Edit .env: set SECRET_KEY, FMG_PRIMARY_HOST, and FMG_API_TOKEN (or username/password)

# Generate a SECRET_KEY
uv run python manage_users.py secret

# Create a local admin account
uv run python manage_users.py add admin --role admin

# Start the development server
uv run python wsgi.py
```

See the [Quick Start section in README.md](README.md#quick-start-macos--local-development--using-uv)
for the full setup walkthrough, including optional HTTPS configuration.

## Workflow

- Fork the repo and create a branch per feature or fix (use `kebab-case` names).
- Keep changes focused — one feature or bug fix per pull request.
- Follow the existing code style; run linters before committing.

## Testing and linting

```bash
# Run tests
uv run pytest

# Run linter
uv run flake8
```

## Pull request checklist

- [ ] Tests pass (`pytest`)
- [ ] Linting passes (`flake8`)
- [ ] README or docs updated if the change affects user-visible behaviour
- [ ] Security considerations addressed (no credentials committed, no new attack surface introduced)

## Adding a new page

See the [Extending the Application section in README.md](README.md#extending-the-application)
for the five-step pattern every new tab follows.
