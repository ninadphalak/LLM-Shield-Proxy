# Security Policy & Vulnerability Reporting

## Security Overview
LLM-Shield-Proxy is engineered for extreme zero-egress data privacy and enterprise compliance (SOC 2 / HIPAA). Security and confidentiality are core to the architecture.

## Supported Versions
As an open-source project, **only the absolute latest release version** is actively supported with security updates. 

We do not backport security patches to older versions. If a vulnerability is found and patched (e.g., in `1.0.20`), users on older versions (e.g., `1.0.14`) are expected to upgrade to the latest release to secure their environment. The onus is entirely on the user to ensure they are pulling the latest Docker image or PyPI package.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| Older Versions | :x:         |

## Reporting a Vulnerability

If you discover a security vulnerability in LLM-Shield-Proxy, please **do not** open a public issue.

Instead, confidentially report the issue directly to the core maintainer:

- **Contact:** Ninad Phalak
- **Email:** `ninad.phalak@gmail.com`

Please include in your report:
- A detailed description of the vulnerability.
- Steps to reproduce or proof-of-concept payload/code.
- Impact assessment.

We aim to respond to security reports within 24–48 hours and release a patch expeditiously.
