# Album Slideshow Plus

A fork of [Album Slideshow Camera](https://github.com/eyalgal/album_slideshow)
v1.18.0 by Eyal Gal (MIT licence). It keeps every upstream feature and
source: Google Photos, Immich, PhotoPrism, iCloud, Synology Photos,
Nextcloud, Ente, UGREEN, local/NAS folders and Media Source. It adds the
features below.

It installs under its own domain, `album_slideshow_plus`, so it runs
alongside the original. Entities are named `… album_slideshow_plus_<album> …`
and the card is `custom:album-slideshow-plus-card`. Upstream docs in
[`docs/`](docs/) still apply to everything the two share. Replace
`album_slideshow` with `album_slideshow_plus` in action names.

## What's new

| Feature | Where | Inspired by |
|---|---|---|
| **New-photo spotlight**: photos added today (or in the last 24 h / 3 / 7 days) are re-shown every N slides or N minutes, taking turns, on top of the normal rotation | Integration | Pix-Star "Repeat recent pix", Loop "New photos on loop", Aura |
| New-photo detection for every source (first-seen tracking), the album's own "added" date where it has one, or the file's modified time | Integration | Upstream `newest_added` only works for Google, Nextcloud and Ente |
| Faster album checks while the spotlight is on (default every 30 min) | Integration | Otherwise a new photo waits for the hourly-or-longer refresh |
| `album_slideshow_plus_new_photos` event when new photos arrive | Integration | Immich Kiosk webhooks |
| **Memories**: a photo from today's date in an earlier year every N slides or minutes, mixed into the normal slideshow | Integration | ImmichFrame/Kiosk "Memories". Upstream's "On this day" filter shows nothing else |
| **Group similar shots**: several snaps of the same moment play once, as the sharpest shot | Integration | Burst/duplicate culling in photo apps |
| **Story mode**: after a random photo, the next few shots from the same outing, in order | Integration | Immich Kiosk discussion #596 (batch/context grouping) |
| **Sleep schedule**: hold the slideshow overnight; the card goes black or dim with a faint clock | Integration + card | Immich Kiosk `sleep_start`/`sleep_end`, photo-frame on/off times |
| Show new photo / Show memory / Mark all as seen buttons and actions | Integration | Loop "until dismissed" |
| **Ken Burns** slow zoom and pan during each slide | Card | WallPanel `image_animation_ken_burns`, Immich Kiosk `image_effect`, ImmichFrame ImageZoom/ImagePan |
| **Progress bar** for time left on the slide | Card | Immich Kiosk `show_progress_bar`, WallPanel |
| **Burn-in protection**: captions move a few pixels every 2 minutes | Card | Immich Kiosk `burn_in_*`, WallPanel info box movement |
| Caption fields **`spotlight`** ("New photo" / "On this day · 3 years ago") and **`story`** ("2 / 4") | Card | Loop's "new" marker |

## Install

### HACS (custom repository)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gera-devp&repository=album-slideshow-plus-card&category=integration)

1. In Home Assistant open **HACS**, then the **⋮** menu (top right), then
   **Custom repositories**.
2. Repository: `https://github.com/gera-devp/album-slideshow-plus-card`. Type: **Integration**. Click **Add**.
3. Search HACS for **Album Slideshow Plus**, open it and click
   **Download**.
4. Restart Home Assistant.
5. **Settings → Devices & services → Add integration → Album Slideshow
   Plus**, then set up your source. A NAS folder mounted into the
   container is a **Local folder**, for example `/config/media/nas_photos`.
6. Hard-refresh the dashboard (Ctrl+Shift+R) so the card script loads.

HACS then offers each new release as an update.

### Manual

Download `album_slideshow_plus.zip` from the
[latest release](https://github.com/gera-devp/album-slideshow-plus-card/releases/latest). Extract it into
`config/custom_components/album_slideshow_plus/`, so that `manifest.json`
sits directly in that folder. Then restart Home Assistant.

The original Album Slideshow can stay installed alongside. Each one renders
its own slides, so remove the old one once you've switched, to save CPU on
a small host.

## New-photo spotlight

Turn on **Spotlight new photos** (`every_n_slides` or `every_n_minutes`)
and set **Spotlight every**. With 5 there, every 5th slide is a new photo.
That means 4 ordinary slides, then one new photo, taking turns across all
new photos. With 1, only new photos play while there are any. In minutes
mode, the minutes are converted to slides using the slide interval: 10
minutes at a 60-second interval is every 10th slide.

* **New photo window**: `today` (since local midnight), `last_24_hours`,
  `last_3_days` or `last_7_days`.
* **New photo detection**:
  * `auto` (default): the album's own added date where the source has one
    (Google Photos, Nextcloud, Ente). Otherwise, when this slideshow first
    saw the photo.
  * `first_seen`: always when this slideshow first saw the photo.
  * `file_modified`: a local folder file's modified time. Useful when a
    script writes new files into the folder. Copied files often keep their
    original time.
* On the very first run, every photo already in the album is recorded as
  "already there", so installing doesn't flag your whole library as new.
  Photos that arrive after that count as new.
* **New photo check** (minutes, default 30, 0 = off): while the spotlight
  is on, the album is re-read this often, so a photo copied to the NAS at
  10:00 starts showing within half an hour. A folder scan is cheap. For
  Google, this is one album fetch per check.
* A new photo shows on the very next slide once it's found, if a spotlight
  is due. Several new portrait photos share a paired slide.
* **Mark all photos as seen** stops the current new photos from being
  spotlighted until more arrive.

Entities: `select.…_spotlight_new_photos`, `number.…_spotlight_every`,
`select.…_new_photo_window`, `select.…_new_photo_detection`,
`number.…_new_photo_check`, `sensor.…_new_photos` (count, with
`filenames` and `newest_added_at`), `button.…_show_new_photo`,
`button.…_mark_all_photos_as_seen`.

## Group similar shots

When you take a few snaps of the same thing, only the sharpest plays.
Shots are grouped when both of these hold:

* **Taken within the Similar shot window** of each other (default 30 s,
  2–600 s). This uses the photo's own capture time from EXIF.
* **They look the same.** Each candidate gets a visual fingerprint (a
  difference hash of a tiny greyscale copy, plus its average colour). A
  re-framed or re-exposed snap still matches; a different subject shot
  seconds later doesn't.

From each group the **sharpest** shot is kept. Sharpness is the variance
of edge strength, so a shaken or out-of-focus take loses. It judges focus
and motion blur only, not closed eyes or expressions. The group counts as
one photo everywhere: the normal rotation, the new-photo spotlight, story
mode and pairing. Nothing is deleted; turn **Group similar shots** off and
every snap plays again.

* Only photos with another photo inside the window are read in full, so a
  NAS library isn't downloaded end to end. Each one is checked once, in the
  background.
* Photos without their own capture time (EXIF stripped, as in many
  WhatsApp copies) are never grouped. A file's modified time would group
  whatever was copied together.
* For sources other than local folders there is no fingerprint. Shots
  taken within the window at the same pixel size are grouped, and the
  largest file is kept.

Entities: `switch.…_group_similar_shots` (on by default),
`number.…_similar_shot_window`, `sensor.…_similar_shots_skipped` (with
`groups`, `photos_compared` and `examples` of kept/skipped files). Camera
attributes: `similar_shots` (how many snaps the photo on screen stands for),
`similar_groups`, `similar_shots_folded`.

## Memories, story mode and sleep

* **Memories (on this day)** + **Memories every**: the same cadence choice.
  It picks a photo taken on today's date in an earlier year, or up to 3
  days either side if there is none. A new-photo spotlight wins when both
  are due, and the memory follows on the next slide.
* **Story length** (1 = off, up to 8) and **Story window** (hours), in
  Random order: after a random photo, the next *length − 1* photos taken
  within the window after it play in the order they were taken. It needs
  capture dates (EXIF for local folders).
* **Sleep schedule** switch, with **Sleep start** and **Sleep end** time
  entities (default 22:00–07:00). While asleep, the slideshow holds and
  stops downloading. The card shows `sleep_screen: black` (default), `dim`
  or `none`, with a faint clock unless `sleep_clock: false`.

## Card options added

```yaml
type: custom:album-slideshow-plus-card
entity: camera.album_slideshow_plus_kj_photos
transition: fade
duration: 1200
aspect_ratio: 16/9
motion: ken_burns        # none | ken_burns
motion_zoom: 1.15        # 1.02 - 1.5
progress_bar: bottom     # none | top | bottom
progress_color: '#ffffff80'
sleep_screen: black      # black | dim | none
sleep_clock: true
burn_in_shift: 6         # px, 0 = off
captions:
  - show: [spotlight]
    position: top-left
    font_size: 18px
    font_weight: semibold
    spotlight_new_text: New photo          # tokens: {ago} {count}
    spotlight_memory_text: On this day · {ago}   # tokens: {ago} {years}
  - show: [date, location]
    position: bottom-left
  - show: [current_time]
    position: bottom-right
    font_size: 28px
```

All of these are also in the card's visual editor, under **Motion, progress
and sleep**. The spotlight, memories, story and sleep settings appear under
**Slideshow settings**.

## New camera attributes

`spotlight` (`new` / `memory` / null), `is_new`, `added_at`,
`new_photo_count`, `memory_count`, `memory_years_ago`, `story_position`
(`"2/4"`), `slides_until_spotlight`, `spotlight_mode`, `spotlight_every`,
`new_photo_window`, `new_photo_detection`, `memories_mode`,
`memories_every`, `story_length`, `story_window_hours`, `sleep_schedule`,
`sleep_start`, `sleep_end`, `sleeping`, `slide_hold_seconds`,
`slide_ends_at`, `group_similar_shots`, `similar_window_seconds`,
`similar_groups`, `similar_shots_folded`, `similar_shots`.

## Actions and events

| Action | Data |
|---|---|
| `album_slideshow_plus.show_new_photo` | `entry_id` |
| `album_slideshow_plus.show_memory` | `entry_id` |
| `album_slideshow_plus.mark_all_seen` | `entry_id` (returns `cleared`) |

All upstream actions are there too, under the new domain
(`album_slideshow_plus.next_slide`, `hide_photo`, …).

Event `album_slideshow_plus_new_photos`: `entry_id`, `album`, `count`,
`photo_ids`, `filenames`. Example: get a phone notification when new photos
land, and show one straight away:

```yaml
alias: New photos on the slideshow
triggers:
  - trigger: event
    event_type: album_slideshow_plus_new_photos
actions:
  - action: album_slideshow_plus.show_new_photo
    data:
      entry_id: "{{ trigger.event.data.entry_id }}"
  - action: notify.notify
    data:
      message: "{{ trigger.event.data.count }} new photo(s) in {{ trigger.event.data.album }}"
```

## Not included (yet)

These were requested upstream or exist elsewhere, and are still open:
photo collages (upstream #24), Synology smart albums (#32), animated
GIF/WebP (#25, declined upstream), liking a photo back to the source
(Immich Kiosk), and mixing different source types in one camera.
Upstream supports several Google albums in one camera.

## Credits

Album Slideshow Camera © 2026 Eyal Gal, MIT licence. See `LICENSE`. The
Plus changes are released under the same licence.
