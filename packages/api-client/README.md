# @acme/api-client

**Generated. Do not hand-edit `src/generated.ts`.**

FastAPI emits `openapi.json` from Pydantic models; `openapi-typescript` generates the
types here. CI regenerates and fails the build if the committed output differs
(ADR-0014).

Without that check, the TypeScript view of the API diverges from the Python models within
about a month, and the failures are silent: an optional field becomes required, a nullable
becomes non-nullable, and the mobile client crashes for users on an old version.

## Regenerating

```bash
pnpm generate:api
```

## Hand-written code

`src/index.ts` and any client wrapper are hand-written and reviewed normally. Only
`src/generated.ts` is off-limits.
