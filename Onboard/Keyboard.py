### Logging ###
import logging
_logger = logging.getLogger("Keyboard")
###############

import string

from gi.repository import GObject, Gtk, Gdk

from gettext import gettext as _

from Onboard.KeyGtk import *
from Onboard import KeyCommon
from Onboard.MouseControl import MouseController
from Onboard.WordPredictor import *

try:
    from Onboard.utils import run_script, get_keysym_from_name, dictproperty
except DeprecationWarning:
    pass

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

### Logging ###
import logging
_logger = logging.getLogger("Keyboard")
###############

class Keyboard:
    "Cairo based keyboard widget"

    active_scan_key = None # Key currently being scanned.
    scanning_x = None
    scanning_y = None

    color_scheme = None
    alt_locked = False
    layer_locked = False

### Properties ###

    # The number of pressed keys per modifier
    _mods = {1:0,2:0, 4:0,8:0, 16:0,32:0,64:0,128:0}
    def _get_mod(self, key):
        return self._mods[key]
    def _set_mod(self, key, value):
        self._mods[key] = value
        self._on_mods_changed()
    mods = dictproperty(_get_mod, _set_mod)

    # currently active layer
    def _get_active_layer_index(self):
        return config.active_layer_index
    def _set_active_layer_index(self, index):
        config.active_layer_index = index
    active_layer_index = property(_get_active_layer_index,
                                  _set_active_layer_index)

    def _get_active_layer(self):
        layers = self.get_layers()
        if not layers:
            return None
        index = self.active_layer_index
        if index < 0 or index >= len(layers):
            index = 0
        return layers[index]
    def _set_active_layer(self, layer):
        index = 0
        for i, layer in enumerate(self.get_layers()):
            if layer is layer:
                index = i
                break
        self.active_layer_index = index
    active_layer = property(_get_active_layer, _set_active_layer)

    def assure_valid_active_layer(self):
        """
        Reset layer index if it is out of range. e.g. due to
        loading a layout with fewer panes.
        """
        index = self.active_layer_index
        if index < 0 or index >= len(self.get_layers()):
            self.active_layer_index = 0

