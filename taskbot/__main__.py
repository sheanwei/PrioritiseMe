"""Run TaskBot:  python -m taskbot [--port N]"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .core.app import create_app
from .core.config import HOST, settings


def main() -> None:
    s = settings()
    parser = argparse.ArgumentParser(prog="taskbot")
    parser.add_argument("--port", type=int, default=s.port, help="overrides TASKBOT_PORT")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(f"TaskBot on http://{HOST}:{args.port}  (llm={s.llm_mode}, mail={s.mail_mode})", flush=True)
    uvicorn.run(create_app(), host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
