[⬅️ Back to README](README.md)

# Contributing to LLM-Shield-Proxy

Contributions, bug reports, and documentation corrections are welcome.

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

## Documentation style

- Lead with what the feature does, what changes for the operator, and what it does not prove.
- Use short sentences, active voice, and common words. Define necessary technical terms on first use.
- State measured results with their workload and environment. Do not turn component measurements
  into end-to-end claims.
- Avoid marketing adjectives, metaphors, and absolute claims such as “zero overhead,” “seamless,”
  or “production-ready.”
- Preserve negative results and known limitations. If evidence is missing, say that directly.
- Treat examples as starting points, not deployment-ready configurations.

## Submission Workflow
1. Fork the repository and create a feature branch (`git checkout -b feature/amazing-feature`).
2. Commit your changes with clear, descriptive commit messages.
3. Submit a Pull Request targeting the `main` branch.
4. Wait for code review and latency benchmark verification by Ninad Phalak.
