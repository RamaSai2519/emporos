"""`emporos research vault status` — what is sealed and how many opens are left (EM-191 §4.3).

Read-only. It reads the committed seal and unseal records and prints them; it cannot open the
vault, because opening is a committed record plus a named candidate, never a command."""

from __future__ import annotations

import typer

from emporos.cli.vault_files import VaultFiles
from emporos.core.errors import EmporosError

vault_app = typer.Typer(help="The one-shot holdout vault.", no_args_is_help=True)


@vault_app.command("status")
def vault_status() -> None:
    """Show the sealed range, the opens used and the opens left."""
    try:
        gate = VaultFiles().load()
    except EmporosError as error:
        typer.secho(f"vault: {error.message}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    seal = gate.seal
    scope = (
        "every instrument (time-only)"
        if seal.instrument_ids is None
        else (f"{len(seal.instrument_ids)} sealed instruments")
    )
    typer.echo(f"sealed: {seal.first_day} to {seal.last_day}, {scope}")
    typer.echo(f"seal hash: {seal.content_hash}")
    typer.echo(f"opens used: {gate.opens_used} of {seal.max_opens} ({gate.opens_left} left)")
    if gate.opens_left == 0:
        typer.echo("the vault is burned: only forward paper trading is clean evidence now")
