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

    // Catches ANY link target ending in these specific anchors and replaces the whole path
    newContent = newContent.replace(
        /\]\([^)]*#cryptographic-canary-prompt-tripwires\)/g,
        '](/docs/features/advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires)'
    );

    newContent = newContent.replace(
        /\]\([^)]*#entity-weighted-blast-radius-limits\)/g,
        '](/docs/features/advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits)'
    );

    newContent = newContent.replace(
        /\]\([^)]*#llm-finops-chargeback-meter\)/g,
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

console.log(`\nDone! Modified ${modifiedCount} files. Your build should now be 100% clean.`);