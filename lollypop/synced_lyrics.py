#!/usr/bin/env python3
"""Apple Music-style synced lyrics for the Now Playing panel.

The parser accepts ordinary LRC (``[mm:ss.xx]``), multiple timestamps per
line, ``[offset:...]`` metadata and the enhanced LRC form with
``<mm:ss.xx>`` word tags.  The renderer is a vertical list of Gtk.Label rows;
the active row is redrawn with a clipped accent-coloured copy of its Pango
layout, which produces a smooth left-to-right fill even when a line wraps.

The module deliberately has no dependency on Lollypop or on the rest of the
extension, so it can be exercised on its own.
"""

from __future__ import annotations

import bisect
import math
import re
import time
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

DEFAULT_ACCENT = (0.65, 0.55, 0.98)
SCROLL_PAUSE_SECONDS = 4.0
SCROLL_DURATION = 0.32
LAST_LINE_FALLBACK_MS = 6000

_TIME_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_TAG = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
_META_TAG = re.compile(r"^\s*\[([A-Za-z][A-Za-z0-9_-]*):([^\]]*)\]\s*$")

_GAP_CHARS = "♪♫🎵🎶 \t"


# ---------------------------------------------------------------- LRC parsing


@dataclass(frozen=True)
class LyricLine:
    """One timed lyric line.

    ``words`` holds ``(start_ms, char_start, char_end)`` tuples for enhanced
    LRC files that carry word-level timings.  Char ranges index into
    ``text``.
    """

    start_ms: int
    text: str
    words: tuple[tuple[int, int, int], ...] = ()

    @property
    def is_gap(self):
        stripped = self.text.strip()
        return not stripped or all(char in _GAP_CHARS for char in stripped)


def _timestamp_ms(minutes, seconds, fraction):
    value = int(minutes) * 60000 + int(seconds) * 1000
    if fraction:
        value += int(fraction.ljust(3, "0")[:3])
    return value


def _tag_ms(match):
    return _timestamp_ms(match.group(1), match.group(2), match.group(3))


def _split_words(raw, line_ms):
    """Return (text, word_ranges) with inline ``<mm:ss.xx>`` tags removed."""
    tags = list(_WORD_TAG.finditer(raw))
    if not tags:
        return raw.strip(), ()

    parts = []
    words = []
    current_ms = line_ms
    last_end = 0
    char_pos = 0

    for match in tags:
        segment = raw[last_end:match.start()]
        if segment:
            parts.append(segment)
            words.append((current_ms, char_pos, char_pos + len(segment)))
            char_pos += len(segment)
        current_ms = _tag_ms(match)
        last_end = match.end()

    tail = raw[last_end:]
    if tail:
        parts.append(tail)
        words.append((current_ms, char_pos, char_pos + len(tail)))
        char_pos += len(tail)

    plain = "".join(parts)
    leading = len(plain) - len(plain.lstrip())
    plain = plain.strip()

    if leading and words:
        adjusted = []
        for start, char_start, char_end in words:
            char_start -= leading
            char_end -= leading
            if char_end > 0:
                adjusted.append((start, max(0, char_start), char_end))
        words = adjusted

    # Defensive: word timestamps should progress forwards with the text.
    monotonic = []
    previous = -1
    for start, char_start, char_end in words:
        start = max(start, previous)
        previous = start
        monotonic.append((start, char_start, char_end))

    return plain, tuple(monotonic)


def parse_lrc(text):
    """Parse LRC text into a sorted, de-duplicated list of LyricLine."""
    if not text:
        return []

    raw_entries = []
    offset = 0

    for raw_line in str(text).splitlines():
        line = raw_line.rstrip("\r")

        meta = _META_TAG.match(line)
        if meta:
            if meta.group(1).lower() == "offset":
                try:
                    offset = int(meta.group(2).strip())
                except (TypeError, ValueError):
                    pass
            continue

        tags = list(_TIME_TAG.finditer(line))
        if not tags:
            continue

        content = line[tags[-1].end():]
        first_start = _tag_ms(tags[0])
        plain, words = _split_words(content, first_start)

        for tag in tags:
            start = _tag_ms(tag)
            entry_words = ()
            if words:
                shift = start - first_start
                entry_words = tuple(
                    (max(0, word_start + shift), char_start, char_end)
                    for word_start, char_start, char_end in words
                )
            raw_entries.append((start, plain, entry_words))

    entries = []
    for start, plain, words in raw_entries:
        start = max(0, start + offset)
        if words:
            words = tuple(
                (max(0, word_start + offset), char_start, char_end)
                for word_start, char_start, char_end in words
            )
        entries.append(LyricLine(start, plain, words))

    entries.sort(key=lambda entry: entry.start_ms)

    # Several timestamps for the same text (e.g. a repeated chorus) are
    # combined so the scroll list has one row per instant.
    merged = []
    for entry in entries:
        if merged and merged[-1].start_ms == entry.start_ms:
            previous = merged[-1]
            if entry.text:
                text = (
                    previous.text + "\n" + entry.text
                    if previous.text
                    else entry.text
                )
            else:
                text = previous.text
            merged[-1] = LyricLine(entry.start_ms, text, ())
        else:
            merged.append(entry)

    return merged


