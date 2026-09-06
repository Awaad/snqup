"""Tenant-scoped repository base.

ADR-0018 requires that forgetting a tenant filter be structurally impossible
rather than merely discouraged. We declined Postgres RLS (ADR-0005) so
authorization lives in reviewable, testable code - which means this file is the
thing standing between us and a cross-tenant data leak.

The design rule: there is NO way to obtain a query object without the tenant
predicate already applied. `scoped()` is the only entry point, and it is
impossible to call it without a tenant because the tenant is a constructor
argument.

Every subclass also gets soft-delete filtering for free, because `deleted_at`
is on every user-facing table (ADR-0020) and remembering it on every query is
the same class of mistake.

If you find yourself reaching for `select(Model)` directly in a service, stop.
That is the bypass this file exists to prevent, and it is what
tests/test_tenant_isolation.py checks for.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.db import Base


@dataclass(frozen=True, slots=True)
class Tenant:
    """Who the query is on behalf of.

    Two kinds, deliberately not interchangeable (ADR-0018):

      user          a person's own data: their cards, their connection views
      organization  team-owned data: org cards, seats, branding

    Frozen because a tenant that can be reassigned mid-request is a
    cross-tenant leak waiting for the right ordering of statements.
    """

    kind: str  # "user" | "organization"
    id: UUID

    @staticmethod
    def user(user_id: UUID) -> "Tenant":
        return Tenant(kind="user", id=user_id)

    @staticmethod
    def organization(org_id: UUID) -> "Tenant":
        return Tenant(kind="organization", id=org_id)


class TenantScopedRepository[ModelT: Base]:
    """Base for every repository that touches tenant-owned rows.

    Subclasses declare the model and which column carries the tenant. They do
    not write the predicate; they cannot forget it.
    """

    model: type[ModelT]
    #: Column on `model` holding the owning user id.
    user_column: str | None = "user_id"
    #: Column on `model` holding the owning organization id, when it has one.
    organization_column: str | None = None

    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant

    @property
    def session(self) -> AsyncSession:
        return self._session

    @property
    def tenant(self) -> Tenant:
        return self._tenant

    def _tenant_column(self) -> Any:
        if self._tenant.kind == "user":
            if self.user_column is None:
                raise TypeError(
                    f"{type(self).__name__} has no user_column; it cannot be "
                    "queried on behalf of a user tenant."
                )
            return getattr(self.model, self.user_column)

        if self._tenant.kind == "organization":
            if self.organization_column is None:
                raise TypeError(
                    f"{type(self).__name__} has no organization_column; it "
                    "cannot be queried on behalf of an organization tenant."
                )
            return getattr(self.model, self.organization_column)

        raise ValueError(f"unknown tenant kind: {self._tenant.kind!r}")

    def scoped(self, *, include_deleted: bool = False) -> Select[tuple[ModelT]]:
        """The ONLY way to start a query in this repository.

        Returns a select already filtered to the tenant and, unless explicitly
        asked otherwise, to rows that are not soft-deleted.

        `include_deleted` exists for the purge job and for restore-within-grace
        (ADR-0020). It is not a convenience; every call site that passes True
        should be able to say which of those two it is.
        """
        stmt = select(self.model).where(self._tenant_column() == self._tenant.id)

        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is not None and not include_deleted:
            stmt = stmt.where(deleted_at.is_(None))

        return stmt

    async def get(self, entity_id: UUID) -> ModelT | None:
        """Fetch one row belonging to this tenant.

        Returns None when the row belongs to someone else, exactly as it does
        when the row does not exist. The caller must not distinguish the two:
        a different response for "exists but not yours" confirms the row exists
        to someone who should not know that (see CONNECTION_NOT_FOUND in
        contracts/error-codes.md).
        """
        stmt = self.scoped().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(self, *, limit: int = 50) -> list[ModelT]:
        stmt = self.scoped().limit(limit)
        return list((await self._session.execute(stmt)).scalars())

    def add(self, entity: ModelT) -> ModelT:
        """Add a row, asserting it belongs to this tenant.

        Without this check a service could construct an entity carrying someone
        else's tenant id and write it, and no read-side filter would ever catch
        it - the row would simply be invisible to its actual owner and visible
        to the attacker.
        """
        column_name = self.user_column if self._tenant.kind == "user" else self.organization_column
        if column_name is not None:
            actual = getattr(entity, column_name, None)
            if actual is None:
                setattr(entity, column_name, self._tenant.id)
            elif actual != self._tenant.id:
                raise PermissionError(
                    f"refusing to write {type(entity).__name__} owned by "
                    f"{actual} while acting for {self._tenant.id}"
                )

        self._session.add(entity)
        return entity
