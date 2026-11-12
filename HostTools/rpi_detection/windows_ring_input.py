"""Read Raw Input reports only from one explicitly selected Bluetooth ring.

Uses the existing Windows pairing. Does not inject input, change drivers, or
intercept unrelated keyboards. Calibration JSONL preserves raw reports because
the same physical button may produce several HID packets, not several presses.
"""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import struct
import time


UINT_ERROR = 0xFFFFFFFF


class Device(c.Structure):
    _fields_ = [("handle", w.HANDLE), ("type", w.DWORD)]


class Header(c.Structure):
    _fields_ = [("type", w.DWORD), ("size", w.DWORD),
                ("device", w.HANDLE), ("wparam", w.WPARAM)]


class Registration(c.Structure):
    _fields_ = [("page", w.USHORT), ("usage", w.USHORT),
                ("flags", w.DWORD), ("target", w.HWND)]


def windows_api():
    api = c.WinDLL("user32", use_last_error=True)
    declarations = {
        "GetRawInputDeviceList": ([c.POINTER(Device), c.POINTER(w.UINT), w.UINT], w.UINT),
        "GetRawInputDeviceInfoW": ([w.HANDLE, w.UINT, c.c_void_p, c.POINTER(w.UINT)], w.UINT),
        "GetRawInputData": ([w.HANDLE, w.UINT, c.c_void_p, c.POINTER(w.UINT), w.UINT], w.UINT),
        "RegisterRawInputDevices": ([c.POINTER(Registration), w.UINT, w.UINT], w.BOOL),
        "DefWindowProcW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t),
        "CreateWindowExW": ([w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                             c.c_int, c.c_int, c.c_int, c.c_int,
                             w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p], w.HWND),
        "SetTimer": ([w.HWND, c.c_size_t, w.UINT, c.c_void_p], c.c_size_t),
        "KillTimer": ([w.HWND, c.c_size_t], w.BOOL),
        "DestroyWindow": ([w.HWND], w.BOOL),
        "PostQuitMessage": ([c.c_int], None),
        "GetMessageW": ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], w.BOOL),
        "TranslateMessage": ([c.POINTER(w.MSG)], w.BOOL),
        "DispatchMessageW": ([c.POINTER(w.MSG)], c.c_ssize_t),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def device_info(api, handle):
    size = w.UINT()
    if api.GetRawInputDeviceInfoW(handle, 0x20000007, None, c.byref(size)) == UINT_ERROR:
        return None
    path = c.create_unicode_buffer(size.value + 1)
    if api.GetRawInputDeviceInfoW(handle, 0x20000007, path, c.byref(size)) == UINT_ERROR:
        return None
    info = c.create_string_buffer(32)
    c.memmove(info, struct.pack("I", 32), 4)
    size = w.UINT(32)
    if api.GetRawInputDeviceInfoW(handle, 0x2000000B, info, c.byref(size)) == UINT_ERROR:
        return None
    device_type = struct.unpack_from("I", info.raw, 4)[0]
    result = {"handle": handle, "type": device_type, "path": path.value}
    if device_type == 2:
        result.update(zip(("vendor", "product", "version", "usage_page", "usage"),
                          struct.unpack_from("IIIHH", info.raw, 8)))
    elif device_type == 0:
        result.update(usage_page=1, usage=2)
    elif device_type == 1:
        result.update(usage_page=1, usage=6)
    return result


def belongs_to_ring(info, mac):
    return bool(info and re.search(r"_" + re.escape(mac) + r"(?:&|#)", info["path"], re.I))


def desktop_name(api):
    kernel = c.WinDLL("kernel32", use_last_error=True)
    api.GetThreadDesktop.argtypes, api.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    api.GetUserObjectInformationW.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    desktop = api.GetThreadDesktop(kernel.GetCurrentThreadId())
    name = c.create_unicode_buffer(256)
    needed = w.DWORD()
    if not api.GetUserObjectInformationW(desktop, 2, name, c.sizeof(name), c.byref(needed)):
        raise c.WinError(c.get_last_error())
    return name.value


def ring_devices(api, mac):
    for _ in range(3):
        size = w.UINT()
        if api.GetRawInputDeviceList(None, c.byref(size), c.sizeof(Device)) == UINT_ERROR:
            raise c.WinError(c.get_last_error())
        devices = (Device * size.value)()
        count = api.GetRawInputDeviceList(devices, c.byref(size), c.sizeof(Device))
        if count != UINT_ERROR:
            result = [device_info(api, item.handle) for item in devices[:count]]
            return [item for item in result if belongs_to_ring(item, mac)]
    raise RuntimeError("Input devices changed during enumeration; retry")