##################

    def __init__(self, vk):
        self.vk = vk

        #List of keys which have been latched.
        #ie. pressed until next non sticky button is pressed.
        self._latched_sticky_keys = []
        self._locked_sticky_keys = []

        self.canvas_rect = Rect()
        self.button_controllers = {}
        self.editing_snippet = False

        self.input_line = InputLine()
        self.punctuator = Punctuator()
        self.predictor  = None

        self.word_choices = []
        self.word_infos = []

    def destruct(self):
        self.cleanup()

    def initial_update(self):
        """ called when the layout has been loaded """
        self.enable_word_prediction(config.wp.enabled)

        # connect button controllers to button keys
        types = [BCMiddleClick, BCSingleClick, BCSecondaryClick, BCDoubleClick, BCDragClick,
                 BCHoverClick,
                 BCHide, BCShowClick, BCMove, BCPreferences, BCQuit,
                 BCStealthMode, BCAutoLearn, BCAutoPunctuation, BCInputline,
                ]
        for key in self.layout.iter_keys():
            if key.is_layer_button():
                bc = BCLayer(self, key)
                bc.layer_index = key.get_layer_index()
                self.button_controllers[key] = bc
            else:
                for type in types:
                    if type.id == key.id:
                        self.button_controllers[key] = type(self, key)

        self.assure_valid_active_layer()
        self.update_ui()

    def get_layers(self):
        if self.layout:
            return self.layout.get_layer_ids()
        return []

    def iter_keys(self, group_name=None):
        """ iterate through all keys or all keys of a group """
        return self.layout.iter_keys(group_name)

    def utf8_to_unicode(self,utf8Char):
        return ord(utf8Char.decode('utf-8'))

    def get_scan_columns(self):
        for item in self.layout.iter_layer_items(self.active_layer):
            if item.scan_columns:
                return item.scan_columns
        return None

    def scan_tick(self): #at intervals scans across keys in the row and then down columns.
        if self.active_scan_key:
            self.active_scan_key.beingScanned = False

        columns = self.get_scan_columns()
        if columns:
            if not self.scanning_y == None:
                self.scanning_y = (self.scanning_y + 1) % len(columns[self.scanning_x])
            else:
                self.scanning_x = (self.scanning_x + 1) % len(columns)

            if self.scanning_y == None:
                y = 0
            else:
                y = self.scanning_y

            key_id = columns[self.scanning_x][y]
            keys = self.find_keys_from_ids([key_id])
            if keys:
                self.active_scan_key = keys[0]
                self.active_scan_key.beingScanned = True

            self.queue_draw()

        return True

    def get_key_at_location(self, location):
        # First try all keys of the active layer
        for item in reversed(list(self.layout.iter_layer_keys(self.active_layer))):
            if item.visible and item.is_point_within(location):
                return item

        # Then check all non-layer keys (layer switcher, hide, etc.)
        for item in reversed(list(self.layout.iter_layer_keys(None))):
            if item.visible and item.is_point_within(location):
                return item

    def cb_dialog_response(self, dialog, response, snippet_id, \
                           label_entry, text_entry):
        if response == Gtk.ResponseType.OK:
            label = label_entry.get_text().decode("utf-8")
            text = text_entry.get_text().decode("utf-8")
            config.set_snippet(snippet_id, (label, text))
        dialog.destroy()
        self.editing_snippet = False

    def cb_macroEntry_activate(self,widget,macroNo,dialog):
        self.set_new_macro(macroNo, gtk.RESPONSE_OK, widget, dialog)

    def set_new_macro(self,macroNo,response,macroEntry,dialog):
        if response == gtk.RESPONSE_OK:
            config.set_snippet(macroNo, macroEntry.get_text())

        dialog.destroy()

    def _on_mods_changed(self):
        raise NotImplementedException()

    def press_key(self, key, button = 1):
        if not key.sensitive:
            return

        key.pressed = True

        if not key.latched:
            if self.mods[8]:
                self.alt_locked = True
                self.vk.lock_mod(8)

        if not key.sticky or not key.latched:
            # punctuation duties before keypress is sent
            self.send_punctuation_prefix(key)

            # press key
            self.send_press_key(key, button)

            # update input_line with pressed key
            if self.track_input(key):
                self.commit_input_line()

            # Modifier keys may change multiple keys -> redraw everything
            if key.action_type == KeyCommon.MODIFIER_ACTION:
                self.redraw()

        self.redraw(key)

    def release_key(self, key, button = 1):
        if not key.sensitive:
            return

        if key.sticky:
            disable_locked_state = config.lockdown.disable_locked_state

            # special case caps-lock key:
            # CAPS skips latched state and goes directly
            # into the locked position.
            if not key.latched and \
               (not key.id in ["CAPS"] or \
                disable_locked_state):
                key.latched = True
                self._latched_sticky_keys.append(key)

            elif not key.locked and \
                 not disable_locked_state:
                if key in self._latched_sticky_keys: # not CAPS
                    self._latched_sticky_keys.remove(key)
                self._locked_sticky_keys.append(key)
                key.latched = True
                key.locked = True

            else:
                if key in self._latched_sticky_keys: # with disable_locked_state
                    self._latched_sticky_keys.remove(key)
                if key in self._locked_sticky_keys:
                    self._locked_sticky_keys.remove(key)
                self.send_release_key(key)
                key.latched = False
                key.locked = False
                if key.action_type == KeyCommon.MODIFIER_ACTION:
                    self.redraw()   # redraw the whole keyboard
        else:
            self.send_release_key(key, button)

            # add punctuation suffix
            cap_keys = None
            if config.wp.auto_punctuation:
                suffix = self.punctuator.build_suffix() # unicode
                if self.press_key_string(suffix):
                    self.
                    # stuck keys off
                    for key in self.find_keys_from_ids(("LFSH",)):
                        if key.on or key.stuckOn:
                            key.on = False
                            key.stuckOn = False
                            if key in self.stuck:
                                self.stuck.remove(key)
                    # capitalization on
                    cap_keys = self.find_keys_from_ids(("RTSH",))
                    for key in cap_keys:
                        key.on = True
                        key.stuckOn = False
                        if key not in self.stuck:
                            self.stuck.append(key)
                    self.vk.lock_mod(1)
                    self.mods[1] = 1   # shift

            self.find_word_choices()

            # Don't release latched modifiers for click buttons right now.
            # Keep modifier keys unchanged until the actual click happens
            # -> allow clicks with modifiers
            if not key.is_layer_button() and \
               not (key.action_type == KeyCommon.BUTTON_ACTION and \
                key.id in ["middleclick", "secondaryclick"]):
                # release latched modifiers
                self.release_latched_sticky_keys()

            # switch to layer 0
            if not key.is_layer_button() and \
               not key.id in ["move", "showclick"] and \
               not self.editing_snippet:
                if self.active_layer_index != 0 and not self.layer_locked:
                    self.active_layer_index = 0
                    self.redraw()

        self.update_ui()

        self.unpress_key(key)

    def send_press_key(self, key, button=1):

        if key.action_type == KeyCommon.CHAR_ACTION:
            self.vk.press_unicode(self.utf8_to_unicode(key.action))

        elif key.action_type == KeyCommon.KEYSYM_ACTION:
            self.vk.press_keysym(key.action)
        elif key.action_type == KeyCommon.KEYPRESS_NAME_ACTION:
            self.vk.press_keysym(get_keysym_from_name(key.action))
        elif key.action_type == KeyCommon.MODIFIER_ACTION:
            mod = key.action

            if not mod == 8: #Hack since alt puts metacity into move mode and prevents clicks reaching widget.
                self.vk.lock_mod(mod)
            self.mods[mod] += 1
        elif key.action_type == KeyCommon.MACRO_ACTION:
            snippet_id = string.atoi(key.action)
            mlabel, mString = config.snippets.get(snippet_id, (None, None))
            if mString:
                self.press_key_string(mString)

            elif not config.xid_mode:  # block dialog in xembed mode
                dialog = Gtk.Dialog(_("New snippet"),
                                    self.get_toplevel(), 0,
                                    (Gtk.STOCK_CANCEL,
                                     Gtk.ResponseType.CANCEL,
                                     _("_Save snippet"),
                                     Gtk.ResponseType.OK))

                dialog.set_default_response(Gtk.ResponseType.OK)

                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=12, border_width=5)
                dialog.get_content_area().add(box)

                msg = Gtk.Label(_("Enter a new snippet for this button:"),
                                xalign=0.0)
                box.add(msg)

                label_entry = Gtk.Entry(hexpand=True)
                text_entry  = Gtk.Entry(hexpand=True)
                label_label = Gtk.Label(_("_Button label:"),
                                        xalign=0.0,
                                        use_underline=True,
                                        mnemonic_widget=label_entry)
                text_label  = Gtk.Label(_("S_nippet:"),
                                        xalign=0.0,
                                        use_underline=True,
                                        mnemonic_widget=text_entry)

                grid = Gtk.Grid(row_spacing=6, column_spacing=3)
                grid.attach(label_label, 0, 0, 1, 1)
                grid.attach(text_label, 0, 1, 1, 1)
                grid.attach(label_entry, 1, 0, 1, 1)
                grid.attach(text_entry, 1, 1, 1, 1)
                box.add(grid)

                dialog.connect("response", self.cb_dialog_response, \
                               snippet_id, label_entry, text_entry)
                label_entry.grab_focus()
                dialog.show_all()
                self.editing_snippet = True

        elif key.action_type == KeyCommon.KEYCODE_ACTION:
            self.vk.press_keycode(key.action)

        elif key.action_type == KeyCommon.SCRIPT_ACTION:
            if not config.xid_mode:  # block settings dialog in xembed mode
                if key.action:
                    run_script(key.action)

        elif key.action_type == KeyCommon.WORD_ACTION:
            s  = self.get_match_remainder(key.action) # unicode
            if config.wp.auto_punctuation and button != 3:
                self.punctuator.set_end_of_word()
            self.press_key_string(s)

        elif key.action_type == KeyCommon.BUTTON_ACTION:
            controller = self.button_controllers.get(key)
            if controller:
                controller.press(button)

    def release_latched_sticky_keys(self, except_keys = None):
        """ release latched sticky (modifier) keys """
        if len(self._latched_sticky_keys) > 0:
            for key in self._latched_sticky_keys[:]:
                if not except_keys or not key in except_keys:
                    self.send_release_key(key)
                    self._latched_sticky_keys.remove(key)
                    key.latched = False

            # modifiers may change many key labels -> redraw everything
            self.redraw()

    def release_locked_sticky_keys(self):
        """ release locked sticky (modifier) keys """
        if len(self._locked_sticky_keys) > 0:
            for key in self._locked_sticky_keys[:]:
                self.send_release_key(key)
                self._locked_sticky_keys.remove(key)
                key.latched = False
                key.locked = False
                key.pressed = False

            # modifiers may change many key labels -> redraw everything
            self.redraw()

    def send_release_key(self,key, button = 1):
        if key.action_type == KeyCommon.CHAR_ACTION:
            self.vk.release_unicode(self.utf8_to_unicode(key.action))
        elif key.action_type == KeyCommon.KEYSYM_ACTION:
            self.vk.release_keysym(key.action)
        elif key.action_type == KeyCommon.KEYPRESS_NAME_ACTION:
            self.vk.release_keysym(get_keysym_from_name(key.action))
        elif key.action_type == KeyCommon.KEYCODE_ACTION:
            self.vk.release_keycode(key.action);
        elif key.action_type == KeyCommon.MACRO_ACTION:
            pass
        elif key.action_type == KeyCommon.SCRIPT_ACTION:
            pass
        elif key.action_type == KeyCommon.BUTTON_ACTION:
            controller = self.button_controllers.get(key)
            if controller:
                controller.release(button)
        elif key.action_type == KeyCommon.MODIFIER_ACTION:
            mod = key.action

            if not mod == 8:
                self.vk.unlock_mod(mod)

            self.mods[mod] -= 1

        if self.alt_locked:
            self.alt_locked = False
            self.vk.unlock_mod(8)


    def unpress_key(self, key):
        # Makes sure we draw key pressed before unpressing it.
        GObject.idle_add(self.unpress_key_idle, key)

    def unpress_key_idle(self, key):
        key.pressed = False
        self.redraw(key)
        return False


    def press_key_string(self, keystr):
        """
        Send key presses for all characters in a unicode string
        and keep track of the changes in input_line.
        """
        capitalize = False

        keystr = keystr.replace(u"\\n", u"\n")

        for ch in keystr:
            if ch == u"\b":   # backspace?
                keysym = get_keysym_from_name("backspace")
                self.vk.press_keysym  (keysym)
                self.vk.release_keysym(keysym)

                if not config.wp.stealth_mode:
                    self.input_line.delete_left()

            elif ch == u"\x0e":  # set to upper case at sentence begin?
                capitalize = True

            elif ch == u"\n":
                # press_unicode("\n") fails in gedit.
                # -> explicitely send the key symbol instead
                keysym = get_keysym_from_name("return")
                self.vk.press_keysym  (keysym)
                self.vk.release_keysym(keysym)
            else:             # any other printable keys
                self.vk.press_unicode(ord(ch))
                self.vk.release_unicode(ord(ch))

                if not config.wp.stealth_mode:
                    self.input_line.insert(ch)

        return capitalize

    def update_ui(self):
        # update buttons
        for controller in self.button_controllers.values():
            controller.update()

        self.update_inputline()
        self.update_wordlists()

        self.update_layout()

    def update_layout(self):
        layout = self.layout

        # show/hide layers
        layers = layout.get_layer_ids()
        if layers:
            layout.set_visible_layers([layers[0], self.active_layer])

        # show/hide move button
        #keys = self.find_keys_from_ids(["move"])
        #for key in keys:
        #    key.visible = not config.enable_decoration

        # recalculate items rectangles
        rect = self.canvas_rect.deflate(config.get_frame_width())
        #keep_aspect = config.xid_mode and self.supports_alpha()
        keep_aspect = False
        layout.fit_inside_canvas(rect, keep_aspect)

        # recalculate font sizes
        self.update_font_sizes()

    def on_outside_click(self):
        # release latched modifier keys
        mc = config.clickmapper
        if mc.get_click_button() != mc.PRIMARY_BUTTON:
            self.release_latched_sticky_keys()

        self.commit_input_line()

        self.update_ui()

    def get_mouse_controller(self):
        if config.mousetweaks and \
           config.mousetweaks.is_active():
            return config.mousetweaks
        return config.clickmapper

    def track_input(self, key):
        """
        word prediction:
        Sync input_line with single key presses.
        WORD_ACTION and MACRO_ACTION do this in press_key_string.
        """
        end_editing = False

        if config.wp.stealth_mode:
            return  True

        id = key.id.upper()
        char = key.get_label()
        #print  id," '"+char +"'",key.action_type
        if char is None or len(char) > 1:
            char = u""

        if key.action_type == KeyCommon.WORD_ACTION:
            pass # don't reset input on word insertion

        elif key.action_type == KeyCommon.MODIFIER_ACTION:
            pass  # simply pressing a modifier shouldn't stop the word

        elif key.action_type == KeyCommon.BUTTON_ACTION:
            pass

        elif key.action_type == KeyCommon.KEYSYM_ACTION:
            if   id == 'ESC':
                self.input_line.reset()
            end_editing = True

        elif key.action_type == KeyCommon.KEYPRESS_NAME_ACTION:
            if   id == 'DELE':
                self.input_line.delete_right()
            elif id == 'LEFT':
                self.input_line.move_cursor(-1)
            elif id == 'RGHT':
                self.input_line.move_cursor(1)
            else:
                end_editing = True

        elif key.action_type == KeyCommon.KEYCODE_ACTION:
            if   id == 'RTRN':
                char = u"\n"
            elif id == 'SPCE':
                char = u" "
            elif id == 'TAB':
                char = u"\t"

            if id == 'BKSP':
                self.input_line.delete_left()
            elif self.input_line.is_printable(char):
                if self.mods[4]:  # ctrl+key press?
                    end_editing = True
                else:
                    self.input_line.insert(char)
            else:
                end_editing = True
        else:
            end_editing = True

        if not self.input_line.is_valid(): # cursor moved outside known range?
            end_editing = True

        #print end_editing,"'%s' " % self.input_line.line, self.input_line.cursor
        return end_editing

    def update_inputline(self):
        if self.predictor:
            for key in self.find_keys_from_ids(["inputline"]):
                line = self.input_line.line
                if line:
                    key.raise_to_top()
                    key.visible = True
                else:
                    line = u""
                    key.visible = False
                key.set_content(line, self.word_infos, self.input_line.cursor)
                # print [(x.start, x.end) for x in word_infos]

    def update_wordlists(self):
        if self.predictor:
            for item in self.layout.find_ids(["wordlist"]):
                word_keys = self.create_wordlist_keys(self.word_choices,
                                                item.get_rect(), item.context)
                fixed_keys = item.find_ids(["wordlistbg"])
                item.set_items(fixed_keys + word_keys)
                self.redraw(item)
            return

            for key in self.find_keys_from_ids(["wordlist"]):
                key.set_items(self.create_wordlist_keys(self.word_choices,
                                                      key.get_rect(), key.context))
                key.raise_to_top()
                self.redraw(key)

    def find_word_choices(self):
        """ word prediction: find choices, only once per key press """
        self.word_choices = []
        if self.predictor:
            context = self.input_line.get_context()
            self.word_choices = self.predictor.predict(context)
            #print "input_line='%s'" % self.input_line.line

            # update word information for the input line display
            self.word_infos = self.predictor.get_word_infos(self.input_line.line)

    def get_match_remainder(self, index):
        """ returns the rest of matches[index] that hasn't been typed yet """
        if not self.predictor:
            return ""
        text = self.input_line.get_context()
        word_prefix = self.predictor.get_last_context_token(text)
        #print self.word_choices[index], word_prefix
        return self.word_choices[index][len(word_prefix):]

    def commit_input_line(self):
        """ word prediction: try to learn all words and clear the input line """
        changed = self.input_line.is_empty()

        if self.predictor and config.wp.can_auto_learn():
            self.predictor.learn_text(self.input_line.line, True)

        self.punctuator.reset()
        self.input_line.reset()
        self.word_choices = []
        return changed

    def enable_word_prediction(self, enable):
        if enable:
            # only load dictionaries if there is a
            # dynamic or static wordlist in the layout
            if self.find_keys_from_ids(("wordlist", "word0")):
                self.predictor = WordPredictor()
                self.apply_prediction_profile()
        else:
            if self.predictor:
                self.predictor.save_dictionaries()
            self.predictor = None

        # show/hide word-prediction buttons
        for item in self.layout.iter_items():
            if item.group in ("inputline", "wordlist", "word", "wpbutton"):
                item.visible = enable

    def apply_prediction_profile(self):
        if self.predictor:
            # todo: settings
            system_models = ["lm:system:en"]
            user_models = ["lm:user:en"]
            auto_learn_model = user_models
            self.predictor.set_models(system_models,
                                      user_models,
                                      auto_learn_model)

    def send_punctuation_prefix(self, key):
        if config.wp.auto_punctuation:
            if key.action_type == KeyCommon.KEYCODE_ACTION:
                char = key.get_label()
                prefix = self.punctuator.build_prefix(char) # unicode
                self.press_key_string(prefix)

    def cleanup(self):
        # resets still latched and locked modifier keys on exit
        self.release_latched_sticky_keys()
        self.release_locked_sticky_keys()

        for key in self.iter_keys():
            if key.pressed and key.action_type in \
                [KeyCommon.CHAR_ACTION,
                 KeyCommon.KEYSYM_ACTION,
                 KeyCommon.KEYPRESS_NAME_ACTION,
                 KeyCommon.KEYCODE_ACTION]:

                # Release still pressed enter key when onboard gets killed
                # on enter key press.
                _logger.debug(_("Releasing still pressed key '{}'") \
                             .format(key.id))
                self.send_release_key(key)

        # Somehow keyboard objects don't get released
        # when switching layouts, there are still
        # excess references/memory leaks somewhere.
        # Therefore virtkey references have to be released
        # explicitely or Xlib runs out of client connections
        # after a couple dozen layout switches.
        self.vk = None

    def find_keys_from_ids(self, key_ids):
        if self.layout is None:
            return []
        return self.layout.find_ids(key_ids)



