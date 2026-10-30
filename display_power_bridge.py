"""Display Power Bridge: Windows display power and DDC/CI bridge."""
from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from ctypes import wintypes
import winreg

if os.name != "nt":
    raise SystemExit("This utility runs only on Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE
HWND = wintypes.HWND
HINSTANCE = wintypes.HINSTANCE
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
UINT_PTR = ctypes.c_size_t

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_TRAYICON = 0x0400 + 1
WM_NULL = 0
WM_QUIT = 0x0012
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_LBUTTONDBLCLK = 0x0203
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_POWERBROADCAST = 0x0218
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMECRITICAL = 0x0006
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0

NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
IDI_APPLICATION = 32512
MF_STRING = 0
MF_SEPARATOR = 0x800
TPM_NONOTIFY = 0x80
TPM_RETURNCMD = 0x100
TPM_RIGHTBUTTON = 0x2
TRAY_SHOW = 1001
TRAY_TEST = 1002
TRAY_OFF = 1003
TRAY_ON = 1004
TRAY_EXIT = 1005

VCP_POWER = 0xD6
POWER_ON = 0x01
POWER_OFF = 0x04
D6_DESCRIPTIONS = {
    0x01: "0x01 (\u5f00\u673a / \u6b63\u5e38\u663e\u793a)",
    0x02: "0x02 (\u5f85\u673a)",
    0x03: "0x03 (\u6302\u8d77)",
    0x04: "0x04 (\u5173\u95ed\u663e\u793a\u8f93\u51fa)",
    0x05: "0x05 (\u7535\u6e90\u952e\u5f0f\u5355\u5411\u547d\u4ee4\uff0c\u98ce\u9669\u8f83\u9ad8)",
}
D6_LABELS = tuple(D6_DESCRIPTIONS[value] for value in sorted(D6_DESCRIPTIONS))
D6_FROM_LABEL = {label: value for value, label in D6_DESCRIPTIONS.items()}
TRAY_CLICK_LABELS = {"single": "\u5355\u51fb", "double": "\u53cc\u51fb"}
TRAY_ACTION_LABELS = {"show": "\u663e\u793a\u7a97\u53e3", "off": "\u5173\u95ed\u5c4f\u5e55"}
TRAY_CLICK_FROM_LABEL = {label: value for value, label in TRAY_CLICK_LABELS.items()}
TRAY_ACTION_FROM_LABEL = {label: value for value, label in TRAY_ACTION_LABELS.items()}
DEFAULT_MOUSE_REQUIRED_MOVES = 10
DEFAULT_MOUSE_MIN_STEP_PX = 1.0
DEFAULT_MOUSE_PAUSE_GRACE_SECONDS = 0.35
MOUSE_POLL_MS = 50
APP_POLL_MS = 10
DEFAULT_WAKE_STABILIZE_SECONDS = 1.2
DEFAULT_MANUAL_OFF_COOLDOWN_SECONDS = 1.0
TEST_TIMEOUT_SECONDS = 45
CONFIG_VERSION = 1
ERROR_ALREADY_EXISTS = 183
INSTANCE_MUTEX_NAME = "Local\\DisplayPowerBridge_SingleInstance"
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "DisplayPowerBridge"

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE),
                ("description", wintypes.WCHAR * 128)]

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]

class MSG(ctypes.Structure):
    _fields_ = [("hwnd", HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT),
                ("lPrivate", wintypes.DWORD)]

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD),
                ("Data", ctypes.c_ubyte * 1)]

GUID_CONSOLE_DISPLAY_STATE = GUID(
    0x6FE69556, 0x704A, 0x47A0,
    (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))
GUID_MONITOR_POWER_ON = GUID(
    0x02731015, 0x4510, 0x4526,
    (ctypes.c_ubyte * 8)(0x99, 0xE6, 0xE5, 0xA1, 0x7E, 0xBD, 0x1A, 0xEA))

class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
LOWLEVELKEYPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(RECT), ctypes.c_ssize_t)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", HCURSOR), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]

def _guid_equal(left, right):
    return bytes(left) == bytes(right)

def _power_setting_value(setting):
    length = min(int(setting.DataLength), 4)
    if length <= 0:
        return None
    address = ctypes.addressof(setting) + POWERBROADCAST_SETTING.Data.offset
    return int.from_bytes(ctypes.string_at(address, length), "little")

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LOWLEVELKEYPROC, HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, HWND, ctypes.POINTER(RECT)]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.DefWindowProcW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, HWND, wintypes.HMENU, HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = HWND
user32.DestroyWindow.argtypes = [HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), MONITORENUMPROC, ctypes.c_ssize_t]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.LoadIconW.argtypes = [HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.RegisterPowerSettingNotification.argtypes = [HWND, ctypes.POINTER(GUID), wintypes.DWORD]
user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD]
dxva2.SetVCPFeature.restype = wintypes.BOOL
dxva2.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
dxva2.DestroyPhysicalMonitor.restype = wintypes.BOOL

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

def _acquire_single_instance():
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle

