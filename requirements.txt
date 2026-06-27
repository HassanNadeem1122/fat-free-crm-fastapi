# main.py
# ---------------------------------------------------------------------------
# FastAPI application entry point.
# Rails equivalent: config/application.rb + config/routes.rb + config/initializers/
#
# Rails → FastAPI mapping:
#   Rails.application.configure         → FastAPI() constructor
#   config/routes.rb resources :contacts → router.include_router(contacts_router)
#   config/initializers/devise.rb       → JWT config in dependencies.py
#   before_action :authenticate_user!   → Depends(get_current_user) on routers
#   config/middleware.rb                → FastAPI middleware stack
#   Rack::Cors                         → CORSMiddleware
#   ActionDispatch::RequestId          → X-Request-ID middleware
#   Rails.logger                       → Python logging
#   rescue_from ActiveRecord::NotFound → HTTPException 404 handler
#   rescue_from CanCan::AccessDenied   → HTTPException 403 handler
#   config.active_record.schema_format → Alembic migrations
# ---------------------------------------------------------------------------

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import engine, Base
from routers.accounts import router as accounts_router
from routers.contacts import router as contacts_router
from routers.leads import router as leads_router
from routers.opportunities import router as opportunities_router
from routers.auth import router as auth_router

# ---------------------------------------------------------------------------
# Logging
# Rails: config.log_level = :info
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("crm_fastapi")


# ---------------------------------------------------------------------------
# Lifespan: startup + shutdown
# Rails: config/initializers/ run at startup; after_initialize hooks
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Rails: config/initializers/* — runs code at application startup.
    Creates all tables on startup (dev mode).
    In production, use Alembic migrations: `alembic upgrade head`.
    """
    logger.info("CRM FastAPI starting up...")

    # NOTE: In production, do NOT use create_all — use Alembic migrations.
    # This is equivalent to Rails `rails db:schema:load` for dev convenience.
    async with engine.begin() as conn:
        # Import all models to ensure they're registered with Base.metadata
        from models.account import Account, AccountContact, AccountOpportunity  # noqa
        from models.contact import Contact, ContactOpportunity  # noqa
        from models.lead import Lead  # noqa
        from models.opportunity import Opportunity  # noqa
        from models.user import User  # noqa
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables verified/created.")
    yield

    # Shutdown
    logger.info("CRM FastAPI shutting down...")
    await engine.dispose()


# ---------------------------------------------------------------------------
# Application
# Rails: class Application < Rails::Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Fat Free CRM — FastAPI",
    description=(
        "Core CRM API migrated from Ruby on Rails (Fat Free CRM) to Python FastAPI. "
        "Provides full CRUD for Contacts, Leads, Accounts, and Opportunities "
        "with all original business logic preserved."
    ),
    version="1.0.0",
    docs_url="/api/docs",       # Rails: /rails/info/routes
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS Middleware
# Rails: config/initializers/cors.rb (rack-cors gem)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Lock down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Total-Count", "X-Page", "X-Per-Page"],
)


# ---------------------------------------------------------------------------
# Request ID + timing middleware
# Rails: ActionDispatch::RequestId + Lograge request timing
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_id_and_timing(request: Request, call_next):
    """
    Rails: ActionDispatch::RequestId — generates X-Request-ID header.
    Also logs request duration (Rails: Lograge log format).
    """
    import uuid
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.monotonic()

    response = await call_next(request)

    duration_ms = (time.monotonic() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        f"method={request.method} path={request.url.path} "
        f"status={response.status_code} duration={duration_ms:.1f}ms "
        f"request_id={request_id}"
    )
    return response


# ---------------------------------------------------------------------------
# Exception Handlers
# Rails: rescue_from in ApplicationController
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Rails: rescue_from ActiveModel::ValidationError { render json: errors, status: :unprocessable_entity }
    Returns 422 with structured field errors matching Rails error format.
    """
    errors = {}
    for error in exc.errors():
        field = " → ".join(str(loc) for loc in error["loc"] if loc != "body")
        errors.setdefault(field, []).append(error["msg"])

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation failed",
            "errors": errors,          # Rails: { errors: { field: [messages] } }
        },
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Rails: rescue_from ActiveRecord::RecordNotFound, with: :record_not_found"""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "Record not found"},
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    """Rails: rescue_from CanCan::AccessDenied { render status: :forbidden }"""
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": "Access denied"},
    )


# ---------------------------------------------------------------------------
# Routers
# Rails: config/routes.rb
#   resources :contacts
#   resources :leads do
#     member { post :convert }
#   end
#   resources :accounts
#   resources :opportunities
# ---------------------------------------------------------------------------
API_PREFIX = "/api/v1"

app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(accounts_router, prefix=API_PREFIX)
app.include_router(contacts_router, prefix=API_PREFIX)
app.include_router(leads_router, prefix=API_PREFIX)
app.include_router(opportunities_router, prefix=API_PREFIX)


# ---------------------------------------------------------------------------
# Health check
# Rails: Rails::HealthController (added in Rails 7.1)
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health_check():
    """
    Rails: GET /up (Rails 7.1+ health controller).
    Simple liveness probe for load balancers and Kubernetes.
    """
    return {"status": "ok", "service": "crm-fastapi"}




# ---------------------------------------------------------------------------
# Dev server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    # Rails: rails server -b 0.0.0.0 -p 3000
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,          # Rails: --reload via spring/listen
        log_level="info",
    )
