# -*- coding: UTF-8 -*-
"""
KeyCommon hosts the abstract classes for the various types of Keys.
UI-specific keys should be defined in KeyGtk or KeyKDE files.
"""

from math import sqrt

from Onboard.utils import Rect

### Logging ###
import logging
_logger = logging.getLogger("KeyCommon")
###############

### Config Singleton ###
from Onboard.Config import Config
config = Config()
########################

BASE_PANE_TAB_HEIGHT = 40

(CHAR_ACTION, KEYSYM_ACTION, KEYCODE_ACTION, MODIFIER_ACTION, MACRO_ACTION,
    SCRIPT_ACTION, KEYPRESS_NAME_ACTION, BUTTON_ACTION) = range(1,9)


class KeyCommon(object):
    """
    library-independent key class. Specific rendering options
    are stored elsewhere.
    """

    # Type of action to do when key is pressed.
    action_type = None

    # Data used in action.
    action = None

    # True when key is being pressed.
    on = False

    # When key is sticky and pressed twice.
    stuckOn = False

    # Keys that stay stuck when pressed like modifiers.
    sticky = False

    # True when key stays pressed down permanently vs. the transient 'on'
    checked = False

    # True when Onboard is in scanning mode and key is highlighted
    beingScanned = False

    # Size to draw the label text in Pango units
    font_size = 1

    # Index in labels that is currently displayed by this key
    label_index = 0

    # Labels which are displayed by this key
    labels = None

    # horizontal label alignment
    label_x_align = config.DEFAULT_LABEL_X_ALIGN

    # vertical label alignment
    label_y_align = config.DEFAULT_LABEL_Y_ALIGN

    # State of visibility
    visible = True

###################

    def __init__(self):
        pass

    def on_size_changed(self, pane_context):
        raise NotImplementedError()

    def configure_label(self, mods, pane_context):
        if mods[1]:
            if mods[128] and self.labels[4]:
                self.label_index = 4
            elif self.labels[2]:
                self.label_index = 2
            elif self.labels[1]:
                self.label_index = 1
            else:
                self.label_index = 0

        elif mods[128] and self.labels[3]:
            self.label_index = 3

        elif mods[2]:
            if self.labels[1]:
                self.label_index = 1
            else:
                self.label_index = 0
        else:
            self.label_index = 0

    def draw_font(self, pane_context, location, context = None):
        raise NotImplementedError()

    def get_label(self):
        return self.labels[self.label_index]

    def is_active(self):
        return not self.action_type is None

    def get_name(self):
        return ""

    def is_visible(self):
        return self.visible

    def get_bounds(self):
        """ return ((left, top), (right, bottom)) of the bounding rectangle """
        return None

    def point_within_key(self, point, pane_context):
        """ does exactly what the name says - checks for the
            mouse within a key. returns bool. """
        log_point = pane_context.canvas_to_log(point)
        return self.get_rect().point_inside(log_point)



class TabKeyCommon(KeyCommon):
    """ class for those tabs up the right hand side """

    # Pane that this key is on.
    pane = None

    def __init__(self, keyboard, width, pane):
        KeyCommon.__init__(self)

        self.pane = pane
        self.width = width
        self.keyboard = keyboard
        self.modifier = None # what for?
        self.sticky = True

    def draw(self, context):
        """ draws the TabKey object """
        self.height = (self.keyboard.height / len(self.keyboard.panes)) - (BASE_PANE_TAB_HEIGHT / len(self.keyboard.panes))
        self.index = self.keyboard.panes.index(self.pane)

    def get_label(self):
        return ""

    def get_rect(self):
        """ Bounding rectangle in logical coordinates """
        rect = Rect(self.keyboard.kbwidth,
                    self.height * self.index + BASE_PANE_TAB_HEIGHT,
                    self.width,
                    self.height)
        pane_context = self.keyboard.activePane.pane_context
        return pane_context.canvas_to_log_rect(rect)


class BaseTabKeyCommon(KeyCommon):
    """ class for the tab that brings you to the base pane """

    # Pane that this key is on.
    pane = None

    def __init__(self, keyboard, width):
        KeyCommon.__init__(self)

        self.width = width
        self.keyboard = keyboard
        self.modifier = None # what for?
        self.sticky = False

    def get_rect(self):
        """ Bounding rectangle in logical coordinates """
        rect =  Rect(self.keyboard.kbwidth,
                     0,
                     self.width,
                     BASE_PANE_TAB_HEIGHT)
        pane_context = self.keyboard.activePane.pane_context
        return pane_context.canvas_to_log_rect(rect)

    def draw(self,context=None):
        """Don't draw anything for this key"""
        pass

    def get_label(self):
        return ""


