from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path

import uvicorn

from math_harness import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the private local backend used by the macOS app."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing isolated workspace databases.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port. Zero asks macOS to choose an available port.",
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        default=None,
        help="Write connection metadata here after the listening socket is ready.",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="Exit automatically if the owning desktop app process disappears.",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="warning",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.data_dir is not None:
        os.environ["MATH_HARNESS_DATA_DIR"] = str(args.data_dir)

    # Import after parsing so the module-level ASGI app uses the same data root
    # when this entry point is frozen into the macOS helper executable.
    from math_harness.api import create_app

    token = os.getenv("MATH_HARNESS_LOCAL_TOKEN")
    app = create_app(args.data_dir, local_token=token)

    listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listening_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listening_socket.bind(("127.0.0.1", args.port))
    listening_socket.listen(128)
    port = int(listening_socket.getsockname()[1])

    if args.ready_file is not None:
        _write_ready_file(args.ready_file, port)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=args.log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)
    if args.parent_pid is not None:
        threading.Thread(
            target=_watch_parent,
            args=(args.parent_pid, server),
            daemon=True,
            name="math-harness-parent-watch",
        ).start()
    try:
        server.run(sockets=[listening_socket])
    finally:
        listening_socket.close()
        if args.ready_file is not None:
            args.ready_file.unlink(missing_ok=True)


def _write_ready_file(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "base_url": f"http://127.0.0.1:{port}",
        "pid": os.getpid(),
        "port": port,
        "version": __version__,
    }
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _watch_parent(parent_pid: int, server: uvicorn.Server) -> None:
    while not server.should_exit:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            server.should_exit = True
            return
        except PermissionError:
            # The process exists but is owned by another security context.
            pass
        time.sleep(0.5)


if __name__ == "__main__":
    main()
