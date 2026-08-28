"""The Gree Climate Cloud integration."""

from __future__ import annotations

import logging
import ssl

from greeclimate.cloud_api import GreeCloudApi
from greeclimate.mqtt_client import GreeMqttClient

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_SERVER, DOMAIN, GREE_MQTT_SERVERS
from .coordinator import (
    CloudDiscoveryService,
    GreeCloudConfigEntry,
    GreeCloudRuntimeData,
    async_reconnect_mqtt,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: GreeCloudConfigEntry) -> bool:
    """Set up Gree Climate Cloud from a config entry."""
    _LOGGER.info("Setting up Gree Climate Cloud integration")

    try:
        # Pre-load default SSL context in executor to prevent event loop blocking warnings
        ssl_context = await hass.async_add_executor_job(ssl.create_default_context)

        # Create Cloud API client
        api = GreeCloudApi.for_server(
            entry.data[CONF_SERVER],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )

        # Login to cloud
        _LOGGER.debug("Logging in to Gree Cloud")
        credentials = await api.login()

        # Create MQTT client
        _LOGGER.debug("Connecting to Gree MQTT broker")
        mqtt_server = GREE_MQTT_SERVERS.get(entry.data[CONF_SERVER], "mqtt-eu.gree.com")
        if entry.data[CONF_SERVER] not in GREE_MQTT_SERVERS:
            _LOGGER.warning(
                "Unknown server region '%s', falling back to Europe MQTT server",
                entry.data[CONF_SERVER],
            )
        mqtt_client = GreeMqttClient(
            credentials.user_id, credentials.token, server=mqtt_server
        )

        orig_create_default_context = ssl.create_default_context
        ssl.create_default_context = lambda *args, **kwargs: ssl_context
        try:
            await mqtt_client.connect()
        finally:
            ssl.create_default_context = orig_create_default_context

        # Store runtime data
        entry.runtime_data = GreeCloudRuntimeData(
            cloud_api=api,
            mqtt_client=mqtt_client,
            coordinators=[],
        )

        # Discover and setup devices
        discovery = CloudDiscoveryService(hass, entry, api)
        coordinators = await discovery.discover_devices(mqtt_client)
        entry.runtime_data.coordinators = coordinators

        _LOGGER.info("Successfully discovered %d cloud devices", len(coordinators))

        # Setup platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        return True

    except Exception as err:
        _LOGGER.error("Failed to connect to Gree Climate Cloud: %s", err)
        raise ConfigEntryNotReady(f"Failed to connect to Gree Cloud: {err}") from err


async def async_unload_entry(hass: HomeAssistant, entry: GreeCloudConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gree Climate Cloud integration")

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Close all devices safely if supported
        for coordinator in entry.runtime_data.coordinators:
            device = coordinator.device
            if hasattr(device, "close") and callable(device.close):
                try:
                    await device.close()
                except Exception as err:
                    _LOGGER.warning("Error closing device: %s", err)

        # Disconnect MQTT client
        try:
            await entry.runtime_data.mqtt_client.disconnect()
        except Exception as err:
            _LOGGER.warning("Error disconnecting MQTT client: %s", err)

        # Close API session
        try:
            await entry.runtime_data.cloud_api.close()
        except Exception as err:
            _LOGGER.warning("Error closing API session: %s", err)

    return unload_ok