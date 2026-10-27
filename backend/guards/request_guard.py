"""A client-bound guard that checks safety and support relevance before execution."""

import asyncio
import logging

from backend.clients.context import ClientContext
from backend.guards.contracts import GuardAssessment, GuardDecision, GuardEvaluator, GuardPolicy
from backend.workflows.contracts import ConversationRequest


logger = logging.getLogger(__name__)


class RequestGuard:
    def __init__(self, *, client: ClientContext, policy: GuardPolicy, evaluator: GuardEvaluator):
        self.client = client
        self.policy = GuardPolicy.model_validate(policy)
        self.evaluator = evaluator

    async def check(self, request: ConversationRequest, *, history: str = "") -> GuardDecision:
        if request.client_id != self.client.client_id or request.release_id != self.client.release_id:
            raise ValueError("Request client/release does not match this guard")
        try:
            if not isinstance(history, str):
                raise TypeError("Guard history must be text")
            assessment = GuardAssessment.model_validate(await asyncio.wait_for(
                self.evaluator.evaluate(request, policy=self.policy, history=history),
                timeout=self.policy.timeout_seconds,
            ))
        except Exception as exc:
            # Includes timeout, API refusal/malformed data, and provider failures.
            # Cancellation is a BaseException and must propagate to the caller.
            logger.warning(
                "Guard unavailable client=%s release=%s error=%s",
                self.client.client_id, self.client.release_id, type(exc).__name__,
            )
            return self.unavailable()

        if assessment.unsafe_probability > self.policy.max_unsafe_probability:
            return GuardDecision(outcome="unsafe", reply=self.policy.unsafe_reply, assessment=assessment)
        if assessment.relevant_probability < self.policy.min_relevant_probability:
            return GuardDecision(outcome="out_of_scope", reply=self.policy.irrelevant_reply, assessment=assessment)
        return GuardDecision(outcome="allow", assessment=assessment)

    def unavailable(self) -> GuardDecision:
        """A technical failure never grants permission or labels the user unsafe."""
        return GuardDecision(outcome="unavailable", reply=self.policy.unavailable_reply)