def has_synced_lyrics(text):
    """True when the text contains at least two usable timestamped lines."""
    return len(parse_lrc(text)) >= 2


def plain_lyrics(text):
    """Strip LRC metadata/timestamps and return displayable plain lyrics."""
    lines = []
    for raw_line in str(text or "").splitlines():
        if _META_TAG.match(raw_line):
            continue
        line = _WORD_TAG.sub("", raw_line)
        line = _TIME_TAG.sub("", line).strip()
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _line_fraction(line, position_ms, line_end_ms):
    """Character progress through a line, honouring word timings if present."""
    total = len(line.text)
    if total <= 0:
        return 0.0

    if line.words:
        words = line.words
        if position_ms <= words[0][0]:
            return 0.0
        for index, (start, char_start, char_end) in enumerate(words):
            next_start = (
                words[index + 1][0]
                if index + 1 < len(words)
                else line_end_ms
            )
            if position_ms < next_start or index == len(words) - 1:
                if position_ms <= start:
                    return char_start / total
                span = max(1, next_start - start)
                fraction = max(
                    0.0, min(1.0, (position_ms - start) / span)
                )
                return (char_start + (char_end - char_start) * fraction) / total
        return 1.0

    span = max(1, line_end_ms - line.start_ms)
    return max(0.0, min(1.0, (position_ms - line.start_ms) / span))


# ---------------------------------------------------------------- helpers


def _normalise_accent(accent):
    if not accent:
        return DEFAULT_ACCENT
    try:
        values = tuple(float(value) for value in accent[:3])
    except (TypeError, ValueError):
        return DEFAULT_ACCENT
    if max(values) > 1.0:
        values = tuple(value / 255.0 for value in values)
    return tuple(max(0.0, min(1.0, value)) for value in values)


def _hex_color(accent):
    red, green, blue = (
        max(0, min(255, int(round(value * 255))))
        for value in accent
    )
    return f"#{red:02x}{green:02x}{blue:02x}"


def _gap_markup(accent, active=False, phase=0.0):
    color = _hex_color(accent)
    spans = []
    for index in range(3):
        if active:
            wave = 0.5 + 0.5 * math.sin(phase - index * 1.7)
            alpha = 0.22 + 0.78 * wave
        else:
            alpha = 0.28
        spans.append(
            '<span foreground="{}" alpha="{}%">●</span>'.format(
                color, int(round(alpha * 100))
            )
        )
    return " ".join(spans)


# ---------------------------------------------------------------- widgets


