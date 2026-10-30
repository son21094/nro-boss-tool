"""
Chế độ ĐỒNG BỘ BÀN PHÍM: gõ phím ở một cửa sổ game, các cửa sổ còn lại làm theo.

Chỉ đồng bộ bàn phím, không đồng bộ chuột.

Game NroChibi chỉ xử lý phím khi cửa sổ game đang có focus. Vì vậy không thể
gửi WM_KEYDOWN/WM_CHAR vào cửa sổ nền bằng PostMessage: game sẽ bỏ qua. Cách
làm ở đây là:

* Cài hook bàn phím toàn cục ở mức thấp (`WH_KEYBOARD_LL`).
* Khi cửa sổ game đang focus, gom các phím vừa gõ vào hàng đợi.
* Một thread riêng lần lượt đưa từng cửa sổ game lên trước, gửi phím thật
  bằng `keybd_event`, rồi trả focus về cửa sổ đang gõ.
* Bỏ qua các phím điều khiển của tool (F9-F12), phím Windows và các sự kiện
  do chính tool tạo ra để không bị nhân bản vòng lặp.

Mọi cửa sổ game đều phải nhận phím, nên:

* Cửa sổ đang thu nhỏ được khôi phục trước khi gửi, không bị bỏ qua.
* Cửa sổ game mở thêm sau khi bật đồng bộ cũng tự được đưa vào danh sách.
* Phím người dùng gõ trong lúc tool đang phát lại cho các acc khác được
  giữ lại (hook nuốt sự kiện) rồi phát lại ở lượt sau. Nhờ vậy phím không
  rơi vào đúng một acc đang tạm có focus và không acc nào bị sót.
* Mỗi cửa sổ được thử lấy focus vài lần; acc nào không nhận được phím sẽ
  được ghi cảnh báo kèm số hiệu acc.

Trong lúc đồng bộ, tool tự tạm ngừng gõ phím (`controller.input_suspended`)
để không tranh chỗ với thao tác của bạn.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from typing import NamedTuple

from game_window import INPUT_LOCK, GameWindow

LOGGER = logging.getLogger("nro_boss")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_PACKET = 0xE7

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# Giá trị đánh dấu sự kiện do chính Mirror gửi, để hook bỏ qua.
MIRROR_EXTRA_INFO = 0x4E524F42  # "NROB"

# Phím điều khiển của tool và các phím khóa hệ thống: không nhân bản.
SKIP_VKS = frozenset(
    {0x78, 0x79, 0x7A, 0x7B, VK_LWIN, VK_RWIN, VK_CAPITAL, VK_NUMLOCK, VK_SCROLL}
)  # F9-F12, Win, CapsLock, NumLock, ScrollLock

# Phím bổ trợ: không nhân bản riêng lẻ, nhưng trạng thái của chúng được áp
# dụng quanh phím chính (Shift+A, Ctrl+A, Alt+...).
MODIFIER_VKS = frozenset(
    {
        VK_SHIFT,
        VK_CONTROL,
        VK_MENU,
        VK_LSHIFT,
        VK_RSHIFT,
        VK_LCONTROL,
        VK_RCONTROL,
        VK_LMENU,
        VK_RMENU,
    }
)

# Phím bổ trợ trái/phải ứng với phím bổ trợ chung (Shift, Ctrl, Alt).
MODIFIER_GROUPS = {
    VK_SHIFT: (VK_LSHIFT, VK_RSHIFT),
    VK_CONTROL: (VK_LCONTROL, VK_RCONTROL),
    VK_MENU: (VK_LMENU, VK_RMENU),
}

# Chờ người dùng ngừng gõ một chút rồi mới chuyển focus. Nhờ vậy cả một
# chuỗi phím (ví dụ "k 12" + ENTER) được gửi nguyên vẹn, ít nháy focus.
BATCH_IDLE = 0.12
BATCH_MAX = 32

# Sau khi đưa một cửa sổ lên trước, chờ thêm một chút để game kịp nhận lại
# bàn phím. Game thường bỏ qua vài phím đầu tiên ngay sau khi đổi focus.
FOCUS_SETTLE = 0.05
FOCUS_RETRY_DELAY = 0.05
FOCUS_RETRIES = 3

# Cửa sổ vừa được khôi phục từ thu nhỏ cần thêm thời gian để vẽ lại.
RESTORE_SETTLE = 0.15

# Quét lại danh sách cửa sổ game định kỳ để acc mở thêm vẫn được đồng bộ.
WINDOW_RESCAN_INTERVAL = 3.0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KeyEvent(NamedTuple):
    master: int
    vk: int
    scan: int
    extended: bool
    shift: bool
    ctrl: bool
    alt: bool


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

# Khai báo kiểu tham số cho đúng: nếu để mặc định, ctypes cắt HANDLE
# xuống 32 bit và hook sẽ cài sai trên Windows 64 bit.
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    ctypes.c_void_p,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [
    wintypes.BYTE,
    wintypes.BYTE,
    wintypes.DWORD,
    ctypes.c_size_t,
]
user32.keybd_event.restype = None


class Mirror:
    """Nhân bản phím từ cửa sổ game đang focus sang các cửa sổ game khác."""

    def __init__(self, windows, config, controller=None):
        self.windows = list(windows)
        self.config = config
        self.controller = controller
        self.log = LOGGER

        self.active = False
        self._hook_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._ready = threading.Event()
        self._stop_worker = threading.Event()
        self._key_proc = None
        self._hook_ok = False

        # Hàng đợi phím. Hook tuyệt đối không được focus/gửi phím trực tiếp:
        # callback của low-level hook phải trả về nhanh, nếu không Windows sẽ
        # bỏ hook.
        self._queue: queue.Queue = queue.Queue(maxsize=512)

        # Đang trong lúc đưa cửa sổ khác lên trước để gửi phím. Trong khoảng
        # này bỏ qua phím vật lý để tránh nhân bản ngược.
        self._replaying = threading.Event()

        # Theo dõi phím vật lý đang giữ. Chỉ chuyển focus sau khi người dùng
        # đã nhả phím, tránh việc cửa sổ nguồn bị mất keyup và kẹt phím.
        self._held_vks: set[int] = set()
        self._keys_released = threading.Event()
        self._keys_released.set()

        # Trạng thái phím bổ trợ do hook tự theo dõi. Cần thiết vì khi đang
        # phát lại, phím Shift/Ctrl/Alt bị giữ lại (không tới cửa sổ nào),
        # nên GetAsyncKeyState không còn phản ánh đúng.
        self._mod_down: dict[int, bool] = {
            VK_SHIFT: False,
            VK_CONTROL: False,
            VK_MENU: False,
        }

        # Cửa sổ nguồn của lượt phát lại đang chạy. Phím người dùng gõ trong
        # lúc phát lại thuộc về cửa sổ này, không phải cửa sổ đích tạm thời.
        self._replay_master: int | None = None

        # Phím đã bị hook giữ lại để phát lại sau: phải nuốt luôn keyup của
        # chúng, tránh cửa sổ đích nhận một keyup lẻ.
        self._swallowed: set[int] = set()

        # Nhãn "acc N" cho log, cùng các bộ đếm để biết acc nào bị sót.
        self._labels: dict[int, str] = {
            win.hwnd: f"acc {index}" for index, win in enumerate(self.windows)
        }
        self._delivered: dict[int, int] = {}
        self._masters: set[int] = set()
        self._failed: set[int] = set()
        self._restored: set[int] = set()
        self._last_scan = 0.0

        self.mirrored_keys = 0
        self.dropped_keys = 0

    # ------------------------------------------------------------------ #
    # Bật / tắt
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """Bật chế độ đồng bộ phím."""
        if self.active:
            return True

        # Nạp lại danh sách cửa sổ: acc có thể vừa được mở thêm hoặc đóng.
        self._refresh_windows(force=True)
        if not self.windows:
            self.log.warning("Dong bo phim: khong co cua so game nao.")
            return False
        if len(self.windows) < 2:
            self.log.warning("Dong bo phim: can it nhat 2 cua so game.")
            return False

        self._drain_queue()
        self._held_vks.clear()
        self._swallowed.clear()
        self._mod_down = {VK_SHIFT: False, VK_CONTROL: False, VK_MENU: False}
        self._keys_released.set()
        self.mirrored_keys = 0
        self.dropped_keys = 0
        self._delivered.clear()
        self._masters.clear()
        self._failed.clear()
        self._restored.clear()
        self._replay_master = None
        self._stop_worker.clear()
        self._ready.clear()
        self._hook_ok = False
        self.active = True
        if self.controller is not None:
            self.controller.input_suspended = True

        self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self._worker_thread.start()
        self._hook_thread = threading.Thread(target=self._run_hooks, daemon=True)
        self._hook_thread.start()

        self._ready.wait(timeout=2.0)
        if not self._hook_ok:
            self.log.warning("Dong bo phim: khong cai duoc hook ban phim.")
            self.stop()
            return False

        self.log.info(
            "DONG BO PHIM: BAT. Go phim o mot cua so game de %d acc con lai lam theo.",
            len(self.windows) - 1,
        )
        return True

    def stop(self) -> None:
        """Tắt chế độ đồng bộ phím."""
        was_active = self.active
        self.active = False
        self._stop_worker.set()
        self._keys_released.set()
        self._swallowed.clear()
        self._replay_master = None
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

        for thread in (self._hook_thread, self._worker_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        self._hook_thread = None
        self._worker_thread = None

        if self.controller is not None:
            self.controller.input_suspended = False

        if was_active:
            extra = (
                f", {self.dropped_keys} phim bi bo"
                if self.dropped_keys
                else ""
            )
            reached = len(set(self._delivered) | self._masters)
            self.log.info(
                "DONG BO PHIM: TAT (%d phim da nhan ban toi %d/%d acc%s).",
                self.mirrored_keys,
                reached,
                len(self.windows),
                extra,
            )
            self._log_missing_windows()

    def toggle(self) -> bool:
        """Đảo trạng thái đồng bộ, trả về trạng thái mới."""
        if self.active:
            self.stop()
        else:
            self.start()
        return self.active

    # ------------------------------------------------------------------ #
    # Cửa sổ đích
    # ------------------------------------------------------------------ #
    def _foreground_game_window(self):
        """Cửa sổ game đang có focus (None nếu focus đang ở chỗ khác)."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        for win in self.windows:
            if win.hwnd == hwnd:
                return win
        return None

    def _window_by_hwnd(self, hwnd: int):
        for win in self.windows:
            if win.hwnd == hwnd:
                return win
        return None

    def _others(self, master):
        """Các cửa sổ game còn lại.

        Cửa sổ đang thu nhỏ vẫn được giữ lại: trước khi gửi phím, tool khôi
        phục nó. Bỏ qua chúng chính là lý do trước đây một số acc không
        đồng bộ.
        """
        return [win for win in self.windows if win.hwnd != master.hwnd]

    @staticmethod
    def _is_minimized(win) -> bool:
        try:
            return bool(win.is_minimized())
        except Exception:
            try:
                return bool(user32.IsIconic(win.hwnd))
            except Exception:
                return False

    @staticmethod
    def _is_visible(win) -> bool:
        try:
            return bool(user32.IsWindowVisible(win.hwnd))
        except Exception:
            return True

    def _label(self, win) -> str:
        """Tên ngắn của cửa sổ để ghi log (acc N)."""
        return self._labels.get(win.hwnd, f"hwnd {win.hwnd}")

    def _refresh_windows(self, force: bool = False) -> None:
        """Cập nhật danh sách cửa sổ game đang mở.

        Nhờ vậy acc mở thêm sau khi bật đồng bộ vẫn nhận phím, còn cửa sổ đã
        đóng thì được bỏ ra. Việc quét chỉ lấy hwnd nên rất nhẹ.
        """
        now = time.monotonic()
        if not force and now - self._last_scan < WINDOW_RESCAN_INTERVAL:
            return
        self._last_scan = now

        alive = []
        for win in self.windows:
            try:
                if user32.IsWindow(win.hwnd):
                    alive.append(win)
                    continue
            except Exception:
                # Không kiểm tra được thì cứ giữ lại, để bước gửi phím báo lỗi.
                alive.append(win)
                continue
            self.log.warning(
                "Dong bo phim: %s da dong, bo khoi danh sach dong bo.",
                self._label(win),
            )
            self._delivered.pop(win.hwnd, None)
            self._failed.discard(win.hwnd)

        known = {win.hwnd for win in alive}
        try:
            for hwnd in GameWindow.list_hwnds():
                if hwnd in known:
                    continue
                known.add(hwnd)
                win = GameWindow(hwnd)
                alive.append(win)
                self._labels.setdefault(hwnd, f"acc {len(self._labels)}")
                self.log.info(
                    "Dong bo phim: them %s (hwnd=%s) vao danh sach dong bo.",
                    self._label(win),
                    hwnd,
                )
        except Exception as exc:  # noqa: BLE001 - quét lại chỉ là tiện ích
            self.log.debug("Dong bo phim: khong quet lai duoc cua so: %s", exc)

        self.windows = alive

    def _mark_failed(self, win) -> None:
        """Ghi cảnh báo một lần cho acc không đưa được lên trước."""
        if win.hwnd in self._failed:
            return
        self._failed.add(win.hwnd)
        self.log.warning(
            "Dong bo phim: khong dua duoc %s len truoc nen acc nay khong nhan phim.",
            self._label(win),
        )

    def _log_missing_windows(self) -> None:
        """Kết thúc đồng bộ: nhắc những acc không nhận được phím nào."""
        missing = [
            self._label(win)
            for win in self.windows
            if win.hwnd in self._failed
        ]
        if not missing:
            return
        self.log.warning(
            "Dong bo phim: cac acc khong nhan duoc phim: %s.",
            ", ".join(missing),
        )

    def _track_modifier(self, vk: int, is_down: bool) -> None:
        """Theo dõi trạng thái Shift/Ctrl/Alt từ chính hook."""
        for generic, (left, right) in MODIFIER_GROUPS.items():
            if vk in (generic, left, right):
                self._mod_down[generic] = is_down
                return

    def _modifier_down(self, generic: int) -> bool:
        """Phím bổ trợ đang được giữ (theo hệ thống hoặc theo hook)."""
        try:
            if user32.GetAsyncKeyState(generic) & 0x8000:
                return True
        except Exception:
            pass
        return self._mod_down.get(generic, False)

    @staticmethod
    def _win_down() -> bool:
        """Phím Windows đang được giữ: để hệ điều hành xử lý tổ hợp."""
        try:
            return bool(user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or bool(
                user32.GetAsyncKeyState(VK_RWIN) & 0x8000
            )
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Hook bàn phím
    # ------------------------------------------------------------------ #
    def _on_key(self, n_code, w_param, l_param):
        """Hook phím: chỉ ghi sự kiện vào hàng đợi, không gửi trực tiếp."""
        if n_code < 0 or not self.active:
            return user32.CallNextHookEx(None, n_code, w_param, l_param)
        if w_param not in (
            WM_KEYDOWN,
            WM_KEYUP,
            WM_SYSKEYDOWN,
            WM_SYSKEYUP,
        ):
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        try:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if int(info.dwExtraInfo or 0) == MIRROR_EXTRA_INFO:
                # Sự kiện do chính tool gửi, không nhân bản lại.
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            vk = int(info.vkCode)
            if vk in SKIP_VKS or vk == VK_PACKET:
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            replaying = self._replaying.is_set()

            if vk in MODIFIER_VKS:
                self._track_modifier(vk, is_down)
                if is_down:
                    # Trong lúc phát lại, giữ luôn phím bổ trợ để không cửa
                    # sổ đích nào bị kẹt trạng thái Shift/Ctrl/Alt.
                    if replaying and self.active:
                        self._swallowed.add(vk)
                        return 1
                    return user32.CallNextHookEx(None, n_code, w_param, l_param)
                # Keyup tương ứng với keydown đã bị giữ lại: nuốt luôn, kể
                # cả khi đã phát lại xong.
                if vk in self._swallowed:
                    self._swallowed.discard(vk)
                    return 1
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            if not is_down:
                self._held_vks.discard(vk)
                if not self._held_vks:
                    self._keys_released.set()
                # Keyup của phím đã bị giữ lại lúc phát lại: nuốt luôn để
                # cửa sổ đích không nhận keyup lẻ.
                if vk in self._swallowed:
                    self._swallowed.discard(vk)
                    return 1
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            if self._win_down():
                # Tổ hợp phím Windows: để hệ điều hành xử lý.
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            swallow = False
            if replaying and self.active:
                # Đang phát lại cho các acc khác nên focus đang nằm ở một
                # cửa sổ đích tạm thời. Nếu để phím đi tiếp, nó chỉ rơi vào
                # đúng một acc đó. Giữ phím lại, gán cho cửa sổ nguồn và
                # phát lại ở lượt sau để mọi acc đều nhận.
                master_hwnd = self._replay_master
                if master_hwnd is None:
                    return user32.CallNextHookEx(None, n_code, w_param, l_param)
                swallow = True
            else:
                window = self._foreground_game_window()
                if window is None:
                    return user32.CallNextHookEx(None, n_code, w_param, l_param)
                master_hwnd = window.hwnd

            self._held_vks.add(vk)
            self._keys_released.clear()
            event = KeyEvent(
                master=master_hwnd,
                vk=vk,
                scan=int(info.scanCode) & 0xFF,
                extended=bool(info.flags & 0x01),
                shift=self._modifier_down(VK_SHIFT),
                ctrl=self._modifier_down(VK_CONTROL),
                alt=self._modifier_down(VK_MENU),
            )
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self.dropped_keys += 1
                swallow = False

            if swallow and self.active:
                self._swallowed.add(vk)
                return 1
        except Exception as exc:  # noqa: BLE001 - hook không được ném lỗi
            self.log.debug("Dong bo phim loi hook: %s", exc)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ------------------------------------------------------------------ #
    # Gửi phím
    # ------------------------------------------------------------------ #
    def _run_worker(self) -> None:
        """Gom phím trong một khoảng ngắn rồi phát lại cho từng cửa sổ."""
        while not self._stop_worker.is_set():
            try:
                first = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if first is None:
                break
            if not self.active:
                continue

            batch = [first]
            # Mỗi phím mới lại gia hạn thêm BATCH_IDLE. Khi người dùng
            # ngừng gõ, batch mới được phát lại.
            while len(batch) < BATCH_MAX and self.active:
                try:
                    item = self._queue.get(timeout=BATCH_IDLE)
                except queue.Empty:
                    break
                if item is None:
                    return
                batch.append(item)

            # Không đổi focus khi người dùng vẫn đang giữ phím, tránh làm
            # cửa sổ nguồn mất keyup và bị kẹt phím.
            if self._held_vks and self.active:
                self._keys_released.wait(timeout=2.0)
            if not self.active:
                continue
            try:
                self._replay_batch(batch)
            except Exception as exc:  # noqa: BLE001 - giữ worker sống
                self.log.exception("Dong bo phim: loi khi phat lai phim: %s", exc)

    def _replay_batch(self, batch: list[KeyEvent]) -> None:
        # Tách theo cửa sổ nguồn, phòng trường hợp người dùng đổi cửa sổ
        # giữa lúc gom phím.
        groups: dict[int, list[KeyEvent]] = {}
        for event in batch:
            groups.setdefault(event.master, []).append(event)

        for master_hwnd, events in groups.items():
            if not self.active or self._stop_worker.is_set():
                break

            # Acc mở thêm trong lúc đang đồng bộ vẫn phải nhận phím.
            self._refresh_windows()

            master = self._window_by_hwnd(master_hwnd)
            if master is None:
                self.log.debug(
                    "Dong bo phim: cua so nguon da dong, bo qua cum phim."
                )
                continue
            targets = self._others(master)
            if not targets:
                continue

            sent_total = 0
            with INPUT_LOCK:
                # Chỉ chặn phím vật lý sau khi đã có khóa input. Nếu đang
                # chờ acc khác nhả khóa, vẫn gom phím bình thường.
                self._replay_master = master.hwnd
                self._masters.add(master.hwnd)
                self._replaying.set()
                try:
                    for target in targets:
                        if not self.active or self._stop_worker.is_set():
                            break
                        if not self._activate_target(target):
                            if self.active and not self._stop_worker.is_set():
                                self._mark_failed(target)
                            continue
                        sent = 0
                        for event in events:
                            if not self.active or self._stop_worker.is_set():
                                break
                            if user32.GetForegroundWindow() != target.hwnd:
                                # Mất focus giữa chừng: lấy lại rồi mới gửi
                                # tiếp, tránh gõ nhầm sang cửa sổ khác.
                                if not self._activate_target(target):
                                    break
                            self._send_event(event)
                            sent += 1
                        if sent:
                            self._delivered[target.hwnd] = (
                                self._delivered.get(target.hwnd, 0) + sent
                            )
                            self._failed.discard(target.hwnd)
                            sent_total += sent
                        # Cho game kịp xử lý phím cuối trước khi chuyển focus.
                        time.sleep(0.03)
                finally:
                    # Trả focus về cửa sổ nguồn để người dùng gõ tiếp, kể cả
                    # khi vừa bấm tắt đồng bộ giữa lúc gửi phím.
                    try:
                        master.focus()
                    except Exception as exc:  # noqa: BLE001
                        self.log.debug(
                            "Dong bo phim: khong tra focus duoc: %s", exc
                        )
                    self._replaying.clear()
                    self._replay_master = None

            if self.active and not self._stop_worker.is_set():
                self.mirrored_keys += sent_total

    def _activate_target(self, win) -> bool:
        """Đưa một cửa sổ lên trước để nhận phím.

        Khôi phục cửa sổ đang thu nhỏ và thử lại vài lần, vì Windows có thể
        từ chối lần đưa lên đầu tiên.
        """
        try:
            if not user32.IsWindow(win.hwnd):
                return False
        except Exception:
            pass

        minimized = self._is_minimized(win)
        hidden = not self._is_visible(win)
        if minimized or hidden:
            if win.hwnd not in self._restored:
                self._restored.add(win.hwnd)
                self.log.info(
                    "Dong bo phim: khoi phuc %s (dang thu nho/an) de nhan phim.",
                    self._label(win),
                )
            if hidden:
                try:
                    user32.ShowWindow(win.hwnd, 5)  # SW_SHOW
                except Exception:
                    pass
            time.sleep(RESTORE_SETTLE)

        for _ in range(FOCUS_RETRIES):
            if not self.active or self._stop_worker.is_set():
                return False
            try:
                win.focus()
            except Exception as exc:  # noqa: BLE001
                self.log.debug(
                    "Dong bo phim: khong focus duoc %s: %s",
                    self._label(win),
                    exc,
                )
            if user32.GetForegroundWindow() == win.hwnd:
                # Chờ game nhận lại bàn phím sau khi đổi focus.
                time.sleep(FOCUS_SETTLE)
                if user32.GetForegroundWindow() == win.hwnd:
                    return True
            time.sleep(FOCUS_RETRY_DELAY)
        return False

    @staticmethod
    def _send_event(event: KeyEvent) -> None:
        """Gửi một lần nhấn/nhả phím thật vào cửa sổ đang focus."""
        modifiers: list[int] = []
        if event.ctrl:
            modifiers.append(VK_CONTROL)
        if event.shift:
            modifiers.append(VK_SHIFT)
        if event.alt:
            modifiers.append(VK_MENU)

        for vk in modifiers:
            user32.keybd_event(vk, 0, 0, MIRROR_EXTRA_INFO)
            time.sleep(0.012)

        flags = KEYEVENTF_EXTENDEDKEY if event.extended else 0
        user32.keybd_event(
            event.vk, event.scan, flags, MIRROR_EXTRA_INFO
        )
        time.sleep(0.025)
        user32.keybd_event(
            event.vk,
            event.scan,
            flags | KEYEVENTF_KEYUP,
            MIRROR_EXTRA_INFO,
        )

        for vk in reversed(modifiers):
            time.sleep(0.012)
            user32.keybd_event(
                vk, 0, KEYEVENTF_KEYUP, MIRROR_EXTRA_INFO
            )
        time.sleep(0.01)

    # ------------------------------------------------------------------ #
    # Vòng lặp message của hook
    # ------------------------------------------------------------------ #
    def _run_hooks(self) -> None:
        """Cài hook bàn phím và chạy vòng lặp message."""
        self._thread_id = kernel32.GetCurrentThreadId()
        key_proc = HOOKPROC(self._on_key)
        self._key_proc = key_proc
        key_hook = None
        try:
            key_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, key_proc, None, 0
            )
            if key_hook:
                self._hook_ok = True
            else:
                self.log.warning("Dong bo phim: khong cai duoc hook phim.")
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Dong bo phim: loi cai hook: %s", exc)
        self._ready.set()

        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if key_hook:
                try:
                    user32.UnhookWindowsHookEx(key_hook)
                except Exception:
                    pass
            self._thread_id = 0
            if self.active:
                self.log.warning("Dong bo phim: hook phim da dung ngoai y muon.")
                self.active = False
                self._stop_worker.set()
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass
            if self.controller is not None and not self.active:
                self.controller.input_suspended = False

    # ------------------------------------------------------------------ #
    # Hàng đợi
    # ------------------------------------------------------------------ #
    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
