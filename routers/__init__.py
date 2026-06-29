# routers/__init__.py
from .accounts import router as accounts_router
from .contacts import router as contacts_router
from .leads import router as leads_router
from .opportunities import router as opportunities_router

__all__ = [
    "accounts_router",
    "contacts_router",
    "leads_router",
    "opportunities_router",
]
