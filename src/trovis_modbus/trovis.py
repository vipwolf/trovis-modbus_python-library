"""The top-level Trovis557x device object."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from modbus_connection import ModbusError
from modbus_connection.model import Component, ComponentGroup

from .addresses import register_address
from .configurations.address_ranges import (
    control_circuit_count,
    ranges_for_model,
)
from .configurations.hydronic_systems import (
    ConfigurationDefinition,
    ConfigurationTopology,
    get_configuration_definition,
)
from .configurations.sensor_variants import (
    SensorVariantResolution,
    SensorVariantStatus,
    resolve_sensor_variants,
)
from .configurations.settings import Functions, Parameters
from .configurations.trovis_models import get_model_definition_for_reported_model
from .data_model import (
    DEFAULT_WRITE_ACCESS_CODE,
    async_disable_writing,
    async_enable_writing,
    async_read_writing_enabled,
)
from .device_info import DeviceInformation
from .enums import (
    ControlCircuitRole,
    HeatingCircuitControlMode,
    RemoteInputRole,
    SystemActivity,
    SystemOverallStatus,
)
from .metadata import NumberMetadata
from .read_exclusions import (
    normalize_excluded_addresses,
    ranges_excluding_addresses,
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

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit


_LOGGER = logging.getLogger(__name__)
_TROVIS_MODBUS_TIMEOUT = 1
_TROVIS_MODBUS_CONNECT_DELAY = 0


def _configure_modbus_unit(unit: ModbusUnit) -> None:
    """Apply TROVIS-specific Modbus connection requirements when supported."""
    require_timeout = getattr(unit, "require_timeout", None)
    if callable(require_timeout):
        require_timeout(_TROVIS_MODBUS_TIMEOUT)

    require_connect_delay = getattr(unit, "require_connect_delay", None)
    if callable(require_connect_delay):
        require_connect_delay(_TROVIS_MODBUS_CONNECT_DELAY)


@dataclass(frozen=True)
class TrovisProbe:
    """Result of the safe setup probe."""

    model: int
    detected_sensors: tuple[str, ...]

    @property
    def model_name(self) -> str:
        """Return the user-facing model name."""
        return f"Trovis {self.model}"


class Trovis557x:
    """A Samson TROVIS 557x heating controller."""

    def __init__(
        self,
        unit: ModbusUnit,
        *,
        model: int = 5578,
        detected_sensors: Iterable[str] = (),
        excluded_registers: Iterable[int] = (),
        excluded_coils: Iterable[int] = (),
    ) -> None:
        if unit is not None:
            _configure_modbus_unit(unit)
        self._unit = unit
        self.model = model
        self.model_definition = get_model_definition_for_reported_model(model)
        self.excluded_registers = normalize_excluded_addresses(excluded_registers)
        self.excluded_coils = normalize_excluded_addresses(excluded_coils)
        # Probe results may contain several descriptor views of the same raw
        # register. Keep only logical sensor keys supported by this model. This
        # also sanitizes existing config entries created before ModelDefinition
        # became the authoritative model filter.
        self.probed_sensors = frozenset(detected_sensors)
        self.detected_sensors = frozenset(
            sensor_key
            for sensor_key in self.probed_sensors
            if self.model_definition.supports_sensor(sensor_key)
        )
        self.unsupported_detected_sensors = self.probed_sensors - self.detected_sensors
        self.info = DeviceInformation(unit)
        self.controller = Controller(unit)
        self.clock = Clock(unit)
        self.functions = Functions(unit)
        self.parameters = Parameters(unit)
        self.sensors = Sensors(unit)

        self.rk1 = HeatingCircuit(unit, index=1)
        self.rk2 = HeatingCircuit(unit, index=2)
        self.rk3 = HeatingCircuit(unit, index=3)
        self.rk4 = DomesticHotWater(unit)
        self.buffer_tank = BufferTankCircuit(unit)
        self.solar = SolarCircuit(unit)
        self.wmz1 = HeatMeter(unit, index=1)
        self.wmz2 = HeatMeter(unit, index=2)
        self.wmz3 = HeatMeter(unit, index=3)
        self._writing_enabled = False

        all_components = (
            self.info,
            self.controller,
            self.clock,
            self.functions,
            self.parameters,
            self.sensors,
            self.rk1,
            self.rk2,
            self.rk3,
            self.rk4,
            self.buffer_tank,
            self.solar,
            self.wmz1,
            self.wmz2,
            self.wmz3,
        )
        register_ranges, coil_ranges = ranges_for_model(model)
        register_ranges = ranges_excluding_addresses(
            register_ranges,
            self.excluded_registers,
        )
        coil_ranges = ranges_excluding_addresses(
            coil_ranges,
            self.excluded_coils,
        )
        for component in all_components:
            component.configure_readable_ranges(register_ranges, coil_ranges)

        # Ranges describe address availability. ModelDefinition additionally
        # limits logical sensor views that may share one readable register.
        self.sensors.configure_readable_fields(self.model_definition.sensor_keys)
        self._control_circuits = (
            self.rk1,
            self.rk2,
            self.rk3,
        )[: control_circuit_count(model)]

        self._group = ComponentGroup(unit, self.components)

    @classmethod
    async def async_probe(cls, unit: ModbusUnit) -> TrovisProbe:
        """Read only safe identity and sensor data for setup."""
        _configure_modbus_unit(unit)
        model = int(
            (
                await unit.read_holding_registers(
                    register_address(40001),
                    1,
                )
            )[0]
        )

        register_ranges, coil_ranges = ranges_for_model(model)
        model_definition = get_model_definition_for_reported_model(model)
        sensors = Sensors(unit)
        sensors.configure_readable_ranges(register_ranges, coil_ranges)
        sensors.configure_readable_fields(model_definition.sensor_keys)

        try:
            await sensors.async_update()
        except ModbusError as err:
            _LOGGER.warning(
                "Optional TROVIS sensor discovery failed; "
                "continuing setup without detected sensors: %s",
                err,
            )
            detected_sensors = ()
        else:
            detected_sensors = tuple(
                sensor_key
                for sensor_key in sensors.detected_sensor_names
                if model_definition.supports_sensor(sensor_key)
            )

        return TrovisProbe(
            model=model,
            detected_sensors=detected_sensors,
        )

    @property
    def control_circuits(self) -> tuple[HeatingCircuit, ...]:
        """Return the built-in Rk1-Rk3 control circuits for this model."""
        return self._control_circuits

    @property
    def heat_meters(self) -> tuple[HeatMeter, ...]:
        """Return the three indexed M-Bus meter slots."""
        return (self.wmz1, self.wmz2, self.wmz3)

    @property
    def configuration_definition(self) -> ConfigurationDefinition | None:
        """Return the known hydronic definition reported by the controller."""
        system_code = self.info.system_code
        if system_code is None:
            return None

        try:
            return get_configuration_definition(round(system_code * 10))
        except KeyError:
            return None

    @property
    def configuration_topology(self) -> ConfigurationTopology | None:
        """Return the known hydronic topology reported by the controller."""
        definition = self.configuration_definition
        return definition.topology if definition is not None else None

    @property
    def configuration_supported_by_model(self) -> bool | None:
        """Return whether the reported system code is documented for this model."""
        definition = self.configuration_definition
        if definition is None:
            return None
        return definition.supports_model(self.model_definition.model)

    def control_circuit_role(self, index: int) -> ControlCircuitRole:
        """Return the role of technical slot Rk1 through Rk4."""
        if not 1 <= index <= 4:
            raise ValueError("control circuit index must be in range 1..4")

        if index <= 3 and index > len(self._control_circuits):
            return ControlCircuitRole.UNUSED

        topology = self.configuration_topology
        if topology is not None:
            return topology.control_circuit_role(index)

        if index <= len(self._control_circuits):
            return ControlCircuitRole.HEATING
        if index == 4:
            return ControlCircuitRole.DOMESTIC_HOT_WATER
        return ControlCircuitRole.UNUSED

    @property
    def control_circuit_indices(self) -> tuple[int, ...]:
        """Return technical Rk slots enabled by model and hydronic topology."""
        return tuple(
            index
            for index in range(1, 5)
            if self.control_circuit_role(index) is not ControlCircuitRole.UNUSED
        )

    @property
    def room_heating_circuit_indices(self) -> tuple[int, ...]:
        """Return Rk1-Rk3 slots whose hydronic role is room heating."""
        return tuple(
            index
            for index in range(1, len(self._control_circuits) + 1)
            if self.control_circuit_role(index) is ControlCircuitRole.HEATING
        )

    def heating_circuit_uses_outdoor_sensor(self, index: int) -> bool | None:
        """Return whether one room-heating Rk uses weather compensation.

        ``True`` means the same circuit's COx -> F02 is active. Non-heating
        circuit roles do not have a heating-curve mode and therefore return
        ``False`` even if a dormant function coil happens to be set.
        """
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")
        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False
        return self.functions.heating_circuit_uses_outdoor_sensor(index)

    def heating_circuit_uses_room_feedback(self, index: int) -> bool | None:
        """Return whether one room-heating Rk uses room feedback.

        COx -> F01 configures room-temperature feedback. The result never
        depends on the current RFx measurement, so a later sensor failure does
        not change the configured capability. Non-heating circuit roles return
        ``False`` because room feedback is not an effective circuit function.
        """
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")
        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False
        return self.functions.heating_circuit_uses_room_feedback(index)

    def trovis_5570_room_control_unit_available(self, index: int) -> bool:
        """Return whether this model/system offers CO7-F03/F04/F05 for Rk."""
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")
        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False

        definition = self.configuration_definition
        if definition is None or not definition.supports_model(
            self.model_definition.model
        ):
            return False
        if not definition.supports_trovis_5570_room_control_unit(
            self.model_definition.model,
            index,
        ):
            return False

        circuit = self._control_circuits[index - 1]
        return circuit.is_field_readable("trovis_5570_room_control_unit")

    def heating_circuit_uses_trovis_5570(self, index: int) -> bool | None:
        """Return whether a supported room-heating Rk uses TROVIS 5570."""
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")
        if not self.trovis_5570_room_control_unit_available(index):
            # Ignore dormant CO7-F03/F04/F05 values in hydronic systems where
            # the manufacturer does not offer the corresponding function.
            return False

        circuit = self._control_circuits[index - 1]
        return circuit.trovis_5570_room_control_unit

    def remote_input_role(self, index: int) -> RemoteInputRole | None:
        """Return the configured physical meaning of FG1, FG2 or FG3.

        A local 5244/5257 room unit uses FGx for the room-setpoint correction
        when COx -> F01 is active. If a TROVIS 5570 is used over the device
        bus, or room feedback is disabled, the local FGx input remains a free
        0..2000-ohm resistance remote input. Signal validity is deliberately
        not part of this configuration-only role decision.
        """
        if not 1 <= index <= 3:
            raise ValueError(f"FG{index} is not available")
        if index > len(self._control_circuits):
            return None

        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return RemoteInputRole.RESISTANCE_REMOTE

        uses_bus_panel = self.heating_circuit_uses_trovis_5570(index)
        if uses_bus_panel is True:
            return RemoteInputRole.RESISTANCE_REMOTE
        if uses_bus_panel is None:
            return None

        room_feedback = self.heating_circuit_uses_room_feedback(index)
        if room_feedback is True:
            return RemoteInputRole.ROOM_UNIT_OFFSET
        if room_feedback is False:
            return RemoteInputRole.RESISTANCE_REMOTE
        return None

    def sensor_number_metadata(self, sensor_key: str) -> NumberMetadata:
        """Return role-aware numeric metadata for one global sensor key."""
        metadata = self.sensors.require_metadata_for(sensor_key)
        if metadata.number is None:
            raise TypeError(f"sensor {sensor_key!r} is not numeric")

        number = metadata.number
        if sensor_key not in {"fg1", "fg2", "fg3"}:
            return number

        role = self.remote_input_role(int(sensor_key[-1]))
        if role is RemoteInputRole.ROOM_UNIT_OFFSET:
            return replace(
                number,
                min_value=-5,
                max_value=5,
                step=0.1,
                digits=1,
                raw_min=-50,
                raw_max=50,
                unit="K",
            )
        if role is RemoteInputRole.RESISTANCE_REMOTE:
            return replace(
                number,
                min_value=0,
                max_value=2000,
                step=1,
                digits=0,
                raw_min=0,
                raw_max=2000,
                unit="Ω",
            )
        return number

    def sensor_value(self, sensor_key: str) -> float | int | None:
        """Return one global sensor value using its resolved semantic role."""
        value = getattr(self.sensors, sensor_key)
        if value is None or sensor_key not in {"fg1", "fg2", "fg3"}:
            return value

        role = self.remote_input_role(int(sensor_key[-1]))
        if role is RemoteInputRole.ROOM_UNIT_OFFSET:
            # The canonical register field already applies the documented
            # 0.1 scale, which is the correct representation for the local
            # room-panel correction in kelvin.
            return value
        if role is RemoteInputRole.RESISTANCE_REMOTE:
            # The same physical register is documented as a 0..2000-ohm
            # resistance input. Hardware testing shows that the register's
            # canonical 0.1-scaled value must therefore be converted back to
            # whole ohms for this semantic role.
            return value * 10
        return None

    def heating_circuit_uses_four_point_characteristic(
        self,
        index: int,
    ) -> bool | None:
        """Return whether one room-heating Rk uses a four-point curve."""
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")
        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False
        return self.functions.heating_circuit_uses_four_point_characteristic(index)

    def heating_circuit_operating_mode(
        self,
        index: int,
    ) -> HeatingCircuitControlMode | None:
        """Return the active setpoint-generation mode for one heating circuit.

        COx -> F02 disables weather compensation and selects fixed set point
        control. With weather compensation active, COx -> F11 selects the
        four-point characteristic; an unavailable F11 selector retains the
        established gradient-characteristic fallback.
        """
        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return None

        uses_outdoor_sensor = self.heating_circuit_uses_outdoor_sensor(index)
        if uses_outdoor_sensor is None:
            return None
        if not uses_outdoor_sensor:
            return HeatingCircuitControlMode.FIXED_SETPOINT
        if self.heating_circuit_uses_four_point_characteristic(index) is True:
            return HeatingCircuitControlMode.FOUR_POINT
        return HeatingCircuitControlMode.HEATING_CURVE

    def heating_circuit_optimization_available(self, index: int) -> bool:
        """Return whether optimization is available for one heating circuit.

        The documented base requirements are room-temperature feedback
        (COx -> F01) and weather-compensated control. Optional fast-heat-up
        blockers are intentionally not modeled yet because their Modbus
        addresses are not sufficiently verified.
        """
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")

        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False

        return self.heating_circuit_uses_room_feedback(
            index
        ) is True and self._heating_circuit_room_function_uses_weather(index)

    def heating_circuit_adaptation_available(self, index: int) -> bool:
        """Return whether adaptation is available for one heating circuit.

        Adaptation requires room-temperature feedback and weather-compensated
        control and is only available with the gradient characteristic, i.e.
        when COx -> F11 is disabled.
        """
        if not 1 <= index <= len(self._control_circuits):
            raise ValueError(f"Rk{index} is not available on this controller")

        if self.control_circuit_role(index) is not ControlCircuitRole.HEATING:
            return False

        return (
            self.heating_circuit_uses_room_feedback(index) is True
            and self._heating_circuit_room_function_uses_weather(index)
            and self.heating_circuit_uses_four_point_characteristic(index) is False
        )

    def _heating_circuit_room_function_uses_weather(self, index: int) -> bool:
        """Return the F02 weather prerequisite for F07/F08 of the same Rk."""
        return self.heating_circuit_uses_outdoor_sensor(index) is True

    @property
    def has_rk4(self) -> bool:
        """Return whether Rk4/WW is present or retained as safe fallback."""
        return self.control_circuit_role(4) is ControlCircuitRole.DOMESTIC_HOT_WATER

    def control_parameters_available(self, index: int) -> bool:
        """Return whether the COx-F12 control-parameter block applies to Rk."""
        if not 1 <= index <= 4:
            raise ValueError("control circuit index must be in range 1..4")
        definition = self.configuration_definition
        if definition is None or not definition.supports_model(
            self.model_definition.model
        ):
            return False
        if not definition.supports_control_parameters(
            self.model_definition.model,
            index,
        ):
            return False

        if index <= 3:
            if index > len(self._control_circuits):
                return False
            component = self._control_circuits[index - 1]
        else:
            if not self.has_rk4:
                return False
            component = self.rk4

        return component.is_field_readable(
            "three_point_control_enabled"
        ) and component.is_field_readable("control_parameter_kp")

    def two_point_control_parameters_available(self, index: int) -> bool:
        """Return whether the F12=0 parameter set applies to technical Rk."""
        if not self.control_parameters_available(index):
            return False
        definition = self.configuration_definition
        assert definition is not None
        return definition.supports_two_point_control_parameters(
            self.model_definition.model,
            index,
        )

    @property
    def intermediate_heating_available(self) -> bool:
        """Return whether CO4-F07 is available for this model/system pair."""
        definition = self.configuration_definition
        if (
            definition is None
            or not definition.supports_model(self.model_definition.model)
            or not self.has_rk4
        ):
            return False
        if not definition.supports_intermediate_heating(self.model_definition.model):
            return False
        return self.rk4.is_field_readable("intermediate_heating_function_enabled")

    @property
    def has_buffer_tank_circuit(self) -> bool:
        """Return whether Rk1 is assigned the buffer-tank circuit role."""
        return self.control_circuit_role(1) is ControlCircuitRole.BUFFER_TANK

    @property
    def has_buffer_tank_charging_parameters(self) -> bool:
        """Return whether PA1 P16-P19 apply to this model/system pair."""
        definition = self.configuration_definition
        if definition is None or not self.has_buffer_tank_circuit:
            return False
        return definition.supports_buffer_tank_charging_parameters(
            self.model_definition.model
        )

    @property
    def has_solar(self) -> bool:
        """Return whether the selected hydronic system contains a solar circuit."""
        topology = self.configuration_topology
        return topology.solar if topology is not None else False

    @property
    def components(self) -> tuple[Component, ...]:
        """Return every actively polled subsystem."""
        return (
            self.info,
            self.controller,
            self.clock,
            self.functions,
            self.parameters,
            self.sensors,
            *self.control_circuits,
            self.rk4,
            self.buffer_tank,
            self.solar,
            *self.heat_meters,
        )

    @property
    def sensor_variant_resolution(self) -> SensorVariantResolution:
        """Return the current configuration-only sensor-variant diagnosis."""
        return resolve_sensor_variants(
            self.model_definition,
            self.functions,
            self.parameters,
        )

    @property
    def canonical_sensor_keys(self) -> frozenset[str]:
        """Return model-supported sensor keys with an unambiguous role.

        Fixed sensors and conclusively resolved variants are included.
        Inactive and unresolved variants are excluded without guessing from
        their values.
        """
        return frozenset(self.sensor_variant_resolution.canonical_sensor_keys)

    @property
    def available_sensor_keys(self) -> frozenset[str]:
        """Return detected sensors that are safe to expose as normal entities."""
        resolution = self.sensor_variant_resolution

        unresolved_single_role_keys = frozenset(
            result.variant_sensor_keys[0]
            for result in resolution.variants
            if result.status is SensorVariantStatus.UNRESOLVED
            and len(result.variant_sensor_keys) == 1
        )

        return self.detected_sensors & (
            frozenset(resolution.canonical_sensor_keys) | unresolved_single_role_keys
        )

    @property
    def unresolved_detected_sensor_keys(self) -> frozenset[str]:
        """Return detected role-specific views kept only for diagnostics."""
        return self.detected_sensors & frozenset(
            self.sensor_variant_resolution.unresolved_sensor_keys
        )

    @property
    def inactive_detected_sensor_keys(self) -> frozenset[str]:
        """Return detected views whose input is configured for non-sensor use."""
        return self.detected_sensors & frozenset(
            self.sensor_variant_resolution.inactive_sensor_keys
        )

    @property
    def system_activity(self) -> SystemActivity | None:
        """Return combined heating and WW system activity from pump states."""
        heating_states = tuple(
            circuit.pump_running for circuit in self.control_circuits
        )
        ww_state = self.rk4.storage_tank_charging_pump_running

        if all(state is None for state in (*heating_states, ww_state)):
            return None

        heating = any(state is True for state in heating_states)
        ww_active = ww_state is True
        if heating and ww_active:
            return SystemActivity.HEATING_AND_DOMESTIC_HOT_WATER
        if heating:
            return SystemActivity.HEATING
        if ww_active:
            return SystemActivity.DOMESTIC_HOT_WATER
        return SystemActivity.IDLE

    @property
    def any_operating_mode_glt_active(self) -> bool | None:
        """Return whether any present circuit is under GLT mode control.

        ``False`` means every known operating-mode ownership bit is AUTARK. If
        no circuit is known to be under GLT control but at least one present
        ownership bit is unavailable, return ``None`` rather than reporting a
        false all-AUTARK state.
        """
        states: list[bool | None] = []
        for index in self.control_circuit_indices:
            if index <= 3:
                state = self._control_circuits[index - 1].mode_control_autonomous
            else:
                state = self.rk4.mode_control_autonomous
            states.append(state)

        if any(state is False for state in states):
            return True
        if states and all(state is True for state in states):
            return False
        return None

    @property
    def system_overall_status(self) -> SystemOverallStatus | None:
        """Return the actuator-state bit mask for the configured hydronic system."""
        status = SystemOverallStatus.NONE
        known_state = False

        valve_flags = (
            SystemOverallStatus.RK1_VALVE_OPEN,
            SystemOverallStatus.RK2_VALVE_OPEN,
            SystemOverallStatus.RK3_VALVE_OPEN,
        )
        pump_flags = (
            SystemOverallStatus.UP1_RUNNING,
            SystemOverallStatus.UP2_RUNNING,
            SystemOverallStatus.UP3_RUNNING,
        )

        for index in self.control_circuit_indices:
            if index > 3:
                continue

            circuit = self._control_circuits[index - 1]
            valve_setpoint = circuit.valve_setpoint
            pump_running = circuit.pump_running

            if valve_setpoint is not None:
                known_state = True
                if valve_setpoint != 0:
                    status |= valve_flags[index - 1]

            if pump_running is not None:
                known_state = True
                if pump_running is True:
                    status |= pump_flags[index - 1]

        if self.has_rk4:
            slp_running = self.rk4.storage_tank_charging_pump_running
            zp_running = self.rk4.circulation_pump_running

            if slp_running is not None:
                known_state = True
                if slp_running is True:
                    status |= SystemOverallStatus.SLP_RUNNING

            if zp_running is not None:
                known_state = True
                if zp_running is True:
                    status |= SystemOverallStatus.ZP_RUNNING

        return status if known_state else None

    @property
    def writing_enabled(self) -> bool:
        """Whether writing is enabled by the integration safety switch."""
        return self._writing_enabled

    def _operating_mode_state_snapshot(self) -> dict[int, tuple[object, object]]:
        """Return cached effective-mode and ownership state for present Rk slots."""
        snapshot: dict[int, tuple[object, object]] = {}
        for index in self.control_circuit_indices:
            component = self._control_circuits[index - 1] if index <= 3 else self.rk4
            snapshot[index] = (
                component.active_mode,
                component.mode_control_autonomous,
            )
        return snapshot

    @staticmethod
    def _operating_mode_ownership_label(value: object) -> str:
        """Return one readable ownership label for diagnostic logging."""
        if value is True:
            return "AUTARK"
        if value is False:
            return "GLT"
        return "unknown"

    async def async_update(self) -> None:
        """Refresh all active subsystems in pooled Modbus reads."""
        before = self._operating_mode_state_snapshot()
        await self._group.async_update()
        after = self._operating_mode_state_snapshot()

        # Keep operating-mode transitions visible at INFO while the settling
        # behaviour is being characterized. This also catches changes made at
        # the controller or by another Modbus client, not only HA-originated
        # writes. Once the behaviour is fully understood, these can move to
        # DEBUG together with the targeted write logs in data_model.py.
        for index, (active_after, ownership_after) in after.items():
            active_before, ownership_before = before.get(index, (None, None))
            if (active_before, ownership_before) == (None, None):
                continue
            if (active_before, ownership_before) == (
                active_after,
                ownership_after,
            ):
                continue

            _LOGGER.info(
                "Rk%d operating-mode state changed during full read: "
                "active=%s -> %s; ownership=%s -> %s",
                index,
                getattr(active_before, "name", active_before),
                getattr(active_after, "name", active_after),
                self._operating_mode_ownership_label(ownership_before),
                self._operating_mode_ownership_label(ownership_after),
            )

    async def async_read_writing_enabled(self) -> bool:
        """Read the current write-enabled state directly from the controller."""
        return await async_read_writing_enabled(self._unit)

    async def async_enable_writing(
        self,
        access_code: int = DEFAULT_WRITE_ACCESS_CODE,
    ) -> None:
        """Enable TROVIS writing globally."""
        await async_enable_writing(self._unit, access_code)
        self._writing_enabled = True

    async def async_disable_writing(self) -> None:
        """Disable TROVIS writing globally."""
        try:
            await async_disable_writing(self._unit)
        finally:
            self._writing_enabled = False
