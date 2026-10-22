"""Tag widgets (F10): the chip input with autocomplete, coloured chips, the colour picker and the grid renderer."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from nicegui import ui
from nicegui.events import GenericEventArguments

from budget_app.domain import tags as tag_codec
from budget_app.domain.models import TAG_COLOURS, Tag
from budget_app.i18n import t
from budget_app.services import tags as tag_svc
from budget_app.services.context import Context

NEW = "new"  # the chip class of a name typed new: it becomes a tag when the record is saved

# Quasar's chip for each chosen tag, in the tag's colour; the cross removes it again
_CHIP = """
<q-chip dense removable :tabindex="props.tabindex" :class="'tag-chip tag-' + props.opt.colour"
  @remove="props.removeAtIndex(props.index)">{{ props.opt.label }}</q-chip>
"""
_OPTION = """
<q-item v-bind="props.itemProps">
  <q-item-section avatar style="min-width: 0; padding-right: 10px">
    <span :class="'tag-dot tag-' + props.opt.colour"></span>
  </q-item-section>
  <q-item-section><q-item-label>{{ props.opt.label }}</q-item-label></q-item-section>
</q-item>
"""

# AG Grid cell: the row's `_tags` ([name, colour] pairs) as chips, built as elements so names are never HTML
CHIPS_RENDERER = (
    "p => { const box = document.createElement('span'); box.className = 'tag-chips';"
    " for (const [name, colour] of (p.data && p.data._tags) || []) {"
    " const chip = document.createElement('span'); chip.className = 'tag-chip tag-' + colour;"
    " chip.textContent = name; box.appendChild(chip); } return box; }"
)


class TagSelect(ui.select):
    """A multiple select of tags whose chosen tags show as coloured chips. Its value is a list of tag ids; with
    `allow_new`, a name typed and confirmed with Enter is added as it is (a new tag once the record is saved)."""

    def __init__(
        self,
        ctx: Context,
        value: Iterable[str] = (),
        *,
        label: str,
        on_change: Callable | None = None,
        allow_new: bool = True,
        only: Iterable[str] | None = None,
    ) -> None:
        self._colours: dict[str, str] = {}
        self._only = set(only) if only is not None else None
        opts = self._tag_options(ctx)
        super().__init__(
            opts,
            label=label,
            value=[v for v in value if v in opts],
            multiple=True,
            with_input=True,
            new_value_mode="add-unique" if allow_new else None,
            on_change=on_change,
        )
        self.props("dense outlined options-dense hide-dropdown-icon")
        self.add_slot("selected-item", _CHIP)
        self.add_slot("option", _OPTION)

    def _tag_options(self, ctx: Context) -> dict[str, str]:
        tags = [g for g in tag_svc.all_tags(ctx) if self._only is None or g.id in self._only]
        self._colours = {g.id: tag_svc.colour(g) for g in tags}
        return {g.id: g.name for g in tags}

    def refresh_tags(self, ctx: Context) -> None:
        """Reload the tags (after one was added, renamed or deleted), keeping the chosen ones that still exist."""
        before = (dict(self.options), dict(self._colours))
        opts = self._tag_options(ctx)
        if (opts, self._colours) != before:
            self.set_options(opts, value=[v for v in self.value or [] if v in opts])

    def _colour(self, value) -> str:
        return getattr(self, "_colours", {}).get(value, NEW)

    def _update_options(self) -> None:
        super()._update_options()
        self._props["options"] = [
            opt | {"colour": self._colour(self._values[opt["value"]])} for opt in self._props["options"]
        ]

    def _value_to_model_value(self, value):
        model = super()._value_to_model_value(value)
        return [m | {"colour": self._colour(self._values[m["value"]])} for m in model]

    def _event_args_to_value(self, e: GenericEventArguments):
        """A name typed and confirmed with Enter that is already a tag (ignoring case and accents) picks that tag;
        Quasar only matches the exact label, and NiceGUI would drop the name."""
        if isinstance(e.args, list):
            known = {tag_codec.key(label): i for i, label in enumerate(self._labels)}
            args: list = []
            for sent in e.args:
                arg = sent
                if isinstance(sent, str) and tag_codec.key(sent) in known:
                    i = known[tag_codec.key(sent)]
                    arg = {"value": i, "label": self._labels[i]}
                chosen = {a["value"] for a in args if isinstance(a, dict)}
                if not (isinstance(arg, dict) and arg["value"] in chosen):
                    args.append(arg)
            e.args = args
        return super()._event_args_to_value(e)


def tag_select(ctx: Context, value: Iterable[str] = (), on_change: Callable | None = None, **kw) -> TagSelect:
    """The Tags field of the forms: existing tags to pick, a new name to type."""
    return TagSelect(ctx, value, label=kw.pop("label", t("Tags")), on_change=on_change, **kw).classes("w-full")


def chip(tag: Tag, on_click: Callable | None = None) -> ui.label:
    """A tag as a small chip in its colour."""
    el = ui.label(tag.name).classes(f"tag-chip tag-{tag_svc.colour(tag)}")
    if on_click is not None:
        el.on("click", on_click).classes("cursor-pointer")
    return el


def grid_cells(tags: list[Tag]) -> list[list[str]]:
    """The `_tags` of a grid row: [name, colour] per tag, for `CHIPS_RENDERER`."""
    return [[g.name, tag_svc.colour(g)] for g in tags]


class ColourPicker:
    """One round swatch per palette colour; the chosen one has a ring."""

    def __init__(self, value: str) -> None:
        self.value = value if value in TAG_COLOURS else "grey"
        self._swatches: dict[str, ui.button] = {}
        with ui.row().classes("gap-3 items-center px-1"):
            for key, name in TAG_COLOURS.items():
                swatch = ui.button(on_click=lambda k=key: self.set_value(k)).props(
                    f'flat round dense aria-label="{t(name)}"'
                )
                swatch.classes(f"tag-swatch tag-{key}").tooltip(t(name))
                self._swatches[key] = swatch
        self._mark()

    def set_value(self, key: str) -> None:
        self.value = key
        self._mark()

    def _mark(self) -> None:
        for key, swatch in self._swatches.items():
            if key == self.value:
                swatch.classes(add="selected")
            else:
                swatch.classes(remove="selected")
