from __future__ import annotations

import argparse
import asyncio
import sys

from adb_client import AdbClient, AdbError
from call_controller import CallController
from config import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control básico de llamadas por ADB")
    subparsers = parser.add_subparsers(dest="command", required=True)

    call = subparsers.add_parser("call", help="Iniciar una llamada")
    call.add_argument("serial")
    call.add_argument("number")

    add_call = subparsers.add_parser(
        "add-call", help="Agregar una llamada a otra que ya está activa"
    )
    add_call.add_argument("serial")
    add_call.add_argument("number")

    for command, help_text in (
        ("hangup", "Colgar la llamada"),
        ("answer", "Contestar la llamada"),
        ("status", "Leer el estado de llamada"),
        ("calls", "Listar las llamadas actuales"),
        ("merge", "Combinar una llamada activa y otra en espera"),
        ("speaker", "Activar el altavoz de la llamada"),
    ):
        action = subparsers.add_parser(command, help=help_text)
        action.add_argument("serial")

    subparsers.add_parser("devices", help="Listar dispositivos ADB")
    return parser


async def run(args: argparse.Namespace) -> None:
    adb = AdbClient(settings.adb_path, settings.adb_timeout)
    controller = CallController(adb)
    if args.command == "devices":
        for device in await adb.list_devices():
            print(f"{device.serial}\t{device.state.value}\t{device.model}")
    elif args.command == "call":
        print((await controller.call(args.serial, args.number)).value)
    elif args.command == "add-call":
        print((await controller.add_call(args.serial, args.number)).value)
    elif args.command == "hangup":
        print((await controller.hangup(args.serial)).value)
    elif args.command == "answer":
        print((await controller.answer(args.serial)).value)
    elif args.command == "status":
        print((await controller.get_call_state(args.serial)).value)
    elif args.command == "calls":
        calls = await controller.get_current_calls(args.serial)
        if not calls:
            print("No hay llamadas actuales")
        for call in calls:
            print(f"{call.call_id}\t{call.state.value}")
    elif args.command == "merge":
        await controller.merge_calls(args.serial)
        print("MERGE_REQUESTED")
    elif args.command == "speaker":
        changed = await controller.enable_speaker(args.serial)
        print("SPEAKER_ENABLED" if changed else "ALREADY_ENABLED")


def main() -> int:
    try:
        asyncio.run(run(build_parser().parse_args()))
        return 0
    except (AdbError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
