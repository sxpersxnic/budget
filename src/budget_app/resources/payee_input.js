// The payee field (F09): free text with the known payees as suggestions (Quasar's "text autocomplete" select).
// The typed text is the value, so a new payee needs no Enter; picking a suggestion replaces the text.
// Suggestions match anywhere in the name, ignoring case and accents, in the order given (recently used first).
const fold = (text) =>
  String(text || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLocaleLowerCase();

export default {
  template: `
    <q-select
      ref="qRef"
      v-bind="$attrs"
      :model-value="inputValue || null"
      :options="filtered"
      use-input
      hide-selected
      fill-input
      hide-dropdown-icon
      input-debounce="0"
      @filter="filter"
      @input-value="set"
      @update:model-value="(v) => set(v || '')"
    >
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
    </q-select>
  `,
  props: {
    _autocomplete: Array,
    value: String,
    id: String,
  },
  data() {
    return { inputValue: this.value, filtered: [] };
  },
  watch: {
    value(newValue) {
      this.inputValue = newValue;
    },
  },
  methods: {
    updateValue() {
      this.inputValue = this.value; // called by ui.input when the server sets the value
    },
    filter(text, update) {
      update(() => {
        const needle = fold(text);
        const options = this._autocomplete || [];
        this.filtered = (needle ? options.filter((o) => fold(o).includes(needle)) : options).slice(0, 8);
      });
    },
    set(value) {
      if (value === this.inputValue) return;
      this.inputValue = value;
      this.$emit("update:value", value);
    },
  },
};
