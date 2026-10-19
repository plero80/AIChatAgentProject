from backend.tenancy import TenantContext, load_tenant

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from backend.models.request_validation import RequestValidation
from backend.services.contracts import BaseRequestValidator


class RequestValidator(BaseRequestValidator):

    def __init__(self, llm: BaseChatModel, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.llm = llm

    async def validate(self, message: str, history: str = "") -> RequestValidation:
        content = message
        if history:
            content = (
                "Recent conversation:\n"
                + history
                + "\n\nCurrent user message:\n"
                + message
            )

        return await self.llm.with_structured_output(RequestValidation).ainvoke(
            [
                SystemMessage(content=self.tenant.render("validate")),
                HumanMessage(content=content),
            ]
        )
