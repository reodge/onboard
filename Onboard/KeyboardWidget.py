# -*- coding: utf-8 -*-
""" GTK keyboard widget """

from __future__ import division, print_function, unicode_literals

import sys
import time
from math import sin, pi

from gi.repository          import GLib, Gdk, Gtk

from Onboard.TouchInput     import TouchInput, InputSequence
from Onboard.Keyboard       import EventType
from Onboard.KeyboardPopups import LayoutPopup, \
                                   LayoutBuilderAlternatives, \
                                   LayoutBuilder
from Onboard.KeyGtk         import Key
from Onboard.KeyCommon      import LOD
from Onboard.TouchHandles   import TouchHandles
from Onboard.LayoutView     import LayoutView
from Onboard.AutoShow       import AutoShow
from Onboard.utils          import Rect, Timer, FadeTimer
from Onboard.definitions    import Handle
from Onboard.WindowUtils    import WindowManipulator, WindowRectTracker, \
                                   canvas_to_root_window_rect, \
                                   canvas_to_root_window_point, \
                                   physical_to_monitor_pixel_size, \
                                   get_monitor_dimensions

### Logging ###
import logging
_logger = logging.getLogger("KeyboardWidget")
###############

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

# prepare mask for faster access
BUTTON123_MASK = Gdk.ModifierType.BUTTON1_MASK | \
                 Gdk.ModifierType.BUTTON2_MASK | \
                 Gdk.ModifierType.BUTTON3_MASK

class AutoReleaseTimer(Timer):
    """
    Releases latched and locked modifiers after a period of inactivity.
    Inactivity here means no keys are pressed.
    """
    _keyboard = None

    def __init__(self, keyboard):
        self._keyboard = keyboard

    def start(self, visibility_change = None):
        self.stop()
        delay = config.keyboard.sticky_key_release_delay
        if visibility_change == False:
            hide_delay = config.keyboard.sticky_key_release_on_hide_delay
            if hide_delay:
                if delay:
                    delay = min(delay, hide_delay)
                else:
                    delay = hide_delay
        if delay:
            Timer.start(self, delay)

    def on_timer(self):
        # When sticky_key_release_delay is set, release NumLock too.
        # We then assume Onboard is used in a kiosk setting, and
        # everything has to be reset for the next customer.
        release_all_keys = bool(config.keyboard.sticky_key_release_delay)
        if release_all_keys:
            config.word_suggestions.set_pause_learning(0)
        self._keyboard.release_latched_sticky_keys()
        self._keyboard.release_locked_sticky_keys(release_all_keys)
        self._keyboard.active_layer_index = 0
        self._keyboard.invalidate_ui_no_resize()
        self._keyboard.commit_ui_updates()
        return False


class InactivityTimer(Timer):
    """
    Waits for the inactivity delay and transitions between
    active and inactive state.
    Inactivity here means, the pointer has left the keyboard window
    """
    _keyboard = None
    _active = False

    def __init__(self, keyboard):
        self._keyboard = keyboard

    def is_enabled(self):
        window = self._keyboard.get_kbd_window()
        if not window:
            return False
        screen = window.get_screen()
        return screen and  screen.is_composited() and \
               config.is_inactive_transparency_enabled() and \
               config.window.enable_inactive_transparency and \
               not config.xid_mode

    def is_active(self):
        return self._active

    def begin_transition(self, active):
        self._active = active
        if active:
            Timer.stop(self)
            if self._keyboard.transition_active_to(True):
                self._keyboard.commit_transition()
        else:
            if not config.xid_mode:
                Timer.start(self, config.window.inactive_transparency_delay)

    def on_timer(self):
        self._keyboard.transition_active_to(False)
        self._keyboard.commit_transition()
        return False

class HideInputLineTimer(Timer):
    """
    Temporarily hides the input line when the pointer touches it.
    """
    def __init__(self, keyboard):
        self._keyboard = keyboard

    def handle_motion(self, sequence):
        """
        Handle pointer motion.
        """
        point = sequence.point

        # Hide inputline when the pointer touches it.
        # Show it again when leaving the area.
        for key in self._keyboard.get_text_displays():
            rect = key.get_canvas_border_rect()
            if rect.is_point_within(point):
                if not self.is_running():
                    self.start(0.3)
            else:
                self.stop()
                self._keyboard.hide_input_line(False)

    def on_timer(self):
        """ Hide the input line after delay """
        self._keyboard.hide_input_line(True)
        return False


class TransitionVariable:
    """ A variable taking part in opacity transitions """

    value        = 0.0
    start_value  = 0.0
    target_value = 0.0
    start_time   = 0.0
    duration     = 0.0
    done         = False

    def start_transition(self, target, duration):
        """ Begin transition """
        self.start_value = self.value
        self.target_value = target
        self.start_time = time.time()
        self.duration = duration
        self.done = False

    def update(self):
        """
        Update self.value based on the elapsed time since start_transition.
        """
        range = self.target_value - self.start_value
        if range and self.duration:
            elapsed  = time.time() - self.start_time
            lin_progress = min(1.0, elapsed / self.duration)
        else:
            lin_progress = 1.0
        sin_progress = (sin(lin_progress * pi - pi / 2.0) + 1.0) / 2.0
        self.value = self.start_value + sin_progress * range
        self.done = lin_progress >= 1.0


class TransitionState:
    """ Set of all state variables involved in opacity transitions. """

    def __init__(self):
        self.visible = TransitionVariable()
        self.active  = TransitionVariable()
        self.x       = TransitionVariable()
        self.y       = TransitionVariable()
        self._vars = [self.visible, self.active, self.x, self.y]

        self.target_visibility = False

    def update(self):
        for var in self._vars:
            var.update()

    def is_done(self):
        return all(var.done for var in self._vars)

    def get_max_duration(self):
        return max(x.duration for x in self._vars)


