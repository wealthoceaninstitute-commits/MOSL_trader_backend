from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.client import MofslClient
from app.models.user import User
from app.services import session_manager

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def _require_session(db: AsyncSession, user_id: int, client_db_id: int):
    session = await session_manager.get_client(user_id, client_db_id)
    if not session:
        raise HTTPException(status_code=400, detail=f"Client {client_db_id} is not logged in")
    return session


@router.get("/positions")
async def positions(
    client_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_positions(session["auth_token"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/holdings")
async def holdings(
    client_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_holdings(session["auth_token"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/margins")
async def margins(
    client_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_margin_summary(session["auth_token"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/summary")
async def portfolio_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Aggregated summary across all live clients for this user.
    Fetches margin summary from each live client and combines with DB capital.
    """
    result = await db.execute(
        select(MofslClient).where(
            MofslClient.user_id == current_user.id,
            MofslClient.is_live == True,
            MofslClient.is_active == True,
        )
    )
    clients = result.scalars().all()

    summaries: List[Dict[str, Any]] = []
    total_capital = 0.0
    total_available_margin = 0.0

    for client in clients:
        session = await session_manager.get_client(current_user.id, client.id)
        if not session:
            summaries.append({
                "client_id": client.id,
                "name": client.name,
                "capital": client.capital,
                "available_margin": None,
                "error": "Not in session",
            })
            total_capital += client.capital
            continue

        svc = session["service"]
        try:
            margin_resp = await svc.get_margin_summary(session["auth_token"], session["access_token"])
            available_margin = 0.0
            if margin_resp.get("status") == "SUCCESS":
                rows = margin_resp.get("data", []) or []
                for item in rows:
                    if (item.get("particulars") or "").strip().lower() == "total available margin for cash":
                        try:
                            available_margin = float(item.get("amount", 0) or 0)
                        except Exception:
                            pass
                        break

            summaries.append({
                "client_id": client.id,
                "name": client.name,
                "capital": client.capital,
                "available_margin": available_margin,
            })
            total_capital += client.capital
            total_available_margin += available_margin
        except Exception as exc:
            summaries.append({
                "client_id": client.id,
                "name": client.name,
                "capital": client.capital,
                "available_margin": None,
                "error": str(exc),
            })
            total_capital += client.capital

    return {
        "total_capital": round(total_capital, 2),
        "total_available_margin": round(total_available_margin, 2),
        "clients": summaries,
    }
