from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CustomRegexPattern(BaseModel):
    name: str = Field(..., description="Name of the custom entity (e.g., INTERNAL_ID)")
    pattern: str = Field(..., description="The regex pattern to compile")
    description: Optional[str] = Field("", description="Optional description")

class PolicyProfile(BaseModel):
    name: str = Field(..., description="Name of the policy profile")
    tier1_regex: List[str] = Field(default_factory=list, description="List of active Tier 1 regex names")
    tier2_ner: List[str] = Field(default_factory=list, description="List of active Tier 2/3 entity names")

class CustomRegexConfig(BaseModel):
    custom_patterns: List[CustomRegexPattern] = Field(default_factory=list)
    profiles: List[PolicyProfile] = Field(default_factory=list)
    tenant_mappings: Dict[str, str] = Field(default_factory=dict, description="Map of virtual_key_id to profile name")