class KeyboardWidget(Gtk.DrawingArea, WindowRectTracker, WindowManipulator, LayoutView, TouchInput):

    TRANSITION_DURATION_MOVE = 0.25
    TRANSITION_DURATION_SLIDE = 0.25
    TRANSITION_DURATION_OPACITY_HIDE = 0.3

    def __init__(self, keyboard):
        Gtk.DrawingArea.__init__(self)
        WindowManipulator.__init__(self)
        LayoutView.__init__(self, keyboard)
        TouchInput.__init__(self)

        self.canvas_rect = Rect()

        self._last_click_time = 0
        self._last_click_key = None

        self._outside_click_timer = Timer()
        self._outside_click_detected = False
        self._outside_click_start_time = None

        self._long_press_timer = Timer()
        self._auto_release_timer = AutoReleaseTimer(keyboard)
        self._key_popup = None

        self.dwell_timer = None
        self.dwell_key = None
        self.last_dwelled_key = None

        self.inactivity_timer = InactivityTimer(self)

        self.auto_show = AutoShow(self)
        self.auto_show.enable(config.is_auto_show_enabled())

        self.touch_handles = TouchHandles()
        self.touch_handles_hide_timer = Timer()
        self.touch_handles_fade = FadeTimer()
        self.touch_handles_auto_hide = True

        self._aspect_ratio = None

        self._hide_input_line_timer = HideInputLineTimer(keyboard)

        self._transition_timer = Timer()
        self._transition_state = TransitionState()
        self._transition_state.visible.value = 0.0
        self._transition_state.active.value = 1.0
        self._transition_state.x.value = 0.0
        self._transition_state.y.value = 0.0

        self._configure_timer = Timer()

        self._language_menu = LanguageMenu(self)

        #self.set_double_buffered(False)
        self.set_app_paintable(True)

        # no tooltips when embedding, gnome-screen-saver flickers (Oneiric)
        if not config.xid_mode:
            self.set_has_tooltip(True) # works only at window creation -> always on

        self.connect("parent-set",           self._on_parent_set)
        self.connect("draw",                 self._on_draw)
        self.connect("query-tooltip",        self._on_query_tooltip)
        self.connect("configure-event",      self._on_configure_event)

        self._update_double_click_time()

        self.show()

    def cleanup(self):

        # Enter-notify isn't called when resizing without crossing into
        # the window again. Do it here on exit, at the latest, to make sure
        # the home_rect is updated before is is saved later.
        self.stop_system_drag()

        # stop timer callbacks for unused, but not yet destructed keyboards
        self.touch_handles_fade.stop()
        self.touch_handles_hide_timer.stop()
        self._transition_timer.stop()
        self.inactivity_timer.stop()
        self._long_press_timer.stop()
        self._auto_release_timer.stop()
        self.auto_show.cleanup()
        self.stop_click_polling()
        self._configure_timer.stop()
        self.close_key_popup()

        # free xserver memory
        self.invalidate_keys()
        self.invalidate_shadows()

        LayoutView.cleanup(self)
        TouchInput.cleanup(self)

    def on_layout_loaded(self):
        """ called when the layout has been loaded """
        LayoutView.on_layout_loaded(self)

    def _on_parent_set(self, widget, old_parent):
        win = self.get_kbd_window()
        if win:
            self.touch_handles.set_window(win)
            self.update_resize_handles()

    def set_startup_visibility(self):
        win = self.get_kbd_window()
        assert(win)

        # Show the keyboard when turning off auto-show.
        # Hide the keyboard when turning on auto-show.
        #   (Fix this when we know how to get the active accessible)
        # Hide the keyboard on start when start-minimized is set.
        # Start with active transparency if the inactivity_timer is enabled.
        #
        # start_minimized            False True  False True
        # auto_show                  False False True  True
        # --------------------------------------------------
        # window visible on start    True  False False False

        visible = config.is_visible_on_start()

        # Start with low opacity to stop opacity flashing
        # when inactive transparency is enabled.
        screen = self.get_screen()
        if screen and screen.is_composited() and \
            self.inactivity_timer.is_enabled():
            win.set_opacity(0.05, True) # keep it slightly visible just in case

        # transition to initial opacity
        self.transition_visible_to(visible, 0.0, 0.4)
        self.transition_active_to(True, 0.0)
        self.commit_transition()

        # kick off inactivity timer, i.e. inactivate on timeout
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)

        # Be sure to initially show/hide window and icon palette
        win.set_visible(visible)

    def pre_render_keys(self, window, w, h):
        if self.is_new_layout_size(w, h):
            self.update_layout(Rect(0, 0, w, h))

            self.invalidate_for_resize()

            win = window.get_window()
            if win:
                context = win.cairo_create()
                self.render(context)

    def is_new_layout_size(self, w, h):
        return self.canvas_rect.w != w or \
               self.canvas_rect.h != h

    def update_layout(self, canvas_rect = None):
        layout = self.get_layout()
        if not layout:
            return

        # recalculate item rectangles
        if canvas_rect is None:
            self.canvas_rect = Rect(0, 0,
                                    self.get_allocated_width(),
                                    self.get_allocated_height())
        else:
            self.canvas_rect = canvas_rect

        rect = self.canvas_rect.deflate(self.get_frame_width())
        keep_aspect = config.is_keep_aspect_ratio_enabled()

        x_align = 0.5
        aspect_change_range = (0, 100)
        if keep_aspect:
            if config.xid_mode:
                value = config.system_defaults.get("xembed_aspect_change_range")
                if not value is None:
                    value = value[1:-1]
                    begin, end = value.split(",")
                    aspect_change_range = (float(begin), float(end))

                if config.launched_by == config.LAUNCHER_UNITY_GREETER:
                    offset = config.system_defaults.get("xembed_unity_greeter_offset_x")
                    if not offset is None:
                        try:
                            offset = float(offset)
                            rect.x += offset
                            rect.w -= offset
                            x_align = 0.0
                        except ValueError: pass

        layout.fit_inside_canvas(rect, keep_aspect,
                                 aspect_change_range=aspect_change_range,
                                 x_align=x_align, y_align=0.5)

        # update the aspect ratio of the main window
        self.on_layout_updated()

    def update_resize_handles(self):
        """ Tell WindowManipulator about the active resize handles """
        docking = config.is_docking_enabled()

        # frame handles
        WindowManipulator.set_drag_handles(self, self._get_active_drag_handles())
        WindowManipulator.lock_x_axis(self, docking)

        # touch handles
        self.touch_handles.set_active_handles(self._get_active_drag_handles(True))
        self.touch_handles.lock_x_axis(docking)

    def update_auto_show(self):
        """
        Turn on/off auto-show in response to user action (preferences)
        and show/hide the window accordingly.
        """
        enable = config.is_auto_show_enabled()
        self.auto_show.enable(enable)
        self.auto_show.show_keyboard(not enable)

    def update_transparency(self):
        """
        Updates transparencies in response to user action.
        Temporarily presents the window with active transparency when
        inactive transparency is enabled.
        """
        self.transition_active_to(True)
        self.commit_transition()
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)
        else:
            self.inactivity_timer.stop()
        self.redraw() # for background transparency

    def touch_inactivity_timer(self):
        """ extend active transparency, kick of inactivity_timer """
        if self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(True)
            self.inactivity_timer.begin_transition(False)

    def update_inactive_transparency(self):
        if self.inactivity_timer.is_enabled():
            self.transition_active_to(False)
            self.commit_transition()

    def _update_double_click_time(self):
        """ Scraping the bottom of the barrel to speed up key presses """
        self._double_click_time = Gtk.Settings.get_default() \
                        .get_property("gtk-double-click-time")

    def transition_visible_to(self, visible, opacity_duration = None,
                                             slide_duration = None):
        result = False
        state = self._transition_state
        win = self.get_kbd_window()

        # hide popup
        if not visible:
            self.close_key_popup()

        # bail in xembed mode
        if config.xid_mode:
            return False

        # stop reposition updates when we're hiding anyway
        if win and not visible:
            win.stop_auto_position()

        if config.is_docking_enabled():
            if slide_duration is None:
                slide_duration = self.TRANSITION_DURATION_SLIDE
            opacity_duration = 0.0
            opacity_visible = True

            if win:
                if visible:
                    begin_rect = win.get_hidden_rect()
                    end_rect = win.get_visible_rect()
                else:
                    begin_rect = win.get_rect()
                    end_rect = win.get_docking_hideout_rect()
                state.y.value = begin_rect.y
                y             = end_rect.y
                state.x.value = begin_rect.x
                x             = end_rect.x

                result |= self._init_transition(state.x, x, slide_duration)
                result |= self._init_transition(state.y, y, slide_duration)
        else:
            opacity_visible  = visible

        if opacity_duration is None:
            if opacity_visible:
                # No duration when showing. Don't fight with compiz in unity.
                opacity_duration = 0.0
            else:
                opacity_duration = self.TRANSITION_DURATION_OPACITY_HIDE

        result |= self._init_opacity_transition(state.visible, opacity_visible,
                                                opacity_duration)
        state.target_visibility = visible

        return result

    def transition_active_to(self, active, duration = None):
        # not in xembed mode
        if config.xid_mode:
            return False

        if duration is None:
            if active:
                duration = 0.15
            else:
                duration = 0.3
        return self._init_opacity_transition(self._transition_state.active,
                                             active, duration)

    def transition_position_to(self, x, y):
        result = False
        state = self._transition_state
        duration = self.TRANSITION_DURATION_MOVE

        # not in xembed mode
        if config.xid_mode:
            return False

        win = self.get_kbd_window()
        if win:
            begin_rect = win.get_rect()
            state.y.value = begin_rect.y
            state.x.value = begin_rect.x

        result |= self._init_transition(state.x, x, duration)
        result |= self._init_transition(state.y, y, duration)

        return result

    def sync_transition_position(self, rect):
        """
        Update transition variables with the actual window position.
        Necessary on user positioning.
        """
        state = self._transition_state
        state.y.value        = rect.y
        state.x.value        = rect.x
        state.y.target_value = rect.y
        state.x.target_value = rect.x

    def _init_opacity_transition(self, var, target_value, duration):

        # No fade delay for screens that can't fade (unity-2d)
        screen = self.get_screen()
        if screen and not screen.is_composited():
            duration = 0.0

        target_value = 1.0 if target_value else 0.0

        return self._init_transition(var, target_value, duration)

    def _init_transition(self, var, target_value, duration):
        # Transition not yet in progress?
        if var.target_value != target_value:
            var.start_transition(target_value, duration)
            return True
        return False

    def commit_transition(self):
        # not in xembed mode
        if config.xid_mode:
            return

        duration = self._transition_state.get_max_duration()
        if duration == 0.0:
            self._on_transition_step()
        else:
            self._transition_timer.start(0.02, self._on_transition_step)

    def _on_transition_step(self):
        state = self._transition_state
        state.update()

        done              = state.is_done()

        active_opacity    = config.window.get_active_opacity()
        inactive_opacity  = config.window.get_inactive_opacity()
        invisible_opacity = 0.0

        opacity  = inactive_opacity + state.active.value * \
                   (active_opacity - inactive_opacity)
        opacity *= state.visible.value

        window = self.get_kbd_window()
        if window:
            if window.get_opacity() != opacity:
                window.set_opacity(opacity)

            visible_before = window.is_visible()
            visible_later  = state.target_visibility

            # move
            x = int(state.x.value)
            y = int(state.y.value)
            wx, wy = window.get_position()
            if x != wx or y != wy:
                window.reposition(x, y)

            # show/hide
            visible = (visible_before or visible_later) and not done or \
                      visible_later and done
            if window.is_visible() != visible:
                window.set_visible(visible)

                # on_leave_notify does not start the inactivity timer
                # while the pointer remains inside of the window. Do it
                # here when hiding the window.
                if not visible and \
                   self.inactivity_timer.is_enabled():
                    self.inactivity_timer.begin_transition(False)

                # start/stop on-hide-release timer
                self._auto_release_timer.start(visible)

            if done:
                window.on_transition_done(visible_before, visible_later)

        return not done

    def toggle_visible(self):
        """ main method to show/hide onboard manually """
        self.set_visible(not self.is_visible())

    def is_visible(self):
        """ is the keyboard window currently visible? """
        window = self.get_kbd_window()
        return window.is_visible() if window else False

    def set_visible(self, visible):
        """ main method to show/hide onboard manually """
        self.lock_auto_show_visible(visible)  # pause auto show
        self.transition_visible_to(visible, 0.0)

        # briefly present the window
        if visible and self.inactivity_timer.is_enabled():
            self.transition_active_to(True, 0.0)
            self.inactivity_timer.begin_transition(False)

        self.commit_transition()
        win = self.get_kbd_window()

    def lock_auto_show_visible(self, visible):
        """
        If the user unhides onboard, don't auto-hide it until
        he manually hides it again.
        """
        if config.is_auto_show_enabled():
            self.auto_show.lock_visible(visible)

    def freeze_auto_show(self, thaw_time = None):
        """
        Stop both, hiding and showing.
        """
        if config.is_auto_show_enabled():
            self.auto_show.freeze(thaw_time)

    def thaw_auto_show(self, thaw_time = None):
        """
        Reenable both, hiding and showing.
        """
        if config.is_auto_show_enabled():
            self.auto_show.thaw(thaw_time)

    def start_click_polling(self):
        if self.keyboard.has_latched_sticky_keys() or \
           self._key_popup or \
           config.are_word_suggestions_enabled():
            self._outside_click_timer.start(0.01, self._on_click_timer)
            self._outside_click_detected = False
            self._outside_click_start_time = time.time()

    def stop_click_polling(self):
        self._outside_click_timer.stop()

    def _on_click_timer(self):
        """ poll for mouse click outside of onboards window """
        rootwin = Gdk.get_default_root_window()
        dunno, x, y, mask = rootwin.get_pointer()
        if mask & BUTTON123_MASK:
            self._outside_click_detected = True
        elif self._outside_click_detected:
            # button released anywhere outside of onboard's control
            _logger.debug("click polling: outside click")
            self.stop_click_polling()
            self.close_key_popup()
            self.keyboard.on_outside_click()
            return False

        # stop after 30 seconds
        if time.time() - self._outside_click_start_time > 30.0:
            self.stop_click_polling()
            self.keyboard.on_cancel_outside_click()
            return False

        return True

    def get_drag_window(self):
        """ Overload for WindowManipulator """
        return self.get_kbd_window()

    def get_drag_threshold(self):
        """ Overload for WindowManipulator """
        return config.get_drag_threshold()

    def on_drag_initiated(self):
        """ Overload for WindowManipulator """
        window = self.get_drag_window()
        if window:
            window.on_user_positioning_begin()
        self.grab_xi_pointer(True)

    def on_drag_activated(self):
        if self.is_resizing():
            self._lod = LOD.MINIMAL
        self.keyboard.hide_touch_feedback()

    def on_drag_done(self):
        """ Overload for WindowManipulator """
        self.grab_xi_pointer(False)
        window = self.get_drag_window()
        if window:
            window.on_user_positioning_done()

        self.reset_lod()

    def get_always_visible_rect(self):
        """
        Returns the bounding rectangle of all move buttons
        in canvas coordinates.
        Overload for WindowManipulator
        """
        bounds = None
        if config.is_docking_enabled():
            pass
        else:
            keys = self.keyboard.find_items_from_ids(["move"])
            for key in keys:
                if key.is_visible():
                    r = key.get_canvas_border_rect()
                    if not bounds:
                        bounds = r
                    else:
                        bounds = bounds.union(r)

        if bounds is None:
            bounds = self.canvas_rect

        return bounds

    def hit_test_move_resize(self, point):
        """ Overload for WindowManipulator """
        hit = self.touch_handles.hit_test(point)
        if hit is None:
            hit = WindowManipulator.hit_test_move_resize(self, point)
        return hit

    def _on_configure_event(self, widget, user_data):
        if self.is_new_layout_size(self.get_allocated_width(),
                                   self.get_allocated_height()):
            self.update_layout()
            self.touch_handles.update_positions(self.canvas_rect)
            self.invalidate_for_resize(self._lod)

    def on_enter_notify(self, widget, event):
        self._update_double_click_time()

        # ignore event if a mouse button is held down
        # we get the event once the button is released
        if event.state & BUTTON123_MASK:
            return

        # ignore unreliable touch enter event for inactivity timer
        # -> smooths startup, only one transition in set_startup_visibility()
        source_device = event.get_source_device()
        source = source_device.get_source()
        if source != Gdk.InputSource.TOUCHSCREEN:

            # stop inactivity timer
            if self.inactivity_timer.is_enabled():
                self.inactivity_timer.begin_transition(True)

        # stop click polling
        self.stop_click_polling()

        # Force into view for WindowManipulator's system drag mode.
        #if not config.xid_mode and \
        #   not config.window.window_decoration and \
        #   not config.is_force_to_top():
        #    GLib.idle_add(self.force_into_view)

    def on_leave_notify(self, widget, event):
        # ignore event if a mouse button is held down
        # we get the event once the button is released
        if event.state & BUTTON123_MASK:
            return

        # Ignore leave events when the cursor hasn't acually left
        # our window. Fixes window becoming idle-transparent while
        # typing into firefox awesomebar.
        # Can't use event.mode as that appears to be broken and
        # never seems to become GDK_CROSSING_GRAB (Precise).
        if self.canvas_rect.is_point_within((event.x, event.y)):
            return

        self.stop_dwelling()
        self.reset_touch_handles()

        # start a timer to detect clicks outside of onboard
        self.start_click_polling()

        # Start inactivity timer, but ignore the unreliable
        # leave event for touch input.
        source_device = event.get_source_device()
        source = source_device.get_source()
        if source != Gdk.InputSource.TOUCHSCREEN:
            if self.inactivity_timer.is_enabled():
                self.inactivity_timer.begin_transition(False)

        # Reset the cursor, so enabling the scanner doesn't get the last
        # selected one stuck forever.
        self.reset_drag_cursor()

    def do_set_cursor_at(self, point, hit_key = None):
        """ Set/reset the cursor for frame resize handles """
        if not config.xid_mode:
            allow_drag_cursors = not hit_key and \
                                 not config.has_window_decoration()
            self.set_drag_cursor_at(point, allow_drag_cursors)

    def on_input_sequence_begin(self, sequence):
        """ Button press/touch begin """
        self.stop_click_polling()
        self.stop_dwelling()
        self.close_key_popup()

        # There's no reliable enter/leave for touch input
        # -> turn up inactive transparency on touch begin
        if sequence.is_touch() and \
           self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(True)

        point = sequence.point
        key = None

        # hit-test touch handles first
        hit_handle = None
        if self.touch_handles.active:
            hit_handle = self.touch_handles.hit_test(point)
            self.touch_handles.set_pressed(hit_handle)
            if not hit_handle is None:
                # handle clicked -> stop auto-hide until button release
                self.stop_touch_handles_auto_hide()
            else:
                # no handle clicked -> hide them now
                self.show_touch_handles(False)

        # hit-test keys
        if hit_handle is None:
            key = self.get_key_at_location(point)

        # enable/disable the drag threshold
        if not hit_handle is None:
            self.enable_drag_protection(False)
        elif key and key.id == "move":
            # Move key needs to support long press;
            # always use the drag threshold.
            self.enable_drag_protection(True)
            self.reset_drag_protection()
        else:
            self.enable_drag_protection(config.drag_protection)

        # handle resizing
        if key is None and \
           not config.has_window_decoration() and \
           not config.xid_mode:
            if WindowManipulator.handle_press(self, sequence):
                return True

        # bail if we are in scanning mode
        if config.scanner.enabled:
            return True

        # press the key
        sequence.active_key = key
        sequence.initial_active_key = key
        if key:
            # single click?
            if self._last_click_key != key or \
               sequence.time - self._last_click_time > self._double_click_time:

                # handle key press
                sequence.event_type = EventType.CLICK
                self.key_down(sequence)

                # start long press detection
                delay = config.keyboard.long_press_delay
                if key.id == "move":  # don't show touch handles too easily
                    delay += 0.3
                self._long_press_timer.start(delay,
                                             self._on_long_press, sequence)

            # double click
            else:
                sequence.event_type = EventType.DOUBLE_CLICK
                self.key_down(sequence)

            self._last_click_key = key
            self._last_click_time = sequence.time

        return True

    def on_input_sequence_update(self, sequence):
        """ Pointer motion/touch update """
        if not sequence.primary:  # only drag with the very first sequence
            return

        # Redirect to long press popup for drag selection.
        popup = self._key_popup
        if popup:
            popup.redirect_sequence_update(sequence,
                                           popup.on_input_sequence_update)
            return

        point = sequence.point
        hit_key = None

        # hit-test touch handles first
        hit_handle = None
        if self.touch_handles.active:
            hit_handle = self.touch_handles.hit_test(point)
            self.touch_handles.set_prelight(hit_handle)

        # hit-test keys
        if hit_handle is None:
            hit_key = self.get_key_at_location(point)

        if sequence.state & BUTTON123_MASK:

            # move/resize
            # fallback=False for faster system resizing (LP: #959035)
            fallback = True #self.is_moving() or config.is_force_to_top()

            # move/resize
            WindowManipulator.handle_motion(self, sequence, fallback = fallback)

            # stop long press when drag threshold has been overcome
            if self.is_drag_active():
                self.stop_long_press()

            # drag-select new active key
            active_key = sequence.active_key
            if not self.is_drag_initiated() and \
               active_key != hit_key:
                self.stop_long_press()

                if self._overcome_initial_key_resistance(sequence) and \
                   (not active_key or not active_key.activated) and \
                    not self._key_popup:
                    sequence.active_key = hit_key
                    self.key_down_update(sequence, active_key)

        else:
            if not hit_handle is None:
                # handle hovered over: extend the time touch handles are visible
                self.start_touch_handles_auto_hide()

            # Show/hide the input line
            self._hide_input_line_timer.handle_motion(sequence)

            # start dwelling if we have entered a dwell-enabled key
            if hit_key and \
               hit_key.sensitive:
                controller = self.keyboard.button_controllers.get(hit_key)
                if controller and controller.can_dwell() and \
                   not self.is_dwelling() and \
                   not self.already_dwelled(hit_key) and \
                   not config.scanner.enabled and \
                   not config.lockdown.disable_dwell_activation:
                    self.start_dwelling(hit_key)

            self.do_set_cursor_at(point, hit_key)

        # cancel dwelling when the hit key changes
        if self.dwell_key and self.dwell_key != hit_key or \
           self.last_dwelled_key and self.last_dwelled_key != hit_key:
            self.cancel_dwelling()

    def on_input_sequence_end(self, sequence):
        """ Button release/touch end """

        # Redirect to long press popup for end of drag-selection.
        popup = self._key_popup
        if popup and \
           popup.got_motion():  # keep popup open if it wasn't entered
            popup.redirect_sequence_end(sequence,
                                        popup.on_input_sequence_end)

        # key up
        active_key = sequence.active_key
        if active_key and \
           not config.scanner.enabled:
            self.key_up(sequence)

        self.stop_drag()
        self.stop_long_press()

        # reset cursor when there was no cursor motion
        point = sequence.point
        hit_key = self.get_key_at_location(point)
        self.do_set_cursor_at(point, hit_key)

        # reset touch handles
        self.reset_touch_handles()
        self.start_touch_handles_auto_hide()

        # There's no reliable enter/leave for touch input
        # -> start inactivity timer on touch end
        if sequence.is_touch() and \
           self.inactivity_timer.is_enabled():
            self.inactivity_timer.begin_transition(False)

    def on_drag_gesture_begin(self, num_touches):
        self.stop_long_press()

        if num_touches and \
           not self.is_drag_initiated():
            self.show_touch_handles()
            self.start_move_window()
        return True

    def on_drag_gesture_end(self, num_touches):
        self.stop_move_window()
        return True

    def on_tap_gesture(self, num_touches):
        if num_touches == 3:
            self.show_touch_handles()
            return True
        return False

    def _on_long_press(self, sequence):
        long_pressed = self.keyboard.key_long_press(sequence.active_key,
                                                    self, sequence.button)
        sequence.cancel_key_action = long_pressed # cancel generating key-stroke

    def stop_long_press(self):
        self._long_press_timer.stop()

    def key_down(self, sequence):
        self.keyboard.key_down(sequence.active_key, self, sequence)
        self._auto_release_timer.start()

    def key_down_update(self, sequence, old_key):
        assert(not old_key or not old_key.activated) # old_key must be undoable
        self.keyboard.key_up(old_key, self, sequence, False)
        self.keyboard.key_down(sequence.active_key, self, sequence, False)

    def key_up(self, sequence):
        self.keyboard.key_up(sequence.active_key, self, sequence,
                             not sequence.cancel_key_action)

    def is_dwelling(self):
        return not self.dwell_key is None

    def already_dwelled(self, key):
        return self.last_dwelled_key is key

    def start_dwelling(self, key):
        self.cancel_dwelling()
        self.dwell_key = key
        self.last_dwelled_key = key
        key.start_dwelling()
        self.dwell_timer = GLib.timeout_add(50, self._on_dwell_timer)

    def cancel_dwelling(self):
        self.stop_dwelling()
        self.last_dwelled_key = None

    def stop_dwelling(self):
        if self.dwell_timer:
            GLib.source_remove(self.dwell_timer)
            self.dwell_timer = None
            self.redraw([self.dwell_key])
            self.dwell_key.stop_dwelling()
            self.dwell_key = None

    def _on_dwell_timer(self):
        if self.dwell_key:
            self.redraw([self.dwell_key])

            if self.dwell_key.is_done():
                key = self.dwell_key
                self.stop_dwelling()

                sequence = InputSequence()
                sequence.button = 0
                sequence.event_type = EventType.DWELL
                sequence.active_key = key
                sequence.point = key.get_canvas_rect().get_center()
                sequence.root_point = \
                        canvas_to_root_window_point(self, sequence.point)

                self.key_down(sequence)
                self.key_up(sequence)

                return False
        return True

    def _on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        if config.show_tooltips and \
           not self.is_drag_initiated() and \
           not self.last_event_was_touch():
            key = self.get_key_at_location((x, y))
            if key and key.tooltip:
                r = Gdk.Rectangle()
                r.x, r.y, r.width, r.height = key.get_canvas_rect()
                tooltip.set_tip_area(r)   # no effect on Oneiric?
                tooltip.set_text(_(key.tooltip))
                return True
        return False

    def show_touch_handles(self, show = True, auto_hide = True):
        """
        Show/hide the enlarged resize/move handels.
        Initiates an opacity fade.
        """
        if show and config.lockdown.disable_touch_handles:
            return

        if show:
            self.touch_handles.set_prelight(None)
            self.touch_handles.set_pressed(None)
            self.touch_handles.active = True
            self.touch_handles_auto_hide = auto_hide

            size, size_mm = get_monitor_dimensions(self)
            self.touch_handles.set_monitor_dimensions(size, size_mm)
            self.touch_handles.update_positions(self.canvas_rect)

            if auto_hide:
                self.start_touch_handles_auto_hide()

            start, end = 0.0, 1.0
        else:
            self.stop_touch_handles_auto_hide()
            start, end = 1.0, 0.0

        if self.touch_handles_fade.target_value != end:
            self.touch_handles_fade.time_step = 0.025
            self.touch_handles_fade.fade_to(start, end, 0.2,
                                      self._on_touch_handles_opacity)

    def reset_touch_handles(self):
        if self.touch_handles.active:
            self.touch_handles.set_prelight(None)
            self.touch_handles.set_pressed(None)

    def start_touch_handles_auto_hide(self):
        """ (re-) starts the timer to hide touch handles """
        if self.touch_handles.active and self.touch_handles_auto_hide:
            self.touch_handles_hide_timer.start(4,
                                                self.show_touch_handles, False)

    def stop_touch_handles_auto_hide(self):
        """ stops the timer to hide touch handles """
        self.touch_handles_hide_timer.stop()

    def _on_touch_handles_opacity(self, opacity, done):
        if done and opacity < 0.1:
            self.touch_handles.active = False

        self.touch_handles.opacity = opacity

        # Convoluted workaround for a weird cairo glitch (Precise).
        # When queuing all handles for drawing, the background under
        # the move handle is clipped erroneously and remains transparent.
        # -> Divide handles up into two groups, draw only one
        #    group at a time and fade with twice the frequency.
        if 0:
            self.touch_handles.redraw()
        else:
            for handle in self.touch_handles.handles:
                if bool(self.touch_handles_fade.iteration & 1) != \
                   (handle.id in [Handle.MOVE, Handle.NORTH, Handle.SOUTH]):
                    handle.redraw()

            if done:
                # draw the missing final step
                GLib.idle_add(self._on_touch_handles_opacity, 1.0, False)

    def _on_draw(self, widget, context):
        decorated = LayoutView.draw(self, widget, context)

        # draw touch handles (enlarged move and resize handles)
        if self.touch_handles.active:
            corner_radius = config.CORNER_RADIUS if decorated else 0
            self.touch_handles.set_corner_radius(corner_radius)
            self.touch_handles.draw(context)

    def _overcome_initial_key_resistance(self, sequence):
        """
        Drag-select: Increase the hit area of the initial key
        to make it harder to leave the the key the button was
        pressed down on.
        """
        DRAG_SELECT_INITIAL_KEY_ENLARGEMENT = 0.4

        active_key = sequence.active_key
        if active_key and active_key is sequence.initial_active_key:
            rect = active_key.get_canvas_border_rect()
            k = min(rect.w, rect.h) * DRAG_SELECT_INITIAL_KEY_ENLARGEMENT
            rect = rect.inflate(k)
            if rect.is_point_within(sequence.point):
                return False
        return True

    def get_kbd_window(self):
        return self.get_parent()

    def can_draw_frame(self):
        """ Overload for LayoutView """
        co = self.get_kbd_window().get_orientation_config_object()
        return not config.is_dock_expanded(co)

    def get_min_window_size(self):
        min_mm = (50, 20)  # just large enough to grab with a 3 finger gesture
        return physical_to_monitor_pixel_size(self, min_mm, (150, 100))

    def get_frame_width(self):
        """ Width of the frame around the keyboard; canvas coordinates. """
        if config.xid_mode:
            return config.UNDECORATED_FRAME_WIDTH
        if config.has_window_decoration():
            return 0.0
        co = self.get_kbd_window().get_orientation_config_object()
        if config.is_dock_expanded(co):
            return 0.5
        if config.window.transparent_background:
            return 3.0
        return config.UNDECORATED_FRAME_WIDTH

    def get_hit_frame_width(self):
        return 10

    def _get_active_drag_handles(self, all_handles = False):
        if config.xid_mode:  # none when xembedding
            handles = ()
        else:
            if config.is_docking_enabled():
                expand = self.get_kbd_window().get_dock_expand()
                if expand:
                    handles = (Handle.NORTH, Handle.SOUTH, Handle.MOVE)
                else:
                    handles = Handle.ALL
            else:
                handles = Handle.ALL

            if not all_handles:
                # filter through handles enabled in config
                config_handles = config.window.resize_handles
                handles = tuple(set(handles).intersection(set(config_handles)))

        return handles

    def get_click_type_button_rects(self):
        """
        Returns bounding rectangles of all click type buttons
        in root window coordinates.
        """
        keys = self.keyboard.find_items_from_ids(["singleclick",
                                                  "secondaryclick",
                                                  "middleclick",
                                                  "doubleclick",
                                                  "dragclick"])
        rects = []
        for key in keys:
            r = key.get_canvas_border_rect()
            rects.append(canvas_to_root_window_rect(self, r))

        return rects

    def get_key_screen_rect(self, key):
        """
        Returns bounding rectangles of key in in root window coordinates.
        """
        r = key.get_canvas_border_rect()
        x0, y0 = self.get_window().get_root_coords(r.x, r.y)
        x1, y1 = self.get_window().get_root_coords(r.x + r.w,
                                                   r.y + r.h)
        return Rect(x0, y0, x1 - x0, y1 -y0)

    def on_layout_updated(self):
        # experimental support for keeping window aspect ratio
        # Currently, in Oneiric, neither lightdm, nor gnome-screen-saver
        # appear to honor these hints.
        layout = self.get_layout()

        aspect_ratio = None
        if config.is_keep_aspect_ratio_enabled():
            log_rect = layout.get_border_rect()
            aspect_ratio = log_rect.w / float(log_rect.h)
            aspect_ratio = layout.get_log_aspect_ratio()

        if self._aspect_ratio != aspect_ratio:
            window = self.get_kbd_window()
            if window:
                geom = Gdk.Geometry()
                if aspect_ratio is None:
                    window.set_geometry_hints(self, geom, 0)
                else:
                    geom.min_aspect = geom.max_aspect = aspect_ratio
                    window.set_geometry_hints(self, geom, Gdk.WindowHints.ASPECT)

                self._aspect_ratio = aspect_ratio

    def refresh_pango_layouts(self):
        """
        When the systems font dpi setting changes, our pango layout object
        still caches the old setting, leading to wrong font scaling.
        Refresh the pango layout object.
        """
        _logger.info("Refreshing pango layout, new font dpi setting is '{}'" \
                .format(Gtk.Settings.get_default().get_property("gtk-xft-dpi")))

        Key.reset_pango_layout()
        self.invalidate_label_extents()
        self.keyboard.invalidate_ui()
        self.keyboard.commit_ui_updates()

    def show_popup_alternative_chars(self, key, alternatives):
        """
        Popup with alternative chars.
        """
        popup = self._create_key_popup(self.get_kbd_window())
        result = LayoutBuilderAlternatives \
                    .build(key, self.get_color_scheme(), alternatives)
        popup.set_layout(*result)

        self._show_key_popup(popup, key)
        self._key_popup = popup

        self.keyboard.hide_touch_feedback()

    def show_popup_layout(self, key, layout):
        """
        Popup with predefined layout items.
        """
        popup = self._create_key_popup(self.get_kbd_window())
        result = LayoutBuilder \
                    .build(key, self.get_color_scheme(), layout)
        popup.set_layout(*result)
        self._show_key_popup(popup, key)
        self._key_popup = popup

        self.keyboard.hide_touch_feedback()

    def close_key_popup(self):
        if self._key_popup:
            self._key_popup.destroy()
            self._key_popup = None

    def _create_key_popup(self, parent):
        popup = LayoutPopup(self.keyboard,
                         self.close_key_popup)
        popup.supports_alpha = self.supports_alpha
        popup.set_transient_for(parent)
        popup.set_opacity(parent.get_opacity())
        return popup

    def _show_key_popup(self, popup, key):
        r = key.get_canvas_border_rect()
        root_rect = canvas_to_root_window_rect(self, r)
        popup.position_at(root_rect.x + root_rect.w * 0.5,
                         root_rect.y, 0.5, 1.0)
        popup.show()
        return popup

    def show_snippets_dialog(self, snippet_id):
        """ Show dialog for creating a new snippet """

        # turn off AT-SPI listeners to prevent D-BUS deadlocks (Quantal).
        self.keyboard.on_focusable_gui_opening()

        dialog = Gtk.Dialog(title=_("New snippet"),
                           transient_for=self.get_toplevel(),
                           flags=0)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(_("_Save snippet"), Gtk.ResponseType.OK)

        # Don't hide dialog behind the keyboard in force-to-top mode.
        if config.is_force_to_top():
            dialog.set_position(Gtk.WindowPosition.CENTER)

        dialog.set_default_response(Gtk.ResponseType.OK)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=12, border_width=5)
        dialog.get_content_area().add(box)

        msg = Gtk.Label(label=_("Enter a new snippet for this button:"),
                        xalign=0.0)
        box.add(msg)

        label_entry = Gtk.Entry(hexpand=True)
        text_entry  = Gtk.Entry(hexpand=True, activates_default = True)
        label_label = Gtk.Label(label=_("_Button label:"),
                                xalign=0.0,
                                use_underline=True,
                                mnemonic_widget=label_entry)
        text_label  = Gtk.Label(label=_("S_nippet:"),
                                xalign=0.0,
                                use_underline=True,
                                mnemonic_widget=text_entry)

        grid = Gtk.Grid(row_spacing=6, column_spacing=3)
        grid.attach(label_label, 0, 0, 1, 1)
        grid.attach(text_label, 0, 1, 1, 1)
        grid.attach(label_entry, 1, 0, 1, 1)
        grid.attach(text_entry, 1, 1, 1, 1)
        box.add(grid)

        dialog.connect("response", self._on_snippet_dialog_response, \
                       snippet_id, label_entry, text_entry)

        label_entry.grab_focus()
        dialog.show_all()

    def _on_snippet_dialog_response(self, dialog, response, snippet_id, \
                                    label_entry, text_entry):
        if response == Gtk.ResponseType.OK:
            label = label_entry.get_text()
            text = text_entry.get_text()

            if sys.version_info.major == 2:
                label = label.decode("utf-8")
                text = text.decode("utf-8")

            config.set_snippet(snippet_id, (label, text))
        dialog.destroy()

        self.keyboard.on_snippets_dialog_closed()

        # Reenable AT-SPI keystroke listeners.
        # Delay this until the dialog is really gone.
        GLib.idle_add(self.keyboard.on_focusable_gui_closed)

    def show_language_menu(self, key, button, closure = None):
        self._language_menu.popup(key, button, closure)

    def is_language_menu_showing(self):
        return self._language_menu.is_showing()


