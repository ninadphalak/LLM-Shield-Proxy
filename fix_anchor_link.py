import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# Fix the anchor link in Flagship Features
text = text.replace("(#️-redaction-modes)", "(#️-dual-pipeline-redaction-architecture)")

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
