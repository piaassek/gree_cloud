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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DISPATCH_DEVICE_DISCOVERED
from .coordinator import CloudDeviceDataUpdateCoordinator, GreeCloudConfigEntry, is_hwhp_device
from .entity import GreeCloudEntity

_LOGGER = logging.getLogger(__name__)

@dataclass(kw_only=True, frozen=True)
class GreeCloudSwitchEntityDescription(SwitchEntityDescription):
    get_value_fn: Callable[[Device], bool]
    set_value_fn: Callable[[Device, bool], None]

def _set_light(device: Device, value: bool) -> None:
    device.light = value

def _set_quiet(device: Device, value: bool) -> None:
    device.quiet = value

def _set_fresh_air(device: Device, value: bool) -> None:
    device.fresh_air = value

def _set_xfan(device: Device, value: bool) -> None:
    device.xfan = value

def _set_anion(device: Device, value: bool) -> None:
    device.anion = value

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
        key="Panel Light", translation_key="light",
        get_value_fn=lambda d: d.light, set_value_fn=_set_light,
    ),
    GreeCloudSwitchEntityDescription(
        key="Quiet", translation_key="quiet",
        get_value_fn=lambda d: d.quiet, set_value_fn=_set_quiet,
    ),
    GreeCloudSwitchEntityDescription(
        key="Fresh Air", translation_key="fresh_air",
        get_value_fn=lambda d: d.fresh_air, set_value_fn=_set_fresh_air,
    ),
    GreeCloudSwitchEntityDescription(
        key="XFan", translation_key="xfan",
        get_value_fn=lambda d: d.xfan, set_value_fn=_set_xfan,
    ),
    GreeCloudSwitchEntityDescription(
        key="Health mode", translation_key="health_mode",
        get_value_fn=lambda d: d.anion, set_value_fn=_set_anion,
        entity_registry_enabled_default=False,
    ),
    GreeCloudSwitchEntityDescription(
        key="UvcControl", name="Sterylizacja UVC", icon="mdi:lightbulb-germicidal",
        get_value_fn=_create_getter("UvcControl"), set_value_fn=_create_setter("UvcControl"),
    ),
    GreeCloudSwitchEntityDescription(
        key="AntiDirectBlow", name="Unikaj bezpośredniego nawiewu", icon="mdi:weather-windy-variant",
        get_value_fn=_create_getter("AntiDirectBlow"), set_value_fn=_create_setter("AntiDirectBlow"),
    ),
    GreeCloudSwitchEntityDescription(
        key="SvSt", name="Oszczędzanie Energii (SE)", icon="mdi:leaf",
        get_value_fn=_create_getter("SvSt"), set_value_fn=_create_setter("SvSt"),
    ),
    GreeCloudSwitchEntityDescription(
        key="AutoCleanSta", name="Auto-Czyszczenie", icon="mdi:spray-bottle",
        get_value_fn=_create_getter("AutoCleanSta"), set_value_fn=_create_setter("AutoCleanSta"),
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
            GreeCloudSwitch(coordinator=coordinator, description=description)
            for description in GREE_CLOUD_SWITCHES
        )

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(hass, DISPATCH_DEVICE_DISCOVERED, init_device)
    )


class GreeCloudSwitch(GreeCloudEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.SWITCH
    entity_description: GreeCloudSwitchEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device.device_info.mac}_{description.key}"

    @property
    def icon(self) -> str | None:
        if self.entity_description.key == "UvcControl" and not self.is_on:
            return "mdi:lightbulb-outline"
        if hasattr(self.entity_description, "icon") and self.entity_description.icon:
            return self.entity_description.icon
        return super().icon

    @property
    def is_on(self) -> bool:
        return self.entity_description.get_value_fn(self.coordinator.device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.entity_description.set_value_fn(self.coordinator.device, True)
        await self.coordinator.push_state_update()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.entity_description.set_value_fn(self.coordinator.device, False)
        await self.coordinator.push_state_update()
        self.async_write_ha_state()