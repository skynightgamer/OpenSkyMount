"""Command-line entry point: ``python -m openskymount.server``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSkyMount local server")
    parser.add_argument("--host", default=os.environ.get("OPENSKYMOUNT_HOST", "127.0.0.1"),
                        help="bind address; use 0.0.0.0 to allow other devices on your LAN")
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPENSKYMOUNT_PORT", "8000")))
    parser.add_argument("--state-dir", default=os.environ.get("OPENSKYMOUNT_STATE", "state"),
                        help="directory for persisted settings")
    parser.add_argument("--driver", default=os.environ.get("OPENSKYMOUNT_DRIVER", "sim"),
                        help="sim (default) | tcp://host:port (OSM protocol, e.g. fake firmware)")
    args = parser.parse_args()

    import uvicorn

    from .app import create_app

    app = create_app(state_dir=Path(args.state_dir), driver=args.driver)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
