// Album Slideshow Plus card features: spotlight badge, story caption,
// Ken Burns motion, progress bar, sleep screen and the new options.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const filename = path.join(__dirname, "../custom_components/album_slideshow_plus/www/album-slideshow-plus-card.js");
const source = fs.readFileSync(filename, "utf8");
const context = vm.createContext({
  console, setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
  requestAnimationFrame: (fn) => fn(), getComputedStyle: () => ({ width: "40px" }), Intl, Date,
  CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
});
vm.runInContext(source.slice(0, source.indexOf("\ndefineAlbumSlideshowCards();")), context);
const Card = vm.runInContext("createAlbumSlideshowCardClass(class {})", context);
const Editor = vm.runInContext("createAlbumSlideshowCardEditorClass(class {})", context);

function card(config = {}) {
  const instance = Object.create(Card.prototype);
  instance.setConfig({ entity: "camera.test", ...config });
  instance._hass = { locale: { language: "en" }, config: { time_zone: "UTC" } };
  return instance;
}

function fakeElement() {
  const props = new Map();
  const classes = new Set();
  return {
    style: {
      setProperty: (key, value) => props.set(key, value),
      removeProperty: (key) => props.delete(key),
      get: (key) => props.get(key),
    },
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    },
    offsetWidth: 1,
    hidden: true,
    textContent: "",
    props,
  };
}

test("new card options are parsed with safe defaults", () => {
  const c = card()._config;
  assert.equal(c.motion, "none");
  assert.equal(c.progress_bar, "none");
  assert.equal(c.sleep_screen, "black");
  assert.equal(c.sleep_clock, true);
  assert.equal(c.burn_in_shift, 0);
  const custom = card({ motion: "Ken_Burns", motion_zoom: 9, progress_bar: "bottom", sleep_screen: "dim", burn_in_shift: 100 })._config;
  assert.equal(custom.motion, "ken_burns");
  assert.equal(custom.motion_zoom, 1.5);
  assert.equal(custom.progress_bar, "bottom");
  assert.equal(custom.sleep_screen, "dim");
  assert.equal(custom.burn_in_shift, 40);
  assert.equal(card({ motion: "spin", progress_bar: "left" })._config.motion, "none");
});

test("spotlight and story are accepted caption fields", () => {
  const c = card({ captions: [{ show: ["spotlight", "story", "date"] }] })._config;
  assert.deepEqual([...c.captions[0].show], ["spotlight", "story", "date"]);
  assert.equal(c.captions[0].spotlight_new_text, "New photo");
});

test("spotlight badge text for new photos and memories", () => {
  const instance = card({ captions: [{ show: ["spotlight"] }] });
  const cap = instance._config.captions[0];
  instance._captionData = { spotlight: "new", added_at: null };
  assert.equal(instance._spotlightText(cap), "New photo");
  instance._captionData = { spotlight: "memory", memory_years_ago: 3 };
  assert.equal(instance._spotlightText(cap), "On this day · 3 years ago");
  instance._captionData = { spotlight: "memory", memory_years_ago: null };
  assert.equal(instance._spotlightText(cap), "On this day");
  instance._captionData = { spotlight: null };
  assert.equal(instance._spotlightText(cap), "");
  const customized = card({ captions: [{ show: ["spotlight"], spotlight_new_text: "Just added ({count} today)" }] });
  customized._captionData = { spotlight: "new", new_photo_count: 4 };
  assert.equal(customized._spotlightText(customized._config.captions[0]), "Just added (4 today)");
});

test("slide-wide fields appear once on a paired slide", () => {
  const instance = card({ captions: [{ show: ["spotlight", "date"] }] });
  instance._captionData = { spotlight: "new" };
  const cap = instance._config.captions[0];
  const first = instance._captionLines({ captured_at: null }, cap, new Date(), 0);
  const second = instance._captionLines({ captured_at: null }, cap, new Date(), 1);
  assert.deepEqual([...first], ["New photo"]);
  assert.deepEqual([...second], []);
});

test("story caption shows the position", () => {
  const instance = card({ captions: [{ show: ["story"] }] });
  instance._captionData = { story_position: "2/4" };
  assert.equal(instance._storyText(), "2 / 4");
  instance._captionData = { story_position: null };
  assert.equal(instance._storyText(), "");
});

