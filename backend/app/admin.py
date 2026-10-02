"""Separate admin login, signed HTTP-only cookies, and CSRF-protected deletion."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.agents import agent_router
from backend.app.api_health import api_health_router
from backend.app.config import Settings
from backend.app.engine import GameError

COOKIE = "amaze_admin"
SESSION_SECONDS = 8 * 60 * 60


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AdminAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.failures = defaultdict(list)

    def secret(self):
        if not self.settings.admin_password_hash or not self.settings.admin_session_secret:
            raise GameError(
                "Admin login is not configured. Run npm run admin:setup on the server.", 503
            )
        return self.settings.admin_session_secret.get_secret_value().encode()

    def sign(self, value):
        return hmac.new(self.secret(), value.encode(), hashlib.sha256).hexdigest()

    def csrf(self, token):
        return self.sign(f"csrf:{token}")

    def issue(self):
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "username": self.settings.admin_username,
                    "expires": int(time.time()) + SESSION_SECONDS,
                    "nonce": secrets.token_hex(16),
                }
            ).encode()
        ).decode()
        return f"{payload}.{self.sign(payload)}"

    def require(self, request: Request):
        token = request.cookies.get(COOKIE, "")
        try:
            if not token or len(token) > 2048:
                raise ValueError
            payload, signature = token.rsplit(".", 1)
            if not hmac.compare_digest(self.sign(payload), signature):
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(payload))
            if (
                claims["username"] != self.settings.admin_username
                or claims["expires"] <= time.time()
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise GameError("Please sign in as an administrator.", 401) from None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("X-Admin-CSRF", ""), self.csrf(token)):
                raise GameError("Invalid admin request. Refresh the page and try again.", 403)
        return {"username": claims["username"], "csrf_token": self.csrf(token)}

    def login(self, request, body):
        self.secret()
        address = request.client.host if request.client else "unknown"
        now = time.time()
        failures = [at for at in self.failures[address] if now - at < 15 * 60]
        self.failures[address] = failures
        if len(failures) >= 5:
            raise GameError("Too many sign-in attempts. Try again in 15 minutes.", 429)
        username_matches = hmac.compare_digest(
            body.username.encode(), self.settings.admin_username.encode()
        )
        password_matches = verify_password(
            body.password, self.settings.admin_password_hash.get_secret_value()
        )
        if not username_matches or not password_matches:
            failures.append(now)
            raise GameError("Incorrect username or password.", 401)
        self.failures.pop(address, None)
        return self.issue()


def admin_router(settings: Settings):
    router = APIRouter(prefix="/api/admin", tags=["admin"])
    auth = AdminAuth(settings)
    admin_dependency = Depends(auth.require)
    router.include_router(agent_router(admin_dependency))
    router.include_router(api_health_router(settings, admin_dependency))

    @router.post("/login")
    def login(body: Login, request: Request, response: Response):
        token = auth.login(request, body)
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/api/admin",
        )
        return {"username": settings.admin_username, "csrf_token": auth.csrf(token)}

    @router.get("/auth")
    def current_admin(admin=admin_dependency):
        return admin

    @router.post("/logout")
    def logout(response: Response, _admin=admin_dependency):
        response.delete_cookie(COOKIE, path="/api/admin", samesite="strict")
        return {"signed_out": True}

    @router.get("/stats")
    def stats(request: Request, _admin=admin_dependency):
        return request.app.state.store.admin_stats()

    @router.get("/players")
    def players(
        request: Request, limit: int = 100, cursor: UUID | None = None, _admin=admin_dependency
    ):
        if not 1 <= limit <= 100:
            raise GameError("Page size must be between 1 and 100.", 422)
        return request.app.state.store.admin_players(limit, str(cursor) if cursor else None)

    @router.get("/players/{player_id}")
    def player(player_id: UUID, request: Request, _admin=admin_dependency):
        return request.app.state.store.admin_player(str(player_id))

    @router.get("/sessions")
    def sessions(
        request: Request,
        limit: int = 100,
        cursor: UUID | None = None,
        player_id: UUID | None = None,
        _admin=admin_dependency,
    ):
        if not 1 <= limit <= 100:
            raise GameError("Page size must be between 1 and 100.", 422)
        return request.app.state.store.admin_sessions(
            limit, str(cursor) if cursor else None, str(player_id) if player_id else None
        )

    @router.get("/sessions/{session_id}")
    def session(session_id: UUID, request: Request, _admin=admin_dependency):
        return request.app.state.store.admin_session(str(session_id))

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: UUID, request: Request, _admin=admin_dependency):
        return request.app.state.store.delete_session(str(session_id))

    @router.delete("/players/{player_id}")
    def delete_player(player_id: UUID, request: Request, _admin=admin_dependency):
        return request.app.state.store.delete_player(str(player_id))

    return router
