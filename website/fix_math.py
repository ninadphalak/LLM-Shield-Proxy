import os, re
docs_dir = r'C:\git_repo\LLM-Shield-Proxy\website\docs'
for root, dirs, files in os.walk(docs_dir):
    for file in files:
        if file.endswith('.md'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find and replace all inline math $...$ with `...`
            # But be careful not to match `$ ` or ` $`.
            content = re.sub(r'\$([^\n\$]+)\$', r'`\1`', content)
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
