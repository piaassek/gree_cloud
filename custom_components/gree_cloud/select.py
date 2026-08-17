"""Support for Gree Cloud select entities (Swing and Display control)."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
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

# Protocol mapping: 0 = Off, 1 = Auto (full swing), 2-6 = 5 fixed positions
SWING_V_MAP: dict[str, str] = {
    "0": "off",
    "1": "auto",
    "2": "fixed_top",
    "3": "fixed_mid_top",
    "4": "fixed_mid",
    "5": "fixed_mid_bottom",
    "6": "fixed_bottom",
}

SWING_H_MAP: dict[str, str] = {
    "0": "off",
    "1": "auto",
    "2": "fixed_left",
    "3": "fixed_mid_left",
    "4": "fixed_mid",
    "5": "fixed_mid_right",
    "6": "fixed_right",
}

SWING_V_INV = {v: k for k, v in SWING_V_MAP.items()}
SWING_H_INV = {v: k for k, v in SWING_H_MAP.items()}


@dataclass(kw_only=True, frozen=True)
class GreeCloudSelectEntityDescription(SelectEntityDescription):
    """Class describing Gree Cloud select entities."""

    gree_key: str
    options_map: dict[str, str]
    options_inv: dict[str, str]


SELECT_TYPES: tuple[GreeCloudSelectEntityDescription, ...] = (
    GreeCloudSelectEntityDescription(
        key="swing_vertical",
        translation_key="swing_vertical",
        gree_key="SwUpDn",
        icon="mdi:arrow-up-down",
        options=list(SWING_V_MAP.values()),
        options_map=SWING_V_MAP,
        options_inv=SWING_V_INV,
    ),
    GreeCloudSelectEntityDescription(
        key="swing_horizontal",
        translation_key="swing_horizontal",
        gree_key="SwingLfRig",
        icon="mdi:arrow-left-right",
        options=list(SWING_H_MAP.values()),
        options_map=SWING_H_MAP,
        options_inv=SWING_H_INV,
    ),
)

DISPLAY_OPTIONS = ["off", "always_on", "auto"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreeCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Gree Cloud select entities."""

    @callback
    def init_device(coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        if is_hwhp_device(coordinator):
            return

        entities: list[SelectEntity] = [
            GreeCloudSelect(coordinator=coordinator, description=description)
            for description in SELECT_TYPES
        ]
        entities.append(GreeCloudDisplaySelect(coordinator=coordinator))

        async_add_entities(entities)

    for coordinator in entry.runtime_data.coordinators:
        init_device(coordinator)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, get_device_discovered_signal(entry.entry_id), init_device
        )
    )


class GreeCloudSelect(GreeCloudEntity, SelectEntity):
    """Representation of a Gree Cloud select entity."""

    entity_description: GreeCloudSelectEntityDescription

    def __init__(
        self,
        coordinator: CloudDeviceDataUpdateCoordinator,
        description: GreeCloudSelectEntityDescription,
    ) -> None:
        """Initialize the Gree Cloud select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device.device_info.mac}_{description.key}_v3"
        )

    @property
    def current_option(self) -> str | None:
        """Return current option from device memory."""
        raw_val = None
        if hasattr(self.coordinator.device, "raw_properties"):
            raw_val = self.coordinator.device.raw_properties.get(
                self.entity_description.gree_key
            )

        if raw_val is not None:
            str_val = str(raw_val)
            return self.entity_description.options_map.get(
                str_val, self.entity_description.options_map.get("1", "auto")
            )

        return self.entity_description.options_map.get("0", "off")

    async def async_select_option(self, option: str) -> None:
        """Send selected option to Gree Cloud."""
        raw_val = int(self.entity_description.options_inv[option])

        if hasattr(self.coordinator.device, "raw_properties"):
            self.coordinator.device.raw_properties[
                self.entity_description.gree_key
            ] = raw_val

            if (
                hasattr(self.coordinator.device, "_dirty")
                and self.entity_description.gree_key
                not in self.coordinator.device._dirty
            ):
                self.coordinator.device._dirty.append(
                    self.entity_description.gree_key
                )

        await self.coordinator.push_state_update()
        self.async_write_ha_state()


class GreeCloudDisplaySelect(GreeCloudEntity, SelectEntity):
    """Display backlight select entity with 3 states (Off, Always on, Auto)."""

    _attr_translation_key = "display"
    _attr_icon = "mdi:lightbulb-auto"
    _attr_options = DISPLAY_OPTIONS

    def __init__(self, coordinator: CloudDeviceDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.device.device_info.mac}_display_select_v3"
        )

    @property
    def current_option(self) -> str | None:
        """Read current display state from cloud properties."""
        raw = getattr(self.coordinator.device, "raw_properties", {})
        lig = raw.get("Lig", 0)
        ligsen = raw.get("LigSen", 0)

        if lig == 0:
            return "off"
        if lig == 1 and ligsen == 1:
            return "always_on"
        if lig == 1 and ligsen == 0:
            return "auto"

    @property
    def icon(self) -> str:
        """Return dynamic icon based on current display mode."""
        opt = self.current_option
        if opt == "always_on":
            return "mdi:lightbulb-on"
        if opt == "auto":
            return "mdi:lightbulb-auto"
        return "mdi:lightbulb-off-outline"

    async def async_select_option(self, option: str) -> None:
        """Send display backlight mode to device."""
        dev = self.coordinator.device
        if not hasattr(dev, "raw_properties"):
            return

        if option == "always_on":
            dev.raw_properties["Lig"] = 1
            dev.raw_properties["LigSen"] = 1
        elif option == "auto":
            dev.raw_properties["Lig"] = 1
            dev.raw_properties["LigSen"] = 0
        else:  # off
            dev.raw_properties["Lig"] = 0
            dev.raw_properties["LigSen"] = 0

        if hasattr(dev, "_dirty"):
            if "Lig" not in dev._dirty:
                dev._dirty.append("Lig")
            if "LigSen" not in dev._dirty:
                dev._dirty.append("LigSen")

        await self.coordinator.push_state_update()
        self.async_write_ha_state()