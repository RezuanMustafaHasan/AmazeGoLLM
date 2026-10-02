"""Admin-only LLM evaluations: one durable, leased model decision per request."""

import base64
import hashlib
import time
from copy import deepcopy
from typing import Literal
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, model_validator

from backend.app.agent_image import render_board
from backend.app.agent_llm import PROVIDERS, RULES, build_prompt, invoke_model, parse_decision
from backend.app.agent_store import public_run
from backend.app.engine import (
    GameError,
    apply_action,
    apply_agent_error,
    new_session,
    now_iso,
    observation,
)
from backend.app.ufl import ModelId, gateway_key, gateway_url


class CreateAgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    # Accept old provider labels for existing clients; all calls now use UFL.
    provider: Literal["ufl", "gemini", "anthropic", "openai", "deepseek", "kimi"] = "ufl"
    model: ModelId
    start_level: StrictInt = Field(ge=1, le=100000)
    end_level: StrictInt = Field(ge=1, le=100000)
    lives: StrictInt = Field(default=3, ge=1, le=100)
    observation_mode: Literal["image_only", "image_and_state"] = "image_only"
    max_turns_per_level: StrictInt = Field(default=2000, ge=1, le=10000)
    max_output_tokens: StrictInt = Field(default=4096, ge=256, le=16384)
    timeout_seconds: StrictInt = Field(default=90, ge=10, le=180)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.end_level < self.start_level:
            raise ValueError("The end level must be at least the start level.")
        if self.end_level - self.start_level >= 1000:
            raise ValueError("Select at most 1000 levels per agent session.")
        return self


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class AgentControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["resume", "pause", "stop"]


