"""
In-memory session registry.
Key: (user_id: int, client_db_id: int)
Value: dict with 'service' (MofslClientService), 'auth_token', 'access_token'
"""

from typing import Dict, List, Optional, Tuple, Any
import asyncio

from app.services.mofsl_client import MofslClientService

_sessions: Dict[Tuple[int, int], Dict[str, Any]] = {}
_lock = asyncio.Lock()


async def get_client(user_id: int, client_db_id: int) -> Optional[Dict[str, Any]]:
    """Return the session dict for (user_id, client_db_id), or None."""
    return _sessions.get((user_id, client_db_id))


async def set_client(
    user_id: int,
    client_db_id: int,
    service: MofslClientService,
    auth_token: str,
    access_token: str,
) -> None:
    """Store or replace a session."""
    async with _lock:
        _sessions[(user_id, client_db_id)] = {
            "service": service,
            "auth_token": auth_token,
            "access_token": access_token,
        }


async def remove_client(user_id: int, client_db_id: int) -> None:
    """Remove a session if present."""
    async with _lock:
        _sessions.pop((user_id, client_db_id), None)


async def get_user_clients(user_id: int) -> List[Dict[str, Any]]:
    """Return all session dicts for a given user, each augmented with client_db_id."""
    result = []
    for (uid, cid), session in _sessions.items():
        if uid == user_id:
            result.append({**session, "client_db_id": cid})
    return result


async def clear_user(user_id: int) -> None:
    """Remove all sessions belonging to user_id."""
    async with _lock:
        keys_to_remove = [k for k in _sessions if k[0] == user_id]
        for k in keys_to_remove:
            _sessions.pop(k, None)


def get_session_sync(user_id: int, client_db_id: int) -> Optional[Dict[str, Any]]:
    """Synchronous lookup (safe for use in non-async contexts like scheduler)."""
    return _sessions.get((user_id, client_db_id))


def all_live_sessions() -> List[Dict[str, Any]]:
    """Return all active sessions as list of {user_id, client_db_id, service, auth_token, access_token}."""
    return [
        {
            "user_id": k[0],
            "client_db_id": k[1],
            **v,
        }
        for k, v in _sessions.items()
    ]