class ButtonController(object):
    """
    MVC inspired controller that handles events and the resulting
    state changes of buttons.
    """
    def __init__(self, keyboard, key):
        self.keyboard = keyboard
        self.key = key

    def press(self, button):
        """ button pressed """
        pass

    def release(self, button):
        """ button released """
        pass

    def update(self):
        """ asynchronous ui update """
        pass

    def can_dwell(self):
        """ can start dwell operation? """
        return False

    def set_visible(self, visible):
        if self.key.visible != visible:
            self.key.visible = visible
            self.keyboard.redraw(self.key)

    def set_sensitive(self, sensitive):
        if self.key.sensitive != sensitive:
            self.key.sensitive = sensitive
            self.keyboard.redraw(self.key)

    def set_latched(self, latched = None):
        if not latched is None and self.key.latched != latched:
            self.key.latched = latched
            self.keyboard.redraw(self.key)

    def set_locked(self, locked = None):
        if not locked is None and self.key.locked != locked:
            self.key.locked = locked
            self.keyboard.redraw(self.key)


class BCClick(ButtonController):
    """ Controller for click buttons """
    def release(self, button):
        mc = self.keyboard.get_mouse_controller()
        if mc.get_click_button() == self.button and \
           mc.get_click_type() == self.click_type:
            # stop click mapping, resets to primary button and single click
            mc.set_click_params(MouseController.PRIMARY_BUTTON,
                                MouseController.CLICK_TYPE_SINGLE)
        else:
            # Exclude click type buttons from the click mapping.
            # -> They will receive only single left clicks.
            #    This allows to reliably cancel the click.
            rects = self.keyboard.get_click_type_button_rects()
            config.clickmapper.set_exclusion_rects(rects)

            # start the click mapping
            mc.set_click_params(self.button, self.click_type)

    def update(self):
        mc = self.keyboard.get_mouse_controller()
        self.set_latched(mc.get_click_button() == self.button and \
                         mc.get_click_type() == self.click_type)
        self.set_sensitive(
            mc.supports_click_params(self.button, self.click_type))

