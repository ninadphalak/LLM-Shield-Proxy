# Streaming Privacy Gateway Specification Governance

The Streaming Privacy Gateway (SPG) specification is intended to become an
implementation-neutral test contract. LLM-Shield-Proxy is its first reference implementation,
not the authority that certifies itself or other products.

The community is still small, so the initial maintainer currently has substantial control. This
process records that control and explains how independent reviewers can join. A maintainer-only
decision is not community consensus.

## Scope

Governance covers normative conformance requirements, the report schema, result labeling,
versioning, and the process for accepting independent implementations and reproductions. Product
features and deployment defaults remain under the reference implementation's normal governance.

## Roles

- **Contributor:** anyone who opens an issue, supplies a counterexample, submits a result, or
  proposes text.
- **Reviewer:** a contributor with demonstrated subject-matter work who has reviewed at least two
  proposals or reproductions. Reviewers are listed publicly with their relevant affiliations.
- **Specification maintainer:** a reviewer responsible for releases, repository hygiene, and
  recording decisions. Ninad Phalak is the initial maintainer.

Reviewer status is earned through completed technical review, not purchased, granted for a
testimonial, or conditioned on a favorable view of the reference implementation.

## Proposing a change

Open an **SPG specification proposal** using the repository issue template. A proposal must state:

1. the failure mode or ambiguity being addressed;
2. the proposed normative behavior;
3. at least one positive fixture and one negative or counterexample fixture;
4. report-schema impact;
5. compatibility and migration impact; and
6. the proposer's relationship to any implementation directly affected by the change.

Editorial fixes that do not change observable conformance behavior may use a normal pull request.

## Decision process

- Normative proposals remain open for at least 14 calendar days.
- The maintainer requests implementation, security-assurance, and operator review when those
  perspectives are affected.
- A normative change should receive two approvals, including one person who did not author the
  reference-implementation change.
- While fewer than two independent reviewers exist, the initial maintainer may merge a necessary
  change after the review window, but the decision record must be labeled **maintainer-only**. It
  must not be described as community consensus.
- Unresolved objections and rejected alternatives remain linked from the decision record.

Security fixes may use private disclosure before publication. The eventual public record should
explain the normative change without exposing an unpatched vulnerability.

## Versioning

The specification uses semantic versioning for its observable contract:

- **Patch:** clarification or editorial correction that does not change whether a report passes.
- **Minor:** backward-compatible requirement or optional report field.
- **Major:** removal, incompatible schema change, or a requirement that can change an existing
  conforming result to non-conforming.

Every published result identifies the specification version, implementation revision, dependency
or image digest, configuration, environment, and report checksum.

## Result labels

Results identify who ran and funded them:

- **Implementation-affiliated:** produced by a contributor to the evaluated implementation.
- **Vendor-run:** produced or controlled by an organization responsible for the evaluated product.
- **Sponsored independent run:** produced by an unaffiliated evaluator whose compensation was not
  conditioned on a positive result.
- **Independent run:** produced by an unaffiliated evaluator without implementation-owner control.

Payment, equipment, cloud credits, consulting, and pre-publication review rights must be disclosed.
Failed and partial runs remain valid contributions when their artifacts are reproducible.

A target needs three runs from three submitters before the results table presents a verdict.
Until then, it says `unreplicated`. The reference implementation currently has one run from one
submitter, so it is also unreplicated.

Maintainer runs do not count toward replication. Conflicting runs appear as separate rows instead
of being averaged. [Submitting a result](./submitting) lists the required files and review steps.

## No certification program yet

The project does not currently operate a certification body, compliance seal, or paid conformance
logo. A passing report is evidence for the documented revision and environment only. It is not a
security guarantee or regulatory certification.

## Becoming a reviewer

Start by reproducing a report, contributing a counterexample, or reviewing an open proposal. After
two substantive reviews, open a governance issue listing the work and affiliation. Existing
reviewers record the decision publicly.

The immediate community goal is to recruit at least three external reviewers: one privacy/security
engineer, one platform operator, and one researcher or assurance practitioner.
