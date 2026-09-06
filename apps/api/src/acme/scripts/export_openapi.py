"""Emit the OpenAPI schema for client codegen.

CI runs this, regenerates packages/api-client, and fails the build if the
committed output differs (ADR-0014). Without that check the TypeScript view of
the API diverges from the Pydantic models within about a month, and the
failures are silent.

    uv run python -m acme.scripts.export_openapi > openapi.json
"""

import json
import sys

from acme.main import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
