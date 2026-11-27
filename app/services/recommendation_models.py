from typing import Annotated, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

class RecommendationRequest(BaseModel):
    household_id: int = Field(gt=0)
    participant_ids: List[int] = Field(min_length=1)
    meal_type: str = Field(min_length=1,max_length=30)
    cook_time_limit_minutes: Optional[int] = Field(default=None,gt=0)
    purchase_allowed: bool = False
    desired_menu: Optional[str] = None
    excluded_ingredients: List[str] = Field(default_factory=list)
    excluded_recipe_ids: List[int] = Field(default_factory=list)

class MealSessionRequest(BaseModel):
    household_id: int = Field(gt=0)
    participant_ids: List[int] = Field(min_length=1)
    meal_type: str = Field(min_length=1,max_length=30)

class MealRecordItem(BaseModel):
    member_id: int = Field(gt=0)
    servings: float = Field(gt=0,le=10)

class MealRecordRequest(BaseModel):
    session_id: int = Field(gt=0)
    recipe_id: int = Field(gt=0)
    eaten_at: str
    items: List[MealRecordItem] = Field(min_length=1)

class ConversationRequest(BaseModel):
    household_id: int = Field(gt=0)
    meal_session_id: Optional[int] = Field(default=None, gt=0)
    purpose: Literal['recommendation', 'cooking', 'feedback', 'general']

class MessageRequest(BaseModel):
    role: Literal['user']
    content: str = Field(min_length=1, max_length=1000)
    member_id: Optional[int] = Field(default=None, gt=0)

class FeedbackRequest(BaseModel):
    candidate_recipe_id: int = Field(gt=0)
    action: Literal['selected', 'rejected', 'cooked', 'skipped', 'rated']
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    member_id: Optional[int] = Field(default=None, gt=0)
    reason_code: Optional[str] = Field(default=None, max_length=150)
    reason_text: Optional[str] = Field(default=None, max_length=500)

class PreferenceSignal(BaseModel):
    feature_type: Literal['ingredient', 'recipe', 'cuisine', 'cooking_method', 'taste', 'cook_time', 'meal_type']
    feature_value: str = Field(min_length=1, max_length=100)
    signal: Literal['prefer', 'avoid']

class CookingAdjustmentRequest(BaseModel):
    member_id: Optional[int] = Field(default=None, gt=0)
    ingredient: str = Field(min_length=1, max_length=100)
    action: Literal['added', 'removed']

class CookingSessionRequest(BaseModel):
    meal_session_id: int = Field(gt=0)
    recommendation_request_id: int = Field(gt=0)
    recipe_id: int = Field(gt=0)

class CookingChangeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ingredient: str = Field(min_length=1, max_length=100)
    quantity_g: float = Field(ge=0, le=10000, allow_inf_nan=False)

class CookingMealItem(BaseModel):
    member_id: int = Field(gt=0)
    servings: float = Field(ge=0, le=10, allow_inf_nan=False)

class CookingCompleteRequest(BaseModel):
    confirmed: Literal[True]
    eaten_at: str
    items: List[CookingMealItem] = Field(min_length=1)
    inventory_event_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(
        default_factory=list, max_length=100
    )

class CookingQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)

class InventoryNormalizeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    quantity: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=30)

class InventoryItemRequest(InventoryNormalizeRequest):
    household_id: int = Field(gt=0)
    ingredient_id: int = Field(gt=0)
    freshness_state: Literal['good','normal','use_soon','unsafe'] = 'normal'
    product_name: Optional[str] = Field(default=None, max_length=150)
    label_confirmed: bool = False
    expiry_date: Optional[str] = None
    opened_at: Optional[str] = None
