from __future__ import annotations

from datetime import timedelta
import logging

from aiohttp import BasicAuth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_BASE_URL, CONF_SCAN_INTERVAL,
    CONF_AUTH_TYPE, CONF_USERNAME, CONF_PASSWORD, CONF_TOKEN,
    AUTH_NONE, AUTH_BASIC, AUTH_BEARER,
)

PLATFORMS = ["sensor"]
_LOGGER = logging.getLogger(__name__)


def _build_auth(entry: ConfigEntry):
    auth_type = entry.data.get(CONF_AUTH_TYPE, AUTH_NONE)

    headers = {"Accept": "application/json"}
    auth = None

    if auth_type == AUTH_BASIC:
        auth = BasicAuth(
            entry.data.get(CONF_USERNAME, ""),
            entry.data.get(CONF_PASSWORD, ""),
        )

    elif auth_type == AUTH_BEARER:
        token = entry.data.get(CONF_TOKEN, "")
        headers["Authorization"] = f"Bearer {token}"

    return headers, auth


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url: str = entry.data[CONF_BASE_URL].rstrip("/")
    scan_interval: int = entry.data[CONF_SCAN_INTERVAL]
    session = async_get_clientsession(hass)

    headers, auth = _build_auth(entry)

    async def async_update_datastreams():
        url = f"{base_url}/Datastreams?$top=200"
        try:
            resp = await session.get(url, headers=headers, auth=auth)
            resp.raise_for_status()
            data = await resp.json()
            return data.get("value", [])
        except Exception as e:
            raise UpdateFailed(f"Datastreams fetch failed: {e}") from e

    coordinator = DataUpdateCoordinator(
        hass,
        logger=_LOGGER,
        name=f"{DOMAIN}_datastreams",
        update_method=async_update_datastreams,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "base_url": base_url,
        "session": session,
        "coordinator": coordinator,
        "headers": headers,
        "auth": auth,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
