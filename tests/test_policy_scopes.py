"""Tests for Granular Entity Policy Scopes (O(1) pre-compilation routing)."""

import os
import tempfile

import yaml

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import PIIEngine
from llm_shield_proxy.engines.vault import Vault


def test_granular_policy_scopes():
    """Tests Pydantic validation, tenant routing, and differential masking."""
    custom_yaml = {
        "custom_patterns": [
            {
                "name": "INTERNAL_PROJECT",
                "pattern": r"(?i)PRJ-\d{4}",
            }
        ],
        "profiles": [
            {
                "name": "hipaa_strict",
                "tier1_regex": ["SSN", "PHONE", "EMAIL"],
                "tier2_ner": ["PERSON"]
            },
            {
                "name": "dev_general",
                "tier1_regex": ["INTERNAL_PROJECT"],
                "tier2_ner": []
            }
        ],
        "tenant_mappings": {
            "tenant_a_hipaa": "hipaa_strict",
            "tenant_b_dev": "dev_general"
        }
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as tmp:
        yaml.dump(custom_yaml, tmp)
        tmp_path = tmp.name

    try:
        original_path = settings.CUSTOM_REGEX_PATH
        settings.CUSTOM_REGEX_PATH = tmp_path

        engine = PIIEngine(enable_tier2=False, enable_tier3=True)
        vault_hipaa = Vault(synthetic=False)
        vault_dev = Vault(synthetic=False)
        vault_global = Vault(synthetic=False)

        # Retrieve profiles dynamically (simulating hot-path)
        hipaa_profile = engine.get_profile("tenant_a_hipaa")
        dev_profile = engine.get_profile("tenant_b_dev")
        unmapped_profile = engine.get_profile("unmapped_tenant")

        sample_text = "Patient John Doe (SSN 123-45-6789) is on project PRJ-9021."

        # Tenant A (hipaa_strict) should redact Person and SSN, but ignore INTERNAL_PROJECT
        redacted_hipaa = engine.redact_text(sample_text, vault_hipaa, hipaa_profile)
        assert "[PERSON_1]" in redacted_hipaa
        assert "[SSN_1]" in redacted_hipaa
        assert "PRJ-9021" in redacted_hipaa

        # Tenant B (dev_general) should redact INTERNAL_PROJECT, but ignore Person and SSN
        redacted_dev = engine.redact_text(sample_text, vault_dev, dev_profile)
        assert "[INTERNAL_PROJECT_1]" in redacted_dev
        assert "John Doe" in redacted_dev
        assert "123-45-6789" in redacted_dev

        # Unmapped Tenant (Global Strict Fallback) should redact everything configured (SSN, Person, Internal Project)
        redacted_global = engine.redact_text(sample_text, vault_global, unmapped_profile)
        assert "[PERSON" in redacted_global
        assert "[SSN_1]" in redacted_global
        assert "[INTERNAL_PROJECT_1]" in redacted_global

    finally:
        settings.CUSTOM_REGEX_PATH = original_path
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
