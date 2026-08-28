"""Helper and wrapper classes for Gree Cloud module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import ssl
from typing import Any

from greeclimate.cloud_api import GreeCloudApi
from greeclimate.cloud_device import CloudDevice
from greeclimate.device import Props
from greeclimate.deviceinfo import DeviceInfo
from greeclimate.mqtt_client import GreeMqttClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SERVER,
    DOMAIN,
    GREE_MQTT_SERVERS,
    HWHP_PROP_POW_CONSUMP,
    HWHP_PROP_SET_TEM_DEC,
    HWHP_PROP_SET_TEM_INT,
    HWHP_PROP_WATER_PERCENT,
    HWHP_PROP_WATER_TEMP,
    HWHP_PROP_WMOD,
    HWHP_PROP_WSTATE,
    MAX_ERRORS,
    PROP_ENERGY_TOTAL,
    UPDATE_INTERVAL,
    get_device_discovered_signal,
)

_LOGGER = logging.getLogger(__name__)

# Extra raw properties requested from the device in addition to the standard Props enum.
# These cover Hot Water Heat Pump (HWHP) devices and extra AC cloud sensors.
_STANDARD_PROPS: list[str] = [x.value for x in Props]
_HWHP_EXTRA_PROPS = [
    HWHP_PROP_WATER_TEMP,
    HWHP_PROP_SET_TEM_INT,
    HWHP_PROP_SET_TEM_DEC,
    HWHP_PROP_WSTATE,
    HWHP_PROP_POW_CONSUMP,
    HWHP_PROP_WMOD,
    HWHP_PROP_WATER_PERCENT,
    # AC additional cloud properties:
    "OutEnvTem",
    "EnvTem",
    "CompressorTem",
    "InEvaTem",
    "CompressorFqy",
    "ElcAllConsumption",
    "PM2P5",
    "UvcControl",
    "SwUpDn",
    "SwingLfRig",
    "ChildLock",
    "AntiDirectBlow",
    "SvSt",
    "AutoClean",
    "AutoCleanSta",
    "AutoCleanStaEx",
    "StCln",
    "SelfClean",
    "Clean",
    "FilClr",
    "Dazzling",
    "BuzzerCtrl",
    "AllErr",
    "JFErrorCode",
    "ShutdownFault",
    "wifiStatus",
    "TemsSenOut",
    "LigSen",
    "Add0.5",
    "Dfltr",
    "ReplaceHEPA",
    "ElcEn",
    "CoolFeel",
    "NobodySave",
]

# Extra properties reported by AC units.
_ENERGY_EXTRA_PROPS = [PROP_ENERGY_TOTAL]


class HWHPAwareCloudDevice(CloudDevice):
    """CloudDevice subclass that also requests HWHP-specific properties.

    The Gree WHIO Hot Water Heat Pump reports current water temperature under
    the ``WatTmp`` property key which is not part of the standard ``Props``
    enum. This subclass overrides ``update_state`` to include HWHP and extra
    sensors in the status request, supports arbitrary custom property keys in
    ``push_state_update``, and implements anti-bounce protection.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize HWHP aware cloud device."""
        super().__init__(*args, **kwargs)
        self.state_update_callback: Callable[[], None] | None = None
        self._pending_updates: dict[str, Any] = {}
        self._last_push_time: float = 0.0

    async def push_state_update(self) -> None:
        """Send pending property changes to the cloud device."""
        if not self._dirty:
            return

        opt: list[str] = []
        p: list[Any] = []

        for prop in list(self._dirty):
            if isinstance(prop, Props):
                opt.append(prop.value)
                val = self._props.get(prop, 0)
                p.append(val)
                self._pending_updates[prop.value] = val
            elif isinstance(prop, str):
                opt.append(prop)
                val = self.raw_properties.get(prop, 0)
                p.append(val)
                self._pending_updates[prop] = val

        if not opt:
            self._dirty.clear()
            return

        command = {
            "t": "cmd",
            "opt": opt,
            "p": p,
        }

        self._last_push_time = asyncio.get_running_loop().time()

        await self._mqtt_client.publish_command(
            self._parent_mac,
            command,
            self.device_cipher,
            self._child_mac,
        )
        self._dirty.clear()

    def handle_state_update(self, **kwargs: Any) -> None:
        """Handle incoming state update from MQTT and notify coordinator immediately."""
        now = asyncio.get_running_loop().time()

        # Anti-bounce protection: for 2.5s after user pushes a command,
        # don't allow stale in-flight MQTT packets to revert user's pending changes.
        if self._pending_updates and (now - self._last_push_time < 2.5):
            cols = kwargs.get("cols", [])
            dat = kwargs.get("dat", [])
            if cols and dat and len(cols) == len(dat):
                data_dict = dict(zip(cols, dat))
                all_matched = True
                for k, v in list(self._pending_updates.items()):
                    if k in data_dict:
                        if data_dict[k] == v:
                            self._pending_updates.pop(k, None)
                        else:
                            all_matched = False
                            idx = cols.index(k)
                            dat[idx] = v
                if all_matched:
                    self._pending_updates.clear()
        else:
            self._pending_updates.clear()

        super().handle_state_update(**kwargs)
        if self.state_update_callback is not None:
            self.state_update_callback()

    async def update_state(self) -> None:
        """Update device state, including HWHP-specific properties."""
        _LOGGER.debug(
            "Updating HWHP-aware cloud device state: %s", self.device_info.name
        )

        props: list[str] = list(
            dict.fromkeys(_STANDARD_PROPS + _HWHP_EXTRA_PROPS + _ENERGY_EXTRA_PROPS)
        )
        if not self.hid:
            props.append("hid")

        self._response_event = asyncio.Event()
        self._response_data = None

        command = {"t": "status", "cols": props}

        await self._mqtt_client.publish_command(
            self._parent_mac,
            command,
            self.device_cipher,
            self._child_mac,
        )

        try:
            await asyncio.wait_for(
                self._response_event.wait(), timeout=self._command_timeout
            )
            if self._response_data:
                self.handle_state_update(**self._response_data)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timeout waiting for state update from %s", self.device_info.name
            )
        finally:
            self._response_event = None
            self._response_data = None


def is_hwhp_device(coordinator: "CloudDeviceDataUpdateCoordinator") -> bool:
    """Return True if the device appears to be a Hot Water Heat Pump.

    Detection requires a positive WatTmp raw value (actual = raw - 100).
    Standard AC units return 0 for unknown properties; a real HWHP reports
    actual water temperature (40–80 °C → raw 140–180), so raw > 0 is the
    discriminator.
    """
    raw = coordinator.device.raw_properties.get(HWHP_PROP_WATER_TEMP)
    return raw is not None and raw > 0


def _is_mqtt_disconnected(error: Exception) -> bool:
    """Return True if *error* indicates the MQTT client is not connected."""
    msg = str(error).lower()
    return any(m in msg for m in ("code:4", "not currently connected", "not connected"))


type GreeCloudConfigEntry = ConfigEntry[GreeCloudRuntimeData]


@dataclass
class GreeCloudRuntimeData:
    """Runtime data for Gree Climate Cloud integration."""

    cloud_api: GreeCloudApi
    mqtt_client: GreeMqttClient
    coordinators: list[CloudDeviceDataUpdateCoordinator]
    mqtt_reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def async_reconnect_mqtt(
    hass: HomeAssistant, entry: GreeCloudConfigEntry
) -> bool:
    """Re-establish the MQTT connection after a broker disconnect.

    Returns True if the reconnect succeeded, False otherwise.
    Acquires the per-entry lock so that concurrent poll cycles don't each
    try to reconnect simultaneously.
    """
    runtime = entry.runtime_data
    lock = runtime.mqtt_reconnect_lock

    if lock.locked():
        # Another coroutine is already reconnecting — wait for it to finish.
        async with lock:
            pass
        return runtime.mqtt_client.is_connected

    async with lock:
        _LOGGER.warning("MQTT disconnected — attempting to reconnect")

        old_client = runtime.mqtt_client
        mqtt_server = GREE_MQTT_SERVERS.get(entry.data[CONF_SERVER], "mqtt-eu.gree.com")

        try:
            # Re-login to get a fresh token (tokens can expire).
            credentials = await runtime.cloud_api.login()

            new_client = GreeMqttClient(
                credentials.user_id,
                credentials.token,
                server=mqtt_server,
            )
            ssl_context = await hass.async_add_executor_job(ssl.create_default_context)
            orig_create_default_context = ssl.create_default_context
            ssl.create_default_context = lambda *args, **kwargs: ssl_context
            try:
                await new_client.connect()
            finally:
                ssl.create_default_context = orig_create_default_context
        except Exception as err:
            _LOGGER.error("MQTT reconnect failed during connect: %s", err)
            return False

        # Swap the client reference on every device and re-subscribe.
        for coordinator in runtime.coordinators:
            device = coordinator.device
            try:
                old_client.remove_message_handler(device._handle_mqtt_message)
            except Exception:
                pass
            device._mqtt_client = new_client
            try:
                # bind() re-subscribes to response/status/connect topics.
                await device.bind()
            except Exception as err:
                _LOGGER.warning(
                    "Failed to re-bind device %s after reconnect: %s",
                    device.device_info.name,
                    err,
                )

        runtime.mqtt_client = new_client

        # Best-effort cleanup of the old client.
        try:
            await old_client.disconnect()
        except Exception:
            pass

        _LOGGER.info("MQTT reconnect successful")
        return True


class CloudDeviceDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages polling for state changes from cloud devices."""

    config_entry: GreeCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GreeCloudConfigEntry,
        device: CloudDevice,
    ) -> None:
        """Initialize the cloud data update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}-{device.device_info.name}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            always_update=False,
        )
        self.device = device
        self._error_count: int = 0
        if isinstance(device, HWHPAwareCloudDevice):
            device.state_update_callback = self._on_device_push_update

    @callback
    def _on_device_push_update(self) -> None:
        """Handle real-time MQTT push state update."""
        _LOGGER.debug("Real-time MQTT state update received for %s", self.name)
        self._error_count = 0
        self.async_set_updated_data(copy.deepcopy(self.device.raw_properties))

    async def _async_update_data(self) -> dict[str, Any]:
        """Update the state of the device from cloud."""
        _LOGGER.debug(
            "Updating cloud device state: %s, error count: %d",
            self.name,
            self._error_count,
        )
        try:
            await self.device.update_state()
            self._error_count = 0
            return copy.deepcopy(self.device.raw_properties)

        except asyncio.TimeoutError as error:
            self._error_count += 1
            if self._error_count >= MAX_ERRORS:
                _LOGGER.warning(
                    "Cloud device %s is unavailable after %d timeouts",
                    self.name,
                    self._error_count,
                )
                raise UpdateFailed(
                    f"Cloud device {self.name} is unavailable, timeout"
                ) from error
            _LOGGER.debug(
                "Timeout updating cloud device %s (attempt %d/%d)",
                self.name,
                self._error_count,
                MAX_ERRORS,
            )
            return copy.deepcopy(self.device.raw_properties)

        except Exception as error:
            if _is_mqtt_disconnected(error):
                _LOGGER.warning(
                    "MQTT disconnected while updating %s — triggering reconnect",
                    self.name,
                )
                reconnected = await async_reconnect_mqtt(self.hass, self.config_entry)
                if reconnected:
                    try:
                        await self.device.update_state()
                        self._error_count = 0
                        return copy.deepcopy(self.device.raw_properties)
                    except Exception as retry_error:
                        _LOGGER.warning(
                            "State update failed after reconnect for %s: %s",
                            self.name,
                            retry_error,
                        )

            self._error_count += 1
            if self._error_count >= MAX_ERRORS:
                _LOGGER.error("Cloud device %s failed to update: %s", self.name, error)
                raise UpdateFailed(
                    f"Cloud device {self.name} failed to update"
                ) from error
            _LOGGER.warning(
                "Error updating cloud device %s (attempt %d/%d): %s",
                self.name,
                self._error_count,
                MAX_ERRORS,
                error,
            )
            return copy.deepcopy(self.device.raw_properties)

    async def push_state_update(self) -> Any:
        """Send state updates to the cloud device."""
        try:
            return await self.device.push_state_update()
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timeout sending state update to cloud device: %s", self.name
            )
        except Exception as error:
            if _is_mqtt_disconnected(error):
                _LOGGER.warning(
                    "MQTT disconnected while pushing state to %s — triggering reconnect",
                    self.name,
                )
                reconnected = await async_reconnect_mqtt(self.hass, self.config_entry)
                if reconnected:
                    try:
                        return await self.device.push_state_update()
                    except Exception as retry_error:
                        _LOGGER.warning(
                            "Push state failed after reconnect for %s: %s",
                            self.name,
                            retry_error,
                        )
                        return None
            _LOGGER.error(
                "Error sending state update to cloud device %s: %s", self.name, error
            )
            return None


class CloudDiscoveryService:
    """Cloud discovery service for Gree devices."""

    def __init__(
        self, hass: HomeAssistant, entry: GreeCloudConfigEntry, api: GreeCloudApi
    ) -> None:
        """Initialize cloud discovery service."""
        self.hass = hass
        self.entry = entry
        self.api = api

    async def discover_devices(
        self, mqtt_client: GreeMqttClient
    ) -> list[CloudDeviceDataUpdateCoordinator]:
        """Discover all cloud devices."""
        coordinators = []

        try:
            # Get all devices from cloud
            _LOGGER.debug("Fetching devices from Gree Cloud")
            cloud_devices = await self.api.get_all_devices()

            _LOGGER.info("Found %d cloud devices", len(cloud_devices))

            # Create coordinator for each device
            for cloud_dev_info in cloud_devices:
                try:
                    # Create DeviceInfo for CloudDevice
                    device_info = DeviceInfo(
                        ip="0.0.0.0",  # Not used for cloud devices
                        port=0,  # Not used for cloud devices
                        mac=cloud_dev_info.mac,
                        name=cloud_dev_info.name,
                    )

                    # Create cloud device instance
                    device = HWHPAwareCloudDevice(
                        mqtt_client=mqtt_client,
                        device_info=device_info,
                        device_key=cloud_dev_info.key,
                        cipher_version=1,
                    )

                    # Bind to cloud device (subscribe to MQTT topics)
                    await device.bind()

                    _LOGGER.debug(
                        "Bound to cloud device: %s (MAC: %s)",
                        device.device_info.name,
                        device.device_info.mac,
                    )

                    # Create coordinator
                    coordinator = CloudDeviceDataUpdateCoordinator(
                        self.hass, self.entry, device
                    )
                    coordinators.append(coordinator)

                    # Initial refresh
                    await coordinator.async_config_entry_first_refresh()

                    # Notify about discovered device
                    async_dispatcher_send(
                        self.hass,
                        get_device_discovered_signal(self.entry.entry_id),
                        coordinator,
                    )

                except Exception as err:
                    _LOGGER.exception(
                        "Failed to setup cloud device %s: %s",
                        cloud_dev_info.name,
                        err,
                    )

        except Exception as err:
            _LOGGER.exception("Failed to discover cloud devices: %s", err)

        return coordinators