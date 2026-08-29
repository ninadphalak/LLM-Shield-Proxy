const fs = require('fs');
const path = require('path');

const DOCS_DIR = path.join(__dirname, 'docs');

function walkSync(dir, callback) {
    const files = fs.readdirSync(dir);
    files.forEach((file) => {
        const filepath = path.join(dir, file);
        const stats = fs.statSync(filepath);
        if (stats.isDirectory()) {
            walkSync(filepath, callback);
        } else if (stats.isFile() && (filepath.endsWith('.md') || filepath.endsWith('.mdx'))) {
            callback(filepath);
        }
    });
}

function fixLinks(content) {
    let newContent = content;

    // 1. Fix the /docs/ index links -> Point them to the site homepage
    newContent = newContent.replace(/\]\(\/docs\/\)/g, '](/)');

    // 2. Fix sibling links that were moved to different feature categories
    newContent = newContent.replace(
        /\]\(\.\/stateless-redis-ttl-vault(?:\.md)?\)/g,
        '](/docs/features/data-protection-pii-redaction/stateless-redis-ttl-vault)'
    );
    newContent = newContent.replace(
        /\]\(\.\/automatic-finops-stream-options-injection(?:\.md)?\)/g,
        '](/docs/features/ultra-low-latency-streaming-traffic-engineering/automatic-finops-stream-options-injection)'
    );
    newContent = newContent.replace(
        /\]\(\.\/multi-provider-translators(?:\.md)?\)/g,
        '](/docs/features/ultra-low-latency-streaming-traffic-engineering/multi-provider-translators)'
    );
    newContent = newContent.replace(
        /\]\(\.\/zero-overhead-opentelemetry-otel-tracing(?:\.md)?\)/g,
        '](/docs/features/enterprise-auditing-compliance/zero-overhead-opentelemetry-otel-tracing)'
    );

    // 3. Fix anchors that now map to standalone feature pages
    newContent = newContent.replace(
        /\]\(\/docs\/security#cryptographic-canary-prompt-tripwires\)/g,
        '](/docs/features/advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires)'
    );
    newContent = newContent.replace(
        /\]\(\/docs\/security#entity-weighted-blast-radius-limits\)/g,
        '](/docs/features/advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits)'
    );
    newContent = newContent.replace(
        /\]\(\/docs\/compliance-overview#llm-finops-chargeback-meter\)/g,
        '](/docs/features/advanced-threat-defense-enterprise-resilience/llm-finops-chargeback-meter)'
    );

    return newContent;
}

let modifiedCount = 0;

walkSync(DOCS_DIR, (filepath) => {
    const original = fs.readFileSync(filepath, 'utf8');
    const fixed = fixLinks(original);

    if (original !== fixed) {
        fs.writeFileSync(filepath, fixed, 'utf8');
        console.log(`[FIXED] ${path.relative(__dirname, filepath)}`);
        modifiedCount++;
    }
});

console.log(`\nDone! Modified ${modifiedCount} files. Build should now pass.`);