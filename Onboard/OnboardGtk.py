# -*- coding: UTF-8 -*-

from xml.dom import minidom
import sys
import gobject
gobject.threads_init()

import gtk
import re
import string
import virtkey
import gconf
import gettext
import os.path
import gettext

from gettext import gettext as _

from Onboard.Keyboard import Keyboard
from KeyGtk import * 
from Onboard.Pane import Pane
from Onboard.KbdWindow import KbdWindow
from optparse import OptionParser

import KeyCommon

# can't we just import Onboard.utils and then use Onboard.utils.run_script ?
from Onboard.utils import run_script

import Onboard.utils as utils


#setup gettext
app="onboard"
gettext.textdomain(app)
gettext.bindtextdomain(app)

DEFAULT_FONTSIZE = 10

class OnboardGtk(object):
    """
    This class is a mishmash of things that I didn't have time to refactor in to seperate classes.
    It needs a lot of work.
    The name comes from onboards original working name of simple onscreen keyboard.  
    """
    def __init__(self, main=True):
        
        parser = OptionParser()
        parser.add_option("-l", "--layout", dest="filename",help="Specify layout .sok file")
        parser.add_option("-x", dest="x",help="x coord of window")
        parser.add_option("-y", dest="y",help="y coord of window")

        parser.add_option("-s", "--size",dest="size",help="size widthxheight")

        (options,args) = parser.parse_args()            

        self.SOK_INSTALL_DIR = utils.get_install_dir()       
        if not self.SOK_INSTALL_DIR:
            print "Onboard not installed properly"
            return

        sys.path.append(os.path.join(self.SOK_INSTALL_DIR,'scripts'))
        

        # this object is the source of all layout info and where we send key presses to be emulated.
        self.vk = virtkey.virtkey()

        self.gconfClient = gconf.client_get_default()
        # Get the location of the current layout .sok file from gconf.
        self.gconfClient.add_dir("/apps/sok",gconf.CLIENT_PRELOAD_NONE)

        if options.filename:
            filename = options.filename
        else:
            filename = self.gconfClient.get_string("/apps/sok/layout_filename")

        if not filename:
            filename = os.path.join(self.SOK_INSTALL_DIR, 
                                                        layouts, "Default.sok")

        if not os.path.exists(filename):
            self.load_default_layout()
        else:
            self.load_layout(filename)
        
        # populates list of macros or "snippets" from gconf
        self.macros = self.gconfClient.get_list("/apps/sok/macros",gconf.VALUE_STRING)
        
        self.window = KbdWindow(self)
        self.window.set_keyboard(self.keyboard)

        x = -1
        y = -1

        if (options.x):
            x = int(options.x)

        if (options.y):
            y = int(options.y)

        self.window.move(x, y)
        

        if (options.size):
            size = options.size.split("x")
            self.window.set_default_size(int(size[0]),int(size[1]))

        #Create menu for trayicon
        uiManager = gtk.UIManager()
        
        actionGroup = gtk.ActionGroup('UIManagerExample')
        actionGroup.add_actions([('Quit', gtk.STOCK_QUIT, _('_Quit'), None,
                                  _('Quit onBoard'), self.quit),
                                 ('Settings', gtk.STOCK_PREFERENCES, _('_Settings'), None, _('Show settings'), self.cb_settings_item_clicked)])

        uiManager.insert_action_group(actionGroup, 0)

        uiManager.add_ui_from_string("""<ui>
                        <popup>
                            <menuitem action="Settings"/>
                            <menuitem action="Quit"/>
                        </popup>
                    </ui>""")
        trayMenu = uiManager.get_widget("/ui/popup")

        # Create the trayicon 
        try:
            self.statusIcon = gtk.status_icon_new_from_file(os.path.join(self.SOK_INSTALL_DIR, "data", "onboard.svg"))
            self.statusIcon.connect("activate", self.cb_status_icon_clicked)
            self.statusIcon.connect("popup-menu", self.cb_status_icon_menu, trayMenu)

        except AttributeError:
            print _("You need pygtk 2.10 or above for the system tray icon")
        

        self.window.hidden = False

        self.window.show_all()
        
        # Watch settings for changes
        self.gconfClient.notify_add("/apps/sok/sizeX",self.window.do_set_size)
        self.gconfClient.notify_add("/apps/sok/layout_filename",self.do_set_layout)
        self.gconfClient.notify_add("/apps/sok/macros",self.do_change_macros)
        self.gconfClient.notify_add("/apps/sok/scanning_interval", self.do_change_scanningInterval)
        self.gconfClient.notify_add("/apps/sok/scanning", self.do_change_scanning)
        self.gconfClient.notify_add("/apps/sok/trayicon", self.do_set_trayicon)
                
        
        if self.gconfClient.get_bool("/apps/sok/trayicon"):
            self.hide_status_icon()
            self.show_status_icon()
        else:
            self.hide_status_icon()
       
        if main:
            # code moved from 'onboard' executable
            gtk.main()
            self.clean()
    
    def cb_settings_item_clicked(self,widget):
        """
        Callback called when setting button clicked in the trayicon menu. 
        """
        run_script("sokSettings",self)

    def cb_status_icon_menu(self,status_icon, button, activate_time,trayMenu):
        """
        Callback called when trayicon right clicked.  Produces menu.
        """
        trayMenu.popup(None, None, gtk.status_icon_position_menu, 
             button, activate_time, status_icon)    

    def do_set_trayicon(self, cxion_id=None, entry=None, user_data=None,thing=None):
        """
        Callback called when gconf detects that the gconf key specifying 
        whether the trayicon should be shown or not is changed.
        """
        if self.gconfClient.get_bool("/apps/sok/trayicon"):
            self.show_status_icon()
        else:
            self.hide_status_icon()
        
    def show_status_icon(self):     
        """
        Shows the status icon.  When it is shown we set a wm hint so that
        onboard does not appear in the taskbar.
        """
        self.statusIcon.set_visible(True)
        self.window.set_property('skip-taskbar-hint', True)

    def hide_status_icon(self):
        """
        The opposite of the above.
        """
        self.statusIcon.set_visible(False)
        self.window.set_property('skip-taskbar-hint', False)

    def cb_status_icon_clicked(self,widget):
        """
        Callback called when trayicon clicked.
        Toggles whether onboard window visibile or not.

        TODO would be nice if appeared to iconify to taskbar
        """
        if self.window.hidden:
            self.window.deiconify()
            self.window.hidden = False          
        else:
            self.window.iconify()
            self.window.hidden = True
            
            
        

    def unstick(self):
        for key in self.keyboard.basePane.keys.values():
            if key.on :
                self.keyboard.release_key(key)
            
        
    def clean(self): #Called when sok is gotten rid off.
        self.unstick()
        self.window.hide()
        
    def quit(self, widget=None):
        self.clean()
        gtk.main_quit()
            
    def do_change_scanning(self, cxion_id, entry, user_data,thing):
        self.keyboard.scanning = self.gconfClient.get_bool("/apps/sok/scanning")
        self.keyboard.reset_scan()
    
    def do_change_scanningInterval(self, cxion_id, entry, user_data,thing):
        self.keyboard.scanningInterval = self.gconfClient.get_int("/apps/sok/scanningInterval")
    
    def do_change_macros(self,client, cxion_id,entry,user_data):
        self.macros = self.gconfClient.get_list("/apps/sok/macros",gconf.VALUE_STRING)
          

    def do_set_layout(self,client, cxion_id, entry, user_data):
        self.unstick()
        filename = self.gconfClient.get_string("/apps/sok/layout_filename")
        if os.path.exists(filename):
            self.load_layout(filename)
            self.window.set_keyboard(self.keyboard)
        else:
            self.load_default_layout()

        self.window.set_keyboard(self.keyboard)
    
    def hexstring_to_float(self,hexString): 
        return float(string.atoi(hexString,16))

    
    def get_sections_keys(self, section, keys, pane, xOffset, yOffset):
        "gets keys for a specified sections from the XServer."
        
        rows = self.vk.layout_get_keys(section)
        
        for row in rows:
            for key in row:
                shape = key['shape']
                name = key['name'].strip(chr(0)) #since odd characters after names shorter than 4.
                
                if name in utils.modDic:
                    nkey = RectKey(pane,float(shape[0] + xOffset),float(shape[1] + yOffset), float(shape[2]), float(shape[3]),(0.95,0.9,0.85,1))
                    props = utils.modDic[name]
                    
                    action = props[1]
                    action_type = KeyCommon.MODIFIER_ACTION

                    labels = (props[0],"","","","")
                    sticky = True
                
                else:            
                    action = key['keycode']
                    action_type = KeyCommon.KEYCODE_ACTION
                    
                    if name in utils.otherDic:
                        
                        nkey = RectKey(pane,float(shape[0] + xOffset),float(shape[1] + yOffset), float(shape[2]), float(shape[3]),(0.85,0.8,0.65,1))
                        labels = (utils.otherDic[name],"","","","")
                    else:
                        nkey = RectKey(pane,float(shape[0]+ xOffset),float(shape[1] + yOffset), float(shape[2]), float(shape[3]),(0.9,0.85,0.7,1))
                        labDic = key['labels']
                        labels = (labDic[0],labDic[2],labDic[1],labDic[3],labDic[4])
                        
                    sticky = False
                    
                    
                nkey.setProperties(action_type, action, labels, sticky, 0, 0)
                    
                keys[name] =  nkey
    
    def load_default_layout(self):
        self.keyboard = Keyboard(self)
        panes = []
        
        sizeA = self.vk.layout_get_section_size("Alpha")
        sizeK = self.vk.layout_get_section_size("Keypad") 
        sizeE = self.vk.layout_get_section_size("Editing")
        sizeF = (294, 94)
        #Tidy this up
        
        
        listX = [sizeA[0],sizeE[0] + sizeK[0] + 20 + 125 ,sizeF[0]]
        listY = [sizeA[1]+ 1,sizeE[1] + 3, sizeK[1]+3,64 ,sizeF[1]] #alpha,editing,keypad,macros,functions
        listX.sort()
        listY.sort()
        sizeX = listX[len(listX)-1]
        sizeY = listY[len(listY)-1]
            
    
    
        keys = {}
        pane = Pane(self.keyboard,"Alpha", keys,None, float(sizeX), float(sizeY), [0,0,0,0.3],DEFAULT_FONTSIZE)
        panes.append(pane)
        self.get_sections_keys("Alpha", keys,pane,0,0)
            
                
        keys = {}
        pane = Pane(self.keyboard,"Editing",keys,None, float(sizeX), float(sizeY), [0.3,0.3,0.7,0.3],DEFAULT_FONTSIZE)
        panes.append(pane)  
        self.get_sections_keys("Editing", keys, pane, 0, 2)
        self.get_sections_keys("Keypad", keys, pane, sizeE[0] + 20 , 2)
        
        for r in range(3):
            for c in range(3):
                n = c + r*3
                mkey = RectKey(pane,sizeE[0] +sizeK[0] +45 + c*38, 7 + r*28, 33, 24,(0.5,0.5,0.8,1))
                mkey.setProperties(KeyCommon.MACRO_ACTION, str(n), 
                                (_("Snippet\n%d") % (n),"","","",""), False,0,0)
                keys["m%d" % (n)] = mkey
        
        keys = {}
        pane = Pane(self.keyboard,"Functions",keys,None, float(sizeX), float(sizeY), [0.6,0.3,0.7,0.3],DEFAULT_FONTSIZE)
        panes.append(pane)
        y = 0
        for n in range(len(utils.funcKeys)):
            if n  >=8:
                y = 27
                m = n -8
            else :
                m = n
            
            fkey = RectKey(pane,5 + m*30, 5 + y, 25, 24,(0.5,0.5,0.8,1))
            fkey.setProperties(KeyCommon.KEYSYM_ACTION, utils.funcKeys[n][1], 
                                (utils.funcKeys[n][0],"","","",""), False,0,0)
            keys[utils.funcKeys[n][0]] = fkey
        
        settingsKey = RectKey(pane,5, 61, 60.0, 30.0,(0.95,0.5,0.5,1))
        settingsKey.setProperties(KeyCommon.SCRIPT_ACTION, "sokSettings", 
                                    (_("Settings"),"","","",""), False, 0, 0)
        keys["settings"] = settingsKey
        
        switchingKey = RectKey(pane,70 ,61,60.0,30.0,(0.95,0.5,0.5,1))
        switchingKey.setProperties(KeyCommon.SCRIPT_ACTION, "switchButtons", 
                                (_("Switch\nButtons"),"","","",""), False, 0, 0)
        keys["switchButtons"] = switchingKey
        
        
        basePane = panes[0]
        otherPanes = panes[1:]

        self.keyboard.set_basePane(basePane)

        for pane in otherPanes:
            self.keyboard.add_pane(pane)
                
    
    
    def load_keys(self,doc,keys):
        
            for key in doc.getElementsByTagName("key"):  
                try:
                    if key.attributes["id"].value in keys:
                        action = None
                        action_type = None

                        if key.hasAttribute("char"):
                            action = key.attributes["char"].value
                            action_type = KeyCommon.CHAR_ACTION
                        elif key.hasAttribute("keysym"):
                            value = key.attributes["keysym"].value
                            action_type = KeyCommon.KEYSYM_ACTION
                            if value[1] == "x":#Deals for when keysym is hex
                                action = string.atoi(value,16)
                            else:
                                action = string.atoi(value,10)
                        elif key.hasAttribute("press"):
                            action = key.attributes["char"].value
                            action_type = KeyCommon.CHAR_ACTION
                        elif key.hasAttribute("modifier"):
                            try:
                                action = utils.modifiers[
                                            key.attributes["modifier"].value]
                                action_type = KeyCommon.MODIFIER_ACTION
                            except KeyError, (strerror):
                                print "Can't find modifier " + str(strerror)
                                
                        elif key.hasAttribute("macro"):
                            action = key.attributes["macro"].value
                            action_type = KeyCommon.MACRO_ACTION
                        elif key.hasAttribute("script"):
                            action = key.attributes["script"].value
                            action_type = KeyCommon.SCRIPT_ACTION
                        elif key.hasAttribute("keycode"):
                            action = string.atoi(
                                                key.attributes["keycode"].value)
                            action_type = KeyCommon.KEYCODE_ACTION

                        labels = ["","","","",""]
                        #if label specified search for modified labels.
                        if key.hasAttribute("label"):
                            labels[0] = key.attributes["label"].value

                            if key.hasAttribute("cap_label"):
                                labels[1] = key.attributes["cap_label"].value
                            if key.hasAttribute("shift_label"):
                                labels[2] = key.attributes["shift_label"].value
                            if key.hasAttribute("altgr_label"):
                                labels[3] = key.attributes["altgr_label"].value
                            if key.hasAttribute("altgrNshift_label"):
                                labels[4] = key.attributes["altgrNshift_label"].value   
                        #Get labels from keyboard.
                        else:
                            if action_type == KeyCommon.KEYCODE_ACTION:
                                labDic = self.vk.labels_from_keycode(action)
                                labels = (labDic[0],labDic[2],labDic[1],
                                                            labDic[3],labDic[4])

                    
                        if key.hasAttribute("font_offset_x"):
                            offsetX = float(key.attributes["font_offset_x"].value)
                        else:
                            offsetX = 0
                        
                        if key.hasAttribute("font_offset_y"):
                            offsetY = float(key.attributes["font_offset_y"].value)
                        else:
                            offsetY = 0
                        
                        
                        stickyString = key.attributes["sticky"].value
                        if stickyString == "true":
                            sticky = True
                        else:
                            sticky= False
                        
                        keys[key.attributes["id"].value].setProperties(
                                            action_type, action, labels,
                                            sticky, offsetX, offsetY)
                except KeyError, (strerror):
                    print "key missing id: " + str(strerror)

    def parse_path(self, path, pane):
        id = path.attributes["id"].value
        styleString = path.attributes["style"].value
        result = re.search("(fill:#\d?\D?\d?\D?\d?\D?\d?\D?\d?\D?\d?\D?;)", styleString).groups()[0]

        rgba = (self.hexstring_to_float(result[6:8])/255,
        self.hexstring_to_float(result[8:10])/255,
        self.hexstring_to_float(result[10:12])/255,
        1)#not bothered for now

        dList = path.attributes["d"].value.split(" ")
        dList = dList[1:-2] #trim unwanted M, Z
        coordList = []

        transformMatrix = None
        if path.hasAttribute("transform"):
            transform = path.attributes["transform"].value
            if transform.startswith("matrix"):
                #Convert strings to floats
                transformCoords = map(float,transform[7:-1].split(","))

                transformMatrix = (
                    (transformCoords[0],transformCoords[2],transformCoords[4]),
                    (transformCoords[1],transformCoords[3],transformCoords[5]),
                    (0, 0, 1))
            elif transform.startswith("translate"):
                transformCoords = map(float,transform[10:-1].split(","))

                transformMatrix = (
                    (1.0,0.0,transformCoords[0]),
                    (0.0,1.0,transformCoords[1]),
                    (0.0,0.0,1.0)
                )
            else:
                print "Warning: Unhandled transform " + transform

        xTotal = 0.0
        yTotal = 0.0
        numCoords = 0
        for d in dList:
            l = d.split(",")
            if len(l) == 1:
                #A letter
                coordList.append(l)
            else:
                #A coord
                numCoords = numCoords +1

                l = map(float,l)

                if transformMatrix:
                    l = utils.matmult(transformMatrix, l+[1])[:-1]

                xTotal = xTotal + l[0]
                yTotal = yTotal + l[1]

                coordList.append(l[0])
                coordList.append(l[1])

        #Point at which we want the label drawn
        fontCoord = (xTotal/numCoords, yTotal/numCoords)

        return LineKey(pane, coordList, fontCoord, rgba)

    def load_layout(self,kblang):
        self.keyboard = Keyboard(self) 

        scanning = self.gconfClient.get_bool("/apps/sok/scanning")
        if scanning:
            self.keyboard.scanning = scanning
            self.keyboard.reset_scan()
        else:
            self.keyboard.scanning = False
        
        scanningInterval = self.gconfClient.get_int(
                "/apps/sok/scanning_interval")
        if scanningInterval:
            self.keyboard.scanningInterval = scanningInterval
        else:
            self.gconfClient.set_int("/apps/sok/scanning_interval",750)
        kbfolder = os.path.dirname(kblang)

        f = open(kblang)
        langdoc = minidom.parse(f).documentElement
        f.close()
            
            
        panes = []
        
        for paneXML in langdoc.getElementsByTagName("pane"):
            
            try:
                path= "%s/%s" % (kbfolder,paneXML.attributes["filename"].value)
                
                
                f = open(path)
                try:            
                    svgdoc = minidom.parse(f).documentElement
                    keys = {}

                    try:
                        viewPortSizeX = float(svgdoc.attributes['width'].value)
                        viewPortSizeY = float(svgdoc.attributes['height'].value)
                    except ValueError:
                        print "Units for canvas height and width must be px.  In the svg file this corresponds with having no units after the height and width"

                    #find background of pane
                    paneBackground = [0.0,0.0,0.0,0.0]
            
                    if paneXML.hasAttribute("backgroundRed"):
                        paneBackground[0] = paneXML.attributes["backgroundRed"].value
                    if paneXML.hasAttribute("backgroundGreen"):
                        paneBackground[1] = paneXML.attributes["backgroundGreen"].value
                    if paneXML.hasAttribute("backgroundBlue"):
                        paneBackground[2] = paneXML.attributes["backgroundBlue"].value
                    if paneXML.hasAttribute("backgroundAlpha"):
                        paneBackground[3] = paneXML.attributes["backgroundAlpha"].value

                    #scanning
                    columns = []
                    
                    
                    if paneXML.hasAttribute("font"):
                        fontSize = string.atoi(paneXML.attributes["font"].value)
                    else:
                        fontSize = DEFAULT_FONTSIZE
                    
                    pane = Pane(self.keyboard,paneXML.attributes["id"].value,keys,columns, viewPortSizeX, viewPortSizeY, paneBackground, fontSize)

                    for rect in svgdoc.getElementsByTagName("rect"): 
                        id = rect.attributes["id"].value
                        
                        styleString = rect.attributes["style"].value
                        result = re.search("(fill:#\d?\D?\d?\D?\d?\D?\d?\D?\d?\D?\d?\D?;)", styleString).groups()[0]
                
                        rgba = [self.hexstring_to_float(result[6:8])/255,
                        self.hexstring_to_float(result[8:10])/255,
                        self.hexstring_to_float(result[10:12])/255,
                        1]#not bothered for now 

                        keys[id] = RectKey(pane,
                            float(rect.attributes['x'].value),
                            float(rect.attributes['y'].value),
                            float(rect.attributes['width'].value),
                            float(rect.attributes['height'].value),rgba)
                    
                    for path in svgdoc.getElementsByTagName("path"):
                        id = path.attributes["id"].value
                        keys[id] = self.parse_path(path, pane)
                                            
                    
                    svgdoc.unlink()
                    
                    self.load_keys(langdoc,keys)
                    
                    try:
                        
                        for columnXML in paneXML.getElementsByTagName("column"):
                            column = []
                            columns.append(column)
                            for scanKey in columnXML.getElementsByTagName("scankey"):
                                column.append(keys[scanKey.attributes["id"].value])
                    except KeyError, (strerror):
                        print "require %s key, appears in scanning only" % (strerror)
                    

                    panes.append(pane)
                except KeyError, (strerror):
                    print _("require %s") % (strerror)
                    
                f.close()
            except KeyError:
                print _("require filename in pane")
        
        langdoc.unlink()
        
        
        basePane = panes[0]
        otherPanes = panes[1:]

        self.keyboard.set_basePane(basePane)

        for pane in otherPanes:
            self.keyboard.add_pane(pane)

        

            



    
if __name__=='__main__':
    s = Sok()
    gtk.main()
    s.clean()
