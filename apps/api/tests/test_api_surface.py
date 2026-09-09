"""Whole-surface audits.

These check properties of the API as a whole rather than any one endpoint,
which is exactly the class of thing that drifts: a new router is added, one
line is forgotten, and nothing else notices.

Both findings here were real. `/redoc` and `/openapi.json` were live in
production because `docs_url` was disabled and the other two were never set.
"""

import inspect

import pytest

from acme.api import public_routes
from acme.main import create_app

AUTH_DEPS = {"CurrentUserDep", "UserTenantDep", "StaffDep"}


def _endpoints() -> list[tuple[str, tuple[str, ...], bool]]:
    app = create_app()
    rows: list[tuple[str, tuple[str, ...], bool]] = []

    def walk(routes: list[object]) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
                continue
            if not hasattr(route, "endpoint"):
                continue
            try:
                source = inspect.getsource(route.endpoint)  # type: ignore[attr-defined]
            except OSError:
                source = ""
            signature = str(inspect.signature(route.endpoint))  # type: ignore[attr-defined]
            guarded = any(d in signature or d in source for d in AUTH_DEPS) or any(
                "staff" in str(d).lower() for d in getattr(route, "dependencies", [])
            )
            methods = tuple(sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}))
            rows.append((getattr(route, "path", "?"), methods, guarded))

    walk(list(app.routes))
    return rows


def test_every_route_is_authenticated_or_explicitly_public() -> None:
    """The rule from contracts/api-conventions.md.

    Public endpoints are listed in ONE file rather than inferred from a missing
    decorator, because an endpoint that is public by accident looks exactly
    like one that is public on purpose.
    """
    unguarded = [
        f"{','.join(methods)} {path}"
        for path, methods, guarded in _endpoints()
        if not guarded and path not in public_routes.PUBLIC_PATHS
    ]
    assert not unguarded, (
        "routes that require neither auth nor a public listing:\n  "
        + "\n  ".join(sorted(set(unguarded)))
        + "\n\nAdd the auth dependency, or add the path to public_routes.py "
        "as a deliberate decision."
    )


def test_public_list_has_no_stale_entries() -> None:
    """A path listed as public but no longer routed is a rule protecting
    nothing, and it hides the fact that the real route moved."""
    app = create_app()
    routed = {getattr(r, "path", "") for r in app.routes}

    def collect(routes: list[object]) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                routed.update(getattr(r, "path", "") for r in inner.routes)
                collect(inner.routes)

    collect(list(app.routes))
    stale = sorted(public_routes.PUBLIC_PATHS - routed)
    assert not stale, f"listed public but not routed: {stale}"


@pytest.mark.parametrize("attribute", ["docs_url", "redoc_url", "openapi_url"])
def test_schema_surfaces_are_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    """All three, not just /docs.

    This was a real gap: docs_url was disabled and redoc_url was never set, so
    /redoc served every endpoint, schema and field name to anyone who guessed
    the path. openapi_url served the same thing as machine-readable JSON.

    Not a secret in the cryptographic sense - it is reconnaissance, and there
    is no reason to hand out a complete map of the surface.
    """
    from acme.core.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert getattr(app, attribute) is None
    finally:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        get_settings.cache_clear()


def test_every_endpoint_declares_a_response_shape() -> None:
    """A response with no declared schema cannot appear in the generated
    client, so a consumer has to guess - and guesses drift."""
    spec = create_app().openapi()
    undeclared = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            responses = operation.get("responses", {})
            has_body = any(
                "content" in responses[code] for code in responses if code.startswith("2")
            )
            if not has_body and "204" not in responses:
                undeclared.append(f"{method.upper()} {path}")
    assert not undeclared, f"endpoints with no response schema: {undeclared}"


def test_every_endpoint_is_documented() -> None:
    """The generated client carries these through to consumers, and the web and
    mobile teams read them instead of asking."""
    spec = create_app().openapi()
    undocumented = [
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
        if not operation.get("description") and not operation.get("summary")
    ]
    assert not undocumented, f"undocumented endpoints: {undocumented}"


def test_every_domain_is_reachable() -> None:
    """A domain with tables must have a route or a job that reaches it.

    THIS TEST EXISTS BECAUSE IT KEPT HAPPENING. Three domains were built
    thoroughly - models, schemas, services, adapters, their own tests - with no
    path from a user to any of them:

      notifications  jobs wrote rows, nothing delivered them, no endpoint read
                     them
      crm            tables, adapters and OAuth helpers, zero endpoints
      identity       provision() existed and nothing called it, so a valid
                     Supabase token got AUTH_ACCOUNT_DISABLED and NOBODY COULD
                     SIGN UP

    Each was found by a person asking, not by a test. The domain-level tests
    all passed, because a domain in isolation is exactly what they check.

    Deliberately crude: it asks whether any router or worker imports the
    domain's service at all. That is enough to catch a domain wired to nothing,
    which is the failure that actually occurred, and it stays crude so it does
    not become a test of how code is organised.
    """
    import pathlib as _pathlib

    root = _pathlib.Path(__file__).resolve().parents[1] / "src" / "acme"
    domains_dir = root / "domains"

    # `glob("*.py")` also matches nothing in __pycache__, but `rglob` and a
    # careless glob do - and a stale .pyc satisfying this check is a FALSE
    # PASS in the test whose entire job is catching false completeness.
    #
    # Found exactly that way: deleting a router left its compiled copy behind
    # and the check kept passing.
    entry_files = [
        path
        for path in [
            *(root / "api" / "routers").glob("*.py"),
            *(root / "workers").glob("*.py"),
        ]
        if "__pycache__" not in path.parts and path.suffix == ".py"
    ]
    entry_sources = "\n".join(path.read_text() for path in entry_files)

    unreachable = []
    for domain in sorted(p.name for p in domains_dir.iterdir() if p.is_dir()):
        if domain.startswith("_"):
            continue
        models = domains_dir / domain / "models.py"
        # A domain that owns no tables is orchestration and needs no route of
        # its own. `exchange` is the intended example.
        if not models.exists() or "__tablename__" not in models.read_text():
            continue
        if f"acme.domains.{domain}." not in entry_sources:
            unreachable.append(domain)

    assert not unreachable, (
        "domains with tables that no router or worker reaches: "
        f"{unreachable}\n\n"
        "Either add the endpoint or job that uses it, or delete the domain. "
        "A domain wired to nothing looks finished and is not."
    )
