"""Heat-meter codecs and coherent multi-register reads."""

from datetime import time

import pytest
from modbus_connection.mock import MockModbusUnit

from trovis_modbus import HeatMeter, MonthDay, Trovis557x
from trovis_modbus.addresses import register_address


@pytest.mark.parametrize("index", [1, 2, 3])
async def test_indexed_meter_read(unit: MockModbusUnit, index: int) -> None:
    base = register_address(46500 + (index - 1) * 500)
    words = [32767, 1, 2106, 1430, 2, 345]
    words += [1, 2345, 678, 1] * 4
    words += [65535]
    unit.holding.update({base + offset: word for offset, word in enumerate(words)})
    meter = HeatMeter(unit, index=index)
    assert meter.flow_rate is None
    await meter.async_update()

    assert meter.status == 32767
    assert meter.error_status == 1
    assert meter.volume_read_date == MonthDay(21, 6)
    assert meter.volume_read_time == time(14, 30)
    assert meter.operating_time == 2345
    assert meter.flow_rate == pytest.approx(12.345678)
    assert meter.volume == pytest.approx(12.345678)
    assert meter.power == pytest.approx(12345678)
    assert meter.energy == pytest.approx(12345.678)
    assert meter.manufacturer_id == 65535
    for offset in (6, 10, 14, 18):
        events = [
            event
            for event in unit.read_events
            if event.address < base + offset + 4
            and event.address + event.count > base + offset
        ]
        assert len(events) == 1
        assert events[0].address <= base + offset
        assert events[0].address + events[0].count >= base + offset + 4
    assert all(
        base <= event.address < event.address + event.count <= base + 23
        for event in unit.read_events
    )


@pytest.mark.parametrize(
    ("name", "factors"),
    [
        ("flow_rate", (1, 0.001)),
        ("volume", (1, 0.001)),
        ("power", (1, 1000)),
        ("energy", (1000, 1, 1000 / 3.6)),
    ],
)
def test_measurement_codec(name: str, factors: tuple[float, ...]) -> None:
    field = HeatMeter.declared_fields[name]
    for source_unit, factor in enumerate(factors):
        assert field.decode([1, 2345, 678, source_unit]) == pytest.approx(
            12345.678 * factor
        )
        assert field.decode([0, 0, 0, source_unit]) == 0
        assert field.decode([9999, 9999, 999, source_unit]) == pytest.approx(
            99999999.999 * factor
        )
    for words in (
        [],
        [1, 2, 3],
        [0, 0, 0, 99],
        [0, 0, 0, -1],
        [-1, 0, 0, 0],
        [10000, 0, 0, 0],
        [0, 10000, 0, 0],
        [0, -1, 0, 0],
        [0, 0, -1, 0],
        [0, 0, 1000, 0],
    ):
        assert field.decode(words) is None


@pytest.mark.parametrize("index", [0, 4, -1, True, 1.5, "1"])
def test_invalid_index(unit: MockModbusUnit, index: object) -> None:
    with pytest.raises(ValueError, match="1..3"):
        HeatMeter(unit, index=index)


@pytest.mark.parametrize("model", [5573, 55731, 5575, 5576, 5578, 55781, 5579])
async def test_device_polls_all_meter_slots(unit: MockModbusUnit, model: int) -> None:
    device = Trovis557x(unit, model=model)
    assert device.heat_meters == (device.wmz1, device.wmz2, device.wmz3)
    for index, meter in enumerate(device.heat_meters, start=1):
        assert meter in device.components
        base = register_address(46500 + (index - 1) * 500)
        for offset in (6, 10, 14, 18):
            unit.holding.update(
                {
                    base + offset + part: word
                    for part, word in enumerate([index, 2345, 678, 0])
                }
            )
    await device.async_update()
    for index, meter in enumerate(device.heat_meters, start=1):
        base = register_address(46500 + (index - 1) * 500)
        assert meter.flow_rate == pytest.approx(index * 10000 + 2345.678)
        assert meter.flow_rate_unit.value == 0
        for offset in (6, 10, 14, 18):
            events = [
                event
                for event in unit.read_events
                if event.register_type == "holding"
                and event.address < base + offset + 4
                and event.address + event.count > base + offset
            ]
            assert len(events) == 1
            assert events[0].address <= base + offset
            assert events[0].address + events[0].count >= base + offset + 4


