from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    CONF_AUTH_TYPE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    AUTH_NONE,
    AUTH_BASIC,
    AUTH_BEARER,
)


def _normalize_base_url(url: str) -> str:
    return url.strip().rstrip("/")


class FrostOgcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_BASE_URL,
                        default="https://frost.feuerwehren-lkwnd.de/FROST-Server/v1.1",
                    ): str,
                    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
                    vol.Required(CONF_AUTH_TYPE, default=AUTH_BASIC): vol.In(
                        [AUTH_NONE, AUTH_BASIC, AUTH_BEARER]
                    ),
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        self._data = {
            CONF_BASE_URL: _normalize_base_url(user_input[CONF_BASE_URL]),
            CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
            CONF_AUTH_TYPE: user_input[CONF_AUTH_TYPE],
        }

        await self.async_set_unique_id(self._data[CONF_BASE_URL])
        self._abort_if_unique_id_configured()

        if self._data[CONF_AUTH_TYPE] == AUTH_BASIC:
            return await self.async_step_basic()

        if self._data[CONF_AUTH_TYPE] == AUTH_BEARER:
            return await self.async_step_bearer()

        return self.async_create_entry(
            title=f"FROST ({self._data[CONF_BASE_URL]})",
            data=self._data,
        )

    async def async_step_basic(self, user_input=None) -> FlowResult:
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            )
            return self.async_show_form(step_id="basic", data_schema=schema)

        self._data.update(
            {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
        )
        return self.async_create_entry(
            title=f"FROST ({self._data[CONF_BASE_URL]})",
            data=self._data,
        )

    async def async_step_bearer(self, user_input=None) -> FlowResult:
        if user_input is None:
            schema = vol.Schema({vol.Required(CONF_TOKEN): str})
            return self.async_show_form(step_id="bearer", data_schema=schema)

        self._data.update({CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_create_entry(
            title=f"FROST ({self._data[CONF_BASE_URL]})",
            data=self._data,
        )
