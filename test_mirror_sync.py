"""
Kiem tra phan dong bo ban phim bang cua so gia, khong can mo game.

Chay:
    python test_mirror_sync.py

Kiem tra 6 tinh huong tung lam mot so acc khong duoc dong bo:
    1. Cua so dang thu nho van nhan phim (duoc khoi phuc).
    2. Cua so tu choi focus vai lan dau van nhan phim (co thu lai).
    3. Phim go trong luc tool dang phat lai duoc giu lai cho luot sau,
       khong roi vao dung mot acc dang tam co focus.
    4. Keyup cua phim bi giu lai cung bi nuot, khong de lai keyup le.
    5. Nam acc deu nhan du so phim.
    6. Cua so da dong bi bo khoi danh sach, cua so mo them duoc nap vao.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from types import SimpleNamespace

import mirroring
from mirroring import KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP, KeyEvent, Mirror

WINDOWS: list["FakeWindow"] = []


class FakeUser32:
    """Gia lap cac ham Win32 ma Mirror dung toi."""

    def __init__(self) -> None:
        self.foreground = 0
        self.async_state: dict[int, bool] = {}

    def CallNextHookEx(self, *args):
        return 0

    def GetForegroundWindow(self):
        return self.foreground

    def GetAsyncKeyState(self, vk: int):
        return 0x8000 if self.async_state.get(vk) else 0

    def IsWindow(self, hwnd: int) -> bool:
        return any(win.hwnd == hwnd for win in WINDOWS)

    def IsWindowVisible(self, hwnd: int) -> bool:
        return True

    def IsIconic(self, hwnd: int) -> bool:
        return any(win.hwnd == hwnd and win.minimized for win in WINDOWS)

    def ShowWindow(self, hwnd: int, cmd: int) -> bool:
        for win in WINDOWS:
            if win.hwnd == hwnd:
                win.minimized = False
        return True


class FakeWindow:
    """Cua so game gia: chi can hwnd, focus va trang thai thu nho."""

    def __init__(
        self, hwnd: int, minimized: bool = False, fail_focus_times: int = 0
    ) -> None:
        self.hwnd = hwnd
        self.minimized = minimized
        self.fail_focus_times = fail_focus_times
        self.focus_calls = 0

    def is_minimized(self) -> bool:
        return self.minimized

    def focus(self) -> None:
        self.focus_calls += 1
        if self.fail_focus_times > 0:
            # Gia lap Windows tu choi dua cua so len truoc.
            self.fail_focus_times -= 1
            return
        self.minimized = False
        USER32.foreground = self.hwnd


def key(vk: int, master: int) -> KeyEvent:
    return KeyEvent(
        master=master,
        vk=vk,
        scan=0x1C,
        extended=False,
        shift=False,
        ctrl=False,
        alt=False,
    )


_KEEP_ALIVE: list[KBDLLHOOKSTRUCT] = []


def hook_struct(vk: int) -> int:
    info = KBDLLHOOKSTRUCT(
        vkCode=vk, scanCode=0x30, flags=0, time=0, dwExtraInfo=0
    )
    # Giữ tham chiếu để vùng nhớ không bị thu hồi trước khi hook đọc.
    _KEEP_ALIVE.append(info)
    return ctypes.addressof(info)


def main() -> int:
    logging.basicConfig(level=logging.CRITICAL)
    global USER32
    USER32 = FakeUser32()
    WINDOWS.extend(
        [
            FakeWindow(101),
            FakeWindow(102, minimized=True),
            FakeWindow(103, fail_focus_times=2),
            FakeWindow(104),
        ]
    )
    mirroring.user32 = USER32
    mirroring.GameWindow.list_hwnds = lambda *a, **k: [w.hwnd for w in WINDOWS]
    USER32.foreground = 101

    mirror = Mirror(WINDOWS, SimpleNamespace(), controller=None)
    mirror.active = True
    delivered: list[tuple[int, int]] = []

    def fake_send(event: KeyEvent) -> None:
        delivered.append((USER32.foreground, event.vk))

    mirror._send_event = fake_send

    batch = [key(0x4B, 101), key(0x0D, 101)]  # "k" + ENTER
    mirror._replay_batch(batch)

    by_window: dict[int, int] = {}
    for hwnd, _vk in delivered:
        by_window[hwnd] = by_window.get(hwnd, 0) + 1

    assert by_window == {102: 2, 103: 2, 104: 2}, by_window
    print("[PASS] cua so thu nho van nhan phim")
    assert not WINDOWS[1].minimized, "cua so thu nho phai duoc khoi phuc"
    print("[PASS] cua so thu nho duoc khoi phuc")
    assert WINDOWS[2].focus_calls >= 3, "phai thu lai khi focus bi tu choi"
    print("[PASS] focus bi tu choi duoc thu lai")
    assert USER32.foreground == 101, "phai tra focus ve cua so nguon"
    assert mirror.mirrored_keys == 6, mirror.mirrored_keys
    assert len(mirror._delivered) == 3, mirror._delivered
    print("[PASS] phat lai toi tat ca cua so")

    # Phim go trong luc dang phat lai phai duoc giu lai.
    mirror._drain_queue()
    mirror._replaying.set()
    mirror._replay_master = 101
    USER32.foreground = 103  # cua so dich dang tam giu focus
    result = mirror._on_key(0, WM_KEYDOWN, hook_struct(0x42))
    assert result == 1, "phai nuot phim de khong roi vao mot acc duy nhat"
    event = mirror._queue.get_nowait()
    assert event.master == 101 and event.vk == 0x42, event
    print("[PASS] phim go trong luc phat lai duoc giu lai")

    # Keyup tuong ung cung bi nuot, ke ca khi da phat lai xong.
    mirror._replaying.clear()
    mirror._replay_master = None
    assert mirror._on_key(0, WM_KEYUP, hook_struct(0x42)) == 1
    assert 0x42 not in mirror._swallowed
    print("[PASS] keyup lech cap cung bi nuot")

    # Ngoai luc phat lai, phim di qua binh thuong.
    USER32.foreground = 104
    assert mirror._on_key(0, WM_KEYDOWN, hook_struct(0x42)) == 0
    event = mirror._queue.get_nowait()
    assert event.master == 104, event
    print("[PASS] hook xu ly dung khi khong phat lai")

    # Nhieu acc: moi cua so nhan du phim.
    mirror._delivered.clear()
    mirror._failed.clear()
    mirror.mirrored_keys = 0
    produced = [FakeWindow(201 + index) for index in range(5)]
    WINDOWS.extend(produced)
    mirroring.GameWindow.list_hwnds = lambda *a, **k: [w.hwnd for w in produced]
    mirror.windows = produced
    USER32.foreground = 201
    delivered.clear()
    mirror._replay_batch([key(0x4B, 201), key(0x0D, 201)])
    by_window = {}
    for hwnd, _vk in delivered:
        by_window[hwnd] = by_window.get(hwnd, 0) + 1
    assert by_window == {202: 2, 203: 2, 204: 2, 205: 2}, by_window
    print("[PASS] 5 acc deu nhan du phim")

    # Quet lai danh sach cua so.
    closed = produced[1]
    WINDOWS.remove(closed)
    new_window = FakeWindow(999)
    WINDOWS.append(new_window)
    mirroring.GameWindow.list_hwnds = lambda *a, **k: [w.hwnd for w in WINDOWS]
    mirror._refresh_windows(force=True)
    current = [win.hwnd for win in mirror.windows]
    assert closed.hwnd not in current, current
    assert new_window.hwnd in current, current
    print("[PASS] quet lai bo cua so da dong, nap cua so mo them")

    print("\nTat ca kiem tra dong bo deu dat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
