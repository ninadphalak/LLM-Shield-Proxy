import re

with open("old_readme.md", "r", encoding="utf-16") as f:
    old_content = f.read()

with open("README.md", "r", encoding="utf-8") as f:
    new_content = f.read()

def extract_section(text, header_start, next_header_start=None):
    # Find the start
    start_idx = text.find(header_start)
    if start_idx == -1: return ""

    # Find the end
    if next_header_start:
        end_idx = text.find(next_header_start, start_idx + len(header_start))
        if end_idx == -1: end_idx = len(text)
    else:
        # Find next header of same or higher level (e.g. ## if we are looking for ##)
        level_match = re.match(r'^(#+)\s', header_start)
        if level_match:
            level = len(level_match.group(1))
            pattern = re.compile(rf'^{"#" * 1, level}\s', re.MULTILINE)
            match = pattern.search(text, start_idx + len(header_start))
            if match:
                end_idx = match.start()
            else:
                end_idx = len(text)
        else:
            end_idx = len(text)

    return text[start_idx:end_idx].strip()

# Extract sections
orchestrators = extract_section(old_content, "### 🤝 The Orchestrators (What we complement)", "---\n\n## 🛡️ Dual-Pipeline Redaction Modes")
hardware = extract_section(old_content, "## 🏢 Enterprise Hardware Sizing Guide", "---\n\n## ⚡ Performance & Latency Benchmarks")
roadmap = extract_section(old_content, "## 🌍 Open Source Roadmap & Contributions", "---\n\n## 🏢 Enterprise Support & Community")
support = extract_section(old_content, "## 🏢 Enterprise Support & Community", "---\n\n\n## 📚 Enterprise Documentation Hub")
ip = extract_section(old_content, "## 📄 Intellectual Property & Licensing", "---\n\n## Citation")
citation = extract_section(old_content, "## Citation", None)

# Add them to the new README
# I will insert Orchestrators right after the Presidio section
parts = new_content.split("---\n\n## 🛡️ Dual-Pipeline Redaction Modes")

final_readme = parts[0] + orchestrators + "\n\n---\n\n## 🛡️ Dual-Pipeline Redaction Modes" + parts[1]

# Now append the rest before the Documentation list
doc_split = final_readme.split("## 📖 Complete Documentation")
final_readme = doc_split[0] + hardware + "\n\n---\n\n" + roadmap + "\n\n---\n\n" + support + "\n\n---\n\n" + ip + "\n\n---\n\n" + citation + "\n\n---\n\n## 📖 Complete Documentation" + doc_split[1]

with open("README.md", "w", encoding="utf-8") as f:
    f.write(final_readme)
