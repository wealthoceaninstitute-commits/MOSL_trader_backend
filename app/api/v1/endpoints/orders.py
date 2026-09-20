from typing import Any, Dict, List

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

router = APIRouter(prefix="/orders", tags=["orders"])


async def _require_session(db: AsyncSession, user_id: int, client_db_id: int):
    """Return session dict or raise 400."""
    session = await session_manager.get_client(user_id, client_db_id)
    if not session:
        raise HTTPException(status_code=400, detail=f"Client {client_db_id} is not logged in")
    return session


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
