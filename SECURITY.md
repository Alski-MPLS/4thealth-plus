# Security Policy

## Reporting a Vulnerability

If you discover a potential security issue in this project, please report it
responsibly rather than opening a public issue.

**Preferred method:** Open a [GitHub Security Advisory](../../security/advisories/new)
(private by default — only visible to maintainers until disclosed).

**Alternative:** Open a regular issue titled `security: <short description>` and
mark it as sensitive if the platform allows, or email the maintainer directly via
the contact listed on the GitHub profile.

Please include:

- A clear description of the vulnerability
- Steps to reproduce
- Affected versions or components
- Any suggested mitigation

Do **not** include working exploit code in public issue threads.

## Response Timeline

Maintainers will acknowledge receipt within **72 hours** and aim to coordinate
a fix and disclosure timeline within **14 days** for high-severity issues.

## Scope

This project is a **read-only** monitoring dashboard. It never writes configuration
to FortiManager or managed devices. The attack surface is limited to:

- Session authentication and cookie handling
- Input validation on API endpoints
- Credential storage (bcrypt hashes in `users.json`)
- TLS configuration between the app and FortiManager