class AgentRunner:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.invoke = self._invoke

    def _invoke(self, config, key, prompt, png):
        return invoke_model(config, key, prompt, png, base_url=gateway_url(self.settings))

    def cipher(self):
        secret = self.settings.admin_session_secret
        if not secret:
            raise GameError("Configure the admin session secret before creating agents.", 503)
        digest = hashlib.sha256(("amaze-agent-keys:" + secret.get_secret_value()).encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def providers(self):
        try:
            gateway_url(self.settings)
            url_configured = True
        except GameError:
            url_configured = False
        return [
            {
                "id": provider,
                **info,
                "configured": bool(gateway_key(self.settings)),
                "base_url_configured": url_configured,
            }
            for provider, info in PROVIDERS.items()
        ]

    def create(self, body):
        gateway_url(self.settings)
        levels = [
            level
            for level in self.store.all_levels()
            if body.start_level <= level.number <= body.end_level
        ]
        if [level.number for level in levels] != list(range(body.start_level, body.end_level + 1)):
            raise GameError("Choose a contiguous range of available catalog levels.", 422)
        key = body.api_key.get_secret_value().strip() if body.api_key else None
        key = key or gateway_key(self.settings)
        if not key:
            raise GameError(
                "Set UFL_API_KEY in the server environment or enter a UFL key for this run.", 422
            )
        credential = self.cipher().encrypt(key.encode()).decode()
        player_id, _token = self.store.create_player()
        session = new_session(
            str(uuid4()), player_id, levels[0], "agent", body.name, max_lives=body.lives
        )
        self.store.create_session(session)
        run = {
            "id": str(uuid4()),
            "config": {**body.model_dump(mode="json", exclude={"api_key"}), "provider": "ufl"},
            "credential": credential,
            "player_id": player_id,
            "level_ids": [level.id for level in levels],
            "level_index": 0,
            "current_session_id": session["id"],
            "status": "paused",
            "pending": None,
            "in_flight": False,
            "turn_count": 0,
            "results": [],
            "error": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        self.store.create_agent_run(run)
        return self.detail(run["id"])

    def detail(self, run_id):
        run = self.store.get_agent_run(run_id)
        session = self.store.get_session(run["current_session_id"], run["player_id"])
        state = observation(session)
        # The debug solver is never part of an agent observation or its dashboard state.
        state.pop("legal_arrow_ids")
        result = public_run(run)
        result["state"] = state
        result["feedback"] = build_prompt(session, state, run["config"]["observation_mode"])
        result["system_prompt"] = RULES
        pending = run.get("pending")
        result["lease_expires_at"] = pending["expires_at"] if pending else None
        return result

    def control(self, run_id, action):
        def update(run):
            if run["status"] in {"completed", "stopped"}:
                raise GameError("This agent session has ended. Create another session.")
            if action == "resume":
                run["status"] = "running"
                run["error"] = None
            else:
                run["status"] = "paused" if action == "pause" else "stopped"
            run["updated_at"] = now_iso()
            return run, None

        self.store.mutate_agent_run(run_id, update)
        return self.detail(run_id)

    def _guard(self, run, lease):
        if not run.get("pending") or run["pending"]["lease"] != lease:
            raise GameError("Another request recovered this turn. Refresh the agent session.")

    def step(self, run_id, request_id):
        previous = self.store.get_agent_turn(run_id, request_id)
        if previous and previous["status"] in {"finished", "error", "cancelled"}:
            return self.detail(run_id)
        lease = str(uuid4())

        def claim(run):
            if run["status"] in {"completed", "stopped"}:
                raise GameError("This agent session has ended.")
            pending = run.get("pending")
            if pending and pending["expires_at"] > time.time():
                raise GameError("An LLM turn is already in progress. Wait for its result.")
            if not pending:
                pending = {
                    "id": request_id,
                    "next_session_id": str(uuid4()),
                    "number": run["turn_count"] + 1,
                }
            # A crashed step is recovered using its original UUID and saved response.
            pending.update(lease=lease, expires_at=time.time() + 600)
            run.update(pending=pending, in_flight=True, updated_at=now_iso(), error=None)
            if run["status"] == "error":
                run["status"] = "paused"
            return run, None

        run = self.store.mutate_agent_run(run_id, claim)
        turn_id = run["pending"]["id"]
        turn = self.store.get_agent_turn(run_id, turn_id)
        try:
            session = self.store.get_session(
                turn["session_id"] if turn else run["current_session_id"], run["player_id"]
            )
            if not turn and session["status"] != "active":
                index = run["level_index"] + 1
                if index >= len(run["level_ids"]):
                    raise GameError("All selected levels have ended.")
                session_id = run["pending"]["next_session_id"]
                try:
                    session = self.store.get_session(session_id, run["player_id"])
                except GameError as error:
                    if error.code != 404:
                        raise
                    level = self.store.get_level(run["level_ids"][index])
                    session = new_session(
                        session_id,
                        run["player_id"],
                        level,
                        "agent",
                        run["config"]["name"],
                        max_lives=run["config"]["lives"],
                    )
                    self.store.create_session(session)

                def advance(current):
                    self._guard(current, lease)
                    current.update(current_session_id=session_id, level_index=index)
                    return current, None

                run = self.store.mutate_agent_run(run_id, advance)
            if not turn and session["moves"] >= run["config"]["max_turns_per_level"]:
                raise GameError("The per-level turn limit has been reached.")
            observed = deepcopy(session)
            if turn:
                observed.update(turn["before"])
            state = observation(observed)
            if not turn:
                turn = {
                    "id": turn_id,
                    "number": run["pending"]["number"],
                    "status": "calling",
                    "session_id": session["id"],
                    "level_id": session["level_id"],
                    "system_prompt": RULES,
                    "prompt": build_prompt(session, state, run["config"]["observation_mode"]),
                    "before": self.snapshot(state),
                    "created_at": now_iso(),
                    "raw_response": None,
                    "decision": None,
                    "outcome": None,
                    "error": None,
                    "usage": {},
                    "latency_ms": None,
                }
                self._save(run_id, lease, turn)
            if turn["raw_response"] is None:
                try:
                    key = self.cipher().decrypt(run["credential"].encode()).decode()
                except InvalidToken:
                    raise GameError(
                        "The key could not be decrypted. Create a new agent session "
                        "after changing the admin secret.",
                        422,
                    ) from None
                started = time.monotonic()
                try:
                    response = self.invoke(run["config"], key, turn["prompt"], render_board(state))
                except Exception as error:
                    # Provider exception bodies can contain credentials. Store a safe category.
                    category = type(error).__name__
                    turn["error"] = (
                        f"Provider call failed ({category}). Check the API key, "
                        "model image support, quota, and timeout. No life was lost."
                    )
                    turn["status"] = "error"
                    turn["latency_ms"] = round((time.monotonic() - started) * 1000)
                    return self._finish(run_id, lease, turn, None)
                turn.update(
                    response,
                    status="responded",
                    latency_ms=round((time.monotonic() - started) * 1000),
                )
                self._save(run_id, lease, turn)
            latest = self.store.get_agent_run(run_id)
            self._guard(latest, lease)
            if latest["status"] == "stopped":
                turn["status"] = "cancelled"
                return self._finish(run_id, lease, turn, None)
            try:
                decision = parse_decision(turn["raw_response"], [a["id"] for a in state["arrows"]])
                turn["decision"] = decision
                invalid = None
            except ValueError as error:
                invalid = str(error)

            def apply(current):
                if invalid:
                    return apply_agent_error(
                        current,
                        action_id=turn_id,
                        expected_revision=turn["before"]["revision"],
                        message=invalid,
                    )
                return apply_action(
                    current,
                    action_id=turn_id,
                    expected_revision=turn["before"]["revision"],
                    arrow_id=decision["arrow_id"],
                )

            updated, outcome = self.store.mutate_session(session["id"], run["player_id"], apply)
            turn.update(
                outcome=outcome, after=self.snapshot(observation(updated)), status="finished"
            )
            return self._finish(run_id, lease, turn, updated)
        except Exception:
            # Release the lease on infrastructure errors; a persisted response can be retried.
            def release(current):
                if current.get("pending") and current["pending"]["lease"] == lease:
                    current["pending"]["expires_at"] = 0
                    if current["status"] != "stopped":
                        current["status"] = "paused"
                    current["in_flight"] = False
                return current, None

            self.store.mutate_agent_run(run_id, release)
            raise

    @staticmethod
    def snapshot(state):
        return {
            key: deepcopy(state[key])
            for key in [
                "revision",
                "removed_ids",
                "lives_remaining",
                "max_lives",
                "moves",
                "mistakes",
                "status",
                "remaining_count",
            ]
        }

    def _save(self, run_id, lease, turn):
        def save(run):
            self._guard(run, lease)
            return run, turn

        self.store.mutate_agent_run(run_id, save)

    def _finish(self, run_id, lease, turn, session):
        turn["completed_at"] = now_iso()

        def finish(run):
            self._guard(run, lease)
            run.update(
                pending=None, in_flight=False, turn_count=turn["number"], updated_at=now_iso()
            )
            if turn["error"]:
                if run["status"] != "stopped":
                    run["status"] = "error"
                run["error"] = turn["error"]
            if session and session["status"] != "active":
                result = {
                    k: session[k]
                    for k in ["level_id", "status", "moves", "mistakes", "lives_remaining"]
                }
                result["session_id"] = session["id"]
                if not any(r["session_id"] == session["id"] for r in run["results"]):
                    run["results"].append(result)
                if run["level_index"] == len(run["level_ids"]) - 1:
                    run["status"] = "completed"
            elif session and session["moves"] >= run["config"]["max_turns_per_level"]:
                run["status"] = "paused"
                run["error"] = "The per-level turn limit was reached. Stop or create a new session."
            return run, turn

        self.store.mutate_agent_run(run_id, finish)
        return self.detail(run_id)

    def image(self, run_id, turn_id=None, phase="before"):
        run = self.store.get_agent_run(run_id)
        if turn_id:
            turn = self.store.get_agent_turn(run_id, turn_id)
            if not turn or phase not in turn:
                raise GameError("Turn image not available", 404)
            session = self.store.get_session(turn["session_id"], run["player_id"])
            session.update(turn[phase])
        else:
            session = self.store.get_session(run["current_session_id"], run["player_id"])
        return render_board(observation(session))


def agent_router(admin_dependency):
    router = APIRouter(prefix="/agents", tags=["admin agents"], dependencies=[admin_dependency])

    def runner(request):
        return request.app.state.agents

    @router.get("/providers")
    def providers(request: Request):
        return {"providers": runner(request).providers()}

    @router.get("")
    def runs(request: Request, limit: int = 100, cursor: UUID | None = None):
        if not 1 <= limit <= 100:
            raise GameError("Page size must be between 1 and 100.", 422)
        return runner(request).store.list_agent_runs(limit, str(cursor) if cursor else None)

    @router.post("", status_code=201)
    def create(body: CreateAgentRun, request: Request):
        return runner(request).create(body)

    @router.get("/{run_id}")
    def detail(run_id: UUID, request: Request):
        return runner(request).detail(str(run_id))

    @router.post("/{run_id}/step")
    def step(run_id: UUID, body: AgentStep, request: Request):
        return runner(request).step(str(run_id), str(body.request_id))

    @router.post("/{run_id}/control")
    def control(run_id: UUID, body: AgentControl, request: Request):
        return runner(request).control(str(run_id), body.action)

    @router.get("/{run_id}/turns")
    def turns(run_id: UUID, request: Request, limit: int = 50, before: int | None = None):
        if not 1 <= limit <= 100:
            raise GameError("Page size must be between 1 and 100.", 422)
        runner(request).store.get_agent_run(str(run_id))
        return runner(request).store.list_agent_turns(str(run_id), limit, before)

    @router.get("/{run_id}/image")
    def image(
        run_id: UUID,
        request: Request,
        turn_id: UUID | None = None,
        phase: Literal["before", "after"] = "before",
    ):
        return Response(
            runner(request).image(str(run_id), str(turn_id) if turn_id else None, phase),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    return router
