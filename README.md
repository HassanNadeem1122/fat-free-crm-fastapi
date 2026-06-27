[README.md](https://github.com/user-attachments/files/29419820/README.md)

# Fat Free CRM → FastAPI Migration

Migrated the core CRM modules of [Fat Free CRM](https://github.com/fatfreecrm/fat_free_crm) (3,600+ GitHub stars, used by real businesses) from Ruby on Rails to Python FastAPI.

**5,913 lines of Python. 4 modules. 35 endpoints. Full test suite.**

---

## What got migrated

Fat Free CRM is a production Rails CRM that companies actually run. I took the four core business modules and rebuilt them in modern async Python:

- **Contacts** — full CRUD, vCard export, text search, soft delete, restore
- **Leads** — full CRUD, lead conversion to contact/account/opportunity
- **Accounts** — full CRUD, contact linking/unlinking, relationship management
- **Opportunities** — full CRUD, pipeline dashboard, stage tracking

Every piece of business logic from the original Rails code is preserved. ActiveRecord callbacks became SQLAlchemy events. Rails scopes became class methods. Devise auth became JWT. acts_as_paranoid soft delete carried over exactly.

---

## Stack

| Layer | Rails (original) | FastAPI (migrated) |
|---|---|---|
| Framework | Ruby on Rails 6 | FastAPI + Uvicorn |
| ORM | ActiveRecord | SQLAlchemy 2.0 async |
| Auth | Devise (sessions) | JWT Bearer tokens |
| Validation | ActiveModel | Pydantic v2 |
| DB | PostgreSQL | PostgreSQL (asyncpg) |
| Tests | RSpec + FactoryBot | pytest + httpx async |

---

## Project structure

```
crm_fastapi/
├── main.py              # FastAPI app, middleware, error handlers
├── auth.py              # bcrypt + JWT utilities
├── database.py          # async SQLAlchemy engine + session
├── models/              # SQLAlchemy ORM models
│   ├── user.py
│   ├── contact.py
│   ├── lead.py
│   ├── account.py
│   └── opportunity.py
├── schemas/             # Pydantic v2 request/response schemas
├── routers/             # FastAPI route handlers
│   ├── auth.py          # login, register, logout
│   ├── contacts.py      # 8 endpoints
│   ├── leads.py         # 8 endpoints (incl. lead conversion)
│   ├── accounts.py      # 8 endpoints
│   └── opportunities.py # 8 endpoints + dashboard
└── tests/               # pytest-asyncio test suite
```

---

## Running it

**Requirements:** Python 3.11+, PostgreSQL

```bash
git clone https://github.com/YOUR_USERNAME/fat-free-crm-fastapi
cd fat-free-crm-fastapi

pip install -r requirements.txt

export DATABASE_URL=postgresql+asyncpg://user:pass@localhost/crm
export JWT_SECRET=your-secret-key

uvicorn main:app --reload
```

API docs at `http://localhost:8000/api/docs`

**Run tests:**

```bash
pytest tests/ -v
```

Uses SQLite in-memory for tests — no PostgreSQL needed.

---

## API endpoints

```
POST   /api/v1/auth/login
POST   /api/v1/auth/register

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

## How the Rails patterns map to Python

The code has comments on every conversion. A few examples:

**ActiveRecord callbacks → SQLAlchemy events**
```python
# Rails:
# before_save :set_subscribed_users
# after_create :log_activity

# Python:
@event.listens_for(Contact, "before_insert")
def set_subscribed_users(mapper, connection, target):
    ...
```

**Rails scopes → class methods**
```python
# Rails:
# scope :created_by, ->(user) { where(user_id: user.id) }

# Python:
@classmethod
def scope_created_by(cls, user_id: int):
    return cls.scope_active().where(cls.user_id == user_id)
```

**Devise → JWT**
```python
# Rails: authenticate_user! (Devise before_action)
# Python: Depends(get_current_user) on every router
```

---

## What's not included

This migration covers the four core CRM modules. The full Fat Free CRM has additional modules (campaigns, activities, tasks, email integration) that aren't in this repo. The auth layer uses JWT instead of Rails sessions — a real deployment would add refresh tokens and a token denylist for logout.

---

## Why this exists

I run a codebase migration service for companies stuck on legacy Rails, PHP, and .NET stacks. This is a case study showing what AI-assisted migration looks like in practice — speed, quality, and what gets preserved versus rebuilt.

If your team is carrying Rails or PHP debt and wants to talk: [your email or LinkedIn]
