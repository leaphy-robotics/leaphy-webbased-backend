""" Manage session concurrency """
import uuid
from typing import Annotated

from cachetools import TTLCache
from fastapi import Response, Cookie, Depends, HTTPException

from conf import settings

# Hash that stores all known sessions
sessions = TTLCache(maxsize=settings.max_total_sessions, ttl=settings.session_duration)


def get_session_id(
    response: Response, session_id: Annotated[str | None, Cookie()] = None
):
    """Generate or get a consistent session ID for an anonymous user"""
    if not session_id:
        # First time user, create a new session
        session_id = uuid.uuid4().hex
        response.set_cookie("session_id", session_id)

    if session_id not in sessions:
        sessions[session_id] = 0
    elif sessions[session_id] >= settings.max_sessions_per_user:
        raise HTTPException(403, "Too many sessions.")
    return session_id


Session = Annotated[str, Depends(get_session_id)]
