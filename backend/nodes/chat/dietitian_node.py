from backend.tenancy import TenantContext, load_tenant

from backend.services.contracts import BaseDietitianService


from backend.graphs.graph_state import GraphState


class DietitianNode:

    def __init__(self, dietitian_service: BaseDietitianService, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()
        self.dietitian_service = dietitian_service

    async def __call__(self, state: GraphState) -> dict:
        
        dietitian_url = self.dietitian_service.get_dietitian_url()
        text = self.tenant.render("dietitian_contact", contact_url=dietitian_url or "No contact form is configured.")

        return {
            "result": {
                "mode": "dietitian_escort",
                "text": text,
            }
        }
