from backend.tenancy import TenantContext, load_tenant

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from backend.graphs.graph_state import GraphState
from backend.models.recipe_presentation_plan import RecipePresentationPlan
from backend.services.contracts import BaseRecipePresentationService


class ResponseNode:
    def __init__(
        self, llm: BaseChatModel, presentation_service: BaseRecipePresentationService,
        *, tenant: TenantContext | None = None,
    ):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.llm = llm
        self.presentation_service = presentation_service

    async def __call__(self, state: GraphState) -> dict:
        workflow_result = state.get("result") or {}
        if workflow_result.get("mode") == "existing" and workflow_result.get("recipes"):
            plan = await self.llm.with_structured_output(RecipePresentationPlan).ainvoke(
                [
                    ("system", self.tenant.render("recipe_presentation")),
                    *(state.get("messages") or [])[-6:],
                    (
                        "user",
                        json.dumps(
                            {
                                "current_request": state["message"],
                                "available_recipes": workflow_result["recipes"],
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                ]
            )
            plan = RecipePresentationPlan.model_validate(plan)
            presentation = self.presentation_service.build_recipe_response(
                workflow_result, plan.recipe_ids, plan.intro, plan.outro
            )
        else:
            response = await self.llm.ainvoke(
                [
                    ("system", self.tenant.render("general_response")),
                    (
                        "user",
                        f"Original user request:\n{state['message']}\n\n"
                        f"Workflow result:\n{workflow_result}",
                    ),
                ]
            )
            presentation = self.presentation_service.build_text_response(str(response.text))

        # Persist the exact recipes the user saw, so later turns refer to the
        # displayed selection rather than every candidate found by search.
        return {
            "final_response": presentation.reply,
            "response_blocks": [
                block.model_dump(mode="json") for block in presentation.blocks
            ],
            "messages": [AIMessage(content=presentation.reply)],
        }
