"""Support for Gree Cloud select entities (Swing control)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from greeclimate.device import Device

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DISPATCH_DEVICE_DISCOVERED
from .coordinator import CloudDeviceDataUpdateCoordinator, GreeCloudConfigEntry, is_hwhp_device
from .entity import GreeCloudEntity

_LOGGER = logging.getLogger(__name__)

# Protokoły Gree dla żaluzji: 0 = Off, 1 = Auto, 2-6 = 5 stałych pozycji
SWING_V_MAP = {
    "0": "Wyłączony",
    "1": "Pełny zakres (Auto)",
    "2": "Stała: Góra",
    "3": "Stała: Środek-Góra",
    "4": "Stała: Środek",
    "5": "Stała: Środek-Dół",
    "6": "Stała: Dół",
}

SWING_H_MAP = {
    "0": "Wyłączony",
    "1": "Pełny zakres (Auto)",
    "2": "Stała: Lewo",
    "3": "Stała: Środek-Lewo",
    "4": "Stała: Środek",
    "5": "Stała: Środek-Prawo",
    "6": "Stała: Prawo",
}

SWING_V_INV = {v: k for k, v in SWING_V_MAP.items()}
SWING_H_INV = {v: k for k, v in SWING_H_MAP.items()}


@dataclass(kw_only=True, frozen=True)
class GreeCloudSelectEntityDescription(SelectEntityDescription):
    gree_key: str
    options_map: dict[str, str]
    options_inv: dict[str, str]


SELECT_TYPES: tuple[GreeCloudSelectEntityDescription, ...] = (
    GreeCloudSelectEntityDescription(
        key="swing_vertical",
        gree_key="SwUpDn",
        name="Żaluzja pionowa",
        icon="mdi:arrow-up-down",
        options=list(SWING_V_MAP.values()),
        options_map=SWING_V_MAP,
        options_inv=SWING_V_INV,
    ),
    GreeCloudSelectEntityDescription(
        key="swing_horizontal",
        gree_key="SwingLfRig",
        name="Żaluzja pozioma",
        icon="mdi:arrow-left-right",
        options=list(SWING_H_MAP.values()),
        options_map=SWING_H_MAP,
        options_inv=SWING_H_INV,
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
            GreeCloudSelect(coordinator=coordinator, description=description)
            for description in SELECT_TYPES
        )

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(hass, DISPATCH_DEVICE_DISCOVERED, init_device)
    )


class GreeCloudSelect(GreeCloudEntity, SelectEntity):
    entity_description: GreeCloudSelectEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSelectEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        dev_name = coordinator.device.device_info.name if hasattr(coordinator.device.device_info, "name") else "Gree"
        self._attr_name = f"{dev_name} {description.name}"
        self._attr_unique_id = f"{coordinator.device.device_info.mac}_{description.key}"

    @property
    def current_option(self) -> str | None:
        """Odczytuje aktualną pozycję z pamięci urządzenia."""
        raw_val = None
        if hasattr(self.coordinator.device, "raw_properties"):
            raw_val = self.coordinator.device.raw_properties.get(self.entity_description.gree_key)
        
        # Jeśli z chmury przyjdzie np. wartość 7+ (niestandardowe oscylacje), wracamy do domyślnego
        if raw_val is not None:
            str_val = str(raw_val)
            return self.entity_description.options_map.get(str_val, self.entity_description.options_map.get("1"))
            
        return self.entity_description.options_map.get("0")

    async def async_select_option(self, option: str) -> None:
        """Wysyła wybraną pozycję do chmury Gree."""
        raw_val = int(self.entity_description.options_inv[option])
        
        if hasattr(self.coordinator.device, "raw_properties"):
            self.coordinator.device.raw_properties[self.entity_description.gree_key] = raw_val
            
            # Dodajemy zmienną do kolejki publikacji MQTT
            if hasattr(self.coordinator.device, "_dirty") and self.entity_description.gree_key not in self.coordinator.device._dirty:
                self.coordinator.device._dirty.append(self.entity_description.gree_key)
                
        await self.coordinator.push_state_update()
        self.async_write_ha_state()