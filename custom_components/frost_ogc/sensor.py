from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


def _safe_float(v: Any, default: float | None = None) -> float | None:
    """Convert common FROST values to float.

    Handles numbers, numeric strings, and strings like '347.9 m. üNN'.
    """
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            import re

            s = v.replace(",", ".")
            m = re.search(r"[-+]?\d+(\.\d+)?", s)
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
    """Detect if Thing describes a water level gauge (Pegel)."""
    if not props:
        return False

    if str(props.get("type", "")).strip().lower() == "pegel":
        return True

    kws = props.get("keywords") or []
    kws_l = [str(x).strip().lower() for x in kws]
    return ("pegel" in kws_l) or ("wasserstandsmessung" in kws_l)


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

    # coordinator.data contains Datastreams including expanded Thing (via $expand=Thing)
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
    """Shows latest Observation.result for a given FROST Datastream and exposes Thing metadata.

    Additionally computes 'meldehoehe_level' (0..3) for Pegel Things based on MH1/MH2/MH3.
    """

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

        # Device = Thing (group sensors per Thing in HA)
        if desc.thing_id is not None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"thing_{desc.thing_id}")},
                name=desc.thing_name or f"Thing {desc.thing_id}",
                manufacturer="FROST",
                model="OGC SensorThings Thing",
            )

        mh1, mh2, mh3 = _get_meldehoehen(desc.thing_properties or {})

        # Static metadata as attributes
        self._attr_extra_state_attributes = {
            "datastream_id": desc.datastream_id,
            "datastream_description": desc.datastream_description,
            "thing_id": desc.thing_id,
            "thing_name": desc.thing_name,
            "thing_description": desc.thing_description,
            "thing_properties": props,
	    "thing_type": props.get("type"),          # <—
	    "gewaesser": props.get("gewaesser"),      # optional
	    "gemeinde": props.get("gemeinde"),        # optional
	    "betreiber": props.get("betreiber"),      # optional
	    # Convenience (numeric)
            "mh1": mh1,
            "mh2": mh2,
            "mh3": mh3,
            # Computed each update (initialized)
            "meldehoehe_level": 0,
        }

        self._is_pegel = _is_pegel(desc.thing_properties or {})
        self._attr_native_value = None

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
            self._attr_extra_state_attributes["observation_note"] = "no observations"
            for k in ("phenomenonTime", "resultTime", "observation_id"):
                self._attr_extra_state_attributes.pop(k, None)
            # level unknown -> back to 0
            self._attr_extra_state_attributes["meldehoehe_level"] = 0
            return

        obs: dict[str, Any] = values[0]
        self._attr_native_value = obs.get("result")

        # Observation metadata
        self._attr_extra_state_attributes.update(
            {
                "phenomenonTime": obs.get("phenomenonTime"),
                "resultTime": obs.get("resultTime"),
                "observation_id": obs.get("@iot.id"),
            }
        )
        self._attr_extra_state_attributes.pop("observation_note", None)

        # Compute meldehoehe_level (0..3) for Pegel things
        level = 0
        if self._is_pegel:
            p = _safe_float(self._attr_native_value, default=None)
            mh1 = self._attr_extra_state_attributes.get("mh1")
            mh2 = self._attr_extra_state_attributes.get("mh2")
            mh3 = self._attr_extra_state_attributes.get("mh3")

            if p is not None:
                if mh3 is not None and p >= mh3:
                    level = 3
                elif mh2 is not None and p >= mh2:
                    level = 2
                elif mh1 is not None and p >= mh1:
                    level = 1

        self._attr_extra_state_attributes["meldehoehe_level"] = level