class BCSingleClick(BCClick):
    id = "singleclick"
    button = MouseController.PRIMARY_BUTTON
    click_type = MouseController.CLICK_TYPE_SINGLE

class BCMiddleClick(BCClick):
    id = "middleclick"
    button = MouseController.MIDDLE_BUTTON
    click_type = MouseController.CLICK_TYPE_SINGLE

class BCSecondaryClick(BCClick):
    id = "secondaryclick"
    button = MouseController.SECONDARY_BUTTON
    click_type = MouseController.CLICK_TYPE_SINGLE

class BCDoubleClick(BCClick):
    id = "doubleclick"
    button = MouseController.PRIMARY_BUTTON
    click_type = MouseController.CLICK_TYPE_DOUBLE

class BCDragClick(BCClick):
    id = "dragclick"
    button = MouseController.PRIMARY_BUTTON
    click_type = MouseController.CLICK_TYPE_DRAG

class BCHoverClick(ButtonController):

    id = "hoverclick"

    def release(self, button):
        config.enable_hover_click(not config.mousetweaks.is_active())

    def update(self):
        available = bool(config.mousetweaks)
        active    = config.mousetweaks.is_active() \
                    if available else False

        self.set_sensitive(available and \
                           not config.lockdown.disable_hover_click)
        # force locked color for better visibility
        self.set_locked(active)
        #self.set_latched(config.mousetweaks.is_active())

    def can_dwell(self):
        return not (config.mousetweaks and config.mousetweaks.is_active())