def _startup_command():
    """Return a quoted command suitable for the current-user Run key."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}"'

def _is_startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH,
                            0, winreg.KEY_READ) as key:
            value, value_type = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
            return value_type == winreg.REG_SZ and bool(str(value).strip())
    except (FileNotFoundError, OSError):
        return False

def _set_startup_enabled(enabled):
    if enabled:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH,
                                0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ,
                              _startup_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, STARTUP_VALUE_NAME)
    except FileNotFoundError:
        pass

class MonitorInfo:
    __slots__ = ("description", "rect")

    def __init__(self, description: str, rect: tuple[int, int, int, int]):
        self.description = description
        self.rect = rect

    @property
    def key(self):
        return (self.description, self.rect)

    @property
    def label(self):
        l, t, r, b = self.rect
        return f"{self.description}  [{l},{t} - {r},{b}]"

class DdcResult:
    __slots__ = ("attempted", "failures")

    def __init__(self, attempted: int, failures: list[str]):
        self.attempted = attempted
        self.failures = failures

    @property
    def ok(self):
        return self.attempted > 0 and not self.failures

class DdcController:
    def enumerate(self) -> list[MonitorInfo]:
        found: list[MonitorInfo] = []

        @MONITORENUMPROC
        def callback(hmonitor, _hdc, rect_ptr, _data):
            count = wintypes.DWORD()
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)):
                return True
            if not count.value:
                return True
            physical = (PHYSICAL_MONITOR * count.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, physical):
                return True
            rect = rect_ptr.contents
            coords = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
            for item in physical:
                found.append(MonitorInfo(item.description.rstrip("\x00"), coords))
                dxva2.DestroyPhysicalMonitor(item.handle)
            return True

        if not user32.EnumDisplayMonitors(None, None, callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return found

    def set_power(self, targets: list[MonitorInfo], value: int, retries: int = 1) -> DdcResult:
        target_keys = {item.key for item in targets}
        attempted = 0
        failures: list[str] = []
        if value == POWER_ON and retries > 1:
            time.sleep(0.35)

        @MONITORENUMPROC
        def callback(hmonitor, _hdc, rect_ptr, _data):
            nonlocal attempted
            count = wintypes.DWORD()
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)):
                return True
            if not count.value:
                return True
            physical = (PHYSICAL_MONITOR * count.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, physical):
                return True
            rect = rect_ptr.contents
            key_rect = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
            for item in physical:
                info = MonitorInfo(item.description.rstrip("\x00"), key_rect)
                try:
                    if info.key in target_keys:
                        attempted += 1
                        last_error = 0
                        success = False
                        for attempt in range(max(1, retries)):
                            if dxva2.SetVCPFeature(item.handle, VCP_POWER, value):
                                success = True
                                break
                            last_error = ctypes.get_last_error()
                            if attempt + 1 < max(1, retries):
                                time.sleep(0.25)
                        if not success:
                            failures.append(f"{info.label}: Win32 {last_error}")
                finally:
                    dxva2.DestroyPhysicalMonitor(item.handle)
            return True

        if not user32.EnumDisplayMonitors(None, None, callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not attempted:
            failures.append("No selected physical monitor matched the current display layout")
        return DdcResult(attempted, failures)

class GlobalKeyListener:
    def __init__(self, callback, any_key: bool = False):
        self.callback = callback
        self.any_key = any_key
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._ready = threading.Event()
        self._error = None
        self._reported = False
        self._proc = LOWLEVELKEYPROC(self._hook_proc)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="ddc-ci-keyboard-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError("keyboard hook startup timed out")
        if self._error:
            raise self._error

    def _run(self):
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
            if not self._hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            msg = MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    def _hook_proc(self, code, wparam, lparam):
        if code >= 0 and int(wparam) in (WM_KEYDOWN, WM_SYSKEYDOWN) and not self._reported:
            try:
                event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if self.any_key:
                    self._reported = True
                    self.callback("wake")
                elif event.vkCode == VK_ESCAPE:
                    self._reported = True
                    self.callback("failure")
                elif event.vkCode == VK_RETURN:
                    self._reported = True
                    self.callback("success")
            except BaseException:
                pass
        return user32.CallNextHookEx(self._hook, code, wparam, lparam)

    def stop(self):
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = 0

class TrayHost:
    def __init__(self, callbacks):
        self.callbacks = callbacks
        self.class_name = f"DdcCiTray_{id(self):x}"
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.hwnd = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.error = None
        self.stopped = threading.Event()
        self._wndproc = WNDPROC(self._window_proc)
        self.data = None
        self.power_notify_handles = []
        self.thread = threading.Thread(target=self._run, name="ddc-ci-tray", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError("tray startup timed out")
        if self.error:
            raise self.error

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        try:
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = self.hinstance
            wc.lpszClassName = self.class_name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = user32.CreateWindowExW(0, self.class_name, self.class_name, 0,
                                               0, 0, 0, 0, None, None, self.hinstance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            power_notifications = (
                (GUID_CONSOLE_DISPLAY_STATE, "GUID_CONSOLE_DISPLAY_STATE"),
                (GUID_MONITOR_POWER_ON, "GUID_MONITOR_POWER_ON"),
            )
            for power_guid, power_name in power_notifications:
                handle = None
                last_error = 0
                for _attempt in range(3):
                    handle = user32.RegisterPowerSettingNotification(
                        self.hwnd, ctypes.byref(power_guid), DEVICE_NOTIFY_WINDOW_HANDLE)
                    if handle:
                        break
                    last_error = ctypes.get_last_error()
                    time.sleep(0.02)
                if handle:
                    self.power_notify_handles.append(handle)
                    self.callbacks["power_listener_status"](power_name, True, 0)
                else:
                    self.callbacks["power_listener_status"](power_name, False, last_error)
            self.data = NOTIFYICONDATAW()
            self.data.cbSize = ctypes.sizeof(self.data)
            self.data.hWnd = self.hwnd
            self.data.uID = 1
            self.data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self.data.uCallbackMessage = WM_TRAYICON
            self.data.hIcon = user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR))
            self.data.szTip = "Display Power Bridge"
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.data)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set()
            msg = MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except BaseException as exc:
            self.error = exc
            self.ready.set()
        finally:
            if self.data is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.data))
            for handle in self.power_notify_handles:
                user32.UnregisterPowerSettingNotification(handle)
            self.power_notify_handles.clear()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            user32.UnregisterClassW(self.class_name, self.hinstance)
            self.stopped.set()

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message in (WM_QUERYENDSESSION, WM_ENDSESSION):
            self.callbacks["shutdown"]()
            return 1 if message == WM_QUERYENDSESSION else 0
        if message == WM_POWERBROADCAST:
            power_event = int(wparam)
            if power_event == PBT_POWERSETTINGCHANGE and lparam:
                try:
                    setting = ctypes.cast(
                        lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    value = _power_setting_value(setting)
                    if _guid_equal(setting.PowerSetting, GUID_CONSOLE_DISPLAY_STATE):
                        self.callbacks["power_state"](value)
                    elif _guid_equal(setting.PowerSetting, GUID_MONITOR_POWER_ON):
                        self.callbacks["power_state"](0 if value == 0 else 1)
                except Exception:
                    pass
            elif power_event == PBT_APMSUSPEND:
                self.callbacks["suspend"]()
            elif power_event in (PBT_APMRESUMECRITICAL, PBT_APMRESUMEAUTOMATIC,
                                  PBT_APMRESUMESUSPEND):
                self.callbacks["resume"]()
            return 1
        if message == WM_TRAYICON:
            if int(lparam) == WM_LBUTTONUP:
                self.callbacks["left_single"]()
            elif int(lparam) == WM_LBUTTONDBLCLK:
                self.callbacks["left_double"]()
            elif int(lparam) == WM_RBUTTONUP:
                self._show_menu()
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, MF_STRING, TRAY_SHOW, "\u663e\u793a\u7a97\u53e3")
            user32.AppendMenuW(menu, MF_STRING, TRAY_TEST, "\u91cd\u65b0\u6d4b\u8bd5 DDC/CI")
            user32.AppendMenuW(menu, MF_STRING, TRAY_OFF, "\u7acb\u5373\u5173\u95ed\u5c4f\u5e55")
            user32.AppendMenuW(menu, MF_STRING, TRAY_ON, "\u6062\u590d\u5c4f\u5e55")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, TRAY_EXIT, "\u9000\u51fa")
            point = POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            user32.SetForegroundWindow(self.hwnd)
            command = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                                            point.x, point.y, 0, self.hwnd, None)
            mapping = {TRAY_SHOW: "show", TRAY_TEST: "test", TRAY_OFF: "off",
                       TRAY_ON: "on", TRAY_EXIT: "exit"}
            name = mapping.get(int(command))
            if name:
                self.callbacks[name]()
        finally:
            user32.DestroyMenu(menu)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)

    def stop(self):
        if self.thread.is_alive() and self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.stopped.wait(2)
            self.thread.join(timeout=1)

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Display Power Bridge \u00b7 \u663e\u793a\u5668\u7535\u6e90\u6865")
        self.root.geometry("680x1")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.queue = queue.SimpleQueue()
        self.ddc = DdcController()
        self.monitors: list[MonitorInfo] = []
        self.targets: list[MonitorInfo] = []
        app_data = Path(os.environ.get("APPDATA", str(Path.home())))
        self.config_path = app_data / "DisplayPowerBridge" / "config.json"
        self.legacy_config_path = app_data / "DdcCiScreenGuard" / "config.json"
        self.config = self._load_config()
        if self.config:
            self.targets = [MonitorInfo(item["description"], tuple(item["rect"]))
                            for item in self.config.get("monitors", [])]
        self.power_off_value = POWER_OFF
        if self.config:
            try:
                candidate = int(self.config.get("power_off", POWER_OFF))
                if candidate in D6_DESCRIPTIONS:
                    self.power_off_value = candidate
            except (TypeError, ValueError):
                pass
        self.mouse_wake_enabled = bool(self.config.get("mouse_wake", False)) if self.config else False
        self.keyboard_wake_enabled = bool(self.config.get("keyboard_wake", False)) if self.config else False
        self.mouse_required_moves = DEFAULT_MOUSE_REQUIRED_MOVES
        self.mouse_min_step_px = DEFAULT_MOUSE_MIN_STEP_PX
        self.mouse_pause_grace_seconds = DEFAULT_MOUSE_PAUSE_GRACE_SECONDS
        self.wake_stabilize_seconds = DEFAULT_WAKE_STABILIZE_SECONDS
        self.manual_off_cooldown_seconds = DEFAULT_MANUAL_OFF_COOLDOWN_SECONDS
        self.tray_click_mode = "double"
        self.tray_click_action = "show"
        self.startup_enabled = _is_startup_enabled()
        if self.config:
            try:
                required_moves = int(float(self.config.get("mouse_required_moves", DEFAULT_MOUSE_REQUIRED_MOVES)))
                if 1 <= required_moves <= 100000:
                    self.mouse_required_moves = required_moves
            except (TypeError, ValueError):
                pass
            try:
                threshold = float(self.config.get("mouse_min_step_px", DEFAULT_MOUSE_MIN_STEP_PX))
                if 0 <= threshold <= 1000:
                    self.mouse_min_step_px = threshold
                grace = float(self.config.get("mouse_pause_grace_seconds", DEFAULT_MOUSE_PAUSE_GRACE_SECONDS))
                if 0 <= grace <= 10:
                    self.mouse_pause_grace_seconds = grace
            except (TypeError, ValueError):
                pass
            try:
                stabilize = float(self.config.get("wake_stabilize_seconds", DEFAULT_WAKE_STABILIZE_SECONDS))
                if 0 <= stabilize <= 30:
                    self.wake_stabilize_seconds = stabilize
            except (TypeError, ValueError):
                pass
            try:
                cooldown = float(self.config.get(
                    "manual_off_cooldown_seconds",
                    self.config.get("manual_off_delay_seconds", DEFAULT_MANUAL_OFF_COOLDOWN_SECONDS)))
                if 0 <= cooldown <= 30:
                    self.manual_off_cooldown_seconds = cooldown
            except (TypeError, ValueError):
                pass
            if self.config.get("tray_click") in TRAY_CLICK_LABELS:
                self.tray_click_mode = self.config["tray_click"]
            if self.config.get("tray_action") in TRAY_ACTION_LABELS:
                self.tray_click_action = self.config["tray_action"]
        self.test_active = False
        self.test_popup = None
        self.test_deadline_job = None
        self.key_listener = None
        self.test_targets: list[MonitorInfo] = []
        self.recovery_targets: list[MonitorInfo] = []
        self.wake_key_listener = None
        self.mouse_poll_job = None
        self.wake_restore_job = None
        self.manual_off_cooldown_job = None
        self.manual_off_cooldown_active = False
        self.display_off_active = False
        self.auto_power_off_active = False
        self.last_mouse_pos = None
        self.mouse_move_count = 0
        self.mouse_last_move_at = None
        self.mouse_test_popup = None
        self.mouse_test_job = None
        self.mouse_test_last_pos = None
        self.mouse_test_samples = 0
        self.mouse_test_moves = 0
        self.mouse_test_distance = 0.0
        self.mouse_test_last_delta = 0.0
        self.log_widget = None
        self._emergency_restore_lock = threading.RLock()
        self._emergency_restore_done = False
        self._ddc_command_lock = threading.Lock()
        self._fast_auto_off_lock = threading.Lock()
        self._fast_auto_off_claimed = False
        self.listbox = None
        self.status_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.power_event_var = tk.StringVar(value="")
        self.wake_stabilize_var = tk.StringVar(value=self._format_seconds(self.wake_stabilize_seconds))
        self.manual_off_cooldown_var = tk.StringVar(value=self._format_seconds(self.manual_off_cooldown_seconds))
        self.power_off_var = tk.StringVar(value=D6_DESCRIPTIONS[self.power_off_value])
        self.power_hint_var = tk.StringVar(value="")
        self.mouse_wake_var = tk.BooleanVar(value=self.mouse_wake_enabled)
        self.keyboard_wake_var = tk.BooleanVar(value=self.keyboard_wake_enabled)
        self.startup_var = tk.BooleanVar(value=self.startup_enabled)
        self.mouse_required_var = tk.StringVar(value=str(self.mouse_required_moves))
        self.tray_click_var = tk.StringVar(value=TRAY_CLICK_LABELS[self.tray_click_mode])
        self.tray_action_var = tk.StringVar(value=TRAY_ACTION_LABELS[self.tray_click_action])
        self._build_ui()
        self._fit_main_window()
        self._append_log("\u5de5\u5177\u5df2\u542f\u52a8")
        tray_callbacks = {name: (lambda n=name: self.queue.put(n)) for name in
                          ("show", "test", "off", "on", "exit", "left_single", "left_double")}
        tray_callbacks["power_state"] = self._power_state_callback
        tray_callbacks["power_listener_status"] = self._power_listener_status
        tray_callbacks["suspend"] = lambda: self.queue.put("power_suspend")
        tray_callbacks["resume"] = lambda: self.queue.put("power_resume")
        tray_callbacks["shutdown"] = self._emergency_restore
        self.tray = TrayHost(tray_callbacks)
        self.root.after(APP_POLL_MS, self._poll)
        if self.config:
            self.status_var.set("\u5df2\u5b8c\u6210 DDC/CI \u6d4b\u8bd5")
            self.detail_var.set("\u6258\u76d8\u83dc\u5355\u53ef\u4ee5\u5173\u95ed\u6216\u6062\u590d\u5c4f\u5e55")
        else:
            self.show_window()
            self.refresh_monitors()

    def _load_config(self):
        paths = [self.config_path]
        if self.legacy_config_path != self.config_path:
            paths.append(self.legacy_config_path)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == CONFIG_VERSION and data.get("monitors"):
                    return data
            except (OSError, ValueError, TypeError):
                continue
        return None

    @staticmethod
    def _format_seconds(value):
        return str(int(value)) if float(value).is_integer() else f"{float(value):g}"

    def _append_log(self, message):
        if self.log_widget is None:
            return
        stamp = time.strftime("%H:%M:%S")
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def _fit_main_window(self):
        self.root.update_idletasks()
        width = max(680, self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight()
        self.root.geometry(f"{width}x{height}")

    def _set_power(self, targets, value, retries=1):
        with self._ddc_command_lock:
            return self.ddc.set_power(targets, value, retries=retries)

    def _power_state_callback(self, state):
        if state != 0:
            self.queue.put(("power_state", state))
            return
        targets = list(self.targets or self.recovery_targets)
        with self._fast_auto_off_lock:
            if (self._fast_auto_off_claimed or self.display_off_active
                    or self.test_active or not targets):
                self.queue.put(("power_state", state))
                return
            self._fast_auto_off_claimed = True
        self.queue.put(("power_state", state))
        threading.Thread(target=self._fast_auto_off_worker, args=(targets,),
                         name="ddc-ci-fast-auto-off", daemon=True).start()

    def _fast_auto_off_worker(self, targets):
        try:
            result = self._set_power(targets, self.power_off_value)
            payload = (result.ok, "; ".join(result.failures))
        except BaseException as exc:
            payload = (False, str(exc))
        self.queue.put(("power_off_result", payload))

    def _power_listener_status(self, name, registered, error_code=0):
        self.queue.put(("power_listener_status", name, bool(registered), int(error_code or 0)))

    def _save_config(self, targets):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": CONFIG_VERSION, "power_off": self.power_off_value, "power_on": POWER_ON,
                "mouse_wake": self.mouse_wake_enabled,
                "keyboard_wake": self.keyboard_wake_enabled,
                "mouse_required_moves": self.mouse_required_moves,
                "mouse_min_step_px": self.mouse_min_step_px,
                "mouse_pause_grace_seconds": self.mouse_pause_grace_seconds,
                "wake_stabilize_seconds": self.wake_stabilize_seconds,
                "manual_off_cooldown_seconds": self.manual_off_cooldown_seconds,
                "tray_click": self.tray_click_mode,
                "tray_action": self.tray_click_action,
                "startup_enabled": self.startup_enabled,
                "monitors": [{"description": item.description, "rect": list(item.rect)} for item in targets],
                "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.config = data
        self.targets = list(targets)
        self.recovery_targets = []

    def _persist_config_if_ready(self):
        if self.config and self.targets:
            self._save_config(self.targets)

    def _build_ui(self):
        frame = tk.Frame(self.root, padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="DDC/CI \u5c4f\u5e55\u63a7\u5236", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(frame, text="\u9996\u6b21\u8fd0\u884c\u8bf7\u5148\u8fde\u63a5\u597d UU \u6216\u5176\u4ed6\u8fdc\u7a0b\u5de5\u5177\uff0c\u786e\u4fdd\u5c4f\u5e55\u5173\u95ed\u540e\u4ecd\u80fd\u64cd\u4f5c\u7535\u8111\u3002",
                 fg="#9b3d00", wraplength=620, justify="left").pack(anchor="w", pady=(0, 10))
        tk.Label(frame, text="\u53ef\u7528\u7684\u7269\u7406\u663e\u793a\u5668\uff1a").pack(anchor="w")
        self.listbox = tk.Listbox(frame, height=2, selectmode=tk.MULTIPLE, exportselection=False)
        self.listbox.pack(fill="x", pady=(5, 7))
        tk.Button(frame, text="\u5237\u65b0\u663e\u793a\u5668\u5217\u8868", command=self.refresh_monitors).pack(anchor="w")
        power_row = tk.Frame(frame)
        power_row.pack(fill="x", pady=(10, 0))
        tk.Label(power_row, text="VCP 0xD6 \u6d4b\u8bd5\u503c\uff1a").pack(side="left")
        tk.OptionMenu(power_row, self.power_off_var, *D6_LABELS,
                      command=self._power_option_changed).pack(side="left", padx=(6, 0))
        tk.Label(frame, textvariable=self.power_hint_var, fg="#9b3d00",
                 wraplength=620, justify="left").pack(anchor="w", pady=(3, 0))
        wake_row = tk.Frame(frame)
        wake_row.pack(fill="x", pady=(10, 0))
        tk.Checkbutton(wake_row, text="\u9f20\u6807\u8fde\u7eed\u6709\u6548\u79fb\u52a8", variable=self.mouse_wake_var,
                       command=self._settings_changed).pack(side="left")
        tk.Entry(wake_row, textvariable=self.mouse_required_var, width=5).pack(side="left", padx=(4, 3))
        tk.Label(wake_row, text="\u6b21\u540e\u6062\u590d\u663e\u793a").pack(side="left")
        tk.Checkbutton(wake_row, text="\u952e\u76d8\u4efb\u610f\u952e\u6062\u590d\u663e\u793a", variable=self.keyboard_wake_var,
                       command=self._settings_changed).pack(side="left", padx=(16, 0))
        self.mouse_required_var_entry = wake_row.winfo_children()[1]
        self.mouse_required_var_entry.bind("<FocusOut>", lambda _event: self._settings_changed())
        self.mouse_required_var_entry.bind("<Return>", lambda _event: self._settings_changed())
        tray_row = tk.Frame(frame)
        tray_row.pack(fill="x", pady=(7, 0))
        tk.Label(tray_row, text="\u6258\u76d8\u5de6\u952e\uff1a").pack(side="left")
        tk.OptionMenu(tray_row, self.tray_click_var, *TRAY_CLICK_LABELS.values(),
                      command=self._tray_click_changed).pack(side="left", padx=(4, 10))
        tk.Label(tray_row, text="\u6267\u884c\uff1a").pack(side="left")
        tk.OptionMenu(tray_row, self.tray_action_var, *TRAY_ACTION_LABELS.values(),
                      command=self._tray_action_changed).pack(side="left", padx=(4, 0))
        tk.Checkbutton(frame, text="登录 Windows 后自动启动（当前用户）",
                       variable=self.startup_var,
                       command=self._startup_changed).pack(anchor="w", pady=(7, 0))
        tk.Button(frame, text="\u9f20\u6807\u5224\u5b9a\u6d4b\u8bd5 / \u9ad8\u7ea7\u8bbe\u7f6e",
                  command=self.show_mouse_test).pack(anchor="w", pady=(7, 0))
        timing_row = tk.Frame(frame)
        timing_row.pack(fill="x", pady=(7, 0))
        tk.Label(timing_row, text="\u663e\u793a\u94fe\u8def\u7a33\u5b9a\u7b49\u5f85(s)\uff1a").pack(side="left")
        timing_entry = tk.Entry(timing_row, textvariable=self.wake_stabilize_var, width=7)
        timing_entry.pack(side="left", padx=(4, 6))
        tk.Label(timing_row, text="0-30\u79d2\uff0c\u4ec5 Windows \u81ea\u52a8\u5173\u95ed\u5c4f\u5e55\u7684\u6062\u590d\u663e\u793a\uff08\u6ee1\u8db3\u672c\u5de5\u5177\u6761\u4ef6\uff09\u65f6\u7b49\u5f85").pack(side="left")
        timing_entry.bind("<FocusOut>", lambda _event: self._wake_stabilize_changed())
        timing_entry.bind("<Return>", lambda _event: self._wake_stabilize_changed())
        off_row = tk.Frame(frame)
        off_row.pack(fill="x", pady=(4, 0))
        tk.Label(off_row, text="\u624b\u52a8\u5173\u5c4f\u540e\u7684\u51b7\u5374\u671f(s)\uff1a").pack(side="left")
        off_cooldown_entry = tk.Entry(off_row, textvariable=self.manual_off_cooldown_var, width=7)
        off_cooldown_entry.pack(side="left", padx=(4, 6))
        tk.Label(off_row, text="0-30\u79d2\uff0c\u51b7\u5374\u671f\u95f4\u4e0d\u53d1\u9001\u5173\u95ed\u6216\u6062\u590d\u4fe1\u53f7").pack(side="left")
        off_cooldown_entry.bind("<FocusOut>", lambda _event: self._manual_off_cooldown_changed())
        off_cooldown_entry.bind("<Return>", lambda _event: self._manual_off_cooldown_changed())
        tk.Label(frame, textvariable=self.status_var, fg="#1d5f99").pack(anchor="w", pady=(10, 0))
        tk.Label(frame, textvariable=self.detail_var, fg="#555555", wraplength=620, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Label(frame, textvariable=self.power_event_var, fg="#7a4b00", wraplength=620,
                 justify="left").pack(anchor="w", pady=(4, 0))
        log_frame = tk.LabelFrame(frame, text="\u6eda\u52a8\u65e5\u5fd7", padx=5, pady=5)
        log_frame.pack(fill="x", pady=(8, 0))
        log_frame.configure(height=120)
        log_frame.pack_propagate(False)
        self.log_widget = tk.Text(log_frame, height=5, width=78, state=tk.DISABLED,
                                  wrap="word", takefocus=False)
        log_scroll = tk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
        self.log_widget.configure(yscrollcommand=log_scroll.set)
        self.log_widget.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))
        tk.Button(buttons, text="\u5f00\u59cb\u4ea4\u4e92\u6d4b\u8bd5", command=self.begin_test).pack(side="left")
        tk.Button(buttons, text="\u7acb\u5373\u5173\u95ed\u5c4f\u5e55", command=self.turn_off).pack(side="left", padx=(8, 0))
        tk.Button(buttons, text="\u6062\u590d\u5c4f\u5e55", command=self.turn_on).pack(side="left", padx=(8, 0))
        tk.Button(buttons, text="\u9690\u85cf\u5230\u6258\u76d8", command=self.hide_window).pack(side="right")
        self._power_option_changed(self.power_off_var.get())

    def _power_option_changed(self, label):
        self.power_off_value = D6_FROM_LABEL.get(label, POWER_OFF)
        if self.power_off_value == 0x05:
            self.power_hint_var.set("0x05 \u662f\u7535\u6e90\u952e\u5f0f\u5355\u5411\u547d\u4ee4\uff0c\u53ef\u80fd\u5bfc\u81f4\u663e\u793a\u5668\u65e0\u6cd5\u6062\u590d\uff0c\u98ce\u9669\u8f83\u9ad8\u3002")
        elif self.power_off_value == POWER_ON:
            self.power_hint_var.set("0x01 \u662f\u6b63\u5e38\u663e\u793a\u547d\u4ee4\uff0c\u9009\u62e9\u5b83\u4e0d\u4f1a\u5173\u95ed\u5c4f\u5e55\u3002")
        else:
            self.power_hint_var.set("\u6062\u590d\u547d\u4ee4\u56fa\u5b9a\u4f7f\u7528 0x01\uff08\u5f00\u673a / \u6b63\u5e38\u663e\u793a\uff09\u3002")
        self._persist_config_if_ready()

    def _settings_changed(self):
        try:
            required_moves = int(self.mouse_required_var.get().strip())
        except (TypeError, ValueError):
            return
        if not 1 <= required_moves <= 100000:
            return
        self.mouse_required_moves = required_moves
        self.mouse_wake_enabled = bool(self.mouse_wake_var.get())
        self.keyboard_wake_enabled = bool(self.keyboard_wake_var.get())
        if self.display_off_active:
            self._start_wake_monitor()
        self._persist_config_if_ready()

    def _wake_stabilize_changed(self):
        try:
            value = float(self.wake_stabilize_var.get().strip())
        except (TypeError, ValueError):
            return
        if not 0 <= value <= 30:
            return
        self.wake_stabilize_seconds = value
        self.wake_stabilize_var.set(self._format_seconds(value))
        self._persist_config_if_ready()

    def _manual_off_cooldown_changed(self):
        try:
            value = float(self.manual_off_cooldown_var.get().strip())
        except (TypeError, ValueError):
            return
        if not 0 <= value <= 30:
            return
        self.manual_off_cooldown_seconds = value
        self.manual_off_cooldown_var.set(self._format_seconds(value))
        self._persist_config_if_ready()

    def show_mouse_test(self):
        if self.mouse_test_popup is not None:
            self.mouse_test_popup.deiconify()
            self.mouse_test_popup.lift()
            self.mouse_test_popup.focus_force()
            return
        popup = tk.Toplevel(self.root)
        self.mouse_test_popup = popup
        popup.title("\u9f20\u6807\u5224\u5b9a\u6d4b\u8bd5 / \u9ad8\u7ea7\u8bbe\u7f6e")
        popup.geometry("560x430")
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self._close_mouse_test)
        frame = tk.Frame(popup, padx=18, pady=15)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="\u4e0d\u53d1\u9001 DDC/CI \u547d\u4ee4\uff0c\u53ea\u89c2\u5bdf\u5149\u6807\u79fb\u52a8\u548c\u5224\u5b9a\u9608\u503c\u3002",
                 fg="#555555").pack(anchor="w")
        settings = tk.Frame(frame)
        settings.pack(fill="x", pady=(12, 5))
        self.mouse_test_step_var = tk.StringVar(value=self._format_seconds(self.mouse_min_step_px))
        self.mouse_test_grace_var = tk.StringVar(value=self._format_seconds(self.mouse_pause_grace_seconds))
        self.mouse_test_required_var = tk.StringVar(value=str(self.mouse_required_moves))
        tk.Label(settings, text="\u6700\u5c0f\u6709\u6548\u4f4d\u79fb(px)").grid(row=0, column=0, sticky="w")
        tk.Entry(settings, textvariable=self.mouse_test_step_var, width=8).grid(row=0, column=1, padx=(5, 18))
        tk.Label(settings, text="\u5141\u8bb8\u505c\u987f(s)").grid(row=0, column=2, sticky="w")
        tk.Entry(settings, textvariable=self.mouse_test_grace_var, width=8).grid(row=0, column=3, padx=(5, 18))
        tk.Label(settings, text="\u8fde\u7eed\u6709\u6548\u79fb\u52a8(\u6b21)").grid(row=0, column=4, sticky="w")
        tk.Entry(settings, textvariable=self.mouse_test_required_var, width=8).grid(row=0, column=5, padx=(5, 0))
        tk.Button(frame, text="\u5e94\u7528\u9ad8\u7ea7\u8bbe\u7f6e", command=self._apply_mouse_advanced).pack(anchor="w", pady=(2, 10))
        self.mouse_test_vars = {name: tk.StringVar(value="-") for name in
                                ("position", "delta", "samples", "moves", "distance", "state")}
        stats = tk.Frame(frame)
        stats.pack(fill="x")
        stat_labels = (("\u5149\u6807\u5750\u6807", "position"), ("\u672c\u6b21\u4f4d\u79fb", "delta"),
                       ("\u91c7\u6837\u6b21\u6570", "samples"), ("\u6709\u6548\u79fb\u52a8\u6b21\u6570", "moves"),
                       ("\u7d2f\u8ba1\u8ddd\u79bb", "distance"),
                       ("\u5f53\u524d\u5224\u5b9a", "state"))
        for row, (caption, name) in enumerate(stat_labels):
            tk.Label(stats, text=caption + "\uff1a", width=16, anchor="w").grid(row=row, column=0, sticky="w")
            tk.Label(stats, textvariable=self.mouse_test_vars[name], anchor="w").grid(row=row, column=1, sticky="w")
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))
        tk.Button(buttons, text="\u91cd\u7f6e\u7edf\u8ba1", command=self._reset_mouse_test).pack(side="left")
        tk.Button(buttons, text="\u5173\u95ed", command=self._close_mouse_test).pack(side="right")
        self._reset_mouse_test()
        popup.lift()
        popup.focus_force()

    def _apply_mouse_advanced(self):
        try:
            threshold = float(self.mouse_test_step_var.get().strip())
            grace = float(self.mouse_test_grace_var.get().strip())
            required_moves_value = float(self.mouse_test_required_var.get().strip())
        except (TypeError, ValueError):
            messagebox.showerror("DDC/CI", "\u9ad8\u7ea7\u8bbe\u7f6e\u5fc5\u987b\u662f\u6570\u5b57\u3002", parent=self.mouse_test_popup)
            return
        if (not 0 <= threshold <= 1000 or not 0 <= grace <= 10
                or not required_moves_value.is_integer()
                or not 1 <= required_moves_value <= 100000):
            messagebox.showerror("DDC/CI", "\u8bf7\u68c0\u67e5\u8303\u56f4\uff1a\u4f4d\u79fb 0-1000px\uff0c\u505c\u987f 0-10s\uff0c\u6709\u6548\u79fb\u52a8 1-100000\u6b21\u3002", parent=self.mouse_test_popup)
            return
        required_moves = int(required_moves_value)
        self.mouse_min_step_px = threshold
        self.mouse_pause_grace_seconds = grace
        self.mouse_required_moves = required_moves
        self.mouse_required_var.set(str(required_moves))
        self._settings_changed()
        self._persist_config_if_ready()
        if self.display_off_active:
            self._start_wake_monitor()

    def _reset_mouse_test(self):
        self.mouse_test_last_pos = None
        self.mouse_test_samples = 0
        self.mouse_test_moves = 0
        self.mouse_test_distance = 0.0
        self.mouse_test_last_delta = 0.0
        if self.mouse_test_job:
            self.root.after_cancel(self.mouse_test_job)
        self.mouse_test_job = self.root.after(MOUSE_POLL_MS, self._mouse_test_tick)

    def _mouse_test_tick(self):
        self.mouse_test_job = None
        if self.mouse_test_popup is None:
            return
        point = POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            position = (int(point.x), int(point.y))
            self.mouse_test_samples += 1
            if self.mouse_test_last_pos is None:
                self.mouse_test_last_delta = 0.0
            else:
                self.mouse_test_last_delta = math.hypot(
                    position[0] - self.mouse_test_last_pos[0],
                    position[1] - self.mouse_test_last_pos[1])
                self.mouse_test_distance += self.mouse_test_last_delta
                if self.mouse_test_last_delta > 0 and self.mouse_test_last_delta >= self.mouse_min_step_px:
                    self.mouse_test_moves += 1
            self.mouse_test_last_pos = position
            self.mouse_test_vars["position"].set(f"{position[0]}, {position[1]}")
            self.mouse_test_vars["delta"].set(f"{self.mouse_test_last_delta:.2f} px")
            self.mouse_test_vars["state"].set(
                "\u6709\u6548\u79fb\u52a8" if self.mouse_test_last_delta >= self.mouse_min_step_px and self.mouse_test_last_delta > 0
                else "\u672a\u8fbe\u5230\u6700\u5c0f\u4f4d\u79fb")
        self.mouse_test_vars["samples"].set(str(self.mouse_test_samples))
        self.mouse_test_vars["moves"].set(str(self.mouse_test_moves))
        self.mouse_test_vars["distance"].set(f"{self.mouse_test_distance:.2f} px")
        self.mouse_test_job = self.root.after(MOUSE_POLL_MS, self._mouse_test_tick)

    def _close_mouse_test(self):
        if self.mouse_test_job:
            self.root.after_cancel(self.mouse_test_job)
            self.mouse_test_job = None
        if self.mouse_test_popup:
            self.mouse_test_popup.destroy()
            self.mouse_test_popup = None

    def _tray_click_changed(self, label):
        self.tray_click_mode = TRAY_CLICK_FROM_LABEL.get(label, "double")
        self._persist_config_if_ready()

    def _tray_action_changed(self, label):
        self.tray_click_action = TRAY_ACTION_FROM_LABEL.get(label, "show")
        self._persist_config_if_ready()

    def _startup_changed(self):
        requested = bool(self.startup_var.get())
        previous = self.startup_enabled
        try:
            _set_startup_enabled(requested)
        except OSError as exc:
            self.startup_var.set(previous)
            messagebox.showerror("DDC/CI", f"无法更新开机启动项：{exc}", parent=self.root)
            return
        self.startup_enabled = requested
        self._append_log("已启用登录后自动启动" if requested else "已关闭登录后自动启动")
        self._persist_config_if_ready()

    def _confirm_risky_power(self):
        if self.power_off_value != 0x05:
            return True
        return messagebox.askyesno(
            "\u9ad8\u98ce\u9669 DDC/CI \u547d\u4ee4",
            "0x05 \u662f\u7535\u6e90\u952e\u5f0f\u5355\u5411\u547d\u4ee4\uff0c\u53ef\u80fd\u5bfc\u81f4\u663e\u793a\u5668\u65e0\u6cd5\u6062\u590d\u3002\n\n\u786e\u5b9a\u7ee7\u7eed\u53d1\u9001\u5417\uff1f",
            parent=self.root)

    def refresh_monitors(self):
        try:
            self.monitors = self.ddc.enumerate()
        except Exception as exc:
            self.monitors = []
            messagebox.showerror("DDC/CI", f"\u65e0\u6cd5\u679a\u4e3e\u7269\u7406\u663e\u793a\u5668\uff1a{exc}", parent=self.root)
            return
        self.listbox.delete(0, tk.END)
        self.listbox.configure(height=max(1, min(5, len(self.monitors))))
        for item in self.monitors:
            self.listbox.insert(tk.END, item.label)
        if self.monitors:
            target_keys = {item.key for item in self.targets}
            selected = [i for i, item in enumerate(self.monitors) if item.key in target_keys]
            if not selected:
                selected = list(range(len(self.monitors)))
            for index in selected:
                self.listbox.selection_set(index)
            self.status_var.set(f"\u627e\u5230 {len(self.monitors)} \u4e2a\u7269\u7406\u663e\u793a\u5668")
            self.detail_var.set(f"\u9996\u6b21\u6d4b\u8bd5\u4f7f\u7528 {D6_DESCRIPTIONS[self.power_off_value]}\uff0c\u6062\u590d\u4f7f\u7528 0x01")
        else:
            self.status_var.set("\u672a\u627e\u5230\u53ef\u7528\u7684\u7269\u7406\u663e\u793a\u5668")
        self._fit_main_window()

    def _selected(self):
        return [self.monitors[i] for i in self.listbox.curselection()]

    def begin_test(self):
        if self.test_active:
            return
        targets = self._selected()
        if not targets:
            messagebox.showwarning("DDC/CI", "\u8bf7\u5148\u9009\u62e9\u81f3\u5c11\u4e00\u4e2a\u663e\u793a\u5668\u3002", parent=self.root)
            return
        prompt = (f"\u8bf7\u786e\u8ba4 UU \u6216\u5176\u4ed6\u8fdc\u7a0b\u5df2\u8fde\u63a5\u3002\n\n"
                  f"\u5c06\u53d1\u9001 {D6_DESCRIPTIONS[self.power_off_value]}\u3002\n"
                  f"\u8bf7\u7528\u624b\u673a\u8fdc\u7a0b\u89c2\u5bdf\uff0c\u7136\u540e\u70b9\u51fb\u5f39\u7a97\u6309\u94ae\u6216\u6309 Enter/Esc \u5b8c\u6210\u5224\u5b9a\u3002")
        if not messagebox.askokcancel("\u5f00\u59cb\u4ea4\u4e92\u6d4b\u8bd5", prompt, parent=self.root):
            return
        if not self._confirm_risky_power():
            return
        self.test_active = True
        self.test_targets = list(targets)
        off_sent = False
        try:
            self.key_listener = GlobalKeyListener(lambda result: self.queue.put(("test_result", result)))
            self.key_listener.start()
            result = self._set_power(targets, self.power_off_value)
            if not result.ok:
                raise RuntimeError("; ".join(result.failures) or "DDC/CI off failed")
            off_sent = True
            self._show_test_popup(targets)
            self.test_deadline_job = self.root.after(TEST_TIMEOUT_SECONDS * 1000, lambda: self._complete_test("timeout"))
            self.status_var.set(f"\u6d4b\u8bd5\u4e2d\uff1a\u5df2\u53d1\u9001 {D6_DESCRIPTIONS[self.power_off_value]}")
        except Exception as exc:
            self._abort_test()
            if off_sent:
                try:
                    restored = self._set_power(targets, POWER_ON, retries=3)
                    if not restored.ok:
                        self.recovery_targets = list(targets)
                        self.display_off_active = True
                        self._start_wake_monitor()
                except Exception:
                    self.recovery_targets = list(targets)
                    self.display_off_active = True
                    self._start_wake_monitor()
            messagebox.showerror("DDC/CI", f"\u6d4b\u8bd5\u65e0\u6cd5\u5f00\u59cb\uff1a{exc}", parent=self.root)

    def _show_test_popup(self, targets):
        popup = tk.Toplevel(self.root)
        self.test_popup = popup
        popup.title("DDC/CI \u6d4b\u8bd5")
        popup.geometry("500x260")
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", lambda: self._complete_test("failure"))
        frame = tk.Frame(popup, padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="\u8bf7\u5728\u8fdc\u7a0b\u4e2d\u89c2\u5bdf\u5c4f\u5e55", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(frame, text=f"\u5df2\u53d1\u9001 {D6_DESCRIPTIONS[self.power_off_value]}\u3002\n\u5982\u679c\u5c4f\u5e55\u6b63\u5e38\u5173\u95ed\u4e14\u8fdc\u7a0b\u4ecd\u53ef\u64cd\u4f5c\uff0c\u9009\u62e9\u6210\u529f\u3002\n\u5982\u679c\u9ed1\u5c4f\u3001\u65e0\u6cd5\u6062\u590d\u6216\u8fdc\u7a0b\u65ad\u5f00\uff0c\u9009\u62e9\u5931\u8d25\u3002",
                 justify="left", wraplength=450).pack(anchor="w", pady=(12, 10))
        tk.Label(frame, text="Enter = \u6210\u529f    Esc = \u5931\u8d25", fg="#9b3d00").pack(anchor="w")
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", pady=(20, 0))
        tk.Button(buttons, text="\u6210\u529f\uff08Enter\uff09", width=16, command=lambda: self._complete_test("success")).pack(side="left")
        tk.Button(buttons, text="\u5931\u8d25\uff08Esc\uff09", width=16, command=lambda: self._complete_test("failure")).pack(side="left", padx=(12, 0))
        popup.lift()
        popup.focus_force()

    def _complete_test(self, result):
        if not self.test_active:
            return
        self.test_active = False
        if self.test_deadline_job:
            self.root.after_cancel(self.test_deadline_job)
            self.test_deadline_job = None
        if self.key_listener:
            self.key_listener.stop()
            self.key_listener = None
        if self.test_popup:
            self.test_popup.destroy()
            self.test_popup = None
        targets = list(self.test_targets)
        restore_error = None
        try:
            restored = self._set_power(targets, POWER_ON, retries=3)
            if not restored.ok:
                restore_error = "; ".join(restored.failures)
        except Exception as exc:
            restore_error = str(exc)
        if restore_error:
            self.recovery_targets = list(targets)
            self.display_off_active = True
            self._start_wake_monitor()
        else:
            self.recovery_targets = []
            self.display_off_active = False
        if result == "success" and not restore_error:
            self._save_config(targets)
            self.status_var.set("\u6d4b\u8bd5\u6210\u529f\uff0c\u5df2\u4fdd\u5b58\u663e\u793a\u5668\u914d\u7f6e")
            self.detail_var.set("\u53ef\u4ee5\u4f7f\u7528\u6258\u76d8\u83dc\u5355\u5173\u95ed\u548c\u6062\u590d\u5c4f\u5e55")
            messagebox.showinfo("DDC/CI", "\u6d4b\u8bd5\u6210\u529f\uff0c\u914d\u7f6e\u5df2\u4fdd\u5b58\u3002", parent=self.root)
        else:
            self.status_var.set("\u6d4b\u8bd5\u5931\u8d25\uff0c\u672a\u4fdd\u5b58\u914d\u7f6e")
            detail = "\u663e\u793a\u5668\u5df2\u5c1d\u8bd5\u6062\u590d\u3002"
            if restore_error:
                detail += " \u6062\u590d\u547d\u4ee4\u5931\u8d25\uff1a" + restore_error
            self.detail_var.set(detail)
            if result == "timeout":
                messagebox.showwarning("DDC/CI", "\u6d4b\u8bd5\u8d85\u65f6\uff0c\u5df2\u6309\u5931\u8d25\u5904\u7406\u3002", parent=self.root)
        self.test_targets = []

    def _abort_test(self):
        if self.key_listener:
            self.key_listener.stop()
            self.key_listener = None
        if self.test_popup:
            self.test_popup.destroy()
            self.test_popup = None
        self.test_active = False
        if self.test_deadline_job:
            self.root.after_cancel(self.test_deadline_job)
            self.test_deadline_job = None
        self.test_targets = []

    def _start_wake_monitor(self):
        self._stop_wake_monitor()
        if not self.display_off_active:
            return
        self.last_mouse_pos = None
        self.mouse_move_count = 0
        self.mouse_last_move_at = None
        if self.mouse_wake_enabled:
            self.mouse_poll_job = self.root.after(MOUSE_POLL_MS, self._wake_poll)
        if self.keyboard_wake_enabled:
            try:
                self.wake_key_listener = GlobalKeyListener(
                    lambda _result: self.queue.put("wake"), any_key=True)
                self.wake_key_listener.start()
            except Exception as exc:
                self.wake_key_listener = None
                messagebox.showwarning("DDC/CI", f"\u65e0\u6cd5\u542f\u7528\u952e\u76d8\u5524\u9192\uff1a{exc}", parent=self.root)

    def _stop_wake_monitor(self):
        if self.wake_restore_job:
            self.root.after_cancel(self.wake_restore_job)
            self.wake_restore_job = None
        if self.mouse_poll_job:
            self.root.after_cancel(self.mouse_poll_job)
            self.mouse_poll_job = None
        if self.wake_key_listener:
            self.wake_key_listener.stop()
            self.wake_key_listener = None
        self.last_mouse_pos = None
        self.mouse_move_count = 0
        self.mouse_last_move_at = None

    def _schedule_wake_restore(self):
        if not self.display_off_active or self.wake_restore_job:
            return
        if self.manual_off_cooldown_active:
            message = "\u624b\u52a8\u5173\u5c4f\u4ecd\u5728\u51b7\u5374\u671f\uff0c\u5ffd\u7565\u6062\u590d\u6761\u4ef6"
            self.power_event_var.set(message)
            self._append_log(message)
            return
        if not self.auto_power_off_active:
            self._append_log("\u624b\u52a8\u5173\u5c4f\u5df2\u6ee1\u8db3\u6062\u590d\u663e\u793a\u6761\u4ef6\uff0c\u76f4\u63a5\u53d1\u9001 DDC/CI 0x01")
            if not self.turn_on() and self.display_off_active:
                self._start_wake_monitor()
            return
        message = "\u5df2\u6ee1\u8db3\u6062\u590d\u663e\u793a\u6761\u4ef6\uff0c\u7b49\u5f85\u663e\u793a\u94fe\u8def\u7a33\u5b9a"
        self.power_event_var.set(message)
        self._append_log(message)
        delay_ms = max(0, int(round(self.wake_stabilize_seconds * 1000)))
        self.wake_restore_job = self.root.after(delay_ms, self._wake_restore)

    def _wake_restore(self):
        self.wake_restore_job = None
        if not self.display_off_active:
            return
        message = ("\u5df2\u7b49\u5f85 " + self._format_seconds(self.wake_stabilize_seconds)
                   + "\u79d2\uff0c\u5f00\u59cb\u53d1\u9001 DDC/CI 0x01")
        self.power_event_var.set(message)
        self._append_log(message)
        if not self.turn_on() and self.display_off_active:
            self._start_wake_monitor()

    def _handle_power_state(self, state):
        if self.test_active:
            return
        if state == 0:
            with self._fast_auto_off_lock:
                self._fast_auto_off_claimed = False
            if self.display_off_active or not (self.targets or self.recovery_targets):
                return
            self.display_off_active = True
            self.auto_power_off_active = True
            self._start_wake_monitor()
            message = ("\u6536\u5230 Windows \u81ea\u52a8\u5173\u5c4f\u5e7f\u64ad\uff0c"
                       "\u5df2\u7acb\u5373\u542f\u52a8 DDC/CI \u5173\u5c4f\u8865\u53d1")
            self.status_var.set(message)
            self.power_event_var.set(message)
            self._append_log(message)
        elif state in (1, 2):
            if self.display_off_active:
                if self.auto_power_off_active:
                    message = "Windows \u663e\u793a\u72b6\u6001\u5df2\u6062\u590d\uff0c\u7b49\u5f85\u914d\u7f6e\u7684\u6062\u590d\u663e\u793a\u6761\u4ef6"
                    self.power_event_var.set(message)
                    self._append_log(message)
                return
            self.display_off_active = False
            self._stop_wake_monitor()

    def _handle_power_off_result(self, payload):
        ok, detail = payload
        if ok:
            message = "\u81ea\u52a8\u5173\u5c4f\u7684 DDC/CI \u8865\u53d1\u6210\u529f"
        else:
            message = ("\u81ea\u52a8\u5173\u5c4f\u7684 DDC/CI \u8865\u53d1\u5931\u8d25\uff1a"
                       + (detail or "\u672a\u8fd4\u56de\u9519\u8bef\u8be6\u60c5"))
        self.status_var.set(message)
        self.power_event_var.set(message)
        self._append_log(message)

    def _handle_power_suspend(self):
        self.display_off_active = False
        self.auto_power_off_active = False
        self._cancel_manual_cooldown()
        self._stop_wake_monitor()
        self._append_log("\u7cfb\u7edf\u8fdb\u5165\u7761\u7720\uff0c\u5df2\u505c\u6b62\u5524\u9192\u76d1\u542c")

    def _handle_power_resume(self):
        self.display_off_active = False
        self.auto_power_off_active = False
        self._cancel_manual_cooldown()
        self._stop_wake_monitor()
        self._append_log("\u7cfb\u7edf\u5df2\u4ece\u7761\u7720\u6062\u590d")

    def _wake_poll(self):
        self.mouse_poll_job = None
        if not self.display_off_active or not self.mouse_wake_enabled:
            return
        point = POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            position = (int(point.x), int(point.y))
            now = time.monotonic()
            if self.last_mouse_pos is None:
                self.last_mouse_pos = position
            else:
                delta = math.hypot(position[0] - self.last_mouse_pos[0],
                                   position[1] - self.last_mouse_pos[1])
                self.last_mouse_pos = position
                if delta > 0 and delta >= self.mouse_min_step_px:
                    self.mouse_move_count += 1
                    self.mouse_last_move_at = now
                elif (self.mouse_move_count > 0 and self.mouse_last_move_at is not None
                      and now - self.mouse_last_move_at > self.mouse_pause_grace_seconds):
                    self.mouse_move_count = 0
                    self.mouse_last_move_at = None
                if self.mouse_move_count >= self.mouse_required_moves:
                    self.queue.put("wake")
                    return
        self.mouse_poll_job = self.root.after(MOUSE_POLL_MS, self._wake_poll)

    def _cancel_manual_cooldown(self):
        if self.manual_off_cooldown_job:
            self.root.after_cancel(self.manual_off_cooldown_job)
            self.manual_off_cooldown_job = None
        self.manual_off_cooldown_active = False

    def _perform_manual_off(self):
        self.manual_off_cooldown_job = None
        if self.display_off_active:
            return
        try:
            result = self._set_power(self.targets, self.power_off_value)
            if not result.ok:
                raise RuntimeError("; ".join(result.failures))
            self.display_off_active = True
            self.auto_power_off_active = False
            self._start_wake_monitor()
            self.status_var.set(f"\u5c4f\u5e55\u5df2\u53d1\u9001 {D6_DESCRIPTIONS[self.power_off_value]}")
            self._append_log(f"\u624b\u52a8\u5173\u5c4f\uff1a\u5df2\u53d1\u9001 {D6_DESCRIPTIONS[self.power_off_value]}")
            cooldown_ms = max(0, int(round(self.manual_off_cooldown_seconds * 1000)))
            if cooldown_ms:
                self.manual_off_cooldown_active = True
                self.manual_off_cooldown_job = self.root.after(
                    cooldown_ms, self._manual_cooldown_expired)
                message = ("\u624b\u52a8\u5173\u5c4f\u540e\u8fdb\u5165 "
                           + self._format_seconds(self.manual_off_cooldown_seconds)
                           + " \u79d2\u51b7\u5374\u671f\uff0c\u671f\u95f4\u4e0d\u53d1\u9001\u5173\u95ed\u6216\u6062\u590d\u4fe1\u53f7")
                self.status_var.set(message)
                self.power_event_var.set(message)
                self._append_log(message)
        except Exception as exc:
            self._append_log("\u624b\u52a8\u5173\u5c4f\u5931\u8d25\uff1a" + str(exc))
            messagebox.showerror("DDC/CI", str(exc), parent=self.root)

    def _manual_cooldown_expired(self):
        self.manual_off_cooldown_job = None
        self.manual_off_cooldown_active = False
        message = "\u624b\u52a8\u5173\u5c4f\u51b7\u5374\u671f\u7ed3\u675f\uff0c\u53ef\u53d1\u9001\u6062\u590d\u4fe1\u53f7"
        self.status_var.set(message)
        self.power_event_var.set(message)
        self._append_log(message)

    def turn_off(self):
        if not self.targets:
            messagebox.showinfo("DDC/CI", "\u8bf7\u5148\u5b8c\u6210\u9996\u6b21\u4ea4\u4e92\u6d4b\u8bd5\u3002", parent=self.root)
            return
        if self.display_off_active or self.manual_off_cooldown_active:
            return
        self._perform_manual_off()

    def turn_on(self):
        if self.manual_off_cooldown_active:
            message = "\u624b\u52a8\u5173\u5c4f\u4ecd\u5728\u51b7\u5374\u671f\uff0c\u6682\u4e0d\u53d1\u9001\u6062\u590d\u4fe1\u53f7"
            self.status_var.set(message)
            self.power_event_var.set(message)
            self._append_log(message)
            return False
        targets = self.targets or self.recovery_targets
        if not targets:
            return False
        try:
            result = self._set_power(targets, POWER_ON, retries=3)
            if not result.ok:
                raise RuntimeError("; ".join(result.failures))
            self.display_off_active = False
            self.auto_power_off_active = False
            self._stop_wake_monitor()
            self.recovery_targets = []
            self.status_var.set("\u5c4f\u5e55\u5df2\u6062\u590d")
            message = "\u5df2\u53d1\u9001 DDC/CI 0x01\uff0c\u5c4f\u5e55\u5df2\u6062\u590d"
            self.power_event_var.set(message)
            self._append_log(message)
            return True
        except Exception as exc:
            message = "DDC/CI 0x01 \u6062\u590d\u5931\u8d25\uff1a" + str(exc)
            self.power_event_var.set(message)
            self._append_log(message)
            messagebox.showerror("DDC/CI", str(exc), parent=self.root)
            return False

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if self.monitors == []:
            self.refresh_monitors()

    def hide_window(self):
        self.root.withdraw()

    def _tray_left_click(self, event_name):
        expected = "left_single" if self.tray_click_mode == "single" else "left_double"
        if event_name != expected:
            return
        if self.tray_click_action == "off":
            self.turn_off()
        else:
            self.show_window()

    def _poll(self):
        while True:
            try:
                action = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(action, tuple) and action[0] == "test_result":
                self._complete_test(action[1])
            elif isinstance(action, tuple) and action[0] == "power_state":
                self._handle_power_state(action[1])
            elif isinstance(action, tuple) and action[0] == "power_off_result":
                self._handle_power_off_result(action[1])
            elif isinstance(action, tuple) and action[0] == "power_listener_status":
                _, name, registered, error_code = action
                if registered:
                    self._append_log("Windows \u7535\u6e90\u5e7f\u64ad\u5df2\u6ce8\u518c\uff1a" + name)
                else:
                    self._append_log(("Windows \u7535\u6e90\u5e7f\u64ad\u6ce8\u518c\u5931\u8d25\uff1a" + name
                                      + " (Win32 " + str(error_code) + ")"))
            elif action == "power_suspend":
                self._handle_power_suspend()
            elif action == "power_resume":
                self._handle_power_resume()
            elif action == "wake":
                self._schedule_wake_restore()
            elif action in ("left_single", "left_double"):
                self._tray_left_click(action)
            elif action == "show":
                self.show_window()
            elif action == "test":
                self.show_window()
                self.begin_test()
            elif action == "off":
                self.turn_off()
            elif action == "on":
                self.turn_on()
            elif action == "exit":
                self.close()
        self.root.after(APP_POLL_MS, self._poll)

    def _restore_targets(self):
        found = {}
        for group in (self.targets, self.recovery_targets, self.test_targets):
            for item in group:
                found[item.key] = item
        return list(found.values())

    def _emergency_restore(self):
        with self._emergency_restore_lock:
            if self._emergency_restore_done:
                return
            self._emergency_restore_done = True
        targets = self._restore_targets()
        if not targets:
            return
        for attempt in range(3):
            try:
                self._set_power(targets, POWER_ON, retries=1)
            except BaseException:
                pass
            if attempt < 2:
                try:
                    time.sleep(0.25)
                except BaseException:
                    pass

    def close(self):
        self._cancel_manual_cooldown()
        if self.test_active:
            self._emergency_restore()
            self._abort_test()
        else:
            self._emergency_restore()
        self._stop_wake_monitor()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

def main():
    mutex_handle = _acquire_single_instance()
    if mutex_handle is None:
        messagebox.showinfo("DDC/CI", "\u5de5\u5177\u5df2\u5728\u8fd0\u884c\u4e2d\u3002")
        return
    app_holder = [None]

    def exit_signal_handler(signum, _frame):
        app = app_holder[0]
        if app is not None:
            app._emergency_restore()
        raise SystemExit(128 + int(signum))

    for signum_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, signum_name, None)
        if signum is not None:
            signal.signal(signum, exit_signal_handler)
    app = None
    try:
        app = App()
        app_holder[0] = app
        app.run()
    except BaseException as exc:
        if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            try:
                messagebox.showerror("DDC/CI", str(exc))
            except BaseException:
                pass
        raise
    finally:
        if app is not None:
            app._emergency_restore()
        kernel32.CloseHandle(mutex_handle)

if __name__ == "__main__":
    main()
