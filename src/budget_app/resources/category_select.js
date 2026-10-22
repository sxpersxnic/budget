// The category picker (F08): NiceGUI's select.js, but the search matches each option's `search` text
// (everyday name, official name, code and search words, folded in Python by `coicop.fold`) instead of only
// the label, and options show the official name as a second line and a mark for recently used ones.
// F40: the search also matches `note_search`, the words of what the COICOP notes include; those matches are
// listed after the ones by name. Hovering an option (or moving to it with the arrow keys) shows its notes in a
// tooltip; on touch screens an info button opens them inside the option, so a tap still selects. The notes are
// loaded once per page from `notesUrl` and shared by every picker.
// The filter must stay the same as `budget_app.domain.everyday.search`.

const fold = (text) =>
  String(text).toLowerCase().replace(/ß/g, "ss").normalize("NFKD").replace(/\p{M}/gu, "");

const FIELDS = ["includes", "also_includes", "excludes"];
const loaded = new Map(); // notes URL -> promise of {translated, notes}

function loadNotes(url) {
  if (!url) return Promise.resolve(null);
  if (!loaded.has(url)) {
    loaded.set(
      url,
      fetch(url)
        .then((response) => (response.ok ? response.json() : null))
        .catch(() => null),
    );
  }
  return loaded.get(url);
}

// One option's notes: headings with their items, and the "unofficial translation" line for our own translations.
const NOTES = `
  <div class="cat-notes">
    <template v-for="field in fields" :key="field">
      <div v-if="note[field].length" class="cat-notes__part">
        <div class="cat-notes__head">{{ notesLabels[field] }}</div>
        <ul><li v-for="(item, i) in note[field]" :key="i">{{ item }}</li></ul>
      </div>
    </template>
    <div v-if="translated" class="cat-notes__foot">{{ notesLabels.unofficial }}</div>
  </div>
`;

export default {
  props: ["options", "recentTitle", "notesUrl", "notesLabels"],
  template: `
    <q-select
      ref="qRef"
      :options="filteredOptions"
      @filter="filterFn"
      @popup-show="popupShow"
      @popup-hide="removeClass"
      @input-value="forwardInputValue"
      @update:model-value="forwardModelValue"
      @keydown="keydown"
    >
      <template v-slot:option="scope">
        <q-item v-bind="scope.itemProps" :style="indent(scope.opt)" @mousemove="keyboard = false">
          <q-item-section>
            <q-item-label>{{ scope.opt.label }}</q-item-label>
            <q-item-label v-if="scope.opt.caption" caption>{{ scope.opt.caption }}</q-item-label>
            <template v-if="touch && opened === scope.opt.code">
              <template v-for="note in noteList(scope.opt)">${NOTES}</template>
            </template>
          </q-item-section>
          <q-item-section v-if="scope.opt.recent" side>
            <q-icon name="history" size="xs" :title="recentTitle" />
          </q-item-section>
          <q-item-section v-if="touch && noteOf(scope.opt)" side top>
            <q-btn
              flat round dense size="sm"
              :icon="opened === scope.opt.code ? 'expand_less' : 'info_outline'"
              :aria-label="notesLabels?.show"
              :aria-expanded="opened === scope.opt.code"
              @click.stop.prevent="toggle(scope.opt.code)"
              @mousedown.stop
              @touchstart.stop
            />
          </q-item-section>
          <q-tooltip
            v-if="!touch && noteOf(scope.opt)"
            class="cat-notes-tip"
            anchor="center right"
            self="center left"
            :offset="[10, 0]"
            max-width="400px"
            :delay="350"
            :model-value="hovered === scope.opt.code || (keyboard && scope.focused)"
            @update:model-value="(shown) => hover(scope.opt.code, shown)"
          >
            <template v-for="note in noteList(scope.opt)">${NOTES}</template>
          </q-tooltip>
        </q-item>
      </template>
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-select>
  `,
  emits: ["input-value", "update:model-value"],
  data() {
    return {
      initialOptions: this.options,
      filteredOptions: this.options,
      modelValueChanged: false,
      fields: FIELDS,
      notes: null, // code -> {includes, also_includes, excludes}, once loaded
      translated: false,
      touch: Boolean(this.$q?.platform?.has?.touch),
      hovered: null, // code under the mouse (after the tooltip delay)
      keyboard: false, // the arrow keys moved through the options: show the notes of the focused one
      opened: null, // code whose notes are open inside the option (touch)
    };
  },
  methods: {
    indent(opt) {
      // subcategories are indented below their parent; recently used ones are listed flat at the top
      return opt.level > 1 && !opt.recent ? { paddingLeft: `${16 + 12 * (opt.level - 1)}px` } : {};
    },
    noteOf(opt) {
      return (this.notes && opt.code && this.notes[opt.code]) || null;
    },
    noteList(opt) {
      const note = this.noteOf(opt);
      return note ? [note] : [];
    },
    hover(code, shown) {
      if (shown) this.hovered = code;
      else if (this.hovered === code) this.hovered = null;
    },
    toggle(code) {
      this.opened = this.opened === code ? null : code;
    },
    keydown(event) {
      if (["ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"].includes(event.key)) this.keyboard = true;
    },
    async popupShow() {
      this.addClass();
      if (this.notes) return;
      const data = await loadNotes(this.notesUrl);
      if (data) {
        this.translated = Boolean(data.translated);
        this.notes = data.notes;
      }
    },
    forwardModelValue(...args) {
      this.modelValueChanged = true;
      this.$emit("update:model-value", ...args);
    },
    async forwardInputValue(value) {
      this.modelValueChanged = false;
      await this.$nextTick(); // wait for a possible "update:model-value" event indicating a selection
      if (!this.modelValueChanged) {
        this.$emit("input-value", value); // suppress selection-induced events which would re-trigger filtering (#4420)
      }
    },
    filterFn(val, update, abort) {
      update(() => (this.filteredOptions = val ? this.findFilteredOptions() : this.initialOptions));
    },
    findFilteredOptions() {
      const words = fold(this.$el.querySelector("input[type=search]")?.value ?? "")
        .split(/\s+/)
        .filter((word) => word);
      if (!words.length) return this.initialOptions;
      const byName = [];
      const byNotes = [];
      for (const option of this.initialOptions) {
        const text = option.search ?? fold(option.label);
        const more = option.note_search ?? "";
        if (words.every((word) => text.includes(word))) byName.push(option);
        else if (words.every((word) => text.includes(word) || more.includes(word))) byNotes.push(option);
      }
      return byName.concat(byNotes);
    },
    addClass() {
      // prevent the page from scrolling when the select popup is closed (#5031)
      document.documentElement.classList.add("nicegui-select-popup-open");
    },
    async removeClass() {
      this.hovered = null;
      this.opened = null;
      await this.$nextTick();
      document.documentElement.classList.remove("nicegui-select-popup-open");
    },
  },
  unmounted() {
    this.removeClass();
  },
  watch: {
    options: {
      handler(newOptions) {
        this.initialOptions = newOptions;
        this.filteredOptions = newOptions;
      },
      immediate: true,
    },
  },
};
