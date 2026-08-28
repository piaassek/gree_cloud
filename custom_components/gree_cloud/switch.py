"""Support for Gree Cloud switch entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from greeclimate.device import Device

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
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
class GreeCloudSwitchEntityDescription(SwitchEntityDescription):
    """Class describing Gree Cloud switch entities."""

    get_value_fn: Callable[[Device], bool]
    set_value_fn: Callable[[Device, bool], None]


def _set_quiet(device: Device, value: bool) -> None:
    device.quiet = value


def _set_fresh_air(device: Device, value: bool) -> None:
    device.fresh_air = value


def _set_xfan(device: Device, value: bool) -> None:
    device.xfan = value


def _set_anion(device: Device, value: bool) -> None:
    device.anion = value


def _get_autoclean(device: Device) -> bool:
    if hasattr(device, "raw_properties"):
        raw = device.raw_properties
        if (
            raw.get("AutoClean")
            or raw.get("AutoCleanSta")
            or raw.get("AutoCleanStaEx")
            or raw.get("StCln")
            or raw.get("SelfClean")
            or raw.get("Clean")
        ):
            return True
        # Hardware CL cycle: AC is off (Pow: 0), but compressor inverter is running (CompressorFqy > 0)
        if raw.get("Pow") == 0 and raw.get("CompressorFqy", 0) > 0:
            return True
    return False


def _set_autoclean(device: Device, value: bool) -> None:
    val = 1 if value else 0
    if hasattr(device, "raw_properties"):
        # The AC hardware requires both StCln and SelfClean to trigger the CL cycle
        for k in ("StCln", "SelfClean"):
            device.raw_properties[k] = val
            if hasattr(device, "_dirty") and k not in device._dirty:
                device._dirty.append(k)


def _create_getter(key: str) -> Callable[[Device], bool]:
    def _get(device: Device) -> bool:
        if hasattr(device, "raw_properties"):
            return bool(device.raw_properties.get(key, 0))
        return False

    return _get


def _create_setter(key: str) -> Callable[[Device, bool], None]:
    def _set(device: Device, value: bool) -> None:
        val = 1 if value else 0
        if hasattr(device, "raw_properties"):
            device.raw_properties[key] = val
            if hasattr(device, "_dirty") and key not in device._dirty:
                device._dirty.append(key)

    return _set


GREE_CLOUD_SWITCHES: tuple[GreeCloudSwitchEntityDescription, ...] = (
    GreeCloudSwitchEntityDescription(
        key="Quiet",
        translation_key="quiet",
        icon="mdi:volume-off",
        get_value_fn=lambda d: d.quiet,
        set_value_fn=_set_quiet,
    ),
    GreeCloudSwitchEntityDescription(
        key="Fresh Air",
        translation_key="fresh_air",
        icon="mdi:air-filter",
        get_value_fn=lambda d: d.fresh_air,
        set_value_fn=_set_fresh_air,
    ),
    GreeCloudSwitchEntityDescription(
        key="XFan",
        translation_key="xfan",
        icon="mdi:fan",
        get_value_fn=lambda d: d.xfan,
        set_value_fn=_set_xfan,
    ),
    GreeCloudSwitchEntityDescription(
        key="Health mode",
        translation_key="health_mode",
        icon="mdi:pine-tree",
        get_value_fn=lambda d: d.anion,
        set_value_fn=_set_anion,
    ),
    GreeCloudSwitchEntityDescription(
        key="UvcControl",
        translation_key="uvc_control",
        icon="mdi:lightbulb-germicidal",
        get_value_fn=_create_getter("UvcControl"),
        set_value_fn=_create_setter("UvcControl"),
    ),
    GreeCloudSwitchEntityDescription(
        key="AntiDirectBlow",
        translation_key="anti_direct_blow",
        icon="mdi:weather-windy-variant",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_create_getter("AntiDirectBlow"),
        set_value_fn=_create_setter("AntiDirectBlow"),
    ),
    GreeCloudSwitchEntityDescription(
        key="SvSt",
        translation_key="energy_saving",
        icon="mdi:leaf",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_create_getter("SvSt"),
        set_value_fn=_create_setter("SvSt"),
    ),
    GreeCloudSwitchEntityDescription(
        key="AutoClean",
        translation_key="auto_clean",
        icon="mdi:spray-bottle",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_get_autoclean,
        set_value_fn=_set_autoclean,
    ),
    GreeCloudSwitchEntityDescription(
        key="ChildLock",
        translation_key="child_lock",
        icon="mdi:account-lock",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        get_value_fn=_create_getter("ChildLock"),
        set_value_fn=_create_setter("ChildLock"),
    ),
    GreeCloudSwitchEntityDescription(
        key="Dazzling",
        translation_key="dazzling",
        icon="mdi:creation",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        get_value_fn=_create_getter("Dazzling"),
        set_value_fn=_create_setter("Dazzling"),
    ),
    GreeCloudSwitchEntityDescription(
        key="BuzzerCtrl",
        translation_key="buzzer",
        icon="mdi:volume-high",
        entity_category=EntityCategory.CONFIG,
        get_value_fn=_create_getter("BuzzerCtrl"),
        set_value_fn=_create_setter("BuzzerCtrl"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreeCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Gree Cloud switch entities."""

    @callback
    def init_device(coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        if is_hwhp_device(coordinator):
            return
        async_add_entities(
            GreeCloudSwitch(coordinator=coordinator, description=description)
            for description in GREE_CLOUD_SWITCHES
        )

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, get_device_discovered_signal(entry.entry_id), init_device
        )
    )


class GreeCloudSwitch(GreeCloudEntity, SwitchEntity):
    """Representation of a Gree Cloud switch entity."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_has_entity_name = True

    entity_description: GreeCloudSwitchEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSwitchEntityDescription,
    ) -> None:
        """Initialize the Gree Cloud switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device.device_info.mac}_{description.key}_v3"
        )

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self.entity_description.get_value_fn(self.coordinator.device)

    @property
    def icon(self) -> str | None:
        """Return dynamic icon if defined or fallback to description."""
        if self.entity_description.key == "BuzzerCtrl":
            return "mdi:volume-high" if self.is_on else "mdi:volume-mute"
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for switches."""
        if self.entity_description.key == "AutoClean":
            return getattr(self.coordinator.device, "raw_properties", {})
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        self.entity_description.set_value_fn(self.coordinator.device, True)
        await self.coordinator.push_state_update()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        self.entity_description.set_value_fn(self.coordinator.device, False)
        await self.coordinator.push_state_update()
        self.async_write_ha_state()