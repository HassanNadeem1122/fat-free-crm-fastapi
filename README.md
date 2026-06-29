# Fat Free CRM > FastAPI Migration
> Migrated the core of [Fat Free CRM](https://github.com/fatfreecrm/fat_free_crm) — a Ruby on Rails CRM with 3,600+ GitHub stars used by real companies — to modern async Python.

**5,913 lines of Python. 4 modules. 35 endpoints. Full test suite. Zero shortcuts.**

---

## Why this project exists

Most companies running Rails CRMs from 2015 aren't rewriting them — they're duct-taping them. Slow response times, a shrinking pool of Rails devs, and mounting tech debt are the usual signs.

This is a real migration case study. Not a toy. Not a tutorial. I took the actual Fat Free CRM source, read every model and controller, and rebuilt the core in FastAPI — preserving every piece of business logic, every validation, every soft delete, every relationship.

I run a codebase migration service for companies stuck on legacy stacks. This is what the output looks like.

---

## What got migrated

| Module | What it does |
|---|---|
| **Contacts** | Full CRUD, vCard export, text search, soft delete + restore, user assignment |
| **Leads** | Full CRUD, lead-to-contact/account/opportunity conversion, soft delete |
| **Accounts** | Full CRUD, contact linking/unlinking, subsidiary relationships |
| **Opportunities** | Full CRUD, pipeline dashboard, stage tracking, revenue forecasting |

---

## Stack comparison

| Layer | Rails (original) | FastAPI (migrated) |
|---|---|---|
| Framework | Ruby on Rails 6 | FastAPI + Uvicorn |
| ORM | ActiveRecord | SQLAlchemy 2.0 async |
| Auth | Devise (sessions) | JWT Bearer tokens + bcrypt |
| Validation | ActiveModel | Pydantic v2 |
| Database | PostgreSQL | PostgreSQL (asyncpg) |
| Tests | RSpec + FactoryBot | pytest + httpx async |
| Migrations | ActiveRecord migrate | Alembic |

---

## Project structure

```
├── main.py                   # FastAPI app, CORS, middleware, error handlers
├── auth.py                   # bcrypt password hashing + JWT token utilities
├── database.py               # Async SQLAlchemy engine + session factory
├── requirements.txt
│
├── models/
│   ├── user.py               # User model (replaces Devise)
│   ├── contact.py            # Contact with relationships + search
│   ├── lead.py               # Lead with conversion logic
│   ├── account.py            # Account with subsidiary tree
│   └── opportunity.py        # Opportunity with pipeline stages
│
├── schemas/
│   ├── contact.py            # Create / Update / Response variants
│   ├── lead.py
│   ├── account.py
│   └── opportunity.py
│
├── routers/
│   ├── auth.py               # Login, register, logout
│   ├── contacts.py           # 8 endpoints
│   ├── leads.py              # 8 endpoints (incl. conversion)
│   ├── accounts.py           # 8 endpoints
│   ├── opportunities.py      # 8 endpoints + dashboard
│   └── dependencies.py       # get_current_user, get_admin_user
│
└── tests/
    ├── conftest.py           # Async test client + fixtures
    ├── test_contacts.py
    ├── test_leads.py
    ├── test_accounts.py
    └── test_opportunities.py
```

---

## Running it locally

**Requirements:** Python 3.11+, PostgreSQL

```bash
git clone https://github.com/HassanNadeem1122/fat-free-crm-fastapi
cd fat-free-crm-fastapi

pip install -r requirements.txt

export DATABASE_URL=postgresql+asyncpg://user:pass@localhost/crm
export JWT_SECRET=your-random-secret-key

uvicorn main:app --reload
```

Interactive API docs: `http://localhost:8000/api/docs`

**Tests** (no PostgreSQL needed — uses SQLite in-memory):

```bash
pytest tests/ -v
```

---

## All 35 endpoints

```
POST   /api/v1/auth/login
POST   /api/v1/auth/register
POST   /api/v1/auth/logout

GET    /api/v1/contacts
POST   /api/v1/contacts
GET    /api/v1/contacts/{id}
PATCH  /api/v1/contacts/{id}
DELETE /api/v1/contacts/{id}
PUT    /api/v1/contacts/{id}/restore
GET    /api/v1/contacts/{id}/vcard
GET    /api/v1/contacts/{id}/opportunities

GET    /api/v1/leads
POST   /api/v1/leads
GET    /api/v1/leads/{id}
PATCH  /api/v1/leads/{id}
DELETE /api/v1/leads/{id}
PUT    /api/v1/leads/{id}/restore
POST   /api/v1/leads/{id}/convert

GET    /api/v1/accounts
POST   /api/v1/accounts
GET    /api/v1/accounts/{id}
PATCH  /api/v1/accounts/{id}
DELETE /api/v1/accounts/{id}
PUT    /api/v1/accounts/{id}/restore
POST   /api/v1/accounts/{id}/contacts/{contact_id}
DELETE /api/v1/accounts/{id}/contacts/{contact_id}

GET    /api/v1/opportunities
GET    /api/v1/opportunities/dashboard
POST   /api/v1/opportunities
GET    /api/v1/opportunities/{id}
PATCH  /api/v1/opportunities/{id}
DELETE /api/v1/opportunities/{id}
PUT    /api/v1/opportunities/{id}/restore
```

---

## How Rails patterns translate to Python

Every converted pattern has a comment in the code explaining what it replaces. A few examples:

**ActiveRecord callbacks → SQLAlchemy events**
```python
# Rails: before_save :set_subscribed_users
# Rails: after_create :log_activity

@event.listens_for(Contact, "before_insert")
def set_subscribed_users(mapper, connection, target):
    ...
```

**Rails scopes → class methods**
```python
# Rails: scope :created_by, ->(user) { where(user_id: user.id) }

@classmethod
def scope_created_by(cls, user_id: int):
    return cls.scope_active().where(cls.user_id == user_id)
```

**acts_as_paranoid → soft delete pattern**
```python
# Rails: acts_as_paranoid
# Every delete sets deleted_at instead of removing the row.
# Restore clears it. All queries filter deleted_at IS NULL by default.

deleted_at: Mapped[Optional[datetime]] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

**Devise → JWT**
```python
# Rails: authenticate_user! (before_action, Devise)
# FastAPI: Depends(get_current_user) on every protected router

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> UserContext:
    ...
```

**ActiveRecord validations → Pydantic v2**
```python
# Rails: validates :email, presence: true, uniqueness: true, format: { with: URI::MailTo::EMAIL_REGEXP }

class ContactCreate(BaseModel):
    email: EmailStr  # Pydantic validates format automatically
    first_name: str = Field(..., min_length=1, max_length=64)
```

---

## What's not in this repo

This migration covers the four core CRM modules. The full Fat Free CRM also has campaigns, activities, tasks, calendar integration, and email — those aren't here. The auth layer uses JWT instead of Rails sessions; a production deployment would add refresh tokens and a Redis-backed token denylist for logout.

---

## Want this for your codebase?

I migrate legacy Rails, PHP, and .NET codebases to modern stacks. Fast turnaround, clean output, documented conversions.

📧 just1hassanhere@gmail.com
