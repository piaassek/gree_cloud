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
    UnitOfTemperature,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DISPATCH_DEVICE_DISCOVERED
from .coordinator import CloudDeviceDataUpdateCoordinator, GreeCloudConfigEntry, is_hwhp_device
from .entity import GreeCloudEntity

_LOGGER = logging.getLogger(__name__)

@dataclass(kw_only=True, frozen=True)
class GreeCloudSensorEntityDescription(SensorEntityDescription):
    get_value_fn: Callable[[Device], Any]

def _get_raw(key: str) -> Callable[[Device], Any]:
    def _get(device: Device) -> Any:
        if hasattr(device, "raw_properties"):
            return device.raw_properties.get(key)
        return None
    return _get

def _get_temp(key: str) -> Callable[[Device], float | None]:
    def _get(device: Device) -> float | None:
        if hasattr(device, "raw_properties"):
            val = device.raw_properties.get(key)
            if val is not None:
                try:
                    return float(val) - 40.0
                except ValueError:
                    pass
        return None
    return _get

def _get_energy(key: str) -> Callable[[Device], float | None]:
    def _get(device: Device) -> float | None:
        if hasattr(device, "raw_properties"):
            val = device.raw_properties.get(key)
            if val is not None:
                try:
                    return float(val) / 10.0
                except ValueError:
                    pass
        return None
    return _get

SENSORS: tuple[GreeCloudSensorEntityDescription, ...] = (
    GreeCloudSensorEntityDescription(
        key="TemSen", 
        name="Temperatura wewnętrzna", 
        device_class=SensorDeviceClass.TEMPERATURE, 
        state_class=SensorStateClass.MEASUREMENT, 
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, 
        get_value_fn=_get_temp("TemSen")
    ),
    GreeCloudSensorEntityDescription(
        key="OutEnvTem",
        name="Temperatura zewnętrzna",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        get_value_fn=_get_temp("OutEnvTem"),
    ),
    GreeCloudSensorEntityDescription(
        key="CompressorTem",
        name="Temperatura kompresora",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        get_value_fn=_get_temp("CompressorTem"),
    ),
    GreeCloudSensorEntityDescription(
        key="CompressorFqy",
        name="Częstotliwość kompresora",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        icon="mdi:engine-outline",
        get_value_fn=_get_raw("CompressorFqy"),
    ),
    GreeCloudSensorEntityDescription(
        key="ElcAllConsumption",
        name="Całkowite zużycie energii",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:lightning-bolt",
        get_value_fn=_get_energy("ElcAllConsumption"),
    ),
    GreeCloudSensorEntityDescription(
        key="InEvaTem",
        name="Temperatura parownika",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        get_value_fn=_get_temp("InEvaTem"),
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreeCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
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
        async_dispatcher_connect(hass, DISPATCH_DEVICE_DISCOVERED, init_device)
    )

class GreeCloudSensor(GreeCloudEntity, SensorEntity):
    entity_description: GreeCloudSensorEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device.device_info.mac}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.get_value_fn(self.coordinator.device)