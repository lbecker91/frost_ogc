from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


@dataclass(frozen=True)
class FrostDatastreamDescription(SensorEntityDescription):
    datastream_id: int = 0
    unit_symbol: str | None = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    base_url: str = data["base_url"]
    session = data["session"]
    headers = data["headers"]
    auth = data["auth"]

    entities: list[SensorEntity] = []
    for ds in coordinator.data:
        ds_id = ds.get("@iot.id")
        if ds_id is None:
            continue

        name = ds.get("name") or f"Datastream {ds_id}"
        uom = (ds.get("unitOfMeasurement") or {}).get("symbol")

        desc = FrostDatastreamDescription(
            key=str(ds_id),
            name=name,
            datastream_id=int(ds_id),
            unit_symbol=uom,
        )
        entities.append(
            FrostLatestObservationSensor(
                base_url=base_url,
                session=session,
                headers=headers,
                auth=auth,
                entry_id=entry.entry_id,
                desc=desc,
            )
        )

    async_add_entities(entities)


class FrostLatestObservationSensor(SensorEntity):
    """Sensor that shows the latest Observation.result for a given FROST Datastream."""

    _attr_has_entity_name = True

    def __init__(
        self,
        base_url: str,
        session,
        headers: dict[str, str],
        auth,
        entry_id: str,
        desc: FrostDatastreamDescription,
    ) -> None:
        self.entity_description = desc
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._headers = headers
        self._auth = auth

        self._attr_unique_id = f"{entry_id}_datastream_{desc.datastream_id}"
        self._attr_native_unit_of_measurement = desc.unit_symbol

        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_update(self) -> None:
        # Latest Observation for this datastream
        url = (
            f"{self._base_url}/Datastreams({self.entity_description.datastream_id})"
            f"/Observations?$top=1&$orderby=phenomenonTime%20desc"
        )

        resp = await self._session.get(url, headers=self._headers, auth=self._auth)
        resp.raise_for_status()
        data = await resp.json()

        values = data.get("value", [])
        if not values:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {"note": "no observations"}
            return

        obs: dict[str, Any] = values[0]

        # result can be numeric, string, or complex JSON depending on observationType
        self._attr_native_value = obs.get("result")

        self._attr_extra_state_attributes = {
            "phenomenonTime": obs.get("phenomenonTime"),
            "resultTime": obs.get("resultTime"),
            "observation_id": obs.get("@iot.id"),
        }
