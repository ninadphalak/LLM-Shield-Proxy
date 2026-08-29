[⬅️ Back to README](README.md)

# Contributing to LLM-Shield-Proxy

Thank you for your interest in contributing to LLM-Shield-Proxy! We welcome community contributions, bug reports, and enhancements.

## Code of Conduct
All contributors are expected to uphold respectful, inclusive, and professional communication.

## Pull Request Guidelines & Latency Benchmarking

To maintain our enterprise zero-latency guarantees, all Pull Requests must adhere to strict performance standards:

1. **Sub-Millisecond Performance Benchmark:**
   - LLM-Shield-Proxy guarantees zero-egress, ultra-low latency PII detection.
   - All Pull Requests must be manually reviewed and performance-benchmarked by the core maintainer (**Ninad Phalak**) for sub-millisecond overhead before merging.
   - Any PR introducing significant memory leaks, latency regressions (> 1ms overhead for Tier 1 compiled regex), or blocking CPU operations will be rejected.

2. **Automated Testing:**
   - Ensure all existing unit and integration tests pass cleanly (`py -m pytest tests/`).
   - Include test coverage for any new features or bug fixes.

3. **Code Style & Formatting:**
   - Code must adhere to PEP 8 standard formatting and include type hints where applicable.

4. **Website / Frontend Changes:**
   - The [`website/`](website/) Docusaurus site deploys straight to production Firebase
     Hosting on merge to `main` (see [`.github/workflows/deploy-docs.yml`](.github/workflows/deploy-docs.yml)).
     `npm run build` must succeed locally before opening a PR — see
     [website/README.md § Testing & CI Safety](website/README.md#testing--ci-safety) for the
     SSR-safety rules this enforces (no `window`/`document`/naked timers outside `useEffect`
     or `<BrowserOnly>`).

## Submission Workflow
1. Fork the repository and create a feature branch (`git checkout -b feature/amazing-feature`).
2. Commit your changes with clear, descriptive commit messages.
3. Submit a Pull Request targeting the `main` branch.
4. Wait for code review and latency benchmark verification by Ninad Phalak.
