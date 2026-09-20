"""The committed OpenAPI schema is the contract the dashboard's client is generated from: a route
change must show up as a reviewed diff of `docs/api/openapi.json`, never as a surprise in the UI."""

import json
from pathlib import Path

from emporos.api.app import openapi_document

COMMITTED = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.json"


def test_the_committed_schema_is_the_one_the_code_generates() -> None:
    generated = json.loads(json.dumps(openapi_document(), sort_keys=True))
    assert (
        json.loads(COMMITTED.read_text(encoding="utf-8")) == generated
    ), "the API changed: run `emporos api openapi` and commit docs/api/openapi.json"


def test_every_operation_is_named_and_every_success_response_is_typed() -> None:
    schema = openapi_document()
    paths = schema["paths"]
    assert isinstance(paths, dict)
    for path, operations in paths.items():
        for method, operation in operations.items():
            assert operation["operationId"], (path, method)
            if path not in {"/stream"}:  # a server-sent stream has no JSON body schema
                ok = next(r for code, r in operation["responses"].items() if code.startswith("2"))
                assert "application/json" in ok["content"], (path, method)


def test_money_is_a_string_in_every_schema_that_carries_it() -> None:
    schemas = openapi_document()["components"]["schemas"]  # type: ignore[index]
    for name in ("OrderDto", "PositionDto", "ExecutionDto"):
        properties = schemas[name]["properties"]  # type: ignore[index]
        for field in ("limit_price", "average_price", "price", "realised_pnl"):
            if field in properties:
                assert "string" in json.dumps(properties[field]), (name, field)