class _FillLabel(Gtk.Label):
    """A wrapping Gtk.Label with an accent-coloured karaoke overlay.

    GTK draws the base text (dim white), then this widget redraws the exact
    same Pango layout clipped to the sung characters.  Clipping happens per
    visual row, so a wrapped lyric fills the first row before the second.
    """

    def __init__(self, text, accent, xalign=0.0):
        super().__init__(label=text)
        self._accent = accent
        self._char_count = len(text)
        self._fill_chars = 0
        self._active = False

        # xalign=0.5 centres the panel lyrics; the fullscreen view passes 0.0
        # to keep its own left-aligned column untouched.
        self.set_xalign(xalign)
        self.set_yalign(0)
        self.set_line_wrap(True)
        self.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.set_hexpand(True)
        self.get_style_context().add_class("lyrics-line")

    def set_accent(self, accent):
        self._accent = accent
        self.queue_draw()

    def set_active(self, active):
        if self._active == active:
            return
        self._active = active
        if not active:
            self._fill_chars = 0
        self.queue_draw()

    def set_progress(self, fraction):
        chars = int(round(
            max(0.0, min(1.0, fraction)) * self._char_count
        ))
        if chars == self._fill_chars:
            return
        self._fill_chars = chars
        self.queue_draw()

    def do_draw(self, cr):
        handled = Gtk.Label.do_draw(self, cr)

        if not self._active or self._fill_chars <= 0:
            return handled

        layout = self.get_layout()
        text = self.get_text()
        if layout is None or not text:
            return handled

        char_pos = min(self._fill_chars, len(text))
        byte_pos = len(text[:char_pos].encode("utf-8"))
        offset_x, offset_y = self.get_layout_offsets()

        cr.save()
        cr.set_source_rgba(*self._accent, 1.0)

        for index in range(layout.get_line_count()):
            line = layout.get_line(index)
            line_start = line.start_index
            line_end = line_start + line.length

            if byte_pos <= line_start:
                continue

            rect = layout.index_to_pos(line_start)
            line_x = rect.x / Pango.SCALE
            line_y = rect.y / Pango.SCALE
            line_height = rect.height / Pango.SCALE + 2
            line_width = line.index_to_x(line.length, False) / Pango.SCALE

            if byte_pos >= line_end:
                fill_x = line_x + line_width
            else:
                in_line = byte_pos - line_start
                fill_x = (
                    line_x
                    + line.index_to_x(in_line, False) / Pango.SCALE
                )

            clip_start = line_x
            clip_end = fill_x
            if line.get_resolved_direction() == Pango.Direction.RTL:
                clip_start = fill_x
                clip_end = line_x + line_width

            if clip_end <= clip_start:
                continue

            cr.save()
            cr.translate(offset_x, offset_y)
            cr.rectangle(
                clip_start, line_y - 2,
                clip_end - clip_start, line_height + 4,
            )
            cr.clip()
            PangoCairo.show_layout(cr, layout)
            cr.restore()

        cr.restore()
        return handled


class _GapLabel(Gtk.Label):
    """Three pulsing dots used for instrumental or empty timed lines."""

    def __init__(self, accent, xalign=0.0):
        super().__init__()
        self._accent = accent
        self._active = False
        self._phase = 0.0
        self.set_xalign(xalign)
        self.get_style_context().add_class("lyrics-dots")
        self._refresh()

    def _refresh(self):
        self.set_markup(
            _gap_markup(self._accent, self._active, self._phase)
        )

    def set_accent(self, accent):
        self._accent = accent
        self._refresh()

    def set_active(self, active):
        if self._active == active:
            return
        self._active = active
        self._refresh()

    def set_phase(self, phase):
        self._phase = phase
        self._refresh()


class LyricRow(Gtk.EventBox):
    """One clickable lyric row."""

    def __init__(self, line, accent, on_seek=None, xalign=0.0):
        super().__init__()
        self.line = line
        self._accent = accent
        self._on_seek = on_seek
        self._state = "future"

        self.get_style_context().add_class("lyrics-row")
        self.set_can_focus(False)
        self.add_events(
            Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )

        if line.is_gap:
            self.label = _GapLabel(accent, xalign)
        else:
            self.label = _FillLabel(line.text, accent, xalign)

        self.add(self.label)
        self.connect("button-release-event", self._on_button_release)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("realize", self._on_realize)
        self.show_all()

    def set_accent(self, accent):
        self._accent = accent
        self.label.set_accent(accent)

    def set_state(self, state):
        if state == self._state:
            return
        self._state = state

        context = self.label.get_style_context()
        context.remove_class("active")
        context.remove_class("past")
        if state == "active":
            context.add_class("active")
        elif state == "past":
            context.add_class("past")

        self.label.set_active(state == "active")

    def set_progress(self, fraction):
        if isinstance(self.label, _FillLabel):
            self.label.set_progress(fraction)

    def set_gap_phase(self, position_ms):
        if isinstance(self.label, _GapLabel):
            self.label.set_phase(position_ms / 420.0)

    def _on_button_release(self, _widget, event):
        if event.button == 1 and self._on_seek is not None:
            self._on_seek(self.line.start_ms)
            return True
        return False

    def _on_enter(self, *_args):
        self.get_style_context().add_class("hover")

    def _on_leave(self, *_args):
        self.get_style_context().remove_class("hover")

    def _on_realize(self, *_args):
        try:
            display = self.get_display()
            window = self.get_window()
            if display is not None and window is not None:
                cursor = Gdk.Cursor.new_from_name(display, "pointer")
                window.set_cursor(cursor)
        except Exception:
            pass


