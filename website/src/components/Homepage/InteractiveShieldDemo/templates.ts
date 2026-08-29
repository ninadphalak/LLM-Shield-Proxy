/**
 * Canned prompts for the "Tier 3 · ONNX NER" example dropdown. The real
 * proxy runs a local ONNX NER model to catch free-text names, orgs, and
 * addresses that regex alone would miss - that model doesn't ship to the
 * browser, so these templates are pre-written to *look* like the kind of
 * unstructured, real-world text Tier 3 is built for.
 */

export interface DemoTemplate {
  key: string;
  label: string;
  text: string;
}

export const TEMPLATES: DemoTemplate[] = [
  {
    key: 'hipaa',
    label: '🏥 HIPAA Medical Record',
    text: "Patient intake note: Sarah Connor called about her upcoming visit. DOB confirmed, SSN on file is 456-12-7890, contact email sarah.connor@acmehealth.com, callback number 555-341-7788. Please pull her chart before the 3pm appointment.",
  },
  {
    key: 'soc2',
    label: '📋 SOC 2 Audit Log',
    text: "Access review 2026-08-14: user john.doe@northgatebank.com (employee James Wilson) authenticated from 10.42.18.7 using internal service token sk-live-9f8a2c1e7b3d4f5a to pull the Q3 financials export. Flagged for manual review by Robert Chen.",
  },
  {
    key: 'finance',
    label: '🏦 Finance Wire Transfer',
    text: "Wire transfer request from Jane Smith. Card on file: 4111-2222-3333-4444. Verification email jane.smith@example-bank.com, phone 555-982-1044. Approve and notify Maria Garcia once settled.",
  },
  {
    key: 'support',
    label: '🎧 Customer Support Ticket',
    text: "Ticket #4471 from Emily Davis (emily.davis@customerco.io, 555-201-9944): can't log in from home IP 172.16.5.201. Confirmed identity via SSN last 4 shown as 456-12-7890. Escalating to Michael Brown on the infra team.",
  },
];
