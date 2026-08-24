import os
import re
import shutil

FEATURES_FILE = "FEATURES.md"
DOCS_DIR = "docs/features"

def main():
    if not os.path.exists(FEATURES_FILE):
        print(f"Error: {FEATURES_FILE} not found.")
        return

    with open(FEATURES_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all sections and the files they contain
    # Sections are formatted like: ## Section A: Data Protection & PII Redaction
    sections = re.split(r'\n## ', content)

    header = sections[0]
    new_content = header

    file_moves = []

    for section in sections[1:]:
        lines = section.split('\n')
        section_title = lines[0].strip()
        # Create a folder name from the section title (e.g. "Section A: Data Protection & PII Redaction" -> "data-protection-pii")
        # Strip "Section X: "
        folder_base = re.sub(r'^Section [A-Z]:\s*', '', section_title)
        folder_name = re.sub(r'[^a-zA-Z0-9]+', '-', folder_base).strip('-').lower()

        target_dir = os.path.join(DOCS_DIR, folder_name)
        os.makedirs(target_dir, exist_ok=True)

        new_section_content = [f"## {section_title}"]

        for line in lines[1:]:
            match = re.search(r'\*\*\[(.*?)\]\((docs/features/([^/]+?\.md))\)\*\*', line)
            if match:
                title = match.group(1)
                old_path = match.group(2)
                filename = match.group(3)

                new_path = f"docs/features/{folder_name}/{filename}"
                new_line = line.replace(old_path, new_path)
                new_section_content.append(new_line)

                file_moves.append((os.path.join(DOCS_DIR, filename), os.path.join(target_dir, filename)))
            else:
                new_section_content.append(line)

        new_content += "\n".join(new_section_content)

    # Write updated FEATURES.md
    with open(FEATURES_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    # Move files and update their internal links
    success_count = 0
    for old_file, new_file in file_moves:
        if os.path.exists(old_file):
            shultil_moved = False
            try:
                shutil.move(old_file, new_file)
                shultil_moved = True
            except Exception as e:
                print(f"Failed to move {old_file}: {e}")

            if shultil_moved:
                # Update backlinks and image links
                with open(new_file, "r", encoding="utf-8") as f:
                    doc_content = f.read()

                doc_content = doc_content.replace("../../FEATURES.md", "../../../FEATURES.md")
                doc_content = doc_content.replace("./images/", "../images/")

                with open(new_file, "w", encoding="utf-8") as f:
                    f.write(doc_content)
                success_count += 1
        else:
            print(f"Warning: {old_file} not found")

    print(f"Successfully moved and updated {success_count} feature files into subfolders.")

if __name__ == "__main__":
    main()
