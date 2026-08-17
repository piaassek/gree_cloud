"""Base entity for Gree Cloud devices."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CloudDeviceDataUpdateCoordinator


class GreeCloudEntity(CoordinatorEntity[CloudDeviceDataUpdateCoordinator]):
    """Base class for Gree Cloud entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        device = coordinator.device
        props = getattr(device, "raw_properties", {})
        ver = props.get("ver")
        device_ver = getattr(device, "version", None)

        sw_version = None
        if ver and device_ver:
            sw_version = f"{ver} (v{device_ver})"
        elif ver:
            sw_version = str(ver)
        elif device_ver:
            sw_version = f"v{device_ver}"

        self._attr_device_info = HADeviceInfo(
            identifiers={(DOMAIN, device.device_info.mac)},
            name=device.device_info.name,
            manufacturer="Gree",
            model=device.hid or "Unknown Model",
            sw_version=sw_version,
        )
