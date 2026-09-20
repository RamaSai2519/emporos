"""The control-plane HTTP API (plan.md §15): read what the worker persisted, record commands.

    browser ──HTTPS──▶ this app ──▶ MongoDB (reads, and `commands` writes)     [no broker, ever]
                                        ▲
                       worker CommandProcessor ◀── change stream / 1 s poll

The app holds no broker credentials, imports no broker code (an import contract fails the build
if it ever does) and computes no P&L. A state-changing request becomes a validated, idempotent
record in `commands`; the worker revalidates it against live state and runs it through risk and
execution. The UI therefore always shows the real status of a command, never an optimistic one.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.types import Lifespan

from emporos.api.auth import (
    AuthService,
    Claims,
    InvalidPasscodeError,
    InvalidTokenError,
    LoginThrottledError,
)
from emporos.api.models import (
    CommandDetailDto,
    CommandDto,
    ExecutionDto,
    HealthDto,
    LoginRequest,
    OrderDetailDto,
    OrderDto,
    OverviewDto,
    PositionDto,
    ReconciliationDto,
    RiskDto,
    StrategyDto,
    SubmitCommandRequest,
    SystemEventDto,
    TokenResponse,
)
from emporos.api.queries import QueryService
from emporos.control.commands import InvalidCommandError
from emporos.control.processor import Wake
from emporos.control.submitter import CommandSubmitter, IdempotencyConflictError
from emporos.core.clock import Clock

STREAM_POLL_SECONDS = 1.0
HEARTBEAT_EVERY = 15  # polls


@dataclass(frozen=True)
class ApiServices:
    auth: AuthService
    queries: QueryService
    submitter: CommandSubmitter
    clock: Clock
    wake: Wake
    cors_origins: tuple[str, ...] = ()
    stream_poll_seconds: float = STREAM_POLL_SECONDS
    stream_limit: int | None = field(default=None)  # None: run until the client disconnects


def create_app(services: ApiServices, lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    app = FastAPI(title="Emporos control plane", version="1.0.0", lifespan=lifespan)
    if services.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(services.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        )

    def claims(request: Request) -> Claims:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else None
        if token is None and request.url.path == "/stream":
            token = request.query_params.get("access_token")  # EventSource cannot set headers
        if not token:
            raise HTTPException(401, "authentication required", {"WWW-Authenticate": "Bearer"})
        try:
            return services.auth.authenticate(token)
        except InvalidTokenError:
            raise HTTPException(
                401, "invalid or expired token", {"WWW-Authenticate": "Bearer"}
            ) from None

    Authed = Annotated[Claims, Depends(claims)]

    @app.get("/health", response_model=HealthDto)
    async def health() -> HealthDto:
        return HealthDto(status="ok", time=services.clock.now())

    @app.post("/auth/login", response_model=TokenResponse)
    async def login(body: LoginRequest, request: Request) -> TokenResponse:
        client = request.client.host if request.client else "unknown"
        try:
            token = await services.auth.login(body.passcode, client)
        except LoginThrottledError as throttled:
            raise HTTPException(
                429, "too many attempts", {"Retry-After": str(int(throttled.retry_after) + 1)}
            ) from None
        except InvalidPasscodeError:
            raise HTTPException(401, "invalid passcode") from None
        return TokenResponse(token=token.value, expires_at=token.expires_at)

    q = services.queries

    @app.get("/overview", response_model=OverviewDto)
    async def overview(_: Authed) -> OverviewDto:
        return await q.overview()

    @app.get("/positions", response_model=list[PositionDto])
    async def positions(_: Authed, open_only: bool = False) -> list[PositionDto]:
        return await q.positions(open_only)

    @app.get("/orders", response_model=list[OrderDto])
    async def orders(
        _: Authed, state: str | None = None, instrument_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> list[OrderDto]:  # fmt: skip
        return await q.orders(state, instrument_id, limit)

    @app.get("/orders/{order_id}", response_model=OrderDetailDto)
    async def order(_: Authed, order_id: str) -> OrderDetailDto:
        found = await q.order(order_id)
        if found is None:
            raise HTTPException(404, "no such order")
        return found

    @app.get("/executions", response_model=list[ExecutionDto])
    async def executions(_: Authed, limit: int = Query(100, ge=1, le=500)) -> list[ExecutionDto]:
        return await q.executions(limit)

    @app.get("/strategies", response_model=list[StrategyDto])
    async def strategies(_: Authed) -> list[StrategyDto]:
        return await q.strategies()

    @app.get("/risk", response_model=RiskDto)
    async def risk(_: Authed, limit: int = Query(50, ge=1, le=500)) -> RiskDto:
        return await q.risk(limit)

    @app.get("/system/events", response_model=list[SystemEventDto])
    async def system_events(
        _: Authed, limit: int = Query(100, ge=1, le=500)
    ) -> list[SystemEventDto]:
        return await q.system_events(limit)

    @app.get("/system/reconciliations", response_model=list[ReconciliationDto])
    async def reconciliations(
        _: Authed, limit: int = Query(50, ge=1, le=500)
    ) -> list[ReconciliationDto]:
        return await q.reconciliations(limit)

    @app.get("/commands", response_model=list[CommandDto])
    async def commands(_: Authed, limit: int = Query(50, ge=1, le=500)) -> list[CommandDto]:
        return await q.commands(limit)

    @app.get("/commands/{command_id}", response_model=CommandDetailDto)
    async def command(_: Authed, command_id: str) -> CommandDetailDto:
        found = await q.command(command_id)
        if found is None:
            raise HTTPException(404, "no such command")
        return found

    @app.post("/commands", response_model=CommandDto, status_code=202)
    async def submit(body: SubmitCommandRequest, who: Authed, response: Response) -> CommandDto:
        """Record the command and return at once: 202, status PENDING. The outcome is read from
        `GET /commands/{id}` or the stream — never assumed."""
        try:
            record = await services.submitter.submit(
                body.idempotency_key, body.type, body.params, issued_by=who.subject
            )
        except InvalidCommandError as error:
            raise HTTPException(422, str(error)) from None
        except IdempotencyConflictError as error:
            raise HTTPException(409, str(error)) from None
        if record.status != "PENDING":
            response.status_code = 200  # a replay of an already-processed command
        return QueryService.command_dto(record)

    @app.get("/stream")
    async def stream(request: Request, _: Authed) -> StreamingResponse:
        last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        return StreamingResponse(
            _events(request, services, last),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _resume_point(last_event_id: str | None) -> tuple[datetime, str, str] | None:
    """`Last-Event-ID` is `<iso timestamp>|<kind>|<id>`: exactly which change the client saw."""
    if not last_event_id:
        return None
    try:
        stamp, kind, ident = last_event_id.split("|", 2)
        return datetime.fromisoformat(stamp), kind, ident
    except ValueError:
        return None


async def _events(
    request: Request, services: ApiServices, last_event_id: str | None
) -> AsyncIterator[str]:
    """Server-sent events: what changed, oldest first, resumable from `Last-Event-ID`.

    Each event's id names the change (its timestamp, kind and record id), so a client that
    reconnects (EventSource does so automatically) says exactly where it got to and is sent
    everything after that point — nothing missed, nothing repeated, even when several changes share
    a millisecond. Changes are found by polling `updated_at` every second; a change stream only
    WAKES the loop sooner, so the stream keeps working with the stream down.
    """
    resume = _resume_point(last_event_id)
    cursor = resume[0] if resume else services.clock.now()
    last = resume  # changes at or before this point were already delivered
    yield "retry: 2000\n\n"
    polls = 0
    while not await request.is_disconnected():
        for change in await services.queries.changes_since(cursor):
            point = (change["at"], change["kind"], change["id"])
            if last is not None and point <= last:
                continue
            cursor, last = change["at"], point
            payload = json.dumps({"id": change["id"], **change["data"]}, default=str)
            event_id = f"{change['at'].isoformat()}|{change['kind']}|{change['id']}"
            yield f"id: {event_id}\nevent: {change['kind']}\ndata: {payload}\n\n"
        polls += 1
        if polls % HEARTBEAT_EVERY == 0:
            yield ": keep-alive\n\n"
        if services.stream_limit is not None and polls >= services.stream_limit:
            return
        try:
            await services.wake.wait(services.stream_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # a broken wake must not end the stream: the poll interval still holds
            await asyncio.sleep(services.stream_poll_seconds)


def openapi_document() -> dict[str, object]:
    """The API's OpenAPI schema, without any database: the contract the dashboard's typed client is
    generated from. A route change that alters it shows up as a diff in `docs/api/openapi.json`."""
    stub: Any = SimpleNamespace(
        auth=None, queries=None, submitter=None, clock=None, wake=None, cors_origins=()
    )
    return create_app(stub).openapi()
