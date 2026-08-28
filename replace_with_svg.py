import re
import os

# 1. Create the new SVG file
svg_content = """<svg width="900" height="450" viewBox="0 0 900 450" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <style>
      .text { font-family: system-ui, -apple-system, sans-serif; font-size: 13px; font-weight: 500; fill: #1e293b; }
      .title { font-family: system-ui, -apple-system, sans-serif; font-size: 15px; font-weight: 700; fill: #0f172a; }
      .subgraph-box { fill: transparent; stroke: #94a3b8; stroke-width: 1.5; stroke-dasharray: 4 4; rx: 8; }
      .app-node { fill: #ffffff; stroke: #cbd5e1; stroke-width: 2; rx: 8; }
      .router-node { fill: #f8fafc; stroke: #64748b; stroke-width: 2; }
      .stateful-node { fill: #fffbeb; stroke: #f59e0b; stroke-width: 2; rx: 6; }
      .stateless-node { fill: #eff6ff; stroke: #3b82f6; stroke-width: 2; rx: 6; }
      .vault-node { fill: #fffbeb; stroke: #d97706; stroke-width: 2; rx: 12; }
      .arrow-line { stroke: #64748b; stroke-width: 2; fill: none; }
      .arrow-head { fill: #64748b; }
      .arrow-dashed { stroke: #64748b; stroke-width: 2; fill: none; stroke-dasharray: 4 4; }
      .label-text { font-family: system-ui, -apple-system, sans-serif; font-size: 12px; fill: #334155; font-weight: 700; }
      .label-bg { fill: #ffffff; }

      @media (prefers-color-scheme: dark) {
        .text { fill: #f8fafc; }
        .title { fill: #f1f5f9; }
        .subgraph-box { stroke: #475569; }
        .app-node { fill: #0f172a; stroke: #334155; }
        .router-node { fill: #0f172a; stroke: #475569; }
        .stateful-node { fill: #451a03; stroke: #b45309; }
        .stateless-node { fill: #1e3a8a; stroke: #2563eb; }
        .vault-node { fill: #451a03; stroke: #b45309; }
        .label-text { fill: #cbd5e1; }
        .label-bg { fill: #0d1117; } /* Match github dark bg */
      }
    </style>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" class="arrow-head" />
    </marker>
  </defs>

  <!-- Nodes -->
  <!-- App -->
  <rect x="20" y="180" width="160" height="60" class="app-node" />
  <text x="100" y="215" text-anchor="middle" class="text">Browser / IDE / App</text>
  
  <!-- Router (Diamond) -->
  <polygon points="270,170 320,210 270,250 220,210" class="router-node" />
  <text x="270" y="214" text-anchor="middle" class="text" style="font-weight:700;">JSON-RPC?</text>

  <!-- Sub A -->
  <rect x="400" y="30" width="240" height="230" class="subgraph-box" />
  <text x="415" y="55" class="title">A. Human-to-LLM (Choose One)</text>

  <rect x="420" y="70" width="200" height="36" class="stateful-node" />
  <text x="520" y="93" text-anchor="middle" class="text">1. SYNTHETIC</text>

  <rect x="420" y="120" width="200" height="36" class="stateful-node" />
  <text x="520" y="143" text-anchor="middle" class="text">2. STRUCTURAL_TAG</text>

  <rect x="420" y="170" width="200" height="36" class="stateless-node" />
  <text x="520" y="193" text-anchor="middle" class="text">3. SCRUB</text>

  <rect x="420" y="220" width="200" height="36" class="stateless-node" />
  <text x="520" y="243" text-anchor="middle" class="text">4. STATELESS_SYNTHETIC</text>

  <!-- Sub B -->
  <rect x="400" y="310" width="240" height="90" class="subgraph-box" />
  <text x="415" y="335" class="title">B. Machine-to-Machine</text>

  <rect x="420" y="350" width="200" height="36" class="stateless-node" />
  <text x="520" y="373" text-anchor="middle" class="text">STATELESS_SYNTHETIC</text>

  <!-- Redis -->
  <rect x="710" y="90" width="130" height="70" class="vault-node" />
  <text x="775" y="130" text-anchor="middle" class="text" style="font-weight:700;">Redis Vault</text>

  <!-- Arrows -->
  <!-- Client to Router -->
  <path d="M 180 210 L 215 210" class="arrow-line" marker-end="url(#arrow)" />
  
  <!-- Router to Sub A -->
  <path d="M 270 170 L 270 140 L 395 140" class="arrow-line" marker-end="url(#arrow)" />
  <rect x="290" y="128" width="60" height="20" class="label-bg" />
  <text x="320" y="142" text-anchor="middle" class="label-text">No: Text</text>

  <!-- Router to Sub B -->
  <path d="M 270 250 L 270 368 L 395 368" class="arrow-line" marker-end="url(#arrow)" />
  <rect x="290" y="356" width="70" height="20" class="label-bg" />
  <text x="325" y="370" text-anchor="middle" class="label-text">Yes: Agent</text>

  <!-- Syn to Redis -->
  <path d="M 620 88 L 705 110" class="arrow-dashed" marker-end="url(#arrow)" />

  <!-- Tag to Redis -->
  <path d="M 620 138 L 705 130" class="arrow-dashed" marker-end="url(#arrow)" />

</svg>
"""
svg_path = r"c:\git_repo\LLM-Shield-Proxy\docs\assets\diagram-dual-pipeline.svg"
with open(svg_path, "w", encoding="utf-8") as f:
    f.write(svg_content)

# 2. Modify README.md
readme_path = r"c:\git_repo\LLM-Shield-Proxy\README.md"
with open(readme_path, "r", encoding="utf-8") as f:
    readme = f.read()

# Replace Mermaid Dual-Pipeline with SVG image tag
old_dual_pattern = r"```mermaid\nflowchart TD\n    classDef default.*?style SubB.*?\n```"
new_dual = """<br>\n<img src="docs/assets/diagram-dual-pipeline.svg?v=1" alt="Dual-Pipeline Redaction Architecture" width="800" />"""
readme = re.sub(old_dual_pattern, new_dual, readme, flags=re.DOTALL)

# Delete Architecture Diagram Section
old_arch_pattern = r"## 🏗️ Architecture Diagram\n\n```mermaid\nflowchart TD\n    classDef default.*?AES -\.-\>\|4. Rehydrated\| Client\n```\n\n"
readme = re.sub(old_arch_pattern, "", readme, flags=re.DOTALL)

with open(readme_path, "w", encoding="utf-8") as f:
    f.write(readme)
