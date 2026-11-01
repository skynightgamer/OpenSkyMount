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
    parser.add_argument("--password", default=os.environ.get("OPENSKYMOUNT_PASSWORD") or None,
                        help="require this password to open the UI (recommended with --host 0.0.0.0). "
                        "Prefer the OPENSKYMOUNT_PASSWORD environment variable: command lines are visible to other users")
    args = parser.parse_args()
    if args.password is not None and len(args.password) < 6:
        parser.error("--password must have at least 6 characters")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.password:
        print("WARNING: listening on the network without a password; anyone on this network can control the mount. "
              "Set OPENSKYMOUNT_PASSWORD or --password.")

    import uvicorn

    from .app import create_app

    app = create_app(state_dir=Path(args.state_dir), driver=args.driver, password=args.password)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
