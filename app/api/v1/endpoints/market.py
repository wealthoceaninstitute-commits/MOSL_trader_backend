from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.services import session_manager

router = APIRouter(prefix="/market", tags=["market"])


async def _require_session(db: AsyncSession, user_id: int, client_db_id: int):
    session = await session_manager.get_client(user_id, client_db_id)
    if not session:
        raise HTTPException(status_code=400, detail=f"Client {client_db_id} is not logged in")
    return session


@router.get("/ltp")
async def get_ltp(
    client_id: int = Query(...),
    exchange: str = Query(...),
    scripcode: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_ltp(session["auth_token"], session["access_token"], exchange, scripcode)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/scrips")
async def get_scrips(
    client_id: int = Query(...),
    exchangename: str = Query(...),
    searchscrip: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_scrips(session["auth_token"], session["access_token"], exchangename, searchscrip)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/symbols/search")
async def search_symbols(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    Search local symbols table (populated from CSV).
    Returns up to `limit` matching rows.
    """
    try:
        search_q = f"%{q.upper()}%"
        rows = await db.execute(
            text(
                "SELECT * FROM symbols WHERE UPPER(symbol) LIKE :q OR UPPER(scripname) LIKE :q LIMIT :lim"
            ),
            {"q": search_q, "lim": limit},
        )
        keys = rows.keys()
        return [dict(zip(keys, row)) for row in rows.fetchall()]
    except Exception:
        # Table may not exist yet
        return []
