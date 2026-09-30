# Synthetic audit of mutation-test scoring

This pilot checks how an existing mutation runner scores controlled incident-report
traces. It does not execute an LLM or a skill, and it does not measure production
failure rates. The ten rules and twenty defects were authored for this experiment;
independent domain review is pending.

The tested implementation is [AgentAssay at commit
7da9a259](https://github.com/qualixar/agentassay/tree/7da9a259717aee9bfb0abc40ce57b0e1df425881).
The external checkout remains unmodified. The adapter supplies synthetic observations
through its callable API and calls its actual `MutationRunner.run_suite` method.
No upstream source is copied into this repository.

## Scope and controls

[The pilot](skill_test_adequacy.py) declares ten rules for source scope, approval,
credential removal, destination, redaction ordering, size, source retention, audit
content, record scope, and delivery acknowledgement. Each has two deliberately
violating traces. These rules describe this fictional workflow only.

There are also four harmless changes, two declared configuration changes with no
observed violation, one blocked credential-upload attempt, an invalid operator, an
execution timeout, and a scoring-function exception. A blocked attempt is recorded
separately from an outbound effect. Unexecuted cases have null observation fields.
The traces contain a fixed, explicitly synthetic credential marker.

Three evaluators are compared on the same cases:

- The upstream default reads the trace's `success` flag.
- A supplied evaluator averages the ten policy checks.
- A supplied evaluator returns zero if any policy check fails.

The last two evaluators use the same assertions. Their difference tests score
aggregation. The default is not advertised here as a policy checker: it relies on
the caller to supply a meaningful success value or an appropriate evaluator.

## Reproduce

Use a separate environment for the optional external dependency. From this repository:

```sh
git clone https://github.com/qualixar/agentassay.git /path/to/agentassay
git -C /path/to/agentassay checkout 7da9a259717aee9bfb0abc40ce57b0e1df425881
python -m venv /path/to/audit-env
# Activate that environment using the command appropriate to your shell.
python -m pip install pydantic scipy numpy rich pyyaml click jinja2 pytest hypothesis
python -m pip install ./pii-leak-benchmark
python benchmarks/skill_test_adequacy.py --agentassay-source /path/to/agentassay --out /path/to/results.json
```

The script checks the external commit and refuses a dirty checkout. It records source
hashes, dependency versions, every input trace, each verdict, and invocation counts.
No model credentials or network calls are used during the replay. Dependency installation
and source retrieval require network access.

## Observations from the pilot

| Evaluator | Deliberate policy violations detected | Other behavior |
|---|---:|---|
| Default success flag | 0/20 | Does not inspect these policy assertions |
| Mean of ten checks | 0/20 | A score of 0.9 remains above the runner's 0.5 threshold |
| All ten checks required | 20/20 | Detects the explicitly constructed violations |

The 20/20 result is expected by construction. It demonstrates the contract between
the caller and runner; it does not establish superiority over other methods or
predict detection of unseen faults. None of the four harmless controls was rejected.

Two narrower implementation observations are reproducible at this commit:

1. The evaluator exception is converted to score zero without populating the result's
   error field. It is counted as a killed mutant even though its replayed trace is
   compliant. With the strict evaluator, the suite reports 21 kills across 30 cases
   (0.7), including that scoring failure. Operator and execution errors are separately
   identified, but still remain in the overall score's denominator.
2. `AssayConfig(num_trials=30)` and `num_trials=100` each produce 59 callable invocations:
   30 originals and 29 mutants. The invalid operator prevents one mutant invocation.
   This path performs one original/mutant comparison per operator. These observations
   concern `MutationRunner`, not other trial runners or statistical components.

All six configurations were repeated three times with the same verdicts. Repeating a
deterministic replay is a stability check, not a stochastic confidence estimate.
The twenty existing upstream mutation tests passed separately in the pilot environment.

## Prior work and limits

[AgentAssay](https://github.com/qualixar/agentassay) already supports mutation testing
for agent evaluations. [Proteus](https://arxiv.org/abs/2605.11891) combines mutated skills
with audit and runtime-oracle feedback. [Mutation Testing for Multi-Agent
Systems](https://doi.org/10.13140/RG.2.2.36810.53442) separates structural, outcome and
safety criteria and uses observed behavior to establish detectability.

This experiment does not establish an original method. It is an evaluator conformance
fixture and a possible starting point for targeted regression tests. It does not test
installed skill instructions, measure evaluator performance on real organizational
tests, establish adoption, or supply independent review of its rules. A broader study
would need those steps and a comparison with the closest existing implementations.