class LineKeyCommon(KeyCommon):
    """ class for keyboard buttons made of lines """

    def __init__(self, name, pane, coordList, fontCoord, rgba):
        KeyCommon.__init__(self, pane)
        self.coordList = coordList
        self.fontCoord = fontCoord
        # pane? (m)

    def pointCrossesEdge(self, x, y, xp1, yp1, sMouseX, sMouseY):
        """ Checks whether a point, when scanning from top left crosses edge"""
        return ((((y <= sMouseY) and ( sMouseY < yp1)) or
            ((yp1 <= sMouseY) and (sMouseY < y))) and
            (sMouseX < (xp1 - x) * (sMouseY - y) / (yp1 - y) + x))


    def point_within_key(self, location, pane_context):
        """Checks whether point is within shape.
           Currently does not bother trying to work out
           curved paths accurately. """

        _logger.warning("LineKeyGtk should be using the implementation in KeyGtk")

        x = self.coordList[0]
        y = self.coordList[1]
        c = 2
        coordLen = len(self.coordList)
        within = False

        sMouseX,sMouseY = pane_context.canvas_to_log(location)

        while not c == coordLen:

            xp1 = self.coordList[c+1]
            yp1 = self.coordList[c+2]
            try:
                if self.coordList[c] == "L":
                    within = (self.pointCrossesEdge(x,y,xp1,yp1,sMouseX,sMouseY) ^ within) # a xor
                    c +=3
                    x = xp1
                    y = yp1

                else:
                    xp2 = self.coordList[c+3]
                    yp2 = self.coordList[c+4]
                    xp3 = self.coordList[c+5]
                    yp3 = self.coordList[c+6]
                    within = (self.pointCrossesEdge(x,y,xp3,yp3,sMouseX,sMouseY) ^ within) # a xor
                    x = xp3
                    y = yp3
                    c += 7

            except ZeroDivisionError, (strerror):
                print strerror
                print "x: %f, y: %f, yp1: %f" % (x,y,yp1)
        return within

    def draw(self, pane_context, context = None):
        """
        This class is quite hard to abstract, so all of its
        processing lies now in the UI-dependent class.
        """

    def draw_font(self, pane_context):
        KeyCommon.draw_font(self, pane_context,
            (self.coordList[0], self.coordList[1]))

    def get_bounds(self):  # sample implementation, probably not working as is
        """ return ((left, top), (right, bottom)) of the bounding rectangle """
        if self.coordList:
            l,t = self.coordList[0]
            r,b = self.coordList[0]
            for x,y in self.coordList:
                l = min(l,x)
                t = min(t,y)
                r = max(r,x)
                b = max(b,y)
            return (l,t),(r,b)
        return None


class RectKeyCommon(KeyCommon):
    """ An abstract class for rectangular keyboard buttons """

    # Unique identifier of the key
    name = None

    # Coordinates of the key on the keyboard
    location = None

    # Width and height of the key
    geometry = None

    # Fill colour of the key
    rgba = None

    # Mouse over colour of the key
    hover_rgba   = None

    # Pushed down colour of the key
    pressed_rgba   = None

    # On colour of modifier key
    latched_rgba = None

    # Locked colour of modifier key
    locked_rgba  = None

    # Colour for key being scanned
    scanned_rgba  = None

    # Outline colour of the key in flat mode
    stroke_rgba = None

    # Four tuple with values between 0 and 1 containing label color
    label_rgba = None

    def __init__(self, name, location, geometry, rgba):
        KeyCommon.__init__(self)
        self.name = name
        self.location = location
        self.geometry = geometry
        self.rgba = rgba

    def get_name(self):
        return self.name

    def draw(self, pane_context, context = None):
        pass

    def align_label(self, label_size, key_size):
        """ returns x- and yoffset of the aligned label """
        xoffset = self.label_x_align * (key_size[0] - label_size[0])
        yoffset = self.label_y_align * (key_size[1] - label_size[1])
        return xoffset, yoffset

    def get_fill_color(self):
        if self.stuckOn:
            fill = self.locked_rgba
        elif self.on:
            fill = self.latched_rgba
        elif self.checked:
            fill = self.latched_rgba
        elif self.beingScanned:
            fill = self.scanned_rgba
        else:
            fill = self.rgba
        return fill

    def get_bounds(self):
        """ return ((left, top), (right, bottom)) of the bounding rectangle """
        return self.location, (self.location[0]+self.geometry[0],
                               self.location[1]+self.geometry[1])

    def get_rect(self):
        """ Bounding rectangle in logical coordinates """
        return Rect(self.location[0],
                    self.location[1],
                    self.geometry[0],
                    self.geometry[1])

