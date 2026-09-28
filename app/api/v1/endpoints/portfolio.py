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
    MOFSL actual fields:
      symbol, scripname, exchange, clientcode, productname, symboltoken,
      buyquantity, buyamount, sellquantity, sellamount,
      daybuyquantity, daybuyamount, daysellquantity, daysellamount,
      LTP, marktomarket, bookedprofitloss,
      cfbuyquantity, cfbuyamount, cfsellquantity, cfsellamount,
      actualbookedprofitloss, actualmarktomarket,
      series, expirydate, strikeprice, optiontype

    For OPEN positions: Net P&L = (LTP - buy_avg) * net_qty
    For CLOSED positions: Net P&L = bookedprofitloss
    Net qty = buyquantity - sellquantity
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
        net_qty = buy_qty - sell_qty
        net_qty_int = int(net_qty)

        buy_amt = _num(item.get("buyamount") or item.get("daybuyamount") or 0)
        sell_amt = _num(item.get("sellamount") or item.get("daysellamount") or 0)
        buy_avg = round(buy_amt / buy_qty, 2) if buy_qty else 0
        sell_avg = round(sell_amt / sell_qty, 2) if sell_qty else 0

        ltp = _num(item.get("LTP") or item.get("ltp") or 0)
        booked_pnl = _num(
            item.get("bookedprofitloss")
            or item.get("actualbookedprofitloss")
            or 0
        )
        mtm = _num(
            item.get("marktomarket")
            or item.get("actualmarktomarket")
            or item.get("mtm")
            or 0
        )

        # Build display name — use scripname which has full name, fall back to symbol
        symbol = item.get("symbol") or ""
        scripname = item.get("scripname") or item.get("symbolname") or symbol
        series = item.get("series") or ""
        expiry = item.get("expirydate") or ""
        strike = item.get("strikeprice") or ""
        opt_type = item.get("optiontype") or ""

        if series not in ("EQ", "") and expiry and str(expiry) != "0":
            name = f"{scripname} {expiry}"
            if strike and str(strike) != "0":
                name += f" {strike} {opt_type}"
        else:
            name = scripname

        # P&L calculation:
        # Open: unrealised = (LTP - buy_avg) * net_qty + booked_pnl
        # Closed: only booked_pnl matters
        if net_qty_int != 0 and ltp and buy_avg:
            unrealised = round((ltp - buy_avg) * net_qty_int, 2)
            net_profit = round(unrealised + booked_pnl, 2)
        else:
            net_profit = round(booked_pnl or mtm, 2)

        position = {
            "name": name,
            "symbol": symbol,
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


def _parse_holdings(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Transform MOFSL getdpholding response into a flat list.
    MOFSL actual fields (from API docs):
      clientcode, scripisinno, dpquantity, blockedquantity,
      scripname, buyavgprice, poaquantity, collateralquantity,
      outstandingquantity, debitstockquantity, nonpoaquantity,
      rmssellingquantity, btstquantity, buybackquantity,
      tpinquantity, slbmquantity, nbfcquantity,
      bsescripcode, nsesymboltoken

    Note: MOFSL DP Holding does NOT return LTP or current market value.
    Quantity is dpquantity. Avg cost is buyavgprice.
    P&L cannot be computed without LTP — show 0 unless available.
    """
    if raw.get("status") != "SUCCESS":
        return []
    data = raw.get("data") or []
    if not isinstance(data, list):
        data = []

    holdings = []
    for item in data:
        # Actual MOFSL DP holding fields
        qty = _num(
            item.get("dpquantity")
            or item.get("holdingqty")
            or item.get("quantity")
            or item.get("totalholdingqty")
            or 0
        )
        buy_avg = _num(
            item.get("buyavgprice")       # actual MOFSL field
            or item.get("avgcostprice")
            or item.get("averageprice")
            or item.get("avgprice")
            or 0
        )
        # LTP and market value — may not be present in DP holding response
        ltp = _num(item.get("LTP") or item.get("ltp") or 0)
        current_val = _num(
            item.get("currentmarketvalue")
            or (round(ltp * qty, 2) if ltp and qty else 0)
        )
        invested_val = round(buy_avg * qty, 2) if buy_avg and qty else 0
        pnl = _num(
            item.get("profitandloss")
            or item.get("unrealisedpnl")
            or item.get("pnl")
            or (round(current_val - invested_val, 2) if current_val and invested_val else 0)
        )
        pnl_pct = _num(
            item.get("pnlpercentage")
            or item.get("pnlpct")
            or (round(pnl / invested_val * 100, 2) if invested_val else 0)
        )

        # Name: use scripname (full name from MOFSL), symbol from bsescripcode/nsesymboltoken context
        name = item.get("scripname") or item.get("symbolname") or item.get("symbol") or ""
        symbol = item.get("symbol") or item.get("scripname") or ""

        holdings.append({
            "name": name,
            "symbol": symbol,
            "quantity": int(qty),
            "buy_avg": buy_avg,
            "ltp": ltp,
            "current_value": current_val,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
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
