"""
Điều khiển cửa sổ game NroChibi: tìm cửa sổ, chụp màn hình, gửi chuột/bàn phím.

Tool chỉ đọc pixel trên màn hình và gửi input như người dùng thật,
không đụng vào bộ nhớ tiến trình hay gói tin mạng của game.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
from ctypes import wintypes

import cv2
import mss
import numpy as np

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # pragma: no cover - không quan trọng nếu thất bại
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

# Input bàn phím/chuột trên Windows chỉ tới cửa sổ đang có focus. Khi chạy
# nhiều acc, mỗi acc phải giành focus, nếu không thì lệnh của acc này sẽ
# chạy nhầm vào acc kia. Khoá này đảm bảo chỉ một acc giữ focus tại
# một thời điểm.
#
# Dùng RLock vì các hàm bấm phím có thể được gọi lồng nhau trong một
# `input_session()` (session đã giữ khoá sẵn).
INPUT_LOCK = threading.RLock()

# Chế độ gửi phím.
#
# True  = gửi thẳng WM_KEYDOWN/WM_KEYUP vào cửa sổ game bằng PostMessage.
#         KHÔNG cần giành focus, nên các cửa sổ game đứng yên, không nhảy
#         lên nhảy xuống khi nhiều acc cùng đánh boss.
# False = dùng keybd_event, phải giành focus trước (cách cũ). Cửa sổ sẽ
#         nhảy loạn khi chạy nhiều acc, chỉ dùng khi game không nhận
#         PostMessage.
POST_KEYS = True

# Đếm số lần thực sự giành focus. Dùng để kiểm chứng đã sửa được lỗi
# "cửa sổ nhảy loạn": con số này phải gần như đứng yên khi chạy nhiều acc.
FOCUS_STEALS = 0
_FOCUS_STEAL_LOCK = threading.Lock()


def reset_focus_steals() -> None:
    """Đặt lại bộ đếm giành focus."""
    global FOCUS_STEALS
    with _FOCUS_STEAL_LOCK:
        FOCUS_STEALS = 0


def count_focus_steals() -> int:
    """Số lần đã giành focus kể từ lần đặt lại gần nhất."""
    with _FOCUS_STEAL_LOCK:
        return FOCUS_STEALS


def set_post_keys(enabled: bool) -> None:
    """Bật/tắt chế độ gửi phím không cần focus."""
    global POST_KEYS
    POST_KEYS = bool(enabled)


def post_keys_enabled() -> bool:
    """Chế độ gửi phím hiện tại."""
    return POST_KEYS

# Tên cửa sổ game (tiêu đề cửa sổ trong taskbar).
GAME_WINDOW_TITLE = "NroChibi"

# Những chuỗi trong tiêu đề cho biết cửa sổ KHÔNG phải game.
# Ví dụ terminal đang chạy lệnh có chuỗi "NroChibi" sẽ bị loại.
NON_GAME_TITLE_MARKERS = (
    "ung dung tren may",
    "ứng dụng trên máy",
    "powershell",
    "command prompt",
    "cmd.exe",
    "windows terminal",
    "code - ",
    "visual studio code",
    "warp",
)

# Kích thước client tối thiểu để coi là cửa sổ game thật.
MIN_CLIENT_WIDTH = 1000
MIN_CLIENT_HEIGHT = 600

# Khoảng cách giữa hai lần bấm chuột (giây) để game kịp xử lý.
CLICK_DELAY = 0.05
KEY_DELAY = 0.03


class GameWindow:
    """Đại diện cho cửa sổ game đang chạy."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._sct = mss.mss()

    # ------------------------------------------------------------------ #
    # Tìm cửa sổ
    # ------------------------------------------------------------------ #
    @staticmethod
    def find(title: str = GAME_WINDOW_TITLE) -> "GameWindow":
        """Tìm cửa sổ game đang hiển thị đầu tiên.

        Lưu ý: có thể chạy nhiều instance game cùng lúc, hàm này trả về
        cửa sổ hợp lệ đầu tiên tìm thấy.
        """
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title.lower() in buffer.value.lower():
                found.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        if not found:
            raise RuntimeError(
                f"Không tìm thấy cửa sổ game '{title}'. Hãy mở game trước."
            )
        return GameWindow(found[0])

    @staticmethod
    def list_hwnds(title: str = GAME_WINDOW_TITLE) -> list[int]:
        """Liệt kê hwnd của các cửa sổ game đang mở.

        Chỉ trả về hwnd nên rất nhẹ: dùng để quét lại danh sách cửa sổ khi
        tool đang chạy mà không phải tạo đối tượng chụp màn hình mới.
        """
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            window_title = buffer.value
            # Tiêu đề của cửa sổ game đúng bằng tên game, hoặc có tên game
            # ở đầu (vd "NroChibi - Đang chơi"). Cửa sổ của ứng dụng khác
            # chỉ tình cờ chứa tên game ở giữa hoặc cuối chuỗi.
            lowered = window_title.lower()
            if lowered != title.lower() and not lowered.startswith(title.lower()):
                return True
            # Loại cửa sổ của ứng dụng khác chỉ tình cờ là chứa tên game.
            if any(marker in lowered for marker in NON_GAME_TITLE_MARKERS):
                return True
            # Cửa sổ game phải có kích thước client hợp lý.
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            client_w = rect.right - rect.left
            client_h = rect.bottom - rect.top
            if client_w < MIN_CLIENT_WIDTH or client_h < MIN_CLIENT_HEIGHT:
                return True
            found.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        return found

    @staticmethod
    def list_all(title: str = GAME_WINDOW_TITLE) -> list["GameWindow"]:
        """Liệt kê toàn bộ cửa sổ game (hữu ích khi chạy nhiều instance).

        Bỏ qua các cửa sổ không phải game: tiêu đề có chứa tên ứng dụng
        khác (ví dụ terminal đang chạy lệnh có chuỗi "NroChibi" trong
        nội dung), hoặc kích thước khác kích thước game chuẩn.
        """
        return [GameWindow(hwnd) for hwnd in GameWindow.list_hwnds(title)]

    # ------------------------------------------------------------------ #
    # Toạ độ
    # ------------------------------------------------------------------ #
    def client_rect(self) -> tuple[int, int, int, int]:
        """Trả về (left, top, width, height) của vùng client trong màn hình."""
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(origin))
        return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top

    def is_minimized(self) -> bool:
        return bool(user32.IsIconic(self.hwnd))

    # ------------------------------------------------------------------ #
    # Chụp màn hình
    # ------------------------------------------------------------------ #
    def capture(self) -> np.ndarray:
        """Chụp toàn bộ vùng client của cửa sổ game, trả về ảnh BGR.

        Ưu tiên chụp trực tiếp nội dung cửa sổ (PrintWindow) để vẫn đọc được
        khi cửa sổ bị cửa sổ khác che khuất. Nếu không được thì mới chụp
        vùng màn hình tương ứng.
        """
        frame = self.capture_window()
        if frame.size:
            return frame
        return self.capture_screen()

    def capture_window(self) -> np.ndarray:
        """Chụp nội dung cửa sổ bằng PrintWindow, không phụ thuộc cửa sổ có bị che."""
        try:
            import win32gui
            import win32ui
        except ImportError:
            return np.zeros((0, 0, 3), dtype=np.uint8)

        left, top, width, height = self.client_rect()
        if width <= 0 or height <= 0:
            return np.zeros((0, 0, 3), dtype=np.uint8)

        try:
            hwnd_dc = win32gui.GetWindowDC(self.hwnd)
            src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            mem_dc = src_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(src_dc, width, height)
            mem_dc.SelectObject(bitmap)

            # Flag 2 = PW_RENDERFULLCONTENT, giúp lấy nội dung với GPU render.
            ok = ctypes.windll.user32.PrintWindow(self.hwnd, mem_dc.GetSafeHdc(), 2)
            if not ok:
                ok = ctypes.windll.user32.PrintWindow(self.hwnd, mem_dc.GetSafeHdc(), 0)

            info = bitmap.GetInfo()
            buffer = bitmap.GetBitmapBits(True)
            frame = np.frombuffer(buffer, dtype=np.uint8)
            frame = frame.reshape(info["bmHeight"], info["bmWidth"], 4)
            frame = frame[:, :, :3].copy()  # BGR

            win32gui.DeleteObject(bitmap.GetHandle())
            mem_dc.DeleteDC()
            src_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)

            if not ok:
                return np.zeros((0, 0, 3), dtype=np.uint8)
            return frame
        except Exception:
            return np.zeros((0, 0, 3), dtype=np.uint8)

    def capture_screen(self) -> np.ndarray:
        """Chụp vùng màn hình tương ứng với client của cửa sổ game."""
        left, top, width, height = self.client_rect()
        if width <= 0 or height <= 0:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
        raw = self._sct.grab(monitor)
        frame = np.asarray(raw)  # BGRA
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    def capture_region(
        self, x: int, y: int, width: int, height: int
    ) -> np.ndarray:
        """Chụp một vùng con (toạ độ tương đối so với client của game)."""
        frame = self.capture()
        if frame.size == 0:
            return frame
        ch, cw = frame.shape[:2]
        x1 = max(0, min(x, cw))
        y1 = max(0, min(y, ch))
        x2 = max(x1, min(x + width, cw))
        y2 = max(y1, min(y + height, ch))
        return frame[y1:y2, x1:x2]

    # ------------------------------------------------------------------ #
    # Gửi input
    # ------------------------------------------------------------------ #
    def focus(self) -> None:
        """Đưa cửa sổ game lên trước để nhận input.

        Hàm này làm cửa sổ **nhảy lên đỉnh**. Chỉ dùng khi buộc phải gửi
        phím kiểu cũ; ở chế độ PostMessage không gọi tới đây.
        """
        global FOCUS_STEALS
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(self.hwnd)
        if user32.GetForegroundWindow() != self.hwnd:
            self._force_foreground()
        with _FOCUS_STEAL_LOCK:
            FOCUS_STEALS += 1
        time.sleep(0.05)

    def _force_foreground(self) -> None:
        """Cưỡng ép focus mà không cần bấm chuột vào cửa sổ.

        Bấm chuột để giành focus có nguy cơ vô tình bấm trúng bản đồ game và
        làm nhân vật di chuyển. Cách này gắn input của tiến trình ta vào
        tiến trình đang giữ focus rồi chuyển focus sang, an toàn hơn nhiều.
        """
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(foreground, None)
        if not target_thread:
            return
        user32.AttachThreadInput(current_thread, target_thread, True)
        try:
            user32.BringWindowToTop(self.hwnd)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetActiveWindow(self.hwnd)
            user32.SetFocus(self.hwnd)
        finally:
            user32.AttachThreadInput(current_thread, target_thread, False)

    def ensure_focus(self) -> None:
        """Đảm bảo cửa sổ này đang có focus, nếu không thì giành lại.

        Dùng khi chạy nhiều acc: mỗi acc phải giành focus trước khi gõ,
        vì input bàn phím chỉ tới cửa sổ đang focus.

        Ở chế độ `POST_KEYS` (mặc định) hàm này không làm gì, vì phím
        được gửi thẳng vào cửa sổ đích.
        """
        if POST_KEYS:
            return
        if user32.GetForegroundWindow() != self.hwnd:
            self.focus()

    @contextlib.contextmanager
    def input_session(self):
        """Giữ khoá input xuyên suốt một chuỗi thao tác.

        Rất quan trọng khi chạy nhiều acc. Một lệnh chat gồm nhiều bước
        (ESC -> R -> gõ -> ENTER). Nếu mỗi bước giành focus riêng, acc
        khác có thể chen vào giữa chừng và gõ lệnh nhầm sang cửa sổ của
        nó. Session giữ khoá xuyên suốt nên không acc nào chen được vào.

        Ở chế độ `POST_KEYS` không cần focus, khoá này chỉ để các thao
        tác không bị trộn lẫn thứ tự.
        """
        with INPUT_LOCK:
            self.ensure_focus()
            yield

    def client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Đổi toạ độ tương đối trong client sang toạ độ màn hình."""
        left, top, _, _ = self.client_rect()
        return left + x, top + y

    def move_mouse(self, x: int, y: int) -> None:
        sx, sy = self.client_to_screen(x, y)
        user32.SetCursorPos(sx, sy)

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click tại toạ độ tương đối trong client của game.

        Click được gửi thẳng tới cửa sổ game bằng `PostMessage`, không
        di chuyển chuột trên màn hình. Nhờ vậy không bị ảnh hưởng khi
        các cửa sổ game chồng lên nhau hoặc tràn ra ngoài màn hình.
        """
        with INPUT_LOCK:
            self.ensure_focus()
            if button == "right":
                self._post_click(x, y, right=True)
            else:
                self._post_click(x, y, right=False)
            time.sleep(CLICK_DELAY)

    def _post_click(self, x: int, y: int, right: bool = False) -> None:
        """Gửi thông báo chuột tới cửa sổ game tại toạ độ client."""
        WM_MOUSEMOVE = 0x0200
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202
        WM_RBUTTONDOWN = 0x0204
        WM_RBUTTONUP = 0x0205

        lparam = (y << 16) | (x & 0xFFFF)
        user32.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.02)
        if right:
            user32.PostMessageW(self.hwnd, WM_RBUTTONDOWN, 0x0002, lparam)
            time.sleep(CLICK_DELAY)
            user32.PostMessageW(self.hwnd, WM_RBUTTONUP, 0, 0, lparam)
        else:
            user32.PostMessageW(self.hwnd, WM_LBUTTONDOWN, 0x0001, lparam)
            time.sleep(CLICK_DELAY)
            user32.PostMessageW(self.hwnd, WM_LBUTTONUP, 0, 0, lparam)

    # ------------------------------------------------------------------ #
    # Gửi phím không cần focus
    # ------------------------------------------------------------------ #
    def _post_key_message(self, vk: int, down: bool, repeat: int = 0) -> None:
        """Gửi một sự kiện phím thẳng vào hàng đợi message của cửa sổ game."""
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
        lparam = 1 | (scan << 16) | ((repeat & 0xFFFF) << 0)
        if down:
            user32.PostMessageW(self.hwnd, WM_KEYDOWN, vk, lparam)
        else:
            lparam |= 1 << 30  # bit chuyển trạng thái
            lparam |= 1 << 31  # bit thả phím
            user32.PostMessageW(self.hwnd, WM_KEYUP, vk, lparam)

    def _post_char_message(self, char: str) -> None:
        """Gửi ký tự văn bản (WM_CHAR) để ô chat nhận được chữ."""
        WM_CHAR = 0x0102
        code = ord(char)
        if code > 0xFFFF:
            return
        user32.PostMessageW(self.hwnd, WM_CHAR, code, 1)

    def _post_press(self, vk: int, hold: float = 0.03) -> None:
        """Nhấn + thả một phím qua PostMessage (không cần focus)."""
        self._post_key_message(vk, True)
        time.sleep(hold)
        self._post_key_message(vk, False)
        time.sleep(KEY_DELAY)

    def key(self, vk: int, hold: float = 0.03) -> None:
        """Nhấn rồi thả một phím theo mã virtual key."""
        with INPUT_LOCK:
            if POST_KEYS:
                self._post_press(vk, hold)
                return
            self.ensure_focus()
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(hold)
            user32.keybd_event(vk, 0, 0x0002, 0)
            time.sleep(KEY_DELAY)

    def type_text(self, text: str, hold: float = 0.022) -> None:
        """Gõ một chuỗi ký tự, ký tự nào cũng viết thường.

        Toàn bộ chuỗi được gõ trong cùng một lần khoá. Ở chế độ
        `POST_KEYS`, mỗi ký tự được gửi bằng WM_CHAR nên ô chat nhận
        đúng chữ mà không cần cửa sổ phải có focus.

        Chỉ hỗ trợ chữ cái không dấu, số và vài ký tự đặc biệt.
        """
        with INPUT_LOCK:
            if POST_KEYS:
                for char in text:
                    code = CHAR_VK.get(char.lower())
                    if code is None:
                        continue
                    self._post_key_message(code, True)
                    time.sleep(0.005)
                    self._post_char_message(char.lower())
                    time.sleep(0.005)
                    self._post_key_message(code, False)
                    time.sleep(hold)
                return
            self.ensure_focus()
            for char in text:
                code = CHAR_VK.get(char.lower())
                if code is None:
                    continue
                user32.keybd_event(code, 0, 0, 0)
                time.sleep(hold)
                user32.keybd_event(code, 0, 0x0002, 0)
                time.sleep(KEY_DELAY)

    def key_combo(self, modifier: int, vk: int) -> None:
        """Nhấn tổ hợp phím, ví dụ Ctrl+A."""
        with INPUT_LOCK:
            if POST_KEYS:
                self._post_key_message(modifier, True)
                time.sleep(0.03)
                self._post_key_message(vk, True)
                time.sleep(0.03)
                self._post_key_message(vk, False)
                time.sleep(0.03)
                self._post_key_message(modifier, False)
                time.sleep(KEY_DELAY)
                return
            self.ensure_focus()
            user32.keybd_event(modifier, 0, 0, 0)
            time.sleep(0.04)
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.04)
            user32.keybd_event(vk, 0, 0x0002, 0)
            time.sleep(0.04)
            user32.keybd_event(modifier, 0, 0x0002, 0)
            time.sleep(KEY_DELAY)

    def press_burst(self, vks: list[int], count: int, interval: float = 0.12) -> None:
        """Bấm một phím nhiều lần liên tiếp, chỉ giành focus một lần.

        Khi chạy nhiều acc, mỗi lần bấm phím mà giành focus riêng sẽ khiến
        các cửa sổ liên tục nhảy qua nhảy lại, dễ làm rơi phím. Gom thành
        loạt giúp ổn định hơn nhiều.

        Ở chế độ `POST_KEYS` thì không giành focus lần nào cả: phím đi
        thẳng vào cửa sổ đích nên các cửa sổ game đứng yên.
        """
        if not vks or count <= 0:
            return
        with INPUT_LOCK:
            if POST_KEYS:
                for _ in range(count):
                    for vk in vks:
                        self._post_key_message(vk, True)
                        time.sleep(0.02)
                        self._post_key_message(vk, False)
                        time.sleep(0.02)
                    time.sleep(interval)
                return
            self.ensure_focus()
            for _ in range(count):
                for vk in vks:
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.03)
                    user32.keybd_event(vk, 0, 0x0002, 0)
                    time.sleep(0.02)
                time.sleep(interval)

    def scroll(self, amount: int) -> None:
        """Lăn chuột (amount dương = lên)."""
        user32.mouse_event(0x0800, 0, 0, amount * 120, 0)
        time.sleep(0.05)


# Mã virtual key thường dùng.
VK = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "ENTER": 0x0D, "SPACE": 0x20, "TAB": 0x09,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12,
    "BACKSPACE": 0x08, "DEL": 0x2E, "HOME": 0x24, "END": 0x23,
}

# Mã virtual key cho các ký tự có thể gõ vào ô chat.
CHAR_VK = {
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46,
    "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C,
    "m": 0x4D, "n": 0x4E, "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52,
    "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58,
    "y": 0x59, "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    " ": 0x20, "-": 0x2D, "_": 0x2D,
}
