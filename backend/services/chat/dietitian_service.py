from backend.services.contracts import BaseDietitianService
from backend.tenancy import TenantContext, load_tenant


class DietitianService(BaseDietitianService):
    """The file-backed profile is read once during startup."""

    def __init__(self, *, tenant: TenantContext | None = None):
        self.tenant = tenant if tenant is not None else load_tenant()

    def get_dietitian_url(self) -> str:
        return str(self.tenant.profile.contact_url or "")
