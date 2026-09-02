# 30-Day Design Partner Pilot

LLM-Shield-Proxy is accepting a small number of design partners that need to evaluate
streaming privacy controls for LLM or MCP traffic in a regulated environment. The pilot is
an evaluation program, not a compliance certification or a commitment to deploy in
production.

## Who the pilot is for

The best fit is a technical team in one of these groups:

- healthcare or life-sciences AI platforms handling PHI or patient identifiers;
- legal-document and professional-services AI products handling client-confidential data;
- financial-services AI platforms handling NPI, PCI data, credentials, or internal identifiers;
- security consultancies evaluating or deploying LLM gateways for clients; or
- regulated startups preparing technical evidence for a SOC 2 or similar control program.

A participating team should have:

- a named technical evaluator who can run the proxy in a non-production environment;
- an OpenAI-compatible streaming workload, MCP workload, or a representative synthetic fixture;
- one 30-minute working session per week during the pilot;
- authority to share aggregate, PII-free results and technical feedback with the maintainer; and
- acceptance criteria agreed before testing begins.

Do not send production prompts, credentials, patient records, customer data, or other
protected values to the project maintainer. The offline assessor is designed to run inside the
participant's environment and produce aggregate artifacts without including matched values or
source records.

## What happens during the 30 days

### Week 1 - Scope and baseline

- identify the model-provider request to test and the data types in scope;
- pin the proxy version, configuration, and deployment topology;
- run the [privacy-safe offline assessment](/docs/guides/pilot-assessment); and
- agree on success, failure, and stop conditions.

### Week 2 - Integration

- connect one representative application or synthetic test harness;
- exercise the selected masking mode and upstream provider path;
- verify SSE framing and authorized rehydration behavior; and
- record configuration-specific gaps or unsupported payloads.

### Week 3 - Security and operations

- run the conformance checks for fragmentation and configured-upstream egress;
- test fail-closed behavior for the enabled policy gates;
- select and test the intended audit durability mode; and
- run a production-shaped load profile defined by the participant.

### Week 4 - Decision packet

- compare results with the acceptance criteria;
- document limitations, failed cases, and remediation owners;
- produce a PII-free pilot summary with artifact checksums; and
- decide whether to stop, extend the evaluation, or plan a separately reviewed deployment.

## Suggested acceptance criteria

The participant and maintainer choose the criteria that apply before the pilot starts. A
typical packet covers:

1. **Reproducible installation:** a pinned package version or container digest starts with the
   documented configuration.
2. **Outgoing provider request:** the selected test values are absent from the request sent to the
   model provider after the proxy applies its configured changes.
3. **Streaming correctness:** the published fragmentation cases preserve valid SSE framing and
   the expected reconstructed content.
4. **Detection scope:** the chosen detector configuration is evaluated on participant-owned
   positive and hard-negative fixtures.
5. **Failure behavior:** enabled security and policy gates are tested for timeout, unavailable
   dependency, and invalid-configuration cases.
6. **Audit evidence:** the selected delivery mode, signing-key handling, verification, and
   external retention boundary are documented.
7. **Operational fit:** latency, throughput, and process RSS are measured on the participant's
   deployment rather than inferred from component microbenchmarks.
8. **Known limitations:** exclusions and unresolved risks are written into the decision packet.

Passing a pilot means only that the agreed tests passed in the documented environment. It does
not certify the participant, deployment, or product as compliant.

## Confidentiality and publication choices

Participants choose one of three evidence tracks:

- **Confidential:** results remain between the participant and maintainer, subject to any
  separately agreed confidentiality terms.
- **Attributable private reference:** the participant may provide a signed factual evaluation
  statement for limited due-diligence use without public logo or name placement.
- **Public case study:** both parties approve the organization name, architecture, measurements,
  limitations, and final text before publication.

Participation does not require a testimonial, positive conclusion, public endorsement, or
production deployment.

## Apply by email

Email [ninad.phalak@gmail.com](mailto:ninad.phalak@gmail.com?subject=LLM-Shield-Proxy%2030-Day%20Pilot)
with the subject **LLM-Shield-Proxy 30-Day Pilot** and include:

- your name, role, organization, and industry;
- the privacy or governance problem you are evaluating;
- your current LLM framework, gateway, upstream provider, and deployment environment;
- whether the workload uses SSE, MCP, or both;
- the data classes you plan to simulate or evaluate locally;
- your preferred start window; and
- confidential, attributable-private, or public evidence track.

The initial email should contain no sensitive payloads or credentials. If the fit is reasonable,
the next step is a 30-minute scope call and a written one-page pilot charter.
