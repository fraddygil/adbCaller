from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import socket
from pathlib import Path

from adbcaller_agent import __version__
from adbcaller_agent.client import AgentClient, pair_agent
from adbcaller_agent.config import default_config_path, load_config, save_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agente local de ADB Caller SaaS")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pair = subparsers.add_parser("pair", help="Empareja este equipo con un tenant")
    pair.add_argument("--server", required=True, help="URL HTTPS del servidor")
    pair.add_argument("--name", default=socket.gethostname())
    pair.add_argument("--code", help="Código de un uso; se pedirá si se omite")
    pair.add_argument("--config", type=Path, default=default_config_path())
    pair.add_argument("--force", action="store_true", help="Reemplaza una configuración existente")

    run = subparsers.add_parser("run", help="Conecta el agente y observa ADB")
    run.add_argument("--config", type=Path, default=default_config_path())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "pair":
        if args.config.exists() and not args.force:
            raise SystemExit(f"Ya existe {args.config}; use --force para reemplazarlo")
        code = args.code or getpass.getpass("Código de pairing: ")
        config = pair_agent(args.server, code, args.name)
        save_config(config, args.config)
        print(f"Agente {config.agent_id} emparejado; configuración guardada en {args.config}")
        return 0
    if args.command == "run":
        config = load_config(args.config)
        journal_path = args.config.with_name("journal.db")
        asyncio.run(AgentClient(config, journal_path).run_forever())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
