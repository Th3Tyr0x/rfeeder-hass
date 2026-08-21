/**
 * RFeeder Weekly Plan card — weekly grid (compartments x days) for the
 * Robotail RFeeder Home Assistant integration.
 *
 * Usage:
 *   type: custom:rfeeder-weekly-card
 *   entity: sensor.smart_dual_temp_feeder_next_scheduled_feeding   (optional)
 *   name: "Futterplan"                                             (optional)
 *
 * The card reads the on-device schedules from the sensor attributes
 * (schedules: [{id, time_local, weekdays[], feed_options, enabled}]) and
 * writes the whole plan via the rfeeder.sync_weekly_plan service.
 */

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABELS = {
  de: ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
  en: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
};
const STRINGS = {
  de: {
    duration: "Dauer (min)",
    preheat: "Vorwärmen",
    save: "Speichern",
    saving: "Speichern…",
    saved: "Gespeichert ✓",
    once: "Einmalig",
    add: "+",
    noEntity: "Kein Sensor gefunden. Bitte 'entity' in der Karten-Konfiguration angeben.",
  },
  en: {
    duration: "Duration (min)",
    preheat: "Pre-heat",
    save: "Save",
    saving: "Saving…",
    saved: "Saved ✓",
    once: "One-time",
    add: "+",
    noEntity: "No sensor found. Please set 'entity' in the card configuration.",
  },
};

class RFeederWeeklyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._chips = []; // {compartment, dayIdx, time, enabled}
    this._dirty = false;
    this._saving = false;
    this._duration = 5;
    this._preheat = false;
  }

  setConfig(config) {
    this._config = config || {};
    this._entityId = this._config.entity || null;
    this._title = this._config.name || null;
    this._render();
  }

  /** Find the next-scheduled-feeding sensor by content, not by entity id
   *  (entity ids are translated with the UI language). */
  static findSensor(hass) {
    if (!hass) return null;
    const found = Object.values(hass.states).find(
      (s) =>
        s.entity_id.startsWith("sensor.") &&
        s.attributes &&
        Array.isArray(s.attributes.schedules)
    );
    return found ? found.entity_id : null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._entityId) {
      this._entityId = RFeederWeeklyCard.findSensor(hass);
    }
    if (!this._dirty && !this._saving) {
      this._loadFromState();
    }
    this._render();
  }

  _strings() {
    const lang = (this._hass && this._hass.language) || "en";
    return STRINGS[lang.startsWith("de") ? "de" : "en"];
  }

  _dayLabels() {
    const lang = (this._hass && this._hass.language) || "en";
    return DAY_LABELS[lang.startsWith("de") ? "de" : "en"];
  }

  _loadFromState() {
    const state = this._hass && this._entityId ? this._hass.states[this._entityId] : null;
    const schedules = (state && state.attributes && state.attributes.schedules) || [];
    const chips = [];
    for (const s of schedules) {
      if (!s || s.enabled === false) continue;
      const time = s.time_local || s.time_utc;
      const comp = (s.feed_options && s.feed_options.trayCompartmentIndex) || 1;
      const days = Array.isArray(s.weekdays) && s.weekdays.length ? s.weekdays : DAYS.slice();
      for (const day of days) {
        const dayIdx = DAYS.indexOf(String(day).toLowerCase());
        if (dayIdx >= 0 && time) {
          chips.push({ compartment: comp, dayIdx, time: String(time).slice(0, 5), enabled: true });
        }
      }
      const dur = s.feed_options && s.feed_options.feedDurationSeconds;
      if (dur) this._duration = Math.round(dur / 60);
      if (s.feed_options && typeof s.feed_options.heatBeforeFeeding === "boolean") {
        this._preheat = s.feed_options.heatBeforeFeeding;
      }
    }
    this._chips = chips;
  }

  _addChip(compartment, dayIdx, time) {
    if (!time) return;
    this._chips.push({ compartment, dayIdx, time, enabled: true });
    this._chips.sort((a, b) => a.time.localeCompare(b.time));
    this._dirty = true;
    this._render();
  }

  _removeChip(idx) {
    this._chips.splice(idx, 1);
    this._dirty = true;
    this._render();
  }

  async _save() {
    const t = this._strings();
    this._saving = true;
    this._render();
    try {
      // group by compartment+time
      const groups = new Map();
      for (const c of this._chips) {
        const key = `${c.compartment}|${c.time}`;
        if (!groups.has(key)) groups.set(key, new Set());
        groups.get(key).add(DAYS[c.dayIdx]);
      }
      const plan = [];
      for (const [key, daySet] of groups.entries()) {
        const [comp, time] = key.split("|");
        plan.push({
          compartment: Number(comp),
          time,
          weekdays: Array.from(daySet),
          feed_duration_minutes: Number(this._duration) || 5,
          heat_before_feeding: !!this._preheat,
          enabled: true,
        });
      }
      await this._hass.callService("rfeeder", "sync_weekly_plan", {
        plan,
        replace: true,
      });
      this._dirty = false;
      this._saveNote = t.saved;
      setTimeout(() => {
        this._saveNote = null;
        this._render();
      }, 3000);
    } catch (err) {
      this._saveNote = "⚠ " + (err && err.message ? err.message : String(err));
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const t = this._strings();
    const dayLabels = this._dayLabels();
    const state = this._hass && this._entityId ? this._hass.states[this._entityId] : null;
    const title = this._title || (state && state.attributes.friendly_name) || "RFeeder";

    if (this._hass && !state) {
      this.shadowRoot.innerHTML = `<ha-card><div class="pad">${t.noEntity}</div></ha-card>`;
      return;
    }

    const cells = [];
    for (let comp = 1; comp <= 4; comp++) {
      const row = [];
      for (let d = 0; d < 7; d++) {
        const chips = this._chips
          .map((c, i) => ({ ...c, i }))
          .filter((c) => c.compartment === comp && c.dayIdx === d);
        row.push({ d, chips });
      }
      cells.push({ comp, row });
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px; }
        .head { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:8px; }
        .title { font-weight:600; font-size:1.1em; flex:1; }
        .grid { display:grid; grid-template-columns: 52px repeat(7, 1fr); gap:4px; }
        .daylabel { font-size:0.75em; text-align:center; opacity:0.7; padding:2px 0; }
        .complabel { display:flex; align-items:center; justify-content:center; font-weight:600;
                     background:var(--secondary-background-color); border-radius:8px; }
        .cell { min-height:44px; background:var(--secondary-background-color); border-radius:8px;
                padding:2px; display:flex; flex-direction:column; align-items:center; gap:2px; }
        .chip { background:var(--primary-color); color:var(--text-primary-color,#fff);
                border-radius:10px; padding:1px 6px; font-size:0.8em; cursor:pointer;
                line-height:1.6; white-space:nowrap; }
        .chip:hover { opacity:0.8; }
        .add { background:none; border:1px dashed var(--divider-color); border-radius:8px;
               color:var(--primary-text-color); opacity:0.6; cursor:pointer; font-size:0.8em;
               padding:0 6px; }
        .add:hover { opacity:1; }
        .controls { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:10px; }
        .controls label { font-size:0.85em; display:flex; align-items:center; gap:4px; }
        .controls input[type=number] { width:60px; }
        button.save { background:var(--primary-color); color:var(--text-primary-color,#fff);
                      border:none; border-radius:8px; padding:6px 18px; cursor:pointer; }
        button.save:disabled { opacity:0.5; cursor:default; }
        .note { font-size:0.8em; opacity:0.8; }
      </style>
      <ha-card>
        <div class="pad">
          <div class="head"><span class="title">${title}</span><span class="note">${this._saveNote || ""}</span></div>
          <div class="grid">
            <div></div>
            ${dayLabels.map((l) => `<div class="daylabel">${l}</div>`).join("")}
            ${cells
              .map(
                ({ comp, row }) => `
              <div class="complabel">${comp}</div>
              ${row
                .map(
                  ({ d, chips }) => `
                <div class="cell" data-comp="${comp}" data-day="${d}">
                  ${chips
                    .map(
                      (c) =>
                        `<span class="chip" data-idx="${c.i}" title="löschen / delete">${c.time} ✕</span>`
                    )
                    .join("")}
                  <button class="add" data-comp="${comp}" data-day="${d}">${t.add}</button>
                </div>`
                )
                .join("")}
            `
              )
              .join("")}
          </div>
          <div class="controls">
            <label>${t.duration}
              <input type="number" min="1" max="60" step="1" value="${this._duration}" id="dur">
            </label>
            <label>${t.preheat}
              <input type="checkbox" id="preheat" ${this._preheat ? "checked" : ""}>
            </label>
            <button class="save" id="save" ${!this._dirty || this._saving ? "disabled" : ""}>
              ${this._saving ? t.saving : t.save}
            </button>
          </div>
        </div>
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll(".chip").forEach((el) =>
      el.addEventListener("click", () => this._removeChip(Number(el.dataset.idx)))
    );
    this.shadowRoot.querySelectorAll(".add").forEach((el) =>
      el.addEventListener("click", () => this._askTime(Number(el.dataset.comp), Number(el.dataset.day)))
    );
    const dur = this.shadowRoot.getElementById("dur");
    if (dur) dur.addEventListener("change", () => { this._duration = dur.value; this._dirty = true; this._render(); });
    const pre = this.shadowRoot.getElementById("preheat");
    if (pre) pre.addEventListener("change", () => { this._preheat = pre.checked; this._dirty = true; this._render(); });
    const save = this.shadowRoot.getElementById("save");
    if (save) save.addEventListener("click", () => this._save());
  }

  _askTime(compartment, dayIdx) {
    const input = document.createElement("input");
    input.type = "time";
    input.style.position = "fixed";
    input.style.left = "-9999px";
    document.body.appendChild(input);
    input.addEventListener("change", () => {
      this._addChip(compartment, dayIdx, input.value);
      document.body.removeChild(input);
    });
    input.addEventListener("blur", () => {
      if (document.body.contains(input)) document.body.removeChild(input);
    });
    if (input.showPicker) {
      try { input.showPicker(); } catch (e) { input.focus(); }
    } else {
      input.focus();
    }
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig(hass) {
    const entity = RFeederWeeklyCard.findSensor(hass);
    return entity ? { entity } : {};
  }

  static getConfigElement() {
    return document.createElement("rfeeder-weekly-card-editor");
  }
}

customElements.define("rfeeder-weekly-card", RFeederWeeklyCard);

/** Minimal GUI editor: entity picker (pre-filled with the auto-detected
 *  next-feeding sensor) + optional card title. */
class RFeederWeeklyCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config || !this._config.entity) {
      const found = RFeederWeeklyCard.findSensor(hass);
      if (found) this._set("entity", found);
    }
    this._render();
  }

  _set(key, value) {
    this._config = { ...(this._config || {}), [key]: value };
    this.dispatchEvent(
      new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true })
    );
    this._render();
  }

  _render() {
    if (!this.shadowRoot) return;
    const entity = (this._config && this._config.entity) || "";
    const name = (this._config && this._config.name) || "";
    this.shadowRoot.innerHTML = `
      <style>
        .wrap { padding: 8px 0; display: flex; flex-direction: column; gap: 16px; }
        label { font-size: 0.9em; opacity: 0.8; display: block; margin-bottom: 4px; }
        input { width: 100%; box-sizing: border-box; padding: 6px; }
      </style>
      <div class="wrap">
        <div>
          <label>Entity (Next scheduled feeding sensor)</label>
          <ha-entity-picker id="picker"></ha-entity-picker>
        </div>
        <div>
          <label>Name (optional)</label>
          <input id="name" value="${name}">
        </div>
      </div>
    `;
    const picker = this.shadowRoot.getElementById("picker");
    if (picker && this._hass) {
      picker.hass = this._hass;
      picker.value = entity;
      picker.includeDomains = ["sensor"];
      picker.addEventListener("value-changed", (ev) => {
        if (ev.detail && ev.detail.value) this._set("entity", ev.detail.value);
      });
    }
    const nameInput = this.shadowRoot.getElementById("name");
    if (nameInput) {
      nameInput.addEventListener("change", () => this._set("name", nameInput.value));
    }
  }
}

customElements.define("rfeeder-weekly-card-editor", RFeederWeeklyCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "rfeeder-weekly-card",
  name: "RFeeder Weekly Plan",
  description: "Weekly feeding plan grid for the Robotail RFeeder (compartments x days).",
});
