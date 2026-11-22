from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_REFRESH_ALBUM, ATTR_ENTRY_ID
from .const import SERVICE_HIDE_PHOTO, SERVICE_UNDO_HIDE
from .const import SERVICE_MARK_ALL_SEEN, SERVICE_SHOW_MEMORY, SERVICE_SHOW_NEW_PHOTO
from .coordinator import AlbumCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: AlbumCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            PreviousSlideButton(hass, entry, coordinator),
            NextSlideButton(hass, entry, coordinator),
            RefreshAlbumButton(hass, entry, coordinator),
            HidePhotoButton(hass, entry, coordinator),
            UndoHideButton(hass, entry, coordinator),
            _ServiceButton(hass, entry, coordinator, "show_new_photo", "Show new photo",
                           "mdi:star-four-points", SERVICE_SHOW_NEW_PHOTO),
            _ServiceButton(hass, entry, coordinator, "show_memory", "Show memory",
                           "mdi:calendar-heart", SERVICE_SHOW_MEMORY),
            _ServiceButton(hass, entry, coordinator, "mark_all_seen", "Mark all photos as seen",
                           "mdi:check-all", SERVICE_MARK_ALL_SEEN),
        ]
    )


class _BaseButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": f"Album Slideshow Plus {self.entry.title}",
            "manufacturer": "Album Slideshow Plus",
        }


class PreviousSlideButton(_BaseButton):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_previous_button"
        self._attr_name = "Previous slide"
        self._attr_icon = "mdi:skip-previous"

    async def async_press(self) -> None:
        camera = self.hass.data[DOMAIN][self.entry.entry_id].get("camera")
        if camera is not None:
            await camera.async_force_prev()


class NextSlideButton(_BaseButton):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_button"
        self._attr_name = "Next slide"
        self._attr_icon = "mdi:skip-next"

    async def async_press(self) -> None:
        camera = self.hass.data[DOMAIN][self.entry.entry_id].get("camera")
        if camera is not None:
            await camera.async_force_next()


class RefreshAlbumButton(_BaseButton):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh_button"
        self._attr_name = "Refresh album"
        self._attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_ALBUM,
            {ATTR_ENTRY_ID: self.entry.entry_id},
            blocking=False,
        )


class HidePhotoButton(_BaseButton):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_hide_photo"
        self._attr_name = "Hide current photo"
        self._attr_icon = "mdi:eye-off"

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            DOMAIN, SERVICE_HIDE_PHOTO, {ATTR_ENTRY_ID: self.entry.entry_id}, blocking=True
        )


class UndoHideButton(_BaseButton):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: AlbumCoordinator) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_undo_hide"
        self._attr_name = "Undo hide"
        self._attr_icon = "mdi:undo"

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            DOMAIN, SERVICE_UNDO_HIDE, {ATTR_ENTRY_ID: self.entry.entry_id}, blocking=True
        )


class _ServiceButton(_BaseButton):
    """Album Slideshow Plus: a button that calls one of this slideshow's actions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: AlbumCoordinator,
        suffix: str,
        name: str,
        icon: str,
        service: str,
    ) -> None:
        super().__init__(hass, entry, coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_name = name
        self._attr_icon = icon
        self._service = service

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            DOMAIN, self._service, {ATTR_ENTRY_ID: self.entry.entry_id}, blocking=True
        )
