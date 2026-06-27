# schemas/__init__.py
from .account import AccountCreate, AccountUpdate, AccountResponse, AccountList
from .contact import ContactCreate, ContactUpdate, ContactResponse, ContactList
from .lead import LeadCreate, LeadUpdate, LeadResponse, LeadList, LeadConvert
from .opportunity import (
    OpportunityCreate,
    OpportunityUpdate,
    OpportunityResponse,
    OpportunityList,
)

__all__ = [
    "AccountCreate", "AccountUpdate", "AccountResponse", "AccountList",
    "ContactCreate", "ContactUpdate", "ContactResponse", "ContactList",
    "LeadCreate", "LeadUpdate", "LeadResponse", "LeadList", "LeadConvert",
    "OpportunityCreate", "OpportunityUpdate", "OpportunityResponse", "OpportunityList",
]