class BCHide(ButtonController):

    id = "hide"

    def release(self, button):
        self.keyboard.toggle_visible()

    def update(self):
        self.set_sensitive(not config.xid_mode) # insensitive in XEmbed mode

class BCShowClick(ButtonController):

    id = "showclick"

    def release(self, button):
        config.show_click_buttons = not config.show_click_buttons

        # enable hover click when the key was dwell-activated
        # disabled for now, seems too confusing
        if False:
            if button == self.keyboard.DWELL_ACTIVATED and \
               config.show_click_buttons and \
               not config.mousetweaks.is_active():
                config.enable_hover_click(True)

    def update(self):
        allowed = not config.lockdown.disable_click_buttons

        self.set_visible(allowed)

        # Don't show latched state. Toggling the click column
        # should be enough feedback.
        #self.set_latched(config.show_click_buttons)

        # show/hide click buttons
        show_click = config.show_click_buttons and allowed
        for item in self.keyboard.layout.iter_items():
            if item.group == 'click':
                item.visible = show_click
            if item.group == 'noclick':
                item.visible = not show_click


    def can_dwell(self):
        return not config.mousetweaks or not config.mousetweaks.is_active()

class BCMove(ButtonController):

    id = "move"

    def press(self, button):
        self.keyboard.start_move_window()

    def release(self, button):
        self.keyboard.stop_move_window()

    def update(self):
        self.set_visible(not config.window_decoration)
        self.set_sensitive(not config.xid_mode)

