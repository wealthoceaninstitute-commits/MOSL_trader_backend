import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.client import ClientGroup, GroupMember, MofslClient
from app.models.user import User
from app.schemas.client import (
    ClientGroupCreate,
    ClientGroupOut,
    GroupMemberUpdate,
    MofslClientCreate,
    MofslClientOut,
    MofslClientUpdate,
)
from app.services.mofsl_client import MofslClientService
from app.services import session_manager

router = APIRouter(tags=["clients"])


# ────────────────────────────── Clients ──────────────────────────────


@router.get("/clients", response_model=List[MofslClientOut])
async def list_clients(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MofslClient).where(MofslClient.user_id == current_user.id)
    )
    return result.scalars().all()


@router.post("/clients", response_model=MofslClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: MofslClientCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = MofslClient(user_id=current_user.id, **payload.model_dump())
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.patch("/clients/{client_id}", response_model=MofslClientOut)
@router.put("/clients/{client_id}", response_model=MofslClientOut)
async def update_client(
    client_id: int,
    payload: MofslClientUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_client_or_404(db, client_id, current_user.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(client, field, value)
    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_client_or_404(db, client_id, current_user.id)
    await session_manager.remove_client(current_user.id, client_id)
    await db.delete(client)
    await db.commit()


@router.post("/clients/{client_id}/login")
async def login_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_client_or_404(db, client_id, current_user.id)
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"MOFSL login failed: {exc}")

    # Persist tokens in DB
    expiry = datetime.now(timezone.utc) + timedelta(hours=8)
    client.auth_token = auth_token
    client.access_token = access_token
    client.token_expiry = expiry
    client.is_live = True
    await db.commit()

    await session_manager.set_client(current_user.id, client_id, svc, auth_token, access_token)

    return {"status": "success", "client_id": client_id, "token_expiry": expiry}


@router.post("/clients/{client_id}/logout")
async def logout_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_client_or_404(db, client_id, current_user.id)
    await session_manager.remove_client(current_user.id, client_id)
    client.auth_token = None
    client.access_token = None
    client.token_expiry = None
    client.is_live = False
    await db.commit()
    return {"status": "logged_out", "client_id": client_id}


@router.get("/clients/{client_id}/status")
async def client_status(
    client_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client = await _get_client_or_404(db, client_id, current_user.id)
    session = await session_manager.get_client(current_user.id, client_id)
    return {
        "client_id": client_id,
        "is_live": client.is_live,
        "in_memory": session is not None,
        "token_expiry": client.token_expiry,
    }


@router.post("/clients/login-all")
async def login_all_clients(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MofslClient).where(
            MofslClient.user_id == current_user.id,
            MofslClient.is_active == True,
        )
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
            client.is_live = True
            await session_manager.set_client(current_user.id, client.id, svc, auth_token, access_token)
            return {"client_id": client.id, "name": client.name, "status": "success"}
        except Exception as exc:
            return {"client_id": client.id, "name": client.name, "status": "failed", "error": str(exc)}

    results = await asyncio.gather(*[_login_one(c) for c in clients])
    await db.commit()
    return {"results": list(results)}


# ────────────────────────────── Groups ──────────────────────────────


@router.get("/groups", response_model=List[ClientGroupOut])
async def list_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClientGroup).where(ClientGroup.user_id == current_user.id)
    )
    groups = result.scalars().all()
    return [await _enrich_group(db, g, current_user.id) for g in groups]


@router.post("/groups", response_model=ClientGroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: ClientGroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = ClientGroup(
        user_id=current_user.id,
        name=payload.name,
        multiplier=payload.multiplier,
    )
    db.add(group)
    await db.flush()

    for cid in payload.member_ids:
        db.add(GroupMember(group_id=group.id, client_id=cid))

    await db.commit()
    await db.refresh(group)
    return await _enrich_group(db, group, current_user.id)


@router.patch("/groups/{group_id}", response_model=ClientGroupOut)
async def update_group(
    group_id: int,
    payload: ClientGroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_404(db, group_id, current_user.id)
    group.name = payload.name
    group.multiplier = payload.multiplier

    # Replace members
    await db.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    for cid in payload.member_ids:
        db.add(GroupMember(group_id=group_id, client_id=cid))

    await db.commit()
    await db.refresh(group)
    return await _enrich_group(db, group, current_user.id)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_404(db, group_id, current_user.id)
    await db.delete(group)
    await db.commit()


# ────────────────────────────── Helpers ──────────────────────────────


async def _get_client_or_404(db: AsyncSession, client_id: int, user_id: int) -> MofslClient:
    result = await db.execute(
        select(MofslClient).where(MofslClient.id == client_id, MofslClient.user_id == user_id)
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


async def _get_group_or_404(db: AsyncSession, group_id: int, user_id: int) -> ClientGroup:
    result = await db.execute(
        select(ClientGroup).where(ClientGroup.id == group_id, ClientGroup.user_id == user_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


async def _enrich_group(db: AsyncSession, group: ClientGroup, user_id: int) -> ClientGroupOut:
    member_result = await db.execute(
        select(MofslClient)
        .join(GroupMember, GroupMember.client_id == MofslClient.id)
        .where(GroupMember.group_id == group.id, MofslClient.user_id == user_id)
    )
    members = member_result.scalars().all()
    return ClientGroupOut(
        id=group.id,
        user_id=group.user_id,
        name=group.name,
        multiplier=group.multiplier,
        members=members,
        created_at=group.created_at,
    )
