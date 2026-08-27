import yaml

with open(r"charts\llm-shield-proxy\values.yaml", "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

data["podSecurityContext"] = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "fsGroup": 1000
}

data["securityContext"] = {
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {
        "drop": ["ALL"]
    }
}

data["podDisruptionBudget"] = {
    "enabled": True,
    "maxUnavailable": 1
}

with open(r"charts\llm-shield-proxy\values.yaml", "w", encoding="utf-8") as f:
    yaml.dump(data, f, sort_keys=False)