def listen(api, devices, mac, seconds, emit):
    callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    known = {item["handle"]: item for item in devices}
    failure = []
    reports = 0

    def callback(hwnd, message, wparam, lparam):
        nonlocal reports
        try:
            if message == 0xFF:  # WM_INPUT
                header = Header()
                size = w.UINT(c.sizeof(header))
                if api.GetRawInputData(lparam, 0x10000005, c.byref(header), c.byref(size), c.sizeof(Header)) == UINT_ERROR:
                    raise c.WinError(c.get_last_error())
                if header.device not in known:
                    info = device_info(api, header.device)
                    known[header.device] = info if belongs_to_ring(info, mac) else None
                info = known[header.device]
                if info is not None:
                    # Retrieve the payload only AFTER checking the source device.
                    size = w.UINT()
                    if api.GetRawInputData(lparam, 0x10000003, None, c.byref(size), c.sizeof(Header)) == UINT_ERROR:
                        raise c.WinError(c.get_last_error())
                    data = c.create_string_buffer(size.value)
                    if api.GetRawInputData(lparam, 0x10000003, data, c.byref(size), c.sizeof(Header)) == UINT_ERROR:
                        raise c.WinError(c.get_last_error())
                    payload = data.raw[c.sizeof(Header):size.value]
                    item = {"kind": "input", "device": info, "type": header.type}
                    if header.type == 2:
                        report_size, count = struct.unpack_from("II", payload)
                        if report_size <= 0 or 8 + report_size * count > len(payload):
                            raise ValueError("Invalid HID report length")
                        item["reports"] = [payload[8 + n * report_size:8 + (n + 1) * report_size].hex()
                                           for n in range(count)]
                    else:
                        item["raw_hex"] = payload.hex()
                    emit(item)
                    reports += 1
            elif message == 0xFE:  # WM_INPUT_DEVICE_CHANGE: invalidate reused handles.
                known.pop(lparam, None)
                emit({"kind": "device_change"})
            elif message == 0x113:  # WM_TIMER
                api.PostQuitMessage(0)
        except Exception as error:
            failure.append(str(error))
            api.PostQuitMessage(1)
        return api.DefWindowProcW(hwnd, message, wparam, lparam)

    procedure = callback_type(callback)

    class WindowClass(c.Structure):
        _fields_ = [("style", w.UINT), ("proc", callback_type), ("class_extra", c.c_int),
                    ("window_extra", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                    ("cursor", w.HANDLE), ("background", w.HBRUSH),
                    ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [w.LPCWSTR], w.HMODULE
    instance = kernel.GetModuleHandleW(None)
    name = f"BMI30RingCalibration{os.getpid()}"
    window_class = WindowClass(0, procedure, 0, 0, instance, None, None, None, None, name)
    api.RegisterClassW.argtypes, api.RegisterClassW.restype = [c.POINTER(WindowClass)], w.ATOM
    api.UnregisterClassW.argtypes, api.UnregisterClassW.restype = [w.LPCWSTR, w.HINSTANCE], w.BOOL
    if not api.RegisterClassW(c.byref(window_class)):
        raise c.WinError(c.get_last_error())
    window = None
    try:
        window = api.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, w.HWND(-3), None, instance, None)
        if not window:
            raise c.WinError(c.get_last_error())
        usages = sorted({(item["usage_page"], item["usage"]) for item in devices})
        registrations = (Registration * len(usages))(
            *(Registration(page, usage, 0x2100, window) for page, usage in usages))
        if not api.RegisterRawInputDevices(registrations, len(usages), c.sizeof(Registration)):
            raise c.WinError(c.get_last_error())
        if not api.SetTimer(window, 1, int(seconds * 1000), None):
            raise c.WinError(c.get_last_error())
        emit({"kind": "ready", "devices": devices, "seconds": seconds,
              "native_windows_actions_suppressed": False})
        message = w.MSG()
        while True:
            result = api.GetMessageW(c.byref(message), None, 0, 0)
            if result == -1:
                raise c.WinError(c.get_last_error())
            if result == 0:
                break
            api.TranslateMessage(c.byref(message))
            api.DispatchMessageW(c.byref(message))
        if failure:
            raise RuntimeError(failure[0])
        emit({"kind": "end", "input_messages": reports})
    finally:
        if window:
            api.KillTimer(window, 1)
            api.DestroyWindow(window)
        api.UnregisterClassW(name, instance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    mac = args.mac.replace(":", "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", mac) or not 1 <= args.seconds <= 600:
        parser.error("A valid Bluetooth MAC and duration 1..600 seconds are required")
    if not args.list and args.output is None:
        parser.error("--output must name a new calibration JSONL file")
    api = windows_api()
    devices = ring_devices(api, mac)
    if args.list:
        print(json.dumps(devices, indent=2))
        return
    if not devices:
        raise RuntimeError("The selected ring has no active Raw Input interfaces")
    if desktop_name(api).startswith("CodexSandboxDesktop"):
        raise RuntimeError("Run the listener on the normal Windows desktop; the isolated sandbox desktop cannot receive physical input")
    with args.output.open("x", encoding="utf-8") as log:
        def emit(item):
            item = {"epoch": time.time(), "monotonic": time.perf_counter(), **item}
            encoded = json.dumps(item, separators=(",", ":"))
            log.write(encoded + "\n")
            log.flush()
            print(encoded, flush=True)
        try:
            listen(api, devices, mac, args.seconds, emit)
        except BaseException as error:
            emit({"kind": "fatal", "error": str(error)})
            raise


if __name__ == "__main__":
    main()
