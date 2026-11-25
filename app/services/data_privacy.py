"""Privacy boundaries for future authenticated data management."""
from abc import ABC, abstractmethod

# This classification is documentation, not an encryption or authorization mechanism.
SENSITIVE_TABLES = frozenset({'members','member_preferences','member_allergies','member_health_constraints','member_nutrition_targets','inventory_items','meal_sessions','meal_session_participants','meal_records','meal_record_items','nutrition_intakes','recommendation_requests','recommendation_candidates','recommendation_feedback','conversation_sessions','conversation_messages','preference_evidence','inferred_preferences','household_behavior_summary','cooking_sessions'})

def redact_sensitive(_value):
    return '[민감 데이터 생략]'

class PrivateDataManager(ABC):
    """Implement only with authentication, authorization and a deletion/backup policy."""
    @abstractmethod
    def delete_household(self, household_id):
        raise NotImplementedError

    @abstractmethod
    def export_encrypted(self, household_id, destination):
        raise NotImplementedError
