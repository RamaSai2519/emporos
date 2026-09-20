"""`emporos api serve` and `emporos api set-passcode` (plan.md §15).

The API runs on 127.0.0.1 unless told otherwise: public exposure (Caddy, TLS) is deployment, not
this command. It needs MongoDB and a signing secret, and nothing else — no broker credentials.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import uvicorn

from emporos.api.app import create_app, openapi_document
from emporos.api.auth import Argon2Passcodes
from emporos.api.stores import MongoPasscodeStore
from emporos.cli.api_composition import ApiComposer
from emporos.core.alerts import LogAlertSink
from emporos.core.clock import AsyncioSleeper, SystemClock
from emporos.core.config import Settings
from emporos.core.ids import IdGenerator
from emporos.persistence.mongo import MongoClientFactory
from emporos.persistence.repositories import UserRepository
from emporos.risk.config import RiskLimitsLoader

api_app = typer.Typer(help="The control-plane API.", no_args_is_help=True)

_HOST = typer.Option("127.0.0.1", help="Address to listen on.")
_PORT = typer.Option(8000, help="Port to listen on.")
_OPENAPI_OUT = typer.Option(Path("docs/api/openapi.json"), help="Where to write it.")


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED)
    return typer.Exit(code=1)


@api_app.command("serve")
def api_serve(host: str = _HOST, port: int = _PORT) -> None:
    """Serve the read endpoints, the command endpoint and the live stream."""
    settings = Settings.default()
    if not settings.mongo_url:
        raise _fail("MONGO_URL is not set")
    if not settings.api_jwt_secret or len(settings.api_jwt_secret.encode()) < 32:
        raise _fail("API_JWT_SECRET must be set to at least 32 bytes")
    mongo = MongoClientFactory(settings)
    origins = tuple(o.strip() for o in (settings.api_cors_origins or "").split(",") if o.strip())
    services, lifespan = ApiComposer(
        database=mongo.database(),
        clock=SystemClock(),
        sleeper=AsyncioSleeper(),
        ids=IdGenerator(),
        alerts=LogAlertSink(),
        account_id=settings.api_account_id or settings.angelone_client_code or "PAPER",
        jwt_secret=settings.api_jwt_secret.encode(),
        limits=RiskLimitsLoader().load(),
        cors_origins=origins,
    ).build()
    uvicorn.run(create_app(services, lifespan), host=host, port=port, log_level="info")


@api_app.command("set-passcode")
def api_set_passcode() -> None:
    """Set the single operator passcode (stored only as an Argon2id hash)."""
    passcode = typer.prompt("New passcode (min 8 characters)", hide_input=True)
    if passcode != typer.prompt("Repeat passcode", hide_input=True):
        raise _fail("the passcodes do not match")
    try:
        hashed = Argon2Passcodes().hash(passcode)
    except ValueError as error:
        raise _fail(str(error)) from error

    async def store() -> None:
        mongo = MongoClientFactory(Settings.default())
        try:
            await MongoPasscodeStore(UserRepository(mongo.database())).set_passcode_hash(hashed)
        finally:
            await mongo.close()

    asyncio.run(store())
    typer.secho("passcode set", fg=typer.colors.GREEN)


@api_app.command("openapi")
def api_openapi(out: Path = _OPENAPI_OUT) -> None:
    """Write the OpenAPI schema (the contract the dashboard's client is generated from)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    typer.echo(f"wrote {out}")
