"""trovis-modbus — read a Samson Trovis 557x heating controller over Modbus.

Construct ``Trovis557x(unit)`` with a ``modbus_connection.ModbusUnit``, call
``await device.async_update()``, then read its sub-systems as normal Python
objects::

    device.sensors.af1
    device.rk1.room_setpoint_active
    device.rk4.storage_tank_charging_pump_running

Controller domains live in ``subsystems``. Static models, hydronic tables,
range maps, settings, and sensor-variant logic live in ``configurations``.
The public subsystem classes remain available while ``Trovis557x`` exposes
stable ``rk1`` through ``rk4`` slots.
"""

from .addresses import coil_address, register_address
from .configurations.sensor_variants import (
    ResolvedSensorVariant,
    SensorVariantResolution,
    SensorVariantStatus,
    resolve_sensor_variants,
)
from .configurations.settings import Functions, Parameters
from .data_model import DEFAULT_WRITE_ACCESS_CODE
from .device_info import DeviceInformation
from .enums import (
    BufferTankStatus,
    ControlCircuitRole,
    EnergyUnit,
    FlowRateUnit,
    HeatingCircuitControlMode,
    HeatMeterReadMode,
    OperatingMode,
    PowerUnit,
    PumpControlMode,
    RemoteInputRole,
    StorageStatus,
    SystemActivity,
    SystemOverallStatus,
    VolumeUnit,
    Weekday,
)
from .exceptions import (
    TrovisValueValidationError,
    TrovisWriteAccessDisabledError,
    TrovisWriteAccessError,
    TrovisWriteNotImplementedError,
    TrovisWriteVerificationError,
)
from .metadata import (
    BooleanMetadata,
    DatapointMetadata,
    EnumMetadata,
    NumberMetadata,
    OptionMetadata,
    TemporalMetadata,
)
from .subsystems import (
    BufferTankCircuit,
    Clock,
    Controller,
    DomesticHotWater,
    HeatingCircuit,
    HeatMeter,
    Sensors,
    SolarCircuit,
)
from .trovis import Trovis557x
from .utils import (
    OUTDOOR_TEMPERATURES,
    MonthDay,
    TemperatureRange,
    heating_curve,
)

__all__ = [
    "coil_address",
    "register_address",
    "OUTDOOR_TEMPERATURES",
    "BufferTankCircuit",
    "BufferTankStatus",
    "Clock",
    "Controller",
    "ControlCircuitRole",
    "DeviceInformation",
    "EnergyUnit",
    "FlowRateUnit",
    "Functions",
    "HeatingCircuit",
    "HeatingCircuitControlMode",
    "HeatMeter",
    "HeatMeterReadMode",
    "DomesticHotWater",
    "MonthDay",
    "TemperatureRange",
    "OperatingMode",
    "Parameters",
    "PowerUnit",
    "PumpControlMode",
    "RemoteInputRole",
    "SystemActivity",
    "Sensors",
    "SolarCircuit",
    "resolve_sensor_variants",
    "SensorVariantStatus",
    "SensorVariantResolution",
    "ResolvedSensorVariant",
    "StorageStatus",
    "Trovis557x",
    "VolumeUnit",
    "Weekday",
    "heating_curve",
    "DEFAULT_WRITE_ACCESS_CODE",
    "TrovisWriteNotImplementedError",
    "TrovisWriteVerificationError",
    "TrovisWriteAccessDisabledError",
    "TrovisWriteAccessError",
    "TrovisValueValidationError",
    "BooleanMetadata",
    "DatapointMetadata",
    "EnumMetadata",
    "NumberMetadata",
    "OptionMetadata",
    "TemporalMetadata",
    "SystemActivity",
    "SystemOverallStatus",
]
