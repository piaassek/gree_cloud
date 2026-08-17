"""Support for Gree Cloud binary sensor entities."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
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
class GreeCloudBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Class describing Gree Cloud binary sensor entities."""


BINARY_SENSORS: tuple[GreeCloudBinarySensorEntityDescription, ...] = (
    GreeCloudBinarySensorEntityDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreeCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Gree Cloud binary sensor entities."""

    @callback
    def init_device(coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        if is_hwhp_device(coordinator):
            return
        async_add_entities(
            GreeCloudBinarySensor(
                coordinator=coordinator, description=description
            )
            for description in BINARY_SENSORS
        )

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, get_device_discovered_signal(entry.entry_id), init_device
        )
    )


class GreeCloudBinarySensor(GreeCloudEntity, BinarySensorEntity):
    """Representation of a Gree Cloud binary sensor."""

    entity_description: GreeCloudBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudBinarySensorEntityDescription,
    ) -> None:
        """Initialize the Gree Cloud binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device.device_info.mac}_{description.key}_v3"
        )

    @property
    def is_on(self) -> bool:
        """Return True if the binary sensor is on."""
        props = getattr(self.coordinator.device, "raw_properties", {})
        all_err = props.get("AllErr", 0)
        shutdown_fault = props.get("ShutdownFault", 0)
        jf_err = props.get("JFErrorCode", 0)

        return bool(all_err or shutdown_fault or jf_err)

    @property
    def icon(self) -> str:
        """Return dynamic icon based on fault status."""
        return "mdi:alert-circle" if self.is_on else "mdi:check-circle-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        props = getattr(self.coordinator.device, "raw_properties", {})
        attrs: dict[str, Any] = {}

        if (all_err := props.get("AllErr")) is not None:
            attrs["all_err"] = all_err
        if (shutdown_fault := props.get("ShutdownFault")) is not None:
            attrs["shutdown_fault"] = shutdown_fault
        if (jf_err := props.get("JFErrorCode")) is not None:
            attrs["jf_error_code"] = jf_err

        return attrs
