"""Verify modeled fields against the Samson Trovis 557x reference point list.

The reference (``tests/reference/canonical_points.json``) is the address/scale
table from the Tom-Bom-badil SmartHomeNG plugin. It is used as a known reference
for modeled fields, but it is not treated as a complete list of everything the
library must expose.

This test catches wrong addresses, scales, or read-only/writable labels for
fields that are currently modeled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from modbus_connection.model import CoilField, RegisterField

from trovis_modbus import Trovis557x
from trovis_modbus.addresses import coil_address, register_address

_REF = json.loads(
    (Path(__file__).parent / "reference" / "canonical_points.json").read_text()
)
CANON_REG: dict[int, dict[str, Any]] = {e["id"]: e for e in _REF["registers"].values()}
CANON_COIL: dict[int, dict[str, Any]] = {e["id"]: e for e in _REF["coils"].values()}

# Scales confirmed on real hardware that differ from the canonical table (whose
# factors are not perfectly reliable). Tom-Bom-badil's Trovis 5578 reads register
# 117 (AT adaptation rate) as 3.0 K/h, i.e. scale 0.1 — the table lists factor 1.
HARDWARE_VERIFIED_SCALE: dict[int, float] = {
    3: 1.0,  # HR40004 hardware version: raw 1 -> hardware 1.00
    117: 0.1,
}

# Manufacturer-documented points that are modeled by the library but are not
# present in the older SmartHomeNG/5576-derived canonical reference file.
KNOWN_NON_CANONICAL_REGISTERS = {
    register_address(40028),  # 5578-E AE3 / FG3 input
    register_address(40074),  # Effective operating mode Rk1
    register_address(40075),  # Effective operating mode Rk2
    register_address(40076),  # Effective operating mode Rk3
    register_address(40077),  # Effective operating mode Rk4 / DHW
    register_address(40042),  # 5578 analog input 0-10 V
    register_address(40043),  # Summer-operation daily outdoor-temperature average
    register_address(41100),  # PA1 P16 minimum buffer-tank charging setpoint
    register_address(41101),  # PA1 P17 buffer-tank charging end temperature
    register_address(41102),  # PA1 P18 buffer-tank charging temperature boost
    register_address(41103),  # PA1 P19 buffer-tank charging-pump lag factor
    register_address(41104),  # Current buffer-tank charging status
    register_address(41268),  # Rk2 Tv; corrected manufacturer address vs old reference
    register_address(41827),  # Current domestic hot-water storage status
    register_address(42002),  # Analog-input selection (AE_Auswahl)
    register_address(42003),  # Charging-pump sensor selection (SLP_Sensor)
}

# Heat meter registers that are not part of the older canonical reference file.
KNOWN_NON_CANONICAL_REGISTERS.update(
    register_address(46500 + meter_offset + field_offset)
    for meter_offset in (0, 500, 1000)
    for field_offset in (0, 1, 2, 3, 4, 6, 9, 10, 13, 14, 17, 18, 21, 22)
)

# Manufacturer-documented points absent from the older canonical list.
# Values state the expected writable flag.
KNOWN_NON_CANONICAL_COILS = {
    coil_address(cl_number): writable
    for cl_number, writable in (
        (139, False),  # FB10 pulse input for flow/power limitation
        (159, True),  # GLT timeout monitoring
        (402, False),  # FB02 storage tank sensor SF2
        (405, False),  # FB05 flow sensor VF4
        (907, False),  # FB07 flow sensor VF2
        (1829, False),  # Mirrored FB05 flow sensor VF4
        (122, False),
        (123, False),
        (124, False),  # Active room-setpoint control level Rk1-Rk3
        (407, True),  # Hot-water intermediate-heating function block
        (412, True),  # CO4 F12 three-point/two-point control mode
        (414, True),  # CO4 F14 thermal-disinfection function block
        (703, True),
        (704, True),
        (705, True),  # Room control unit Rk1-Rk3
        (1810, False),  # Domestic hot-water storage tank charging active
        (1811, True),  # Domestic hot-water storage tank charging enabled
        (1812, False),  # Domestic hot-water storage tank charging locked
        (2107, True),
        (2207, True),
        (2307, True),  # Optimization Rk1-Rk3
        (2108, True),
        (2208, True),
        (2308, True),  # Adaptation Rk1-Rk3
        (801, False),
        (802, False),
        (803, False),
        (804, False),
        (805, False),
        (806, False),  # CO8 inputs 1-6: sensor or binary input
        (809, False),
        (810, False),
        (811, False),
        (812, False),
        (813, False),  # CO8 inputs 9-13: sensor or binary input
        (815, False),
        (816, False),
        (817, False),  # CO8 inputs 15-17: sensor or binary input
        (905, False),  # Setpoint correction through 0 to 10 V input
        (1425, False),  # CO3 F01 room-temperature feedback
        (1426, False),  # CO3 F02 outdoor sensor / weather-compensated control
        (1435, False),  # CO3 F11 four-point characteristic
        (1436, True),  # CO3 F12 three-point/two-point control mode
        (2125, False),  # CO1 F25 buffer-tank bottom sensor SF3
    )
}


def _canonical_scale(entry: dict[str, Any]) -> float | None:
    """Effective value scale of a canonical entry (None = special/non-numeric)."""
    typ = entry["typ"]
    if typ == "Version":
        return 10 ** (-entry["digits"])
    if typ.startswith("Liste") or typ in ("Datum", "Uhrzeit"):
        return None
    if typ == "Zahl":
        return float(entry["factor"])
    return None  # "???" and similar: don't assert a scale


def _fields() -> list[tuple[str, int, RegisterField | CoilField]]:
    """Every (component, effective address, field) across all components."""
    device = Trovis557x(unit=None)  # type: ignore[arg-type]
    out: list[tuple[str, int, RegisterField | CoilField]] = []
    for component in device.components:
        label = type(component).__name__ + (
            f"[{component._index}]" if component._index != 1 else ""
        )
        for field in component.declared_fields.values():
            out.append((label, component._address(field), field))
    return out


def _override_cases() -> list[Any]:
    """(label, effective override-coil address, field name) for Ebene-gated fields."""
    device = Trovis557x(unit=None)  # type: ignore[arg-type]
    cases: list[Any] = []
    for component in device.components:
        index = component._index
        label = type(component).__name__ + (f"[{index}]" if index != 1 else "")
        for name, (cl_number, stride) in getattr(component, "ebene_coils", {}).items():
            address = coil_address(cl_number + stride * (index - 1))
            cases.append(pytest.param(label, address, name, id=f"{label}.{name}"))
    return cases


REGISTER_CASES = [
    pytest.param(label, addr, field, id=f"{label}.{field.name}")
    for label, addr, field in _fields()
    if isinstance(field, RegisterField)
]
COIL_CASES = [
    pytest.param(label, addr, field, id=f"{label}.{field.name}")
    for label, addr, field in _fields()
    if isinstance(field, CoilField)
]


@pytest.mark.parametrize(("label", "address", "field"), REGISTER_CASES)
def test_register_matches_canonical(
    label: str, address: int, field: RegisterField
) -> None:
    if address in KNOWN_NON_CANONICAL_REGISTERS:
        return

    assert address in CANON_REG, f"{label}.{field.name} address {address} not in spec"
    entry = CANON_REG[address]
    # Plain scaled numbers (not value-mapped) must match the canonical scale.
    scale = getattr(field, "scale", None)
    if scale is not None and getattr(field, "convert", None) is None:
        if address in HARDWARE_VERIFIED_SCALE:
            expected = HARDWARE_VERIFIED_SCALE[address]
        else:
            expected = _canonical_scale(entry)
        if expected is not None:
            assert scale == pytest.approx(expected), (
                f"{label}.{field.name} scale {scale} != spec {expected} "
                f"({entry['name']})"
            )
    if field.writable:
        assert entry["art"] == "rw", f"{label}.{field.name} is read-only in the spec"


@pytest.mark.parametrize(("label", "address", "field"), _override_cases())
def test_override_coil_matches_canonical(label: str, address: int, field: str) -> None:
    """Every 'Ebene' override coil is an rw remote/autonomous (Liste_FA) coil."""
    # Circuit-3 override coils (92/93/97) are absent from the 5576-based
    # reference; they follow the +2/+1 pattern verified on circuits 1 and 2.
    if "[3]" in label and address in (92, 93, 97):
        pytest.skip("circuit-3 override coils absent from reference table")
    assert address in CANON_COIL, f"{label}.{field} override {address} not in spec"
    entry = CANON_COIL[address]
    assert entry["art"] == "rw"
    assert entry["typ"] == "Liste_FA", (
        f"override {address} is {entry['typ']}, not Liste_FA"
    )


@pytest.mark.parametrize(("label", "address", "field"), COIL_CASES)
def test_coil_matches_canonical(label: str, address: int, field: CoilField) -> None:
    # Heating-circuit 3 status coils (1399-1408) are not in the 5576-based
    # reference; they follow the +200 pattern verified on circuits 1 and 2.
    if "HeatingCircuit[3]" in label and (
        address in {92, 93, 97, 119, 120} or 1399 <= address <= 1408
    ):
        pytest.skip("circuit-3 coils absent from 5576 reference table")

    if address in KNOWN_NON_CANONICAL_COILS:
        assert bool(field.writable) is KNOWN_NON_CANONICAL_COILS[address]
        return

    assert address in CANON_COIL, f"{label}.{field.name} address {address} not in spec"
    if field.writable:
        assert CANON_COIL[address]["art"] == "rw", (
            f"{label}.{field.name} is read-only in the spec"
        )