class BCLayer(ButtonController):
    """ layer switch button, switches to layer <layer_index> when released """

    layer_index = None

    def _get_id(self):
        return "layer" + str(self.layer_index)
    id = property(_get_id)

    def release(self, button):
        layer_index = self.key.get_layer_index()
        if self.keyboard.active_layer_index != layer_index:
            self.keyboard.active_layer_index = layer_index
            self.keyboard.layer_locked = False
            self.keyboard.redraw()
        elif self.layer_index != 0:
            if not self.keyboard.layer_locked and \
               not config.lockdown.disable_locked_state:
                self.keyboard.layer_locked = True
            else:
                self.keyboard.active_layer_index = 0
                self.keyboard.layer_locked = False
                self.keyboard.redraw()

    def update(self):
        # don't show latched state for layer 0, it'd be visible all the time
        latched = self.key.get_layer_index() != 0 and \
                  self.key.get_layer_index() == self.keyboard.active_layer_index
        self.set_latched(latched)
        self.set_locked(latched and self.keyboard.layer_locked)


class BCPreferences(ButtonController):

    id = "settings"

    def release(self, button):
        run_script("sokSettings")

    def update(self):
        self.set_sensitive(not config.xid_mode and \
                           not config.running_under_gdm and \
                           not config.lockdown.disable_preferences)

