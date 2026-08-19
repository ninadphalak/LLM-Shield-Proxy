import glob

for filepath in glob.glob('tests/**/*.py', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'monkeypatch.setattr("llm_shield_proxy.core.config.settings.valid_virtual_keys_set"' in content:
        content = content.replace('monkeypatch.setattr("llm_shield_proxy.core.config.settings.valid_virtual_keys_set"', 'monkeypatch.setattr("llm_shield_proxy.core.config.settings._valid_virtual_keys_set"')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed monkeypatch in {filepath}")
