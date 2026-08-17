from pydantic import BaseModel, Field
from typing import List, Optional

class CustomRegexPattern(BaseModel):
    name: str = Field(..., description="Name of the custom entity (e.g., INTERNAL_ID)")
    pattern: str = Field(..., description="The regex pattern to compile")
    description: Optional[str] = Field("", description="Optional description")

class CustomRegexConfig(BaseModel):
    custom_patterns: List[CustomRegexPattern]
