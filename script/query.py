#!/usr/bin/env python3

"""Query a Samson TROVIS 557x over Modbus and print every value.

Connects over Modbus TCP (a network gateway) or a serial/USB port, reads the
whole device once, and dumps every subsystem's values to the terminal. Handy
for checking a real controller without Home Assistant.

The library only needs the connection protocol; this script selects the
pymodbus backend, so install the ``cli`` extra first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from modbus_connection import (
    ModbusConnection,
    ModbusError,
    ModbusSerialParams,
    ModbusTcpParams,
)
from modbus_connection.cli_helper import CountingUnit, print_component

from trovis_modbus import Trovis557x

# (label, attribute name on Trovis557x) — the order in which sections are printed.
SECTIONS: list[tuple[str, str]] = [
    ("Device", "info"),
    ("Controller", "controller"),
    ("Clock", "clock"),
    ("Functions", "functions"),
    ("Parameters", "parameters"),
    ("Measurements", "sensors"),
    ("WMZ1 - Heat meter 1", "wmz1"),
    ("WMZ2 - Heat meter 2", "wmz2"),
    ("WMZ3 - Heat meter 3", "wmz3"),
    ("Rk1 - Control circuit 1", "rk1"),
    ("Rk2 - Control circuit 2", "rk2"),
    ("Rk3 - Control circuit 3", "rk3"),
    ("Rk4 - Domestic hot water", "rk4"),
    ("Rk1 - Buffer tank extension", "buffer_tank"),
    ("Solar circuit", "solar"),
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="transport", required=True)

    # Shared options available on each transport (so --unit can follow the host).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--unit",
        type=int,
        default=246,
        help="Modbus unit/station address (default: 246)",
    )
    tcp = sub.add_parser(
        "tcp",
        parents=[common],
        help="connect over Modbus TCP (network gateway)",
    )
    tcp.add_argument("host", help="hostname or IP of the gateway/device")
    tcp.add_argument("--port", type=int, default=502, help="TCP port (default: 502)")
    tcp.add_argument(
        "--framer",
        choices=("rtu", "socket"),
        default="rtu",
        help=(
            "wire framing: 'rtu' for RTU-over-TCP (transparent serial gateways, "
            "the TROVIS default) or 'socket' for native Modbus TCP (default: rtu)"
        ),
    )
    serial = sub.add_parser(
        "serial",
        parents=[common],
        help="connect over a serial/USB port",
    )
    serial.add_argument("device", help="serial device, e.g. /dev/ttyUSB0")
    serial.add_argument("--baudrate", type=int, default=19200, help="default: 19200")
    serial.add_argument("--parity", choices=("N", "E", "O"), default="N")
    serial.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    serial.add_argument("--bytesize", type=int, choices=(7, 8), default=8)
    return parser.parse_args(argv)


def _connection(args: argparse.Namespace) -> ModbusConnection:
    """Build the connection described by the arguments. Performs no I/O."""
    # Imported here so the module loads (and --help works) without a backend.
    from modbus_connection.pymodbus import PymodbusConnection

    if args.transport == "serial":
        return PymodbusConnection(
            ModbusSerialParams(
                device=args.device,
                baudrate=args.baudrate,
                parity=args.parity,
                stopbits=args.stopbits,
                bytesize=args.bytesize,
            )
        )
    return PymodbusConnection(
        ModbusTcpParams(host=args.host, port=args.port, framer=args.framer)
    )


def _format_evidence(
    evidence: tuple[tuple[str, bool | int | None], ...],
) -> str:
    """Format resolver evidence for a compact diagnostic line."""
    return ", ".join(f"{name}={value!r}" for name, value in evidence)


def _format_sensor_keys(sensor_keys: frozenset[str]) -> str:
    """Format sensor-key sets deterministically for diagnostics."""
    return ", ".join(sorted(sensor_keys)) or "-"


def _print_sensor_variants(device: Trovis557x) -> None:
    """Print configuration-only sensor-variant diagnostics."""
    resolution = device.sensor_variant_resolution
    print()
    print("Sensor selection")
    print(f"  entities: {_format_sensor_keys(device.available_sensor_keys)}")
    print(
        "  unresolved diagnostics: "
        f"{_format_sensor_keys(device.unresolved_detected_sensor_keys)}"
    )
    print(
        "  inactive inputs: "
        f"{_format_sensor_keys(device.inactive_detected_sensor_keys)}"
    )
    print()
    print("Sensor variants")
    for result in resolution.variants:
        variant = " / ".join(result.variant_sensor_keys)
        selected = result.selected_sensor_key or "-"
        candidates = ", ".join(result.candidate_sensor_keys) or "-"
        evidence = _format_evidence(result.evidence) or "none"
        print(
            f"  {variant}: {result.status.value}; selected={selected}; "
            f"candidates={candidates}; {result.reason}; evidence: {evidence}"
        )


def _print(device: Trovis557x) -> None:
    for label, attr in SECTIONS:
        if attr == "buffer_tank" and not device.has_buffer_tank_circuit:
            continue
        if attr == "solar" and not device.has_solar:
            continue
        print()
        print_component(getattr(device, attr), title=label)
    _print_sensor_variants(device)


async def _run(args: argparse.Namespace) -> int:
    connection = _connection(args)
    try:
        await connection.connect()
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1
    counting = CountingUnit(connection.for_unit(args.unit))
    try:
        probe = await Trovis557x.async_probe(counting)
        device = Trovis557x(
            counting,
            model=probe.model,
            detected_sensors=probe.detected_sensors,
        )
        start = time.monotonic()
        await device.async_update()
        elapsed = time.monotonic() - start
    except ModbusError as err:
        print(f"Error reading device: {err}", file=sys.stderr)
        return 1
    finally:
        await connection.close()
    _print(device)
    print(f"\nQueried in {elapsed * 1000:.0f} ms ({counting.reads} Modbus reads)")
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
