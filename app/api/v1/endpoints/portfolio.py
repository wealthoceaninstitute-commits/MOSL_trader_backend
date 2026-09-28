from typing import Any, Dict, List, Optional

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


async def _get_first_live_client(db: AsyncSession, user_id: int):
    """Return the first live+active client for this user, or raise 400."""
    result = await db.execute(
        select(MofslClient).where(
            MofslClient.user_id == user_id,
            MofslClient.is_live == True,
            MofslClient.is_active == True,
        )
    )
    clients = result.scalars().all()
    for client in clients:
        session = await session_manager.get_client(user_id, client.id)
        if session:
            return client.id, session
    raise HTTPException(status_code=400, detail="No live client session found. Please log in a client first.")


def _parse_positions(raw: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Transform MOFSL getposition response into {open: [...], closed: [...]}.
    MOFSL returns: {"status": "SUCCESS", "data": [...]}
    Each item has fields like scripname, symbol, buyqty, sellqty, netqty,
    buyavgprice, sellavgprice, mtm, etc.
    """
    open_pos: List[Dict[str, Any]] = []
    closed_pos: List[Dict[str, Any]] = []

    if raw.get("status") != "SUCCESS":
        return {"open": open_pos, "closed": closed_pos}

    data = raw.get("data") or []
    if not isinstance(data, list):
        data = []

    for item in data:
        net_qty = item.get("netqty", 0)
        try:
            net_qty_int = int(float(str(net_qty or 0)))
        except Exception:
            net_qty_int = 0

        position = {
            "name": item.get("scripname") or item.get("symbolname") or item.get("symbol") or "",
            "symbol": item.get("symbol") or item.get("scripname") or "",
            "quantity": net_qty_int,
            "buy_avg": item.get("buyavgprice") or item.get("avgbuyPrice") or 0,
            "sell_avg": item.get("sellavgprice") or item.get("avgsellPrice") or 0,
            "net_profit": item.get("mtm") or item.get("realisedprofitloss") or 0,
            "ltp": item.get("ltp") or 0,
            "day_pnl": item.get("mtm") or 0,
        }

        if net_qty_int != 0:
            open_pos.append(position)
        else:
            closed_pos.append(position)

    return {"open": open_pos, "closed": closed_pos}


def _parse_holdings(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Transform MOFSL getdpholding response into a flat list."""
    if raw.get("status") != "SUCCESS":
        return []
    data = raw.get("data") or []
    if not isinstance(data, list):
        data = []

    holdings = []
    for item in data:
        holdings.append({
            "name": item.get("scripname") or item.get("symbolname") or "",
            "symbol": item.get("symbol") or "",
            "quantity": item.get("holdingqty") or item.get("quantity") or 0,
            "buy_avg": item.get("avgcostprice") or item.get("avgprice") or 0,
            "ltp": item.get("ltp") or 0,
            "current_value": item.get("currentmarketvalue") or 0,
            "pnl": item.get("profitandloss") or item.get("unrealisedpnl") or 0,
            "pnl_pct": item.get("pnlpercentage") or 0,
        })
    return holdings


@router.get("/positions")
async def positions(
    client_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if client_id is not None:
        session = await _require_session(db, current_user.id, client_id)
    else:
        _, session = await _get_first_live_client(db, current_user.id)

    svc = session["service"]
    try:
        raw = await svc.get_positions(session["auth_token"], session["access_token"])
        return _parse_positions(raw)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/holdings")
async def holdings(
    client_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if client_id is not None:
        session = await _require_session(db, current_user.id, client_id)
    else:
        _, session = await _get_first_live_client(db, current_user.id)

    svc = session["service"]
    try:
        raw = await svc.get_holdings(session["auth_token"], session["access_token"])
        return {"holdings": _parse_holdings(raw)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/margins")
async def margins(
    client_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if client_id is not None:
        session = await _require_session(db, current_user.id, client_id)
    else:
        _, session = await _get_first_live_client(db, current_user.id)

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
