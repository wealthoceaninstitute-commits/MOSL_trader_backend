import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.client import MofslClient
from app.models.user import User
from app.schemas.order import CancelOrderRequest, ModifyOrderRequest, PlaceOrderRequest
from app.services import session_manager
from app.services.copy_engine import fan_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


async def _require_session(db: AsyncSession, user_id: int, client_db_id: int):
    """Return session dict or raise 400."""
    session = await session_manager.get_client(user_id, client_db_id)
    if not session:
        raise HTTPException(status_code=400, detail=f"Client {client_db_id} is not logged in")
    return session


def _classify_status(status: str) -> str:
    """Map MOFSL order status strings to frontend bucket keys."""
    s = str(status).upper()
    if s in ("OPEN", "PENDING", "TRIGGER PENDING", "AFTER MARKET ORDER REQ RECEIVED",
             "MODIFY PENDING", "CANCEL PENDING", "OPEN PENDING"):
        return "pending"
    if s in ("COMPLETE", "TRADED", "FILLED"):
        return "traded"
    if s in ("REJECTED", "VALIDATION PENDING"):
        return "rejected"
    if s in ("CANCELLED", "CANCELLED AFTER MARKET ORDER"):
        return "cancelled"
    return "others"


def _normalize_order(raw: Dict[str, Any], client_name: str, client_db_id: int) -> Dict[str, Any]:
    """Flatten a MOFSL order dict into the shape the frontend expects."""
    return {
        "order_id": raw.get("uniqueorderid") or raw.get("orderid") or raw.get("order_id"),
        "symbol": raw.get("scripname") or raw.get("symbol") or raw.get("tradingsymbol"),
        "transaction_type": (raw.get("buysell") or raw.get("transactiontype") or raw.get("transaction_type") or "").upper(),
        "quantity": raw.get("quantity") or raw.get("qty") or raw.get("quantityinlot"),
        "price": raw.get("price") or raw.get("limitprice"),
        "status": raw.get("orderstatus") or raw.get("status"),
        "name": client_name,
        "client_id": client_db_id,
        # passthrough extras useful for cancel/modify
        "exchange": raw.get("exchange") or raw.get("exchangename"),
        "broker": "mofsl",
    }


@router.get("")
async def list_all_orders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Aggregate order books from ALL live clients for the current user.
    Returns {pending, traded, rejected, cancelled, others} buckets.
    """
    result = await db.execute(
        select(MofslClient).where(
            MofslClient.user_id == current_user.id,
            MofslClient.is_live == True,
            MofslClient.is_active == True,
        )
    )
    live_clients = result.scalars().all()

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pending": [], "traded": [], "rejected": [], "cancelled": [], "others": []
    }

    async def _fetch_one(client: MofslClient):
        session = await session_manager.get_client(current_user.id, client.id)
        if not session:
            logger.warning("No in-memory session for client %s (id=%s) — skipping", client.client_id, client.id)
            return
        svc = session["service"]
        try:
            resp = await svc.get_order_book(session["auth_token"], session["access_token"])
            # MOFSL returns data under "data" key as a list
            orders_raw: List[Dict[str, Any]] = []
            data = resp.get("data") or []
            if isinstance(data, list):
                orders_raw = data
            elif isinstance(data, dict):
                # Some versions nest under "orderbook"
                orders_raw = data.get("orderbook") or data.get("orders") or []

            for raw in orders_raw:
                if not isinstance(raw, dict):
                    continue
                status_str = raw.get("orderstatus") or raw.get("status") or ""
                bucket = _classify_status(status_str)
                normalized = _normalize_order(raw, client.name or client.client_id, client.id)
                buckets[bucket].append(normalized)
        except Exception as exc:
            logger.error("Failed to fetch order book for client %s: %s", client.client_id, exc)

    await asyncio.gather(*[_fetch_one(c) for c in live_clients])
    return buckets


@router.post("/place")
async def place_order(
    payload: PlaceOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    Place order on one or multiple clients.
    - Single client_id in list: place directly on that client.
    - Multiple client_ids: fan-out to all.
    - No client_ids: fan-out to all copy_enabled active clients.
    """
    order_data = payload.model_dump(exclude={"client_ids"})

    if payload.client_ids and len(payload.client_ids) == 1:
        # Single placement
        cid = payload.client_ids[0]
        session = await _require_session(db, current_user.id, cid)
        svc = session["service"]
        # Ensure clientcode is set
        order_data["clientcode"] = svc.client_id
        try:
            resp = await svc.place_order(session["auth_token"], session["access_token"], order_data)
            return [{"client_id": cid, "status": resp.get("status"), "response": resp}]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    # Fan-out (multiple or none)
    source_client_id = payload.client_ids[0] if payload.client_ids else None
    results = await fan_out(
        db=db,
        user_id=current_user.id,
        source_client_id=source_client_id,
        order_payload=order_data,
        client_ids=payload.client_ids,
    )
    return results


@router.post("/modify")
async def modify_order(
    payload: ModifyOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, payload.client_id)
    svc = session["service"]
    modify_data = {
        "clientcode": svc.client_id,
        "uniqueorderid": payload.uniqueorderid,
        "newordertype": payload.ordertype,
        "newprice": payload.price,
        "newtriggerprice": payload.triggerprice,
        "newquantityinlot": payload.quantityinlot,
        "newdisclosedquantity": payload.disclosedquantity,
    }
    try:
        resp = await svc.modify_order(session["auth_token"], session["access_token"], modify_data)
        return resp
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/cancel")
async def cancel_order(
    payload: CancelOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, payload.client_id)
    svc = session["service"]
    try:
        resp = await svc.cancel_order(
            session["auth_token"],
            session["access_token"],
            payload.uniqueorderid,
            payload.exchange,
        )
        return resp
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/book")
async def order_book(
    client_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_order_book(session["auth_token"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/debug-raw")
async def debug_raw_orders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    DEBUG: Returns the raw MOFSL order book response for the first live client.
    Use this to inspect the actual API response structure.
    Remove before production.
    """
    result = await db.execute(
        select(MofslClient).where(
            MofslClient.user_id == current_user.id,
            MofslClient.is_live == True,
        )
    )
    client = result.scalars().first()
    if not client:
        return {"error": "No live clients found", "is_live_clients": []}

    session = await session_manager.get_client(current_user.id, client.id)
    if not session:
        return {
            "error": "Client is marked is_live in DB but has no in-memory session — need to login again",
            "client_id": client.id,
            "client_name": client.name,
            "auth_token_in_db": bool(client.auth_token),
        }

    svc = session["service"]
    try:
        raw = await svc.get_order_book(session["auth_token"], session["access_token"])
        return {"client_id": client.id, "client_name": client.name, "raw_response": raw}
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/trades")
async def trade_book(
    client_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    session = await _require_session(db, current_user.id, client_id)
    svc = session["service"]
    try:
        return await svc.get_trade_book(session["auth_token"], session["access_token"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
