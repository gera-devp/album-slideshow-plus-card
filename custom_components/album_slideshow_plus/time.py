"""Sleep schedule start and end times (Album Slideshow Plus)."""
from __future__ import annotations

from datetime import time as dtime

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_SLEEP_END, DEFAULT_SLEEP_START, DOMAIN
from .spotlight import parse_time
from .store import SlideshowStore


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    store: SlideshowStore = hass.data[DOMAIN][entry.entry_id]["store"]
    async_add_entities([
        SleepTime(entry, store, "sleep_start", "Sleep start", "mdi:weather-night", DEFAULT_SLEEP_START),
        SleepTime(entry, store, "sleep_end", "Sleep end", "mdi:weather-sunny", DEFAULT_SLEEP_END),
    ])


class SleepTime(TimeEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        store: SlideshowStore,
        attribute: str,
        name: str,
        icon: str,
        default: str,
    ) -> None:
        self.entry = entry
        self.store = store
        self._attribute = attribute
        self._default = parse_time(default, dtime(0, 0))
        self._attr_unique_id = f"{entry.entry_id}_{attribute}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": f"Album Slideshow Plus {self.entry.title}",
            "manufacturer": "Album Slideshow Plus",
        }

    @property
    def native_value(self) -> dtime:
        return parse_time(getattr(self.store, self._attribute), self._default)

    async def async_set_value(self, value: dtime) -> None:
        setattr(self.store, self._attribute, value.replace(tzinfo=None).strftime("%H:%M:%S"))
        self.store.notify()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.store.add_listener(self.async_write_ha_state)
        old = await self.async_get_last_state()
        if old and old.state not in (None, "unknown", "unavailable"):
            restored = parse_time(old.state, None)
            if restored is not None:
                setattr(self.store, self._attribute, restored.strftime("%H:%M:%S"))
                self.store.notify()
