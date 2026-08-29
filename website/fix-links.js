const fs = require('fs');
const path = require('path');

// Update this to your actual GitHub repository URL
const REPO_URL = 'https://github.com/YOUR_ORG/LLM-Shield-Proxy/blob/main';
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

    // 1. Convert relative links to the /tests/ directory into absolute GitHub URLs
    newContent = newContent.replace(/\]\((?:\.\.\/)+tests\/([^)]+)\)/g, `](${REPO_URL}/tests/$1)`);

    // 2. Convert root document links into absolute Docusaurus routes
    newContent = newContent.replace(/\]\((?:\.\.\/)*ARCHITECTURE\.md\)/gi, '](/docs/architecture)');
    newContent = newContent.replace(/\]\((?:\.\.\/)*features-overview\.md\)/gi, '](/docs/features-overview)');
    newContent = newContent.replace(/\]\((?:\.\.\/)*POLICIES\.md\)/gi, '](/docs/policies)');
    newContent = newContent.replace(/\]\((?:\.\.\/)*deployment\.md\)/gi, '](/docs/deployment)');
    newContent = newContent.replace(/\]\((?:\.\.\/)*SECURITY\.md\)/gi, '](/docs/security)');
    newContent = newContent.replace(/\]\((?:\.\.\/)*COMPLIANCE\.md\)/gi, '](/docs/compliance-overview)');

    // 3. Route README.md links back to the docs index
    newContent = newContent.replace(/\]\((?:\.\.\/)*README\.md\)/gi, '](/docs/)');

    // 4. Strip .md extension from sibling document links (e.g., ./stateless-redis.md -> ./stateless-redis)
    newContent = newContent.replace(/\]\(\.\/([a-zA-Z0-9-_]+)\.md\)/g, '](./$1)');

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

console.log(`\nDone! Modified ${modifiedCount} files.`);