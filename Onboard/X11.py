
from ctypes import *


#########################
# XLib        
XID = c_ulong
Mask = c_ulong
Atom = c_ulong
VisualID = c_ulong
Time = c_ulong

class XDisplay(Structure): pass
XDisplay._fields_ = [('dummy', c_char*1024)]

libX11 = CDLL('libX11.so')

XOpenDisplay = libX11.XOpenDisplay
XOpenDisplay.restype = POINTER(XDisplay)
XOpenDisplay.argtypes = [c_char_p]

XCloseDisplay = libX11.XCloseDisplay
XCloseDisplay.restype = c_int
XCloseDisplay.argtypes = [POINTER(XDisplay)]


#########################
# XInput
IsXPointer		     = 0
IsXKeyboard		     = 1
IsXExtensionDevice	 = 2
IsXExtensionKeyboard = 3
IsXExtensionPointer  = 4

class XDeviceInfo(Structure): pass
XDeviceInfo._fields_ = [
    ('id', XID),
    ('type', Atom),
    ('name', c_char_p),
    ('num_classes', c_int),
    ('use', c_int),
    ('inputclassinfo', c_void_p),  # XAnyClassPtr
]           

class XDevice(Structure): pass
XDevice._fields_ = [
    ('device_id', XID),
    ('num_classes', c_int),
    ('classes', c_void_p),
]

libXi = CDLL('libXi.so')

#XDeviceInfo *XListInputDevices( XDisplay *XDisplay,int *ndevices_return);
XListInputDevices = libXi.XListInputDevices
XListInputDevices.restype = POINTER(XDeviceInfo)
XListInputDevices.argtypes = [POINTER(XDisplay), POINTER(c_int)]

#int XFreeDeviceList( XDeviceInfo *list);
XFreeDeviceList = libXi.XFreeDeviceList
XFreeDeviceList.restype = c_int
XFreeDeviceList.argtypes = [POINTER(XDeviceInfo)]

# XDevice *XOpenDevice(XDisplay *XDisplay, XID device_id);
XOpenDevice = libXi.XOpenDevice
XOpenDevice.restype = POINTER(XDevice)
XOpenDevice.argtypes = [POINTER(XDisplay), XID]

# XCloseDevice(XDisplay *XDisplay, XDevice *device);
XCloseDevice = libXi.XCloseDevice
XCloseDevice.restype = c_int
XCloseDevice.argtypes = [POINTER(XDisplay)]

# int XGetDeviceButtonMapping(XDisplay *XDisplay, XDevice *device,
#                             unsigned char map_return[],int nmap);
XGetDeviceButtonMapping = libXi.XGetDeviceButtonMapping
XGetDeviceButtonMapping.restype = c_int
XGetDeviceButtonMapping.argtypes = [POINTER(XDisplay), POINTER(XDevice),
                                    POINTER(c_ubyte), c_int]
                                    
# int XSetDeviceButtonMapping(XDisplay *XDisplay, XDevice *device,
#                             unsigned char map[],int nmap);
XSetDeviceButtonMapping = libXi.XSetDeviceButtonMapping
XSetDeviceButtonMapping.restype = c_int
XSetDeviceButtonMapping.argtypes = [POINTER(XDisplay), POINTER(XDevice),
                                    POINTER(c_ubyte), c_int]