class BCQuit(ButtonController):

    id = "quit"

    def release(self, button):
        self.keyboard.emit_quit_onboard()

    def update(self):
        self.set_sensitive(not config.xid_mode and not config.lockdown.disable_quit)


class BCAutoLearn(ButtonController):

    id = "learnmode"

    def release(self, button):
        config.wp.auto_learn = not config.wp.auto_learn

        # don't learn when turning auto_learn off
        if not config.wp.auto_learn:
            self.keyboard.input_line.reset()

        # turning on auto_learn disables stealth_mode
        if config.wp.auto_learn and config.wp.stealth_mode:
            config.wp.stealth_mode = False

    def update(self):
        self.set_latched(config.wp.auto_learn)


class BCAutoPunctuation(ButtonController):

    id = "punctuation"

    def release(self, button):
        config.wp.auto_punctuation = not config.wp.auto_punctuation
        self.keyboard.punctuator.reset()

    def update(self):
        self.set_latched(config.wp.auto_punctuation)


class BCStealthMode(ButtonController):

    id = "stealthmode"

    def release(self, button):
        config.wp.stealth_mode = not config.wp.stealth_mode

        # don't learn, forget words when stealth mode is enabled
        if config.wp.stealth_mode:
            self.keyboard.input_line.reset()

    def update(self):
        self.set_latched(config.wp.stealth_mode)


class BCInputline(ButtonController):

    id = "inputline"

    def release(self, button):
        self.keyboard.commit_input_line()