test("Ken Burns animates only the visible layer, and continues on a same-photo re-render", () => {
  const instance = card({ motion: "ken_burns", motion_zoom: 1.2, duration: 600 });
  const a = fakeElement();
  const b = fakeElement();
  instance.shadowRoot = { getElementById: (id) => ({ a, b }[id]) };
  instance._showing = "a";
  instance._applyMotion({ holdSeconds: 30 }, false);
  assert.ok(a.classList.contains("kb"));
  assert.ok(!b.classList.contains("kb"));
  assert.equal(a.props.get("--kb-dur"), "30600ms");
  assert.equal(a.props.get("--kb-delay"), "0ms");
  const motion = instance._motion;
  for (const end of [motion.from, motion.to]) {
    // Pans stay inside the zoomed image's overhang.
    assert.ok(Math.abs(end.x) <= 10 && Math.abs(end.y) <= 10);
  }
  motion.startedAt -= 5000;
  instance._showing = "b";
  instance._applyMotion({ holdSeconds: 30 }, true);
  assert.equal(instance._motion, motion);
  assert.ok(b.classList.contains("kb"));
  assert.match(b.props.get("--kb-delay"), /^-50\d\dms$/);
  const still = card({ motion: "none" });
  still.shadowRoot = instance.shadowRoot;
  still._showing = "b";
  still._applyMotion({ holdSeconds: 30 }, false);
  assert.ok(!b.classList.contains("kb"));
});

test("progress bar runs to the slide's end and freezes while held", () => {
  const instance = card({ progress_bar: "bottom" });
  const fill = fakeElement();
  instance.shadowRoot = { getElementById: (id) => (id === "progress-fill" ? fill : null) };
  instance._displayedFrameId = 7;
  const now = Date.now();
  instance._syncProgress({ slide_ends_at: (now + 30000) / 1000, slide_hold_seconds: 60 }, true);
  assert.match(fill.style.transition, /^width (29|30)\d{3}ms linear$/);
  assert.equal(fill.style.width, "100%");
  instance._syncProgress({ slide_ends_at: null, slide_hold_seconds: 60, paused: true });
  assert.equal(fill.style.transition, "none");
  assert.equal(fill.style.width, "40px");
});

test("sleep overlay follows the camera's sleeping attribute", () => {
  const instance = card({ sleep_screen: "black", sleep_clock: true });
  const overlay = fakeElement();
  const clock = fakeElement();
  instance.shadowRoot = { getElementById: (id) => ({ sleep: overlay, "sleep-clock": clock }[id]) };
  instance._formatCurrentTime = () => "23:15";
  instance._syncSleep({ sleeping: true });
  assert.equal(overlay.hidden, false);
  assert.ok(overlay.classList.contains("show"));
  assert.equal(clock.textContent, "23:15");
  instance._syncSleep({ sleeping: false });
  assert.ok(!overlay.classList.contains("show"));
  const off = card({ sleep_screen: "none" });
  const overlay2 = fakeElement();
  off.shadowRoot = { getElementById: () => overlay2 };
  off._syncSleep({ sleeping: true });
  assert.ok(!overlay2.classList.contains("show"));
});

test("editor writes only non-default Plus options", () => {
  const editor = Object.create(Editor.prototype);
  editor._config = { type: "custom:album-slideshow-plus-card", entity: "camera.test" };
  editor._siblings = null;
  let written = null;
  editor.dispatchEvent = (event) => { written = event.detail.config; };
  editor._valueChanged({
    stopPropagation() {},
    detail: { value: { entity: "camera.test", motion: "ken_burns", motion_zoom: 1.15, progress_bar: "top",
      progress_color: "", sleep_screen: "black", sleep_clock: true, burn_in_shift: 6 } },
  });
  assert.equal(written.motion, "ken_burns");
  assert.equal(written.motion_zoom, undefined);
  assert.equal(written.progress_bar, "top");
  assert.equal(written.sleep_screen, undefined);
  assert.equal(written.burn_in_shift, 6);
});
