# 30-Day Design Partner Pilot

The LLM-Shield-Proxy project accepts a limited number of design partners evaluating streaming privacy controls in regulated environments. This is a technical evaluation program, not a compliance certification.

## Target Audience
This pilot is designed for technical teams in:
- Healthcare/Life Sciences handling PHI.
- Financial Services handling NPI or PCI data.
- Legal/Professional Services handling confidential client documents.
- Security consultancies deploying LLM gateways.

## Requirements
Participants must provide:
- A designated technical evaluator.
- A non-production testing environment.
- A representative LLM or MCP workload (or synthetic fixtures).
- One 30-minute sync per week.

**Do not send production data or live credentials to the project maintainers.** All testing happens securely within your own environment.

## 30-Day Schedule

### Week 1: Scope & Baseline
Define the test workloads, select masking strategies, pin deployment configurations, and establish concrete success/failure criteria.

### Week 2: Integration
Connect a representative application. Verify the proxy correctly applies redaction and successfully reconstructs fragmented streaming (SSE) chunks. Document any unsupported payloads.

### Week 3: Security & Operations
Execute load tests and conformance benchmarks. Test fail-closed security gates, verify audit log durability, and measure latency/memory overhead in your specific infrastructure.

### Week 4: Decision
Evaluate results against Week 1 criteria. Compile a PII-free technical summary and decide whether to proceed with a production deployment.

## Next Steps
To apply, email `ninad.phalak@gmail.com` with the subject **LLM-Shield-Proxy 30-Day Pilot**. Include your industry, the privacy challenge you are addressing, your current LLM stack, and your preferred start date.
