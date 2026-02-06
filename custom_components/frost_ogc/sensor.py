from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


def _safe_float(v, default=None):
    try:
        if v is None:
            return default
        # Strings wie "140" oder "347.9 m. üNN" -> nur Zahl ziehen, falls nötig
        if isinstance(v, str):
            # schnelle Extraktion der ersten Zahl
            import re
            m = re.search(r"[-+]?\d+(\.\d+)?", v.replace(",", "."))
            if not m:
                return default
            return float(m.group(0))
        return float(v)
    except Exception:
        return default


def _get_meldehoehen(props: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    mh = (props or {}).get("Meldehöhen") or {}
    mh1 = _safe_float(mh.get("MH1"))
    mh2 = _safe_float(mh.get("MH2"))
    mh3 = _safe_float(mh.get("MH3"))
    return mh1, mh2, mh3


def _is_pegel(props: dict[str, Any]) -> bool:
    if not props:
        return False
    if str(props.get("type", "")).lower() == "pegel":
        return True
    # fallback über keywords
    kws = props.get("keywords") or []
    kws_l = [str(x).lower() for x in kws]
    return "pegel" in kws_l or "wasserstandsmessung" in kws_l


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

        value_sensor = FrostLatestObservationSensor(
            base_url=base_url,
            session=session,
            headers=headers,
            auth=auth,
            entry_id=entry.entry_id,
            desc=desc,
        )
        entities.append(value_sensor)

        # Zusätzlich: Stufe-Sensor nur für Pegel
        if _is_pegel(thing_props):
            entities.append(
                FrostMeldehoeheLevelSensor(
                    value_sensor=value_sensor,
                    entry_id=entry.entry_id,
                    desc=desc,
                )
            )

    async_add_entities(entities)


class FrostBaseSensor(SensorEntity):
    _attr_has_entity_name = True

    def _set_device_info_from_desc(self, desc: FrostDatastreamDescription):
        if desc.thing_id is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"thing_{desc.thing_id}")},
                name=desc.thing_name or f"Thing {desc.thing_id}",
                manufacturer="FROST",
                model="OGC SensorThings Thing",
            )


class FrostLatestObservationSensor(FrostBaseSensor):
    """Shows latest Observation.result for a given FROST Datastream and exposes Thing metadata."""

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

        self._set_device_info_from_desc(desc)

        mh1, mh2, mh3 = _get_meldehoehen(desc.thing_properties or {})

        self._attr_extra_state_attributes = {
            "datastream_id": desc.datastream_id,
            "datastream_description": desc.datastream_description,
            "thing_id": desc.thing_id,
            "thing_name": desc.thing_name,
            "thing_description": desc.thing_description,
            "thing_properties": desc.thing_properties or {},
            # bequem direkt hochgezogen:
            "mh1": mh1,
            "mh2": mh2,
            "mh3": mh3,
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
            for k in ("phenomenonTime", "resultTime", "observation_id"):
                self._attr_extra_state_attributes.pop(k, None)
            return

        obs: dict[str, Any] = values[0]
        self._attr_native_value = obs.get("result")

        self._attr_extra_state_attributes.update(
            {
                "phenomenonTime": obs.get("phenomenonTime"),
                "resultTime": obs.get("resultTime"),
                "observation_id": obs.get("@iot.id"),
            }
        )
        self._attr_extra_state_attributes.pop("observation_note", None)


class FrostMeldehoeheLevelSensor(FrostBaseSensor):
    """Numeric sensor 0..3 representing reached Meldehöhe level based on mh1/mh2/mh3 attributes of value sensor."""

    _attr_icon = "mdi:alarm-light-outline"

    def __init__(self, value_sensor: FrostLatestObservationSensor, entry_id: str, desc: FrostDatastreamDescription) -> None:
        self._value_sensor = value_sensor
        self.entity_description = desc

        # eigener Unique ID pro Datastream
        self._attr_unique_id = f"{entry_id}_datastream_{desc.datastream_id}_meldehoehe_level"
        self._attr_name = "Meldehöhe Stufe"
        self._attr_native_unit_of_measurement = None

        self._set_device_info_from_desc(desc)

        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "mh1": self._value_sensor.extra_state_attributes.get("mh1"),
            "mh2": self._value_sensor.extra_state_attributes.get("mh2"),
            "mh3": self._value_sensor.extra_state_attributes.get("mh3"),
        }

    async def async_update(self) -> None:
        # Wert-Sensor sollte kurz vorher aktualisiert worden sein; falls nicht, wird er von HA auch aktualisiert.
        p = _safe_float(self._value_sensor.native_value, default=None)
        mh1 = self._value_sensor.extra_state_attributes.get("mh1")
        mh2 = self._value_sensor.extra_state_attributes.get("mh2")
        mh3 = self._value_sensor.extra_state_attributes.get("mh3")

        self._attr_extra_state_attributes.update({"mh1": mh1, "mh2": mh2, "mh3": mh3})

        if p is None:
            self._attr_native_value = None
            return

        level = 0
        if mh3 is not None and p >= mh3:
            level = 3
        elif mh2 is not None and p >= mh2:
            level = 2
        elif mh1 is not None and p >= mh1:
            level = 1

        self._attr_native_value = level
