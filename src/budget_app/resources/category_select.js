// The category picker (F08): NiceGUI's select.js, but the search matches each option's `search` text
// (everyday name, official name, code and search words, folded in Python by `coicop.fold`) instead of only
// the label, and options show the official name as a second line and a mark for recently used ones.
// The filter must stay the same as `budget_app.domain.everyday.matches`.

const fold = (text) =>
  String(text).toLowerCase().replace(/ß/g, "ss").normalize("NFKD").replace(/\p{M}/gu, "");

export default {
  props: ["options", "recentTitle"],
  template: `
    <q-select
      ref="qRef"
      :options="filteredOptions"
      @filter="filterFn"
      @popup-show="addClass"
      @popup-hide="removeClass"
      @input-value="forwardInputValue"
      @update:model-value="forwardModelValue"
    >
      <template v-slot:option="scope">
        <q-item v-bind="scope.itemProps" :style="indent(scope.opt)">
          <q-item-section>
            <q-item-label>{{ scope.opt.label }}</q-item-label>
            <q-item-label v-if="scope.opt.caption" caption>{{ scope.opt.caption }}</q-item-label>
          </q-item-section>
          <q-item-section v-if="scope.opt.recent" side>
            <q-icon name="history" size="xs" :title="recentTitle" />
          </q-item-section>
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
    };
  },
  methods: {
    indent(opt) {
      // subcategories are indented below their parent; recently used ones are listed flat at the top
      return opt.level > 1 && !opt.recent ? { paddingLeft: `${16 + 12 * (opt.level - 1)}px` } : {};
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
      return this.initialOptions.filter((option) => {
        const text = option.search ?? fold(option.label);
        return words.every((word) => text.includes(word));
      });
    },
    addClass() {
      // prevent the page from scrolling when the select popup is closed (#5031)
      document.documentElement.classList.add("nicegui-select-popup-open");
    },
    async removeClass() {
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
