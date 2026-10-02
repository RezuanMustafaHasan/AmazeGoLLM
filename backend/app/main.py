from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import GoogleAPICallError

from backend.app.admin import admin_router
from backend.app.catalog import load_levels
from backend.app.config import ROOT, Settings
from backend.app.engine import GameError, apply_action, new_session, observation
from backend.app.models import BatchActions, CreateSession, HintAction, TapAction
from backend.app.repository import build_repository

bearer = HTTPBearer(auto_error=False)


def create_app(settings: Settings | None = None, repository=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = repository or build_repository(settings)
        store.set_catalog(load_levels(settings.levels_dir))
        app.state.store = store
        yield
        store.close()

    app = FastAPI(
        title="Amaze Go API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url="/api/redoc",
        description="Human and agent sessions use the same authoritative puzzle engine. "
        "Create a player, use its bearer token, start a session, then observe and tap arrows.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-CSRF"],
    )

    @app.exception_handler(GameError)
    async def game_error(_request, error):
        return JSONResponse(status_code=error.code, content={"detail": error.message})

    @app.exception_handler(GoogleAPICallError)
    async def database_error(_request, _error):
        return JSONResponse(
            status_code=503,
            content={"detail": "Database temporarily unavailable. Please retry your request."},
        )

    def store(request: Request):
        return request.app.state.store

    def current_player(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if not credentials:
            raise GameError("A player bearer token is required", 401)
        return store(request).get_player(credentials.credentials)

    Player = Annotated[dict, Depends(current_player)]
    app.include_router(admin_router(settings))

    @app.get("/api/health", tags=["system"])
    def health(request: Request):
        return {
            "status": "ok",
            "storage": store(request).mode,
            "project_id": settings.firebase_project_id,
            "emulator": bool(settings.firestore_emulator_host),
            "level_source": "git",
        }

    @app.get("/api/v1/levels", tags=["levels"])
    def list_levels(request: Request):
        return {"levels": store(request).level_summaries()}

    @app.get("/api/v1/levels/{level_id}", tags=["levels"])
    def get_level(level_id: str, request: Request):
        return store(request).get_level(level_id).model_dump(mode="json")

    @app.post("/api/v1/players", status_code=201, tags=["players"])
    def create_player(request: Request):
        player_id, token = store(request).create_player()
        return {"player_id": player_id, "access_token": token, "token_type": "bearer"}

    @app.get("/api/v1/players/me", tags=["players"])
    def player_progress(player: Player):
        return {
            "player_id": player["id"],
            "progress": player["progress"],
            "active_session_id": player["active_session_id"],
        }

    @app.post("/api/v1/sessions", status_code=201, tags=["sessions"])
    def start_session(body: CreateSession, request: Request, player: Player):
        level = store(request).get_level(body.level_id)
        session = new_session(str(uuid4()), player["id"], level, body.actor_type, body.actor_name)
        store(request).create_session(session)
        return observation(session)

    @app.get("/api/v1/sessions/{session_id}", tags=["sessions"])
    def get_session(session_id: UUID, request: Request, player: Player):
        return observation(store(request).get_session(str(session_id), player["id"]))

    def act(session_id, request, player, body, hint=False):
        updated, outcome = store(request).mutate_session(
            str(session_id),
            player["id"],
            lambda session: apply_action(
                session,
                action_id=str(body.action_id),
                expected_revision=body.expected_revision,
                arrow_id=None if hint else body.arrow_id,
                hint=hint,
            ),
        )
        return {"state": observation(updated), "outcome": outcome}

    @app.post("/api/v1/sessions/{session_id}/actions", tags=["sessions"])
    def tap_arrow(session_id: UUID, body: TapAction, request: Request, player: Player):
        return act(session_id, request, player, body)

    @app.post("/api/v1/sessions/{session_id}/hints", tags=["sessions"])
    def request_hint(session_id: UUID, body: HintAction, request: Request, player: Player):
        return act(session_id, request, player, body, hint=True)

    @app.post("/api/v1/sessions/{session_id}/actions/batch", tags=["sessions"])
    def batch_actions(session_id: UUID, body: BatchActions, request: Request, player: Player):
        # One transaction verifies the ordered moves and commits their final state.
        # A retried prefix is recognized by UUID before its old revision is checked.
        def apply_batch(session):
            outcomes = []
            for offset, action in enumerate(body.actions):
                session, outcome = apply_action(
                    session,
                    action_id=str(action.action_id),
                    expected_revision=body.expected_revision + offset,
                    arrow_id=action.arrow_id,
                    hint=action.type == "hint",
                )
                outcomes.append(outcome)
            return session, outcomes

        updated, outcomes = store(request).mutate_session(
            str(session_id), player["id"], apply_batch
        )
        return {"state": observation(updated), "outcomes": outcomes}

    @app.get("/api/v1/sessions/{session_id}/history", tags=["sessions"])
    def get_history(session_id: UUID, request: Request, player: Player):
        session = store(request).get_session(str(session_id), player["id"])
        return {
            "session_id": str(session_id),
            "actor_type": session["actor_type"],
            "actor_name": session["actor_name"],
            "history": session["history"],
        }

    if (ROOT / "dist").exists():

        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        def admin_page():
            return FileResponse(ROOT / "dist" / "index.html")

        app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="frontend")

    return app


app = create_app()