class LanguageMenu:
    """ Popup menu for the language button """

    def __init__(self, keyboard_widget):
        self._keyboard_widget = keyboard_widget
        self._keyboard = self._keyboard_widget.keyboard
        self._menu = None
        self._closure = None

    def is_showing(self):
        return not self._menu is None

    def popup(self, key, button, closure = None):
        self._closure = closure
        self._keyboard.on_focusable_gui_opening()

        keyboard = self._keyboard
        languagedb = keyboard._languagedb

        active_lang_id = keyboard.get_active_lang_id()
        system_lang_id = keyboard.get_system_default_lang_id()

        lang_ids = set(languagedb.get_language_ids())
        if system_lang_id in lang_ids:
            lang_ids.remove(system_lang_id)

        max_mru_languages = config.typing_assistance.max_recent_languages
        all_mru_lang_ids = config.typing_assistance.recent_languages
        mru_lang_ids    = [id for id in all_mru_lang_ids if id in lang_ids] \
                          [:max_mru_languages]
        other_lang_ids  = set(lang_ids).difference(mru_lang_ids)
        other_langs = []
        for lang_id in other_lang_ids:
            name = languagedb.get_language_full_name(lang_id)
            if name:
                other_langs.append((name, lang_id))

        def add_system_indicator(name):
            return name + _(" - System Language")

        # language sub menu
        lang_menu = Gtk.Menu()
        for name, lang_id in sorted(other_langs):
            item = Gtk.MenuItem.new_with_label(name)
            item.connect("activate", self._on_other_language_activated, lang_id)
            lang_menu.append(item)

        # popup menu
        menu = Gtk.Menu()

        active_lang_id = keyboard.get_active_lang_id()
        name = languagedb.get_language_full_name(system_lang_id)
        item = Gtk.CheckMenuItem.new_with_mnemonic(name)
        item.set_draw_as_radio(True)
        item.set_active(not active_lang_id)
        item.connect("activate", self._on_language_activated, "")
        menu.append(item)

        item = Gtk.SeparatorMenuItem.new()
        menu.append(item)

        for lang_id in mru_lang_ids:
            name = languagedb.get_language_full_name(lang_id)
            if name:
                item = Gtk.CheckMenuItem.new_with_label(name)
                item.set_draw_as_radio(True)
                item.set_active(lang_id == active_lang_id)
                item.connect("activate", self._on_language_activated, lang_id)
                menu.append(item)

        if other_langs:
            if mru_lang_ids:
                item = Gtk.MenuItem.new_with_mnemonic(_("Other _Languages"))
            else:
                item = Gtk.MenuItem.new_with_mnemonic(_("_Languages"))
            item.set_submenu(lang_menu)
            menu.append(item)

        if lang_ids:
            item = Gtk.SeparatorMenuItem.new()
            menu.append(item)

        menu.connect("unmap", self._language_menu_unmap)
        self._menu = menu

        menu.show_all()

        menu.popup(None, None, self._language_menu_positioning_func,
                   key, button, Gtk.get_current_event_time())


    def _language_menu_unmap(self, menu):
        Timer(0.5, self._keyboard.on_focusable_gui_closed)
        self._menu = None
        if self._closure:
            self._closure()

    def _language_menu_positioning_func(self, menu, key):
        r = self._keyboard_widget.get_key_screen_rect(key)
        x = r.left()
        return x, r.bottom(), True

    def _on_language_activated(self, menu, lang_id):
        system_lang_id = self._keyboard.get_system_default_lang_id()
        if lang_id == system_lang_id:
            lang_id = ""
        self._set_active_lang_id(lang_id)

    def _on_other_language_activated(self, menu, lang_id):
        if lang_id:  # empty string = system default
            self._set_mru_lang_id(lang_id)
        self._set_active_lang_id(lang_id)

    def _set_active_lang_id(self, lang_id):
        self._keyboard.set_active_lang_id(lang_id)

    def _set_mru_lang_id(self, lang_id):
        max_recent_languages = config.typing_assistance.max_recent_languages
        recent_languages = config.typing_assistance.recent_languages[:]
        if lang_id in recent_languages:
            recent_languages.remove(lang_id)
        recent_languages.insert(0, lang_id)
        recent_languages = recent_languages[:max_recent_languages]
        config.typing_assistance.recent_languages = recent_languages

