[![PyPI](https://img.shields.io/pypi/v/trovis-modbus.svg)](https://pypi.org/project/trovis-modbus/)
[![Python](https://img.shields.io/pypi/pyversions/trovis-modbus.svg)](https://pypi.org/project/trovis-modbus/)
[![License](https://img.shields.io/github/license/Tom-Bom-badil/trovis-modbus_python-library.svg)](LICENSE)
[![CI](https://github.com/Tom-Bom-badil/trovis-modbus_python-library/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/Tom-Bom-badil/trovis-modbus_python-library/actions/workflows/ci.yml)

<img width="100%" alt="SAMSON TROVIS controllers" src="https://github.com/Tom-Bom-badil/trovis-modbus_python-library/wiki/images/trovis-lib-pic.png" />

<br/>

# `trovis-modbus` Python library

`trovis-modbus` is an asynchronous, transport-independent Python library for communicating with **SAMSON TROVIS 557x** heating and district heating controllers and compatible OEM variants over Modbus.

The library was developed primarily as the backend for the corresponding [Samson Trovis 557x](https://github.com/Tom-Bom-badil/samson_trovis_557x) Home Assistant Integration. However, as it is strictly kept independent of Home Assistant, it can also be used by other applications and projects.

## 👉 Purpose and scope of this library

`trovis-modbus` is intended for operational monitoring and occasional fine tuning of already commissioned heating systems. It does **not** attempt to reproduce every controller menu, parameter level, special function, register, or coil (there are thousands of them). It is also not intended as an initial commissioning tool for new heating systems - please use the free SAMSON TROVIS-VIEW software for this.

The library:

* provides the auto-discovery and auto-configuration logic required to determine the controller model and its capabilities, the configured hydronic system, sensor and input assignments, and the relevant controller functions, parameters, and configuration settings,

* contains the controller-specific data model, including valid registers and coils, their data types and metadata, and the rules required for safe reads and writes,

* determines which datapoints are valid for the actual installation based on the detected controller model, configured hydronic system, active functions and parameters, and physical sensor/input assignments,

* does **not** create or own the Modbus transport. Applications using the library provide a [`modbus_connection.ModbusUnit`](https://github.com/home-assistant-libs/modbus-connection) and may use any backend supported by `modbus-connection` (pymodbus, tmodbus, ...).

An example script `query.py` in the code of the library shows how to build an application that can query any TROVIS heating controller by using Modbus/TCP, Modbus/RTU with ser2net, or direct serial connection.

## Supported controllers

| Controller               | Rk1-Rk3 / Heating | Rk4 / DHW | Hydronic systems | Comments                             |
| :----------------------- | :---------------: | :-------: | :--------------: | :----------------------------------- |
| SAMSON TROVIS 5573       |         2         |     x     |        29        |                                      |
| SAMSON TROVIS 5573-1     |         2         |     x     |        29        |                                      |
| SAMSON TROVIS 5575       |         2         |     x     |        33        |                                      |
| SAMSON TROVIS 5576       |         2         |     x     |        52        |                                      |
| SAMSON TROVIS 5578       |         3         |     x     |        90        |                                      |
| SAMSON TROVIS 5578-E     |         3         |     x     |        95        |                                      |
| SAMSON TROVIS 5579       |         3         |     x     |        85        |                                      |
| SAUTER EQJW126F001       |         1         |           |         1        | TROVIS 5573, Rk1 and Anlage 1.0 only |
| SAUTER EQJW146F001       |         2         |     x     |        29        | TROVIS 5573                          |
| SAUTER EQJW146F002       |         2         |     x     |        29        | TROVIS 5573-1                        |
| SAUTER EQJW246F002       |         3         |     x     |        90        | TROVIS 5578                          |
| SAUTER EQJW246F003       |         3         |     x     |        95        | TROVIS 5578-E                        |
| YADOS YADO\|MATIC 01      |         2         |     x     |        33        | TROVIS 5575                          |
| YADOS YADO\|MATIC 01-0003 |         2         |     x     |        33        | TROVIS 5575                          |
| YADOS YADO\|MATIC 03      |         2         |     x     |        29        | TROVIS 5573                          |
| YADOS YADO\|MATIC 03-1003 |         2         |     x     |        29        | TROVIS 5573-1                        |
| YADOS YADO\|MATIC 08      |         3         |     x     |        90        | TROVIS 5578-1114                     |
| PEWO PCR06               |         2         |     x     |        33        | TROVIS 5575                          |

<sup>Note: Not all non-SAMSON models have yet been tested. The figures are based on the currently available documentation.</sup>

## 👉 Data provided by the library

Depending on the controller model and its configuration, `trovis-modbus` provides:

* controller identity, firmware, hardware, serial, and system information,

* the current hydronic system,

* measured temperatures and other physical sensor inputs (analog, pulse, etc.),

* controller-wide functions and parameters,

* technical control circuits Rk1 through Rk4 together with their hydronic roles and data
  (known roles: pre-control, heating, domestic hot water, buffer, solar),

* operating modes, setpoints, pump and valve states, and controller date/time,

* derived operating information, including automatically calculated heating curves for all applicable heating circuits and an overall controller status indicator,

* neutral datapoint metadata such as unit, scale, limits, step, enum options, invalid values, and writable state,

* grouped reads and validated writes, including TROVIS write-access handling and the mechanisms required to write-enable special/protected registers.

The exact datapoints exposed for a controller therefore reflect the capabilities and configuration of the actual installation rather than a static model-wide register list.

## Heat meters

The read-only `HeatMeter` component covers HR46500 through HR46522 for meter 1.
Indices 2 and 3 add 500 and 1000 to those references. Normal device updates
include all three model-readable slots:

```python
from trovis_modbus import HeatMeter, Trovis557x

device = Trovis557x(unit)
await device.async_update()
flow_rate = device.wmz1.flow_rate  # m³/h
energy = device.wmz1.energy  # kWh
source_energy_unit = device.wmz1.energy_unit
meters = device.heat_meters  # (wmz1, wmz2, wmz3)

meter = HeatMeter(unit, index=2)
await meter.async_update()
volume = meter.volume  # m³
power = meter.power  # kW
```

Each measurement reads its three numeric parts and source unit together in one
holding-register request. Values normalize to m³/h, m³, kW and kWh; the
`flow_rate_unit`, `volume_unit`, `power_unit` and `energy_unit` enums describe
the original device units. A single request prevents assembly from separate
reads, but does not guarantee a hardware-latched snapshot.

The component also provides numeric `status`, `error_status`, `manufacturer_id`,
combined `operating_time`, `volume_read_date` (`MonthDay`, without a year) and
`volume_read_time` (`datetime.time`). The register table does not specify the
operating-time unit, so none is assumed. Status bits are not interpreted.

Values are `None` before updating or when their fields are excluded or unavailable.
Invalid composite parts, unknown measurement unit selectors and invalid dates or
times also decode to `None`. Excluding any measurement word, including its unit,
disables the entire composite field. Readable slots are polled regardless of
whether a physical meter is connected; slot readability is not presence detection.
M-Bus configuration, read triggers and registers after HR46522 are not yet modeled.

## 👉 Testing and validation

All releases of the library are tested with the following hardware setup:

* 1× `TROVIS 5578`: dedicated testing device, Anlage 6.1 (fully equipped with 17 Pt1000 sensors),

* 1× `TROVIS 5579`: dedicated testing device, Anlage 5.1 (fully equipped with 17 Pt1000 sensors),

* 1× `TROVIS 5576`: live heating controller, Anlage 2.1 (4 required + 4 additional Pt1000 sensors).

Additional software-based tests are part of the project to ensure code quality and consistency.

## 👉 Manual installation

End users shouldn't have to install the library manually - it should normally be
downloaded and installed automatically from `pypi.org` by the application using it.

For developers, or if you still want to install it manually, use:

`pip install trovis-modbus`

## 👉 Documentation, development, contributions, issue and bug reporting

The central project documentation for the entire SAMSON TROVIS 557x project,
including usage and developer guidelines for all sub-projects, is maintained in
the [TROVIS project Wiki](https://github.com/Tom-Bom-badil/samson_trovis_557x/wiki).

That central repository can also be used for issue and bug reporting in the
[Discussions section](https://github.com/Tom-Bom-badil/samson_trovis_557x/discussions). Please use `Discussions` instead of opening an `Issue`. Thank you! :)
