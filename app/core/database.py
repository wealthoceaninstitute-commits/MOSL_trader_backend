from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


_db_url = settings.DATABASE_URL
# SQLite needs check_same_thread=False; PostgreSQL does not accept it
_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    from app.models import user, client  # noqa: F401 - ensure models are imported
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
