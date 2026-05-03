# Security Policy

## Supported Versions

Security fixes are issued for the most recent minor release. Older minor
releases stop receiving security updates as soon as a newer minor lands.

| Version  | Supported |
|----------|-----------|
| 0.8.x    | ✅ — current release line, all fixes land here |
| 0.7.x    | ❌ — superseded by 0.8.0 |
| ≤ 0.6.x  | ❌ — superseded |

The current latest release is **0.8.3** ([release notes](https://github.com/kurok/pyimgtag/releases/tag/v0.8.3)).

## Reporting a Vulnerability

If you discover a security vulnerability in pyimgtag, **please do not open a public issue.**

Instead, report it privately:

1. Go to [Security Advisories](https://github.com/kurok/pyimgtag/security/advisories)
2. Click **"New draft security advisory"**
3. Provide a clear description of the vulnerability, steps to reproduce, and potential impact

Alternatively, contact the maintainers directly through GitHub.

### What to Expect

- **Acknowledgment** within 48 hours
- **Assessment** within 7 days with severity evaluation and timeline
- **Fix release** as soon as practical, depending on severity:
  - Critical: 24-48 hours
  - High: 1-2 weeks
  - Medium/Low: next release cycle
- **Credit** for responsible disclosure (unless you prefer anonymity)

### What Qualifies

- Command injection via CLI arguments or input file paths
- Arbitrary file read/write
- Path traversal when scanning directories or Photos libraries
- Dependency vulnerabilities with exploitable impact on pyimgtag users
- Information leakage through geocoding API calls (unexpected data sent to Nominatim)

### What Does Not Qualify

- Issues requiring local access to the machine already running pyimgtag
- Vulnerabilities in optional dependencies that don't affect pyimgtag's usage
- Model output quality issues (inaccurate tags are not security bugs)
- Rate limiting or denial of service against Nominatim (external service)

## Security Practices

- **CI scanning:** bandit (SAST) and pip-audit (dependency vulnerabilities) run on every push and PR
- **CodeQL:** required to pass before merging to main
- **Least-privilege CI:** all workflows use explicit, minimal `GITHUB_TOKEN` permissions
- **Minimal dependencies:** only `requests` and `Pillow` are required at runtime
- **No secrets in code:** pyimgtag does not store or transmit credentials
- **Local-first design:** image data is never sent to cloud services; only GPS coordinates are sent to Nominatim for reverse geocoding
- **Trusted publishing:** PyPI releases use OpenID Connect trusted publishing, no long-lived API tokens
- **Subprocess safety:** exiftool is called with fixed arguments only (no user-controlled command injection)

## Responsible Disclosure

When you discover a vulnerability, please:

- Report privately before public disclosure
- Give us reasonable time to patch before revealing publicly
- Only access what is needed to confirm the vulnerability
- Do not disrupt service for other users
