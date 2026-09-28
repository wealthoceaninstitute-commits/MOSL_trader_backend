import asyncio
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


def _num(val: Any, default: float = 0.0) -> float:
    try:
        return float(str(val or 0))
    except Exception:
        return default


async def _require_session(db: AsyncSession, user_id: int, client_db_id: int):
    session = await session_manager.get_client(user_id, client_db_id)
    if not session:
        raise HTTPException(status_code=400, detail=f"Client {client_db_id} is not logged in")
    return session


async def _get_first_live_client(db: AsyncSession, user_id: int):
    """Return (MofslClient db row, session dict) for first active live client."""
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
            return client, session
    raise HTTPException(status_code=400, detail="No live client session found. Please log in a client first.")


async def _get_client_by_db_id(db: AsyncSession, client_db_id: int) -> Optional[MofslClient]:
    result = await db.execute(select(MofslClient).where(MofslClient.id == client_db_id))
    return result.scalar_one_or_none()


def _parse_positions(raw: Dict[str, Any], client_name: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Transform MOFSL getposition response into {open: [...], closed: [...]}.
    Uses client_name from DB for the 'name' field (not symbol/scripname).

    MOFSL fields:
      symbol, scripname, exchange, clientcode,
      buyquantity, buyamount, sellquantity, sellamount,
      LTP, marktomarket, bookedprofitloss,
      actualbookedprofitloss, actualmarktomarket,
      series, expirydate, strikeprice, optiontype

    Net qty = buyquantity - sellquantity
    Open P&L  = (LTP - buy_avg) * net_qty + booked_pnl
    Closed P&L = booked_pnl
    """
    open_pos: List[Dict[str, Any]] = []
    closed_pos: List[Dict[str, Any]] = []

    if raw.get("status") != "SUCCESS":
        return {"open": open_pos, "closed": closed_pos}

    data = raw.get("data") or []
    if not isinstance(data, list):
        data = []

    for item in data:
        buy_qty = _num(item.get("buyquantity") or item.get("daybuyquantity") or 0)
        sell_qty = _num(item.get("sellquantity") or item.get("daysellquantity") or 0)
        net_qty_int = int(buy_qty - sell_qty)

        buy_amt = _num(item.get("buyamount") or item.get("daybuyamount") or 0)
        sell_amt = _num(item.get("sellamount") or item.get("daysellamount") or 0)
        buy_avg = round(buy_amt / buy_qty, 2) if buy_qty else 0
        sell_avg = round(sell_amt / sell_qty, 2) if sell_qty else 0

        ltp = _num(item.get("LTP") or item.get("ltp") or 0)
        booked_pnl = _num(
            item.get("bookedprofitloss") or item.get("actualbookedprofitloss") or 0
        )
        mtm = _num(
            item.get("marktomarket") or item.get("actualmarktomarket") or item.get("mtm") or 0
        )

        # Symbol display (F&O gets expiry/strike appended)
        symbol = item.get("symbol") or ""
        scripname = item.get("scripname") or item.get("symbolname") or symbol
        series = item.get("series") or ""
        expiry = item.get("expirydate") or ""
        strike = item.get("strikeprice") or ""
        opt_type = item.get("optiontype") or ""

        if series not in ("EQ", "") and expiry and str(expiry) != "0":
            display_symbol = f"{symbol} {expiry}"
            if strike and str(strike) != "0":
                display_symbol += f" {strike} {opt_type}"
        else:
            display_symbol = scripname  # e.g. "M&M EQ"

        # P&L
        if net_qty_int != 0 and ltp and buy_avg:
            net_profit = round((ltp - buy_avg) * net_qty_int + booked_pnl, 2)
        else:
            net_profit = round(booked_pnl or mtm, 2)

        position = {
            "name": client_name,        # Client name from DB
            "symbol": display_symbol,   # e.g. "M&M EQ" or "NIFTY 25DEC2025 24000 CE"
            "quantity": net_qty_int,
            "buy_avg": buy_avg,
            "sell_avg": sell_avg,
            "net_profit": net_profit,
            "ltp": ltp,
            "day_pnl": round(mtm, 2),
        }

        if net_qty_int != 0:
            open_pos.append(position)
        else:
            closed_pos.append(position)

    return {"open": open_pos, "closed": closed_pos}


async def _fetch_ltp_for_holdings(
    holdings_data: List[Dict[str, Any]],
    svc: Any,
    auth_token: str,
    access_token: str,
) -> Dict[int, float]:
    """
    Fetch LTP for each holding using nsesymboltoken (NSE scripcode).
    Returns dict of {nsesymboltoken: ltp}.
    """
    ltp_map: Dict[int, float] = {}

    async def fetch_one(token: int) -> None:
        try:
            resp = await svc.get_ltp(auth_token, access_token, "NSE", token)
            if resp.get("status") == "SUCCESS":
                data = resp.get("data") or {}
                if isinstance(data, list) and data:
                    data = data[0]
                ltp_val = _num(
                    data.get("LTP") or data.get("ltp")
                    or data.get("lastprice") or data.get("close") or 0
                )
                if ltp_val:
                    ltp_map[token] = ltp_val
        except Exception:
            pass  # LTP fetch failure is non-critical

    tokens = []
    for item in holdings_data:
        tok = item.get("nsesymboltoken") or item.get("bsescripcode")
        if tok:
            try:
                tokens.append(int(tok))
            except Exception:
                pass

    # Fetch concurrently but limit parallelism to avoid rate limits
    for i in range(0, len(tokens), 5):
        batch = tokens[i:i+5]
        await asyncio.gather(*[fetch_one(t) for t in batch])

    return ltp_map


def _parse_holdings(
    raw: Dict[str, Any],
    client_name: str,
    ltp_map: Optional[Dict[int, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Transform MOFSL getdpholding response into a flat list.
    MOFSL actual fields:
      clientcode, scripisinno, dpquantity, blockedquantity,
      scripname, buyavgprice, poaquantity, collateralquantity,
      nsesymboltoken, bsescripcode

    name = client name from DB
    symbol = scripname from MOFSL (e.g. "RELAXO EQ")
    LTP fetched separately via nsesymboltoken
    """
    if raw.get("status") != "SUCCESS":
        return []
    data = raw.get("data") or []
    if not isinstance(data, list):
        data = []

    if ltp_map is None:
        ltp_map = {}

    holdings = []
    for item in data:
        qty = _num(
            item.get("dpquantity")
            or item.get("holdingqty")
            or item.get("quantity")
            or 0
        )
        buy_avg = _num(
            item.get("buyavgprice")
            or item.get("avgcostprice")
            or item.get("averageprice")
            or 0
        )
        # Get LTP from our fetched map using nsesymboltoken
        nse_token = None
        try:
            nse_token = int(item.get("nsesymboltoken") or item.get("bsescripcode") or 0)
        except Exception:
            pass
        ltp = ltp_map.get(nse_token, 0.0) if nse_token else 0.0

        invested_val = round(buy_avg * qty, 2) if buy_avg and qty else 0.0
        current_val = round(ltp * qty, 2) if ltp and qty else 0.0
        pnl = round(current_val - invested_val, 2) if current_val and invested_val else 0.0
        pnl_pct = round(pnl / invested_val * 100, 2) if invested_val else 0.0

        scripname = item.get("scripname") or item.get("symbolname") or item.get("symbol") or ""

        holdings.append({
            "name": client_name,    # Client name from DB
            "symbol": scripname,    # e.g. "RELAXO EQ" or "MOSCHIP EQ"
            "quantity": int(qty),
            "buy_avg": buy_avg,
            "ltp": ltp,
            "current_value": current_val,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
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
        client_row = await _get_client_by_db_id(db, client_id)
        client_name = client_row.name if client_row else "Unknown"
    else:
        client_row, session = await _get_first_live_client(db, current_user.id)
        client_name = client_row.name

    svc = session["service"]
    try:
        raw = await svc.get_positions(session["auth_token"], session["access_token"])
        return _parse_positions(raw, client_name)
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
        client_row = await _get_client_by_db_id(db, client_id)
        client_name = client_row.name if client_row else "Unknown"
    else:
        client_row, session = await _get_first_live_client(db, current_user.id)
        client_name = client_row.name

    svc = session["service"]
    try:
        raw = await svc.get_holdings(session["auth_token"], session["access_token"])
        holdings_data = raw.get("data") or []
        if not isinstance(holdings_data, list):
            holdings_data = []

        # Fetch LTP for all holdings concurrently using nsesymboltoken
        ltp_map = await _fetch_ltp_for_holdings(
            holdings_data, svc, session["auth_token"], session["access_token"]
        )
        return {"holdings": _parse_holdings(raw, client_name, ltp_map)}
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
    """Aggregated summary across all live clients for this user."""
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
