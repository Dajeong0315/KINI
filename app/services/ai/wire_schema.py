"""Strict schema applied immediately before serializing the external request."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .privacy_gateway import ALLOWED_EXCLUDED_REASON_CODES

class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

Amount = Annotated[float, Field(ge=0, le=100000)]

class Nutrition(WireModel):
    energy_kcal: Amount = 0
    carbohydrate_g: Amount = 0
    protein_g: Amount = 0
    fat_g: Amount = 0
    sodium_mg: Amount = 0

class Candidate(WireModel):
    candidate_code: str
    cook_time_minutes: int = Field(gt=0, le=600)
    nutrition_per_serving: Nutrition
    recommendation_reason_codes: list[Literal["PERSONALIZED"]]

class OutboundRecommendation(WireModel):
    schema_version: Literal["ai-recommendation-v1"]
    participant_count: int = Field(gt=0)
    meal_type: Literal["breakfast","lunch","dinner","snack"]
    cook_time_limit_minutes: int | None
    purchase_allowed: bool
    constraint_codes: list[str]
    validated_candidates: list[Candidate] = Field(min_length=1)
    excluded_reason_codes: list[str]

    @model_validator(mode="after")
    def check_codes(self):
        if any(code != f"constraint_{i}" for i, code in enumerate(self.constraint_codes, 1)):
            raise ValueError("Invalid constraint code")
        if any(c.candidate_code != f"candidate_{i}" or not c.nutrition_per_serving.model_fields_set
               for i, c in enumerate(self.validated_candidates, 1)):
            raise ValueError("Invalid candidate")
        if set(self.excluded_reason_codes) - ALLOWED_EXCLUDED_REASON_CODES:
            raise ValueError("Invalid exclusion code")
        return self
