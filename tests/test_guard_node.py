"""Parent step: validation and the branch run together; a rejection cancels the branch."""

import asyncio

from langchain_core.messages import HumanMessage

from Models.MessageAnalysis import MessageAnalysis
from Models.RequestValidation import RequestValidation
from Nodes.GuardNode import FALLBACK_REASON, GuardNode


class Validator:
    def __init__(self, validation=None, error=None, delay=0):
        self.validation = validation
        self.error = error
        self.delay = delay

    async def validate(self, message, history=""):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.validation


class Branch:
    def __init__(self, delay=0, result=None):
        self.delay = delay
        self.result = result or {"result": {"mode": "chat", "text": "ok"}}
        self.called = False
        self.cancelled = False

    async def __call__(self, state):
        self.called = True
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return dict(self.result)


def guard(validator, chat=None, recipe=None, dietitian=None):
    return GuardNode(
        validator=validator,
        chat_node=chat or Branch(),
        recipe_node=recipe or Branch(),
        dietitian_node=dietitian or Branch(),
    )


def state(intent="chat"):
    return {
        "message": "שלום",
        "messages": [HumanMessage(content="שלום")],
        "analysis": MessageAnalysis(
            intent=intent,
            recipe_mode="none",
            ingredients=[],
            preferences=[],
            confidence=0.9,
        ),
    }


def test_rejected_request_cancels_the_branch_and_returns_the_reason():
    branch = Branch(delay=30)
    node = guard(
        Validator(RequestValidation(legitimate=False, reason="זה לא קשור למתכונים"), delay=0.01),
        chat=branch,
    )

    result = asyncio.run(node(state()))

    assert branch.cancelled
    assert result["final_response"] == "זה לא קשור למתכונים"
    assert result["result"] is None
    assert result["validation"].legitimate is False


def test_empty_reason_uses_the_fallback():
    node = guard(Validator(RequestValidation(legitimate=False, reason="  ")))

    result = asyncio.run(node(state()))

    assert result["final_response"] == FALLBACK_REASON


def test_legitimate_request_keeps_the_branch_result():
    branch = Branch(delay=0.05, result={"result": {"mode": "existing", "recipes": []}})
    node = guard(
        Validator(RequestValidation(legitimate=True, reason=""), delay=0.01),
        recipe=branch,
    )

    result = asyncio.run(node(state(intent="food")))

    assert branch.called
    assert not branch.cancelled
    assert result["result"]["mode"] == "existing"
    assert result["validation"].legitimate is True


def test_finished_branch_is_discarded_when_validation_rejects():
    branch = Branch(result={"result": {"mode": "chat", "text": "secret"}, "messages": []})
    node = guard(
        Validator(RequestValidation(legitimate=False, reason="לא"), delay=0.05),
        chat=branch,
    )

    result = asyncio.run(node(state()))

    assert branch.called
    assert result["final_response"] == "לא"
    assert result["result"] is None
    assert "secret" not in str(result.get("messages"))


def test_validator_failure_continues_the_branch():
    branch = Branch(result={"result": {"mode": "chat", "text": "hello"}})
    node = guard(Validator(error=RuntimeError("down")), chat=branch)

    result = asyncio.run(node(state()))

    assert result["result"]["text"] == "hello"
    assert "validation" not in result


def test_food_and_dietitian_pick_their_branches():
    recipe = Branch()
    dietitian = Branch()
    chat = Branch()
    node = guard(
        Validator(RequestValidation(legitimate=True)),
        chat=chat,
        recipe=recipe,
        dietitian=dietitian,
    )

    asyncio.run(node(state(intent="food")))
    asyncio.run(node(state(intent="dietitian_escort")))

    assert recipe.called
    assert dietitian.called
    assert not chat.called
