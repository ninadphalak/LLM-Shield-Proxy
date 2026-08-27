import glob

replacements = {
    'PLUGGABLE_RBAC_ENGINE.md': 'pluggable-rbac-engine.md',
    'architecture_whitepaper.md': 'architecture-whitepaper.md',
    'pluggable-tool-call-rbac-mcp-governance-.md': 'pluggable-tool-call-rbac-mcp-governance.md',
    'tls_mtls_support.md': 'tls-mtls-support.md'
}

for filepath in glob.glob('**/*.md', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content
    for old_val, new_val in replacements.items():
        new_content = new_content.replace(old_val, new_val)

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated links in {filepath}')
