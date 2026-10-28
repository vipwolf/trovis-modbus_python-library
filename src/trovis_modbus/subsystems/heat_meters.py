"""Indexed M-Bus heat meters, initially covering HR46500 through HR46522."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, TypeVar

from modbus_connection.model import RegisterField

from ..addresses import register_address
from ..data_model import TrovisComponent, enum, integer, month_day_value, time_value
from ..enums import (
    ENERGY_UNIT_OPTIONS,
    FLOW_RATE_UNIT_OPTIONS,
    POWER_UNIT_OPTIONS,
    VOLUME_UNIT_OPTIONS,
    EnergyUnit,
    FlowRateUnit,
    PowerUnit,
    VolumeUnit,
)
from ..metadata import DatapointMetadata, NumberMetadata, attach_metadata
from ..utils import MonthDay

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit


_Field = TypeVar("_Field", bound=RegisterField)


def _indexed[Field: RegisterField](field: _Field) -> _Field:
    field.stride = 500
    return field


class _MeasurementField(RegisterField[float]):
    def __init__(self, hr_number: int, unit: str, factors: tuple[float, ...]) -> None:
        super().__init__(register_address(hr_number), count=4, stride=500, unit=unit)
        self._factors = factors
        attach_metadata(
            self,
            DatapointMetadata(
                value_kind="number",
                maker_reference=hr_number,
                number=NumberMetadata(unit=unit),
            ),
        )

    def decode(
        self, words: list[int], scale_exponent: int | None = None
    ) -> float | None:
        if len(words) != 4:
            return None
        high, whole, fraction, source_unit = words
        if not (
            0 <= high <= 9999
            and 0 <= whole <= 9999
            and 0 <= fraction <= 999
            and 0 <= source_unit < len(self._factors)
        ):
            return None
        return (high * 10000 + whole + fraction / 1000) * self._factors[source_unit]


class _OperatingTimeField(RegisterField[int]):
    def __init__(self) -> None:
        super().__init__(register_address(46504), count=2, stride=500)
        attach_metadata(
            self,
            DatapointMetadata(
                value_kind="number",
                maker_reference=46504,
                number=NumberMetadata(digits=0),
            ),
        )

    def decode(self, words: list[int], scale_exponent: int | None = None) -> int | None:
        if len(words) != 2 or any(not 0 <= word <= 65535 for word in words):
            return None
        return words[0] * 1000 + words[1]


class HeatMeter(TrovisComponent):
    """One read-only meter, indexed 1..3 with a 500-register stride.

    Measurements include their source unit in the same four-word field and
    normalize to m³/h, m³, kW and kWh. Source-unit fields remain available.
    """

    status = integer(46500, signed=False, stride=500, min_value=0, max_value=32767)
    error_status = integer(
        46501, signed=False, stride=500, min_value=0, max_value=32767
    )
    volume_read_date = _indexed(
        month_day_value(
            46502,
            min_value=MonthDay(1, 1),
            max_value=MonthDay(31, 12),
            raw_min=101,
            raw_max=3112,
        )
    )
    volume_read_time = _indexed(
        time_value(
            46503,
            min_value=time(0, 0),
            max_value=time(23, 59),
            raw_min=0,
            raw_max=2359,
        )
    )
    operating_time = _OperatingTimeField()
    flow_rate = _MeasurementField(46506, "m³/h", (1, 0.001))
    flow_rate_unit = enum(
        46509, FlowRateUnit, stride=500, options=FLOW_RATE_UNIT_OPTIONS
    )
    volume = _MeasurementField(46510, "m³", (1, 0.001))
    volume_unit = enum(46513, VolumeUnit, stride=500, options=VOLUME_UNIT_OPTIONS)
    power = _MeasurementField(46514, "kW", (1, 1000))
    power_unit = enum(46517, PowerUnit, stride=500, options=POWER_UNIT_OPTIONS)
    energy = _MeasurementField(46518, "kWh", (1000, 1, 1000 / 3.6))
    energy_unit = enum(46521, EnergyUnit, stride=500, options=ENERGY_UNIT_OPTIONS)
    manufacturer_id = integer(
        46522, signed=False, stride=500, min_value=0, max_value=65535
    )

    def __init__(self, unit: ModbusUnit, index: int = 1) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 3:
            raise ValueError("heat meter index must be in range 1..3")
        super().__init__(unit, index=index)
