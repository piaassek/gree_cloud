"""Support for Gree Cloud sensor entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from greeclimate.device import Device

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfTemperature,
)

try:
    from homeassistant.const import UnitOfDensity

    PM25_UNIT = UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
except (ImportError, AttributeError):
    try:
        from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER

        PM25_UNIT = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
    except ImportError:
        PM25_UNIT = "µg/m³"

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import get_device_discovered_signal
from .coordinator import (
    CloudDeviceDataUpdateCoordinator,
    GreeCloudConfigEntry,
    is_hwhp_device,
)
from .entity import GreeCloudEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True, frozen=True)
class GreeCloudSensorEntityDescription(SensorEntityDescription):
    """Class describing Gree Cloud sensor entities."""

    get_value_fn: Callable[[Device], Any]


def _get_raw(key: str) -> Callable[[Device], Any]:
    def _get(device: Device) -> Any:
        if hasattr(device, "raw_properties"):
            return device.raw_properties.get(key)
        return None

    return _get


def _get_temp(key: str, fallback_key: str | None = None) -> Callable[[Device], float | None]:
    def _get(device: Device) -> float | None:
        if hasattr(device, "raw_properties"):
            val = device.raw_properties.get(key)
            if (val is None or val == 0) and fallback_key:
                val = device.raw_properties.get(fallback_key)
            if val is not None and val != 0:
                try:
                    return float(val) - 40.0
                except (ValueError, TypeError):
                    pass
        return None

    return _get


def _get_energy() -> Callable[[Device], float | None]:
    def _get(device: Device) -> float | None:
        if hasattr(device, "raw_properties"):
            val = device.raw_properties.get("ElcAllConsumption")
            if val is None:
                val = device.raw_properties.get("ElcAll")
            if val is not None:
                try:
                    return round(float(val) / 10.0, 2)
                except (ValueError, TypeError):
                    pass
        return None

    return _get


def _get_pm25() -> Callable[[Device], int | None]:
    def _get(device: Device) -> int | None:
        if hasattr(device, "raw_properties"):
            val = device.raw_properties.get("PM2P5")
            if val is not None and val > 0:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    pass
        return None

    return _get


SENSORS: tuple[GreeCloudSensorEntityDescription, ...] = (
    GreeCloudSensorEntityDescription(
        key="TemSen",
        translation_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        get_value_fn=_get_temp("TemSen", "EnvTem"),
    ),
    GreeCloudSensorEntityDescription(
        key="OutEnvTem",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        get_value_fn=_get_temp("OutEnvTem", "TemsSenOut"),
    ),
    GreeCloudSensorEntityDescription(
        key="CompressorTem",
        translation_key="compressor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        get_value_fn=_get_temp("CompressorTem"),
    ),
    GreeCloudSensorEntityDescription(
        key="CompressorFqy",
        translation_key="compressor_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:engine-outline",
        get_value_fn=_get_raw("CompressorFqy"),
    ),
    GreeCloudSensorEntityDescription(
        key="ElcAllConsumption",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:lightning-bolt",
        get_value_fn=_get_energy(),
    ),
    GreeCloudSensorEntityDescription(
        key="InEvaTem",
        translation_key="evaporator_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        get_value_fn=_get_temp("InEvaTem"),
    ),
    GreeCloudSensorEntityDescription(
        key="PM2P5",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PM25_UNIT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        get_value_fn=_get_pm25(),
    ),
    GreeCloudSensorEntityDescription(
        key="wifiStatus",
        translation_key="wifi_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:wifi-check",
        get_value_fn=_get_raw("wifiStatus"),
    ),
    GreeCloudSensorEntityDescription(
        key="all_parameters",
        translation_key="all_parameters",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:code-json",
        get_value_fn=lambda d: f"{len(getattr(d, 'raw_properties', {}))} parametrów",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreeCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Gree Cloud sensor entities."""

    @callback
    def init_device(coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        if is_hwhp_device(coordinator):
            return
        async_add_entities(
            GreeCloudSensor(coordinator=coordinator, description=description)
            for description in SENSORS
        )

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, get_device_discovered_signal(entry.entry_id), init_device
        )
    )


class GreeCloudSensor(GreeCloudEntity, SensorEntity):
    """Representation of a Gree Cloud sensor."""

    entity_description: GreeCloudSensorEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSensorEntityDescription,
    ) -> None:
        """Initialize the Gree Cloud sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device.device_info.mac}_{description.key}"
        )

    @property
    def native_value(self) -> Any:
        """Return native value of the sensor."""
        return self.entity_description.get_value_fn(self.coordinator.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for sensor."""
        if self.entity_description.key == "all_parameters":
            return getattr(self.coordinator.device, "raw_properties", {})
        return {}