class SyncedLyricsView(Gtk.Box):
    """Scrollable lyric list with progressive fill and auto-centring."""

    def __init__(self, on_seek=None, fullscreen=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_seek = on_seek
        self._fullscreen = bool(fullscreen)
        # Panel lyrics are centred (Apple Music now-playing style); the
        # fullscreen view keeps its left-aligned column to match the metadata
        # column its own window lays out.
        self._xalign = 0.0 if self._fullscreen else 0.5
        self._accent = DEFAULT_ACCENT
        self._entries = []
        self._rows = []
        self._starts = []
        self._synced = False
        self._active = -1
        self._duration_ms = 0
        self._first_sync = True
        self._follow_paused_until = 0.0
        self._programmatic = False
        self._scroll_source = 0
        self._anim_source = 0
        self._scroll_token = 0

        self.get_style_context().add_class("synced-lyrics-view")
        if self._fullscreen:
            self.get_style_context().add_class("fullscreen")
            self.set_name("fullscreen-lyrics-view")

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._scroller.set_min_content_height(
            240 if self._fullscreen else 110
        )
        self._scroller.set_hexpand(True)
        self._scroller.set_vexpand(True)
        try:
            self._scroller.set_kinetic_scrolling(True)
        except Exception:
            pass

        self._box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4 if self._fullscreen else 2,
        )
        self._box.set_hexpand(True)
        self._box.set_valign(Gtk.Align.START)
        # Keep the last glyphs clear of the overlay scrollbar.  When the text
        # is centred we add a matching start margin so the centre axis is not
        # nudged left by the gutter.
        self._box.set_margin_end(8)
        if self._xalign == 0.5:
            self._box.set_margin_start(8)
        self._scroller.add(self._box)

        self.pack_start(self._scroller, True, True, 0)
        self._scroller.get_vadjustment().connect(
            "value-changed", self._on_value_changed
        )
        self.show_all()

    # ---- public API ----------------------------------------------------

    def set_accent(self, accent):
        self._accent = _normalise_accent(accent)
        for row in self._rows:
            row.set_accent(self._accent)
        if self._synced and 0 <= self._active < len(self._rows):
            self._rows[self._active].set_gap_phase(0.0)

    def set_message(self, text):
        self._clear()
        if text:
            label = Gtk.Label(label=text)
            label.set_name(
                "fullscreen-lyrics-message"
                if self._fullscreen else "right-panel-lyrics"
            )
            label.get_style_context().add_class("lyrics-message")
            label.set_halign(
                Gtk.Align.CENTER if self._xalign == 0.5 else Gtk.Align.START
            )
            label.set_xalign(self._xalign)
            label.set_yalign(0)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_hexpand(True)
            self._box.pack_start(label, False, False, 0)
            label.show()

    def set_lyrics(self, text):
        entries = parse_lrc(text)
        if len(entries) < 2:
            self.set_message(plain_lyrics(text) or "No lyrics found.")
            return

        self._clear()
        self._synced = True
        self._entries = entries
        self._starts = [entry.start_ms for entry in entries]
        self._rows = []
        # New track: drop any duration carried over from the previous one so
        # the last line's end time cannot be computed from a stale value.
        self._duration_ms = 0

        for entry in entries:
            row = LyricRow(entry, self._accent, self._on_seek, self._xalign)
            self._box.pack_start(row, False, False, 0)
            self._rows.append(row)

        self._active = -2
        self._first_sync = True
        self._follow_paused_until = 0.0
        self._box.show_all()
        self._set_adjustment(0)

    def set_position(self, position_ms, duration_ms=None):
        if not self._synced or not self._rows:
            return

        position_ms = max(0, int(position_ms or 0))
        if duration_ms:
            self._duration_ms = max(0, int(duration_ms))

        index = bisect.bisect_right(self._starts, position_ms) - 1
        if index != self._active:
            self._set_active(index)

        if index < 0:
            return

        start = self._starts[index]
        if index + 1 < len(self._starts):
            end = self._starts[index + 1]
        elif self._duration_ms > start:
            end = self._duration_ms
        else:
            end = start + LAST_LINE_FALLBACK_MS
        if end <= start:
            end = start + 1000

        row = self._rows[index]
        if self._entries[index].is_gap:
            row.set_gap_phase(position_ms)
        else:
            row.set_progress(
                _line_fraction(self._entries[index], position_ms, end)
            )

    def scroll_to_current(self, animate=False):
        if not self._synced:
            return
        if self._active >= 0:
            self._schedule_scroll(self._active, animate)
        else:
            self._schedule_top(animate)

    # ---- internals -----------------------------------------------------

    def _clear(self):
        self._cancel_scroll()
        self._synced = False
        self._active = -1
        self._entries = []
        self._rows = []
        self._starts = []
        self._first_sync = True
        self._follow_paused_until = 0.0
        for child in list(self._box.get_children()):
            self._box.remove(child)

    def _set_active(self, index):
        if index == self._active:
            return
        self._active = index

        for row_index, row in enumerate(self._rows):
            if row_index == index:
                row.set_state("active")
            elif row_index < index:
                row.set_state("past")
            else:
                row.set_state("future")

        if index < 0:
            self._schedule_top(animate=not self._first_sync)
            return

        if time.monotonic() < self._follow_paused_until:
            return
        animate = not self._first_sync
        self._first_sync = False
        self._schedule_scroll(index, animate)

    def _on_value_changed(self, _adjustment):
        if self._programmatic or not self._synced:
            return
        self._follow_paused_until = time.monotonic() + SCROLL_PAUSE_SECONDS
        self._cancel_scroll()

    def _set_adjustment(self, value):
        adjustment = self._scroller.get_vadjustment()
        self._programmatic = True
        try:
            adjustment.set_value(value)
        finally:
            self._programmatic = False

    def _cancel_scroll(self):
        for attribute in ("_scroll_source", "_anim_source"):
            source = getattr(self, attribute, 0)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(self, attribute, 0)
        self._scroll_token += 1

    def _schedule_scroll(self, index, animate):
        self._cancel_scroll()
        self._scroll_token += 1
        token = self._scroll_token
        attempts = [0]

        def measure():
            self._scroll_source = 0
            if token != self._scroll_token or not self._synced:
                return False
            if index >= len(self._rows):
                return False

            row = self._rows[index]
            row_height = row.get_allocated_height()
            if (
                self._scroller.get_allocated_height() <= 1
                or row_height <= 1
            ):
                attempts[0] += 1
                if attempts[0] < 20:
                    self._scroll_source = GLib.timeout_add(25, measure)
                return False

            adjustment = self._scroller.get_vadjustment()
            target = (
                row.get_allocation().y
                + row_height / 2
                - adjustment.get_page_size() / 2
            )
            maximum = max(
                0.0,
                adjustment.get_upper() - adjustment.get_page_size(),
            )
            target = max(0.0, min(target, maximum))

            # A tiny scroll leaves the first row sliced in half directly
            # under the LYRICS heading.  Prefer showing it whole and let the
            # active line sit at most one row below the exact centre.
            if target < row_height:
                target = 0.0

            if animate:
                self._animate_to(target)
            else:
                self._set_adjustment(target)
            return False

        self._scroll_source = GLib.timeout_add(25, measure)

    def _schedule_top(self, animate):
        self._cancel_scroll()

        if animate:
            self._animate_to(0.0)
        else:
            self._set_adjustment(0.0)

    def _animate_to(self, target):
        self._cancel_scroll()
        adjustment = self._scroller.get_vadjustment()
        start_value = adjustment.get_value()
        delta = target - start_value

        if abs(delta) < 1.0:
            self._set_adjustment(target)
            return

        started = time.monotonic()

        def step():
            progress = (time.monotonic() - started) / SCROLL_DURATION
            if progress >= 1.0:
                self._set_adjustment(target)
                self._anim_source = 0
                return False
            eased = 1.0 - (1.0 - progress) ** 3
            self._set_adjustment(start_value + delta * eased)
            return True

        self._anim_source = GLib.timeout_add(16, step)


# ---------------------------------------------------------------- demo

if __name__ == "__main__":
    import sys

    text = """[00:00.00]Instrumental intro
[00:04.00]First line of the demo song
[00:07.50]A second line that is long enough to wrap inside a narrow panel
[00:11.00]
[00:14.00]<00:14.00>Word-level <00:15.00>timings <00:16.00>work too
[00:20.00]Last line
"""
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as handle:
            text = handle.read()

    window = Gtk.Window(title="Synced lyrics demo")
    window.set_default_size(340, 500)
    window.connect("destroy", Gtk.main_quit)

    view = SyncedLyricsView()
    view.set_lyrics(text)
    view.set_accent((176, 156, 236))
    window.add(view)
    window.show_all()

    position = [0]

    def tick():
        view.set_position(position[0], 23000)
        position[0] += 100
        if position[0] > 24000:
            position[0] = 0
        return True

    GLib.timeout_add(100, tick)
    Gtk.main()
