import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _relogin_all_live_clients():
    """
    APScheduler job: re-login every client that is marked is_live=True in DB.
    Runs at 06:10 AM daily to refresh tokens before market open.
    """
    from app.models.client import MofslClient
    from app.services.mofsl_client import MofslClientService
    from app.services import session_manager
    from sqlalchemy import select

    logger.info("[scheduler] Starting daily re-login for all live clients")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MofslClient).where(MofslClient.is_live == True, MofslClient.is_active == True)
        )
        clients = result.scalars().all()

        async def _login_one(client: MofslClient):
            svc = MofslClientService(
                client_id=client.client_id,
                api_key=client.api_key,
                api_secret=client.api_secret,
                totp_secret=client.totp_secret,
                password_hash=client.password_hash,
                two_fa=client.two_fa,
            )
            try:
                auth_token, access_token = await svc.login()
                expiry = datetime.now(timezone.utc) + timedelta(hours=8)
                client.auth_token = auth_token
                client.access_token = access_token
                client.token_expiry = expiry
                await session_manager.set_client(client.user_id, client.id, svc, auth_token, access_token)
                logger.info("[scheduler] Re-logged in client %s (%s)", client.name, client.client_id)
            except Exception as exc:
                logger.error("[scheduler] Re-login failed for client %s: %s", client.client_id, exc)
                client.is_live = False

        await asyncio.gather(*[_login_one(c) for c in clients])
        await db.commit()

    logger.info("[scheduler] Daily re-login complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    logger.info("Database initialized")

    # Schedule daily re-login at 06:10 IST (00:40 UTC)
    scheduler.add_job(
        _relogin_all_live_clients,
        trigger="cron",
        hour=0,
        minute=40,
        id="daily_relogin",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — daily re-login at 06:10 IST")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="WOI AutoTrader API",
    version="1.0.0",
    description="Multi-user MOFSL algorithmic trading platform",
    lifespan=lifespan,
)

# CORS — must be added before routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Routers
app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
