"""Write the API's OpenAPI schema as JSON, for the generated frontend client.

Exports the production surface (no development-only routes) without starting a server or
touching the database: ``python -m firebid.api.export_openapi [output-path]``.
"""

import json
import sys
from pathlib import Path

from firebid.api.app import create_app
from firebid.settings import Settings


def main(argv: list[str]) -> None:
    app = create_app(Settings(env="prod", log_level="WARNING"), health_checks={})
    rendered = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if len(argv) > 1:
        Path(argv[1]).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main(sys.argv)
