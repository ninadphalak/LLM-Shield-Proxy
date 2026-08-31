[⬅️ Back to README](README.md)

# Contributing to LLM-Shield-Proxy

Thank you for your interest in contributing to LLM-Shield-Proxy! We welcome community contributions, bug reports, and enhancements.

## Code of Conduct
All contributors are expected to uphold respectful, inclusive, and professional communication.

## Pull Request Guidelines & Latency Benchmarking

To protect the bounded streaming path, pull requests must preserve the published correctness and measurement contracts:

1. **Performance and Memory Benchmarking:**
   - Do not introduce unbounded buffering or market component timings as end-to-end proxy latency.
   - Add or update configured-upstream conformance fixtures when changing redaction behavior.
   - Changes to the request or streaming path must include workload, environment, sample count, and distributional results from the published benchmark protocol.
   - Pull requests that introduce unbounded memory growth, unexplained latency regressions, or blocking event-loop work must address those findings before merge.

2. **Automated Testing:**
   - Ensure all existing unit and integration tests pass cleanly (`py -m pytest tests/`).
   - Include test coverage for any new features or bug fixes.

3. **Code Style & Formatting:**
   - Code must adhere to PEP 8 standard formatting and include type hints where applicable.

## Submission Workflow
1. Fork the repository and create a feature branch (`git checkout -b feature/amazing-feature`).
2. Commit your changes with clear, descriptive commit messages.
3. Submit a Pull Request targeting the `main` branch.
4. Wait for code review and latency benchmark verification by Ninad Phalak.
