"""Separate classification scores from the application's admission decision."""

from abc import ABC, abstractmethod
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.clients.context import NonEmptyText
from backend.workflows.contracts import ConversationRequest


Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class GuardModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")


class GuardPolicy(GuardModel):
    # Trusted client configuration. These instructions never come from a chat body.
    safety_instructions: NonEmptyText
    support_scope: NonEmptyText
    # Deliberately required: thresholds must be chosen using application examples.
    max_unsafe_probability: Annotated[float, Field(ge=0, lt=1, allow_inf_nan=False)]
    min_relevant_probability: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    unsafe_reply: NonEmptyText
    irrelevant_reply: NonEmptyText
    unavailable_reply: NonEmptyText
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 10.0


class GuardAssessment(GuardModel):
    unsafe_probability: Probability
    relevant_probability: Probability


class GuardDecision(GuardModel):
    outcome: Literal["allow", "unsafe", "out_of_scope", "unavailable"]
    reply: str = ""
    assessment: GuardAssessment | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


class GuardEvaluator(ABC):
    @abstractmethod
    async def evaluate(
        self, request: ConversationRequest, *, policy: GuardPolicy, history: str,
    ) -> GuardAssessment:
        """Classify evidence; the caller applies thresholds and decides admission."""
