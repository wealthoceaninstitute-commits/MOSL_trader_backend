"""
Copy-trade fan-out engine.
"""

import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import CopyTradeLog, MofslClient
from app.services import session_manager


async def fan_out(
    db: AsyncSession,
    user_id: int,
    source_client_id: Optional[int],
    order_payload: Dict[str, Any],
    client_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    Place copies of order_payload on target clients.

    If client_ids is provided, use those specific DB client IDs.
    Otherwise, use all copy_enabled active clients for this user (excluding source).

    Qty is scaled by each target's qty_multiplier (floor, min 1).
    Returns list of per-client result dicts.
    """
    # Determine target clients
    if client_ids:
        stmt = select(MofslClient).where(
            MofslClient.id.in_(client_ids),
            MofslClient.user_id == user_id,
            MofslClient.is_active == True,
        )
    else:
        stmt = select(MofslClient).where(
            MofslClient.user_id == user_id,
            MofslClient.is_active == True,
            MofslClient.copy_enabled == True,
        )
        if source_client_id is not None:
            stmt = stmt.where(MofslClient.id != source_client_id)

    result = await db.execute(stmt)
    targets = result.scalars().all()

    async def _place_for_target(target: MofslClient) -> Dict[str, Any]:
        session = await session_manager.get_client(user_id, target.id)
        if not session:
            err = "Client not logged in"
            await _log(db, user_id, source_client_id, target.id, order_payload, "FAILED", err)
            return {"client_id": target.id, "client_name": target.name, "status": "FAILED", "error": err}

        # Scale quantity
        base_qty = int(order_payload.get("quantityinlot", 1))
        scaled_qty = max(1, math.floor(base_qty * target.qty_multiplier))

        payload = {**order_payload, "quantityinlot": scaled_qty, "clientcode": target.client_id}

        svc = session["service"]
        auth_token = session["auth_token"]
        access_token = session["access_token"]

        try:
            resp = await svc.place_order(auth_token, access_token, payload)
            status = "SUCCESS" if resp.get("status") == "SUCCESS" else "FAILED"
            error = None if status == "SUCCESS" else resp.get("message", str(resp))
            await _log(db, user_id, source_client_id, target.id, order_payload, status, error)
            return {
                "client_id": target.id,
                "client_name": target.name,
                "status": status,
                "response": resp,
                "error": error,
            }
        except Exception as exc:
            err = str(exc)
            await _log(db, user_id, source_client_id, target.id, order_payload, "FAILED", err)
            return {"client_id": target.id, "client_name": target.name, "status": "FAILED", "error": err}

    tasks = [_place_for_target(t) for t in targets]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    await db.commit()
    return list(results)


async def _log(
    db: AsyncSession,
    user_id: int,
    source_client_id: Optional[int],
    target_client_id: int,
    order_payload: Dict[str, Any],
    status: str,
    error: Optional[str],
) -> None:
    log = CopyTradeLog(
        user_id=user_id,
        source_client_id=source_client_id,
        target_client_id=target_client_id,
        symbol=order_payload.get("symbol", ""),
        action=order_payload.get("buyorsell", ""),
        order_type=order_payload.get("ordertype", ""),
        qty=order_payload.get("quantityinlot"),
        price=order_payload.get("price"),
        status=status,
        error=error,
        created_at=datetime.now(timezone.utc),
    )
    db.add(log)
