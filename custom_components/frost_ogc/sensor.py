from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


@dataclass(frozen=True)
class FrostDatastreamDescription(SensorEntityDescription):
    datastream_id: int = 0
    unit_symbol: str | None = None
    datastream_description: str | None = None

    thing_id: int | None = None
    thing_name: str | None = None
    thing_description: str | None = None
    thing_properties: dict[str, Any] | None = None


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
        if not isinstance(ds_id, int):
            continue

        ds_name = ds.get("name") or f"Datastream {ds_id}"
        ds_desc = ds.get("description")

        uom = (ds.get("unitOfMeasurement") or {}).get("symbol")

        # Thing kommt durch $expand=Thing direkt mit
        thing = ds.get("Thing") or {}
        thing_id = thing.get("@iot.id")
        if not isinstance(thing_id, int):
            thing_id = None

        thing_name = thing.get("name")
        thing_desc = thing.get("description")
        thing_props = thing.get("properties") or {}

        desc = FrostDatastreamDescription(
            key=str(ds_id),
            name=ds_name,
            datastream_id=ds_id,
            unit_symbol=uom,
            datastream_description=ds_desc,
            thing_id=thing_id,
            thing_name=thing_name,
            thing_description=thing_desc,
            thing_properties=thing_props,
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
    """Shows latest Observation.result for a given FROST Datastream and exposes Thing metadata."""

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

        # Device = Thing (damit in HA sauber gruppiert wird)
        if desc.thing_id is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"thing_{desc.thing_id}")},
                name=desc.thing_name or f"Thing {desc.thing_id}",
                manufacturer="FROST",
                model="OGC SensorThings Thing",
            )

        # Statische Metadaten direkt als Attribute setzen
        self._attr_extra_state_attributes = {
            "datastream_id": desc.datastream_id,
            "datastream_description": desc.datastream_description,
            "thing_id": desc.thing_id,
            "thing_name": desc.thing_name,
            "thing_description": desc.thing_description,
            "thing_properties": desc.thing_properties or {},
        }

        self._attr_native_value = None

    async def async_update(self) -> None:
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
            self._attr_extra_state_attributes["observation_note"] = "no observations"
            self._attr_extra_state_attributes.pop("phenomenonTime", None)
            self._attr_extra_state_attributes.pop("resultTime", None)
            self._attr_extra_state_attributes.pop("observation_id", None)
            return

        obs: dict[str, Any] = values[0]
        self._attr_native_value = obs.get("result")

        # Observation-Metadaten dynamisch ergänzen
        self._attr_extra_state_attributes.update(
            {
                "phenomenonTime": obs.get("phenomenonTime"),
                "resultTime": obs.get("resultTime"),
                "observation_id": obs.get("@iot.id"),
            }
        )
        self._attr_extra_state_attributes.pop("observation_note", None)
