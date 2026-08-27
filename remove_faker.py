import glob
import re

for filepath in glob.glob('**/*.md', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # We want to replace "Faker" with "canonical locale substitution"
    # But only if it's not in the context of "the open-source Faker library" or something.
    # Actually, the user wants us to get rid of the Faker branding completely where not necessary.
    # Let's see how it's used.

    new_content = re.sub(r'\bFaker\b', 'canonical locale substitution', content, flags=re.IGNORECASE)

    # Wait, replacing all Faker might break "the open-source canonical locale substitution library"
    # So I'll just blindly replace Faker, then fix the specific cases manually if they are weird.

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Removed Faker from {filepath}')