async def test_planner_boundary_keeps_composites_intact(unit: MockModbusUnit) -> None:
    meter = HeatMeter(unit)
    meter.max_span = 7
    await meter.async_update()
    assert len(unit.read_events) > 1
    for name in ("operating_time", "flow_rate", "volume", "power", "energy"):
        resolved = meter.resolved_fields[name]
        events = [
            event
            for event in unit.read_events
            if event.address < resolved.address + resolved.count
            and event.address + event.count > resolved.address
        ]
        assert len(events) == 1
        assert events[0].address <= resolved.address
        assert events[0].address + events[0].count >= resolved.address + resolved.count


@pytest.mark.parametrize("offset", [6, 7, 8, 9])
async def test_exclusion_removes_entire_measurement(
    unit: MockModbusUnit, offset: int
) -> None:
    excluded = register_address(46500 + offset)
    device = Trovis557x(unit, excluded_registers=[excluded])
    assert not device.wmz1.is_field_readable("flow_rate")
    assert device.wmz1.is_field_readable("volume")
    await device.async_update()
    assert device.wmz1.flow_rate is None
    assert device.wmz1.volume == 0
    assert all(
        not event.address <= excluded < event.address + event.count
        for event in unit.read_events
        if event.register_type == "holding"
    )


async def test_metadata_and_read_only_fields(unit: MockModbusUnit) -> None:
    meter = HeatMeter(unit)
    for name in meter.declared_fields:
        metadata = meter.require_metadata_for(name)
        assert not metadata.writable
        assert not meter.declared_fields[name].writable
        with pytest.raises(AttributeError, match="read-only"):
            await meter.write(name, 0)
    for name, expected_unit in (
        ("flow_rate", "m³/h"),
        ("volume", "m³"),
        ("power", "kW"),
        ("energy", "kWh"),
    ):
        assert meter.require_metadata_for(name).number.unit == expected_unit
    assert meter.require_metadata_for("operating_time").number.unit is None
    assert not unit.read_events


def test_temporal_and_operating_time_codecs() -> None:
    fields = HeatMeter.declared_fields
    assert fields["volume_read_date"].decode([3102]) is None
    assert fields["volume_read_time"].decode([2360]) is None
    assert fields["operating_time"].decode([65535, 65535]) == 65600535
    assert fields["operating_time"].decode([0, 0]) == 0
    for words in ([], [1], [1, 2, 3], [-1, 0], [0, 65536]):
        assert fields["operating_time"].decode(words) is None


@pytest.mark.parametrize("index", [1, 2, 3])
def test_manufacturer_field_layout(unit: MockModbusUnit, index: int) -> None:
    expected = {
        "status": (46500, 1),
        "error_status": (46501, 1),
        "volume_read_date": (46502, 1),
        "volume_read_time": (46503, 1),
        "operating_time": (46504, 2),
        "flow_rate": (46506, 4),
        "flow_rate_unit": (46509, 1),
        "volume": (46510, 4),
        "volume_unit": (46513, 1),
        "power": (46514, 4),
        "power_unit": (46517, 1),
        "energy": (46518, 4),
        "energy_unit": (46521, 1),
        "manufacturer_id": (46522, 1),
    }
    meter = HeatMeter(unit, index=index)
    assert set(meter.declared_fields) == set(expected)
    for name, (reference, count) in expected.items():
        resolved = meter.resolved_fields[name]
        assert resolved.address == register_address(reference + (index - 1) * 500)
        assert resolved.count == count
        assert meter.require_metadata_for(name).maker_reference == reference
        assert not resolved.field.writable
