"""
Điều khiển việc chuyển khu trong NroChibi.

Game có sẵn lệnh chat `k X` (chuyển khu X). Tool dùng cách gõ lệnh chat
vì ổn định hơn click vào bản đồ.

Hai chi tiết quan trọng phát hiện khi thử trên game thật:

1. Phím `R` chỉ mở ô chat khi chat đang đóng. Nếu chat đã mở, bấm `R`
   sẽ gõ thành chữ "r" trong ô chat, biến lệnh `k 15` thành `rk 15`.
   Vì vậy phải kiểm tra trạng thái chat trước khi bấm `R`.

2. Game giới hạn tần suất chuyển khu. Chuyển quá nhanh sẽ hiện hộp
   thoại "Không thể đổi khu vực lúc này, vui lòng đợi N giây". Hộp thoại
   này che mất ô chat, nên phải đóng và chờ hết trước khi gõ lệnh.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import cv2
import numpy as np

from game_window import VK, GameWindow

LOGGER = logging.getLogger("nro_boss")

# Lệnh chuyển khu trong game.
ZONE_COMMAND = "k"
# Phím mở ô chat.
CHAT_KEY = "R"

# --------------------------------------------------------------------- #
# Nhận diện ô nhập chat
# --------------------------------------------------------------------- #
# Ô nhập nằm trong hộp thoại "Chat" ở giữa phía dưới.
CHAT_INPUT_BOX = (0.33, 0.88, 0.69, 0.95)

# Màu nền ô nhập chat: xanh lá nhạt.
# Đo trên ảnh game thật: BGR (171, 243, 166) -> HSV (62, 81, 243).
# LƯU Ý: `_ratio` chuyển ảnh sang HSV nên các ngưỡng dưới đây phải là HSV.
CHAT_INPUT_LOW = np.array([45, 30, 150], dtype=np.uint8)
CHAT_INPUT_HIGH = np.array([85, 160, 255], dtype=np.uint8)

# Ngưỡng coi là chat đang mở.
CHAT_OPEN_RATIO = 0.12

# Vùng chữ bên trong ô nhập, tránh viền và nút X đỏ bên phải.
CHAT_TEXT_BOX = (0.345, 0.900, 0.640, 0.936)
# Khi ô trống, game vẽ chữ mờ "chat" màu xám (gray ~152). Khi đã gõ,
# chữ mờ biến mất và chữ thật gần đen (gray < 120).
CHAT_PLACEHOLDER_LOW = 130
CHAT_PLACEHOLDER_HIGH = 190
CHAT_PLACEHOLDER_MIN_PIXELS = 10
CHAT_TEXT_DARK = 120
CHAT_TEXT_MIN_PIXELS = 12
# Số lần BACKSPACE tối đa cho một lần xoá (tránh vòng lặp vô hạn).
CHAT_CLEAR_MAX_ATTEMPTS = 60

# --------------------------------------------------------------------- #
# Nhận diện hộp thoại chặn
# --------------------------------------------------------------------- #
# Vùng hẹp quanh tâm nút OK (nền vàng đậm) của hộp thoại.
# Dùng vùng hẹp để nền cát của bản đồ không gây báo động giả.
OK_BUTTON_BOX = (0.47, 0.93, 0.55, 0.96)

# Màu nút OK: vàng đậm, độ bão hoà cao.
OK_LOW = np.array([15, 140, 140], dtype=np.uint8)
OK_HIGH = np.array([33, 255, 255], dtype=np.uint8)

# Tâm nút OK, dùng để click.
OK_BUTTON_X = 0.51
OK_BUTTON_Y = 0.945

# Ngưỡng coi là có hộp thoại.
DIALOG_RATIO = 0.6


def _ratio(frame_bgr: np.ndarray, box: tuple[float, float, float, float],
          low: np.ndarray, high: np.ndarray) -> float:
    """Tỉ lệ pixel trong dải màu, tính trên vùng cho trước."""
    if frame_bgr is None or frame_bgr.size == 0:
        return 0.0
    height, width = frame_bgr.shape[:2]
    x1 = int(box[0] * width)
    x2 = int(box[2] * width)
    y1 = int(box[1] * height)
    y2 = int(box[3] * height)
    region = frame_bgr[y1:y2, x1:x2]
    if region.size == 0:
        return 0.0
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, low, high)
    return float(mask.sum()) / float(mask.size) / 255.0


def is_chat_open(window: GameWindow) -> bool:
    """Kiểm tra ô nhập chat có đang hiện không."""
    frame = window.capture()
    ratio = _ratio(frame, CHAT_INPUT_BOX, CHAT_INPUT_LOW, CHAT_INPUT_HIGH)
    return ratio > CHAT_OPEN_RATIO


def _chat_text_gray(frame: np.ndarray) -> np.ndarray | None:
    """Cắt vùng chữ trong ô chat và chuyển sang ảnh xám."""
    if frame is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    x1 = int(CHAT_TEXT_BOX[0] * width)
    x2 = int(CHAT_TEXT_BOX[2] * width)
    y1 = int(CHAT_TEXT_BOX[1] * height)
    y2 = int(CHAT_TEXT_BOX[3] * height)
    region = frame[y1:y2, x1:x2]
    if region.size == 0:
        return None
    return cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)


def chat_input_state(frame: np.ndarray) -> str:
    """Trạng thái ô chat: 'empty', 'text' hoặc 'unknown'.

    Dựa vào chữ mờ "chat" của game: ô trống luôn hiện chữ xám này, còn
    khi đã gõ chữ thì nó biến mất. Cách này không bị con trỏ nhấp nháy
    đánh lừa như khi đếm pixel đen.
    """
    gray = _chat_text_gray(frame)
    if gray is None:
        return "unknown"
    placeholder = int(np.count_nonzero(
        (gray >= CHAT_PLACEHOLDER_LOW) & (gray <= CHAT_PLACEHOLDER_HIGH)
    ))
    dark = int(np.count_nonzero(gray < CHAT_TEXT_DARK))
    # Chữ gõ vào có viền anti-alias rơi vào dải xám, nên không thể chỉ đếm
    # pixel xám. Ô trống thật sự có nhiều pixel xám hơn hẳn pixel đen.
    if placeholder >= CHAT_PLACEHOLDER_MIN_PIXELS and placeholder >= dark:
        return "empty"
    if dark >= CHAT_TEXT_MIN_PIXELS:
        return "text"
    return "unknown"


def has_block_dialog(window: GameWindow) -> bool:
    """Kiểm tra hộp thoại "không thể đổi khu" có đang hiện không."""
    frame = window.capture()
    ratio = _ratio(frame, OK_BUTTON_BOX, OK_LOW, OK_HIGH)
    return ratio > DIALOG_RATIO


def dismiss_dialog(window: GameWindow) -> bool:
    """Đóng hộp thoại đang mở bằng cách click nút OK."""
    if not has_block_dialog(window):
        return False
    _, _, width, height = window.client_rect()
    window.click(int(width * OK_BUTTON_X), int(height * OK_BUTTON_Y))
    time.sleep(0.4)
    return not has_block_dialog(window)


def wait_for_dialog_gone(window: GameWindow, timeout: float = 15.0) -> bool:
    """Chờ hộp thoại chặn biến mất hẳn.

    Hộp thoại này che mất ô chat. Phải đóng và chờ hết trước khi gõ
    lệnh, nếu không lệnh sẽ bị gõ nhầm chỗ.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not has_block_dialog(window):
            return True
        dismiss_dialog(window)
        time.sleep(0.4)
    return not has_block_dialog(window)


def clear_chat_input(window: GameWindow) -> None:
    """Xóa sạch nội dung đang có trong ô chat.

    Không dùng Ctrl+A: ô chat của game không hiểu tổ hợp chọn tất cả, nó
    coi phím A là ký tự bình thường và gõ luôn chữ "a" vào ô chat. Lỗi
    này tạo ra lệnh dạng "ak 15" thay vì "k 15".

    Cũng không bấm BACKSPACE mù: khi ô chat đã trống, bấm thêm BACKSPACE
    có thể làm game đóng luôn ô chat, khiến lệnh được gõ ra ngoài màn
    hình. Vì vậy mỗi lần chỉ bấm một phím rồi chụp lại, dừng ngay khi
    thấy chữ mờ "chat" của ô trống hiện lại.
    """
    if not is_chat_open(window):
        return
    for _ in range(CHAT_CLEAR_MAX_ATTEMPTS):
        if chat_input_state(window.capture()) != "text":
            return
        window.key(VK["BACKSPACE"], hold=0.01)
        time.sleep(0.02)
    LOGGER.warning(
        "Khong xoa het duoc o chat sau %d lan BACKSPACE", CHAT_CLEAR_MAX_ATTEMPTS
    )


def close_panel(window: GameWindow) -> None:
    """Đóng panel đang mở bằng phím ESC."""
    window.key(VK["ESC"])
    time.sleep(0.2)


def open_chat(window: GameWindow, timeout: float = 1.0) -> bool:
    """Mở ô chat, nhưng chỉ khi chat đang đóng.

    Nếu chat đã mở thì bấm R sẽ gõ thành chữ "r" trong ô chat.

    Trả về True khi ô chat đã mở thật sự. Chờ tối đa `timeout` giây vì
    panel có thể mất một nhịp mới hiện; nếu không mở được thì caller
    tuyệt đối không được gõ lệnh, tránh gõ lệnh ra ngoài màn hình game.
    """
    if is_chat_open(window):
        return True
    window.key(VK["R"])
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        time.sleep(0.1)
        if is_chat_open(window):
            return True
    return False


def send_command(window: GameWindow, command: str) -> None:
    """Đóng hộp thoại chặn, mở chat, gõ lệnh và gửi.

    Toàn bộ chuỗi thao tác nằm trong một `input_session()` để giữ focus
    của đúng cửa sổ này xuyên suốt. Nếu không, khi chạy nhiều acc thì
    giữa các bước acc khác có thể cướp focus và lệnh sẽ bị gõ nhầm sang
    cửa sổ khác.

    Thứ tự rất quan trọng: hộp thoại "Không thể đổi khu vực lúc này" che
    mất ô chat, nếu không đóng nó trước thì phím R sẽ bị nuốt và chat
    không mở được, khiến lệnh gõ vào sai chỗ.
    """
    with window.input_session():
        dismiss_dialog(window)
        close_panel(window)
        wait_for_dialog_gone(window)
        if not open_chat(window):
            LOGGER.warning("Khong mo duoc o chat nen khong gui lenh")
            return
        clear_chat_input(window)
        window.type_text(command)
        time.sleep(0.12)
        window.key(VK["ENTER"])
        time.sleep(0.2)


def go_to_zone(window: GameWindow, zone: int) -> None:
    """Chuyển tới khu `zone` bằng lệnh `k zone`."""
    send_command(window, f"{ZONE_COMMAND} {zone}")


# --------------------------------------------------------------------- #
# Đổi khu có xác nhận
# --------------------------------------------------------------------- #
@dataclass
class SwitchResult:
    """Kết quả một lần đổi khu."""

    ok: bool
    """Đã đổi khu được chưa."""

    zone: int | None
    """Khu thật sau khi đổi (None nếu không đọc được)."""

    verified: bool
    """Có xác nhận được bằng chỉ số khu không (không phải đoán)."""

    reason: str
    """Lý do ngắn gọn để ghi log: changed / already / unchanged / blocked / stop."""


def _sleep(seconds: float, should_stop=None) -> bool:
    """Ngủ `seconds` giây nhưng dừng sớm nếu có yêu cầu. True nếu bị dừng."""
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if should_stop is not None and should_stop():
            return True
        time.sleep(min(0.2, max(0.0, deadline - time.time())))
    return False


def switch_to_zone(
    window: GameWindow,
    zone: int,
    reader,
    timeout: float = 7.0,
    retries: int = 3,
    confirm_interval: float = 0.25,
    retry_wait: float = 10.0,
    should_stop=None,
    on_status=None,
) -> SwitchResult:
    """Chuyển tới khu `zone` và xác nhận bằng chỉ số khu thật.

    Điểm quan trọng: gửi lệnh `k X` **không có nghĩa là đã đổi khu**. Game
    có thể chặn bằng hộp thoại "Không thể đổi khu vực lúc này", hoặc nuốt
    mất lệnh. Vì vậy sau khi gửi, ta chụp dòng chỉ số khu ở góc trên trái
    và chỉ coi là thành công khi dòng chữ đó **thật sự đổi khác**.

    Mỗi lần đổi thành công, `reader` ghi nhớ ảnh của khu vừa tới, nên
    những lần sau đọc khu rất nhanh và chính xác.
    """
    last_reason = "timeout"
    for attempt in range(1, max(1, retries) + 1):
        if should_stop is not None and should_stop():
            return SwitchResult(False, reader.read(window), False, "stop")

        # Đã đứng đúng khu rồi thì không cần gửi lệnh (đỡ bị chặn).
        current = reader.read(window)
        if current == zone:
            return SwitchResult(True, zone, True, "already")

        before = reader.signature(window)
        if on_status is not None:
            on_status(f"GUI LENH K{zone} ({attempt})")
        go_to_zone(window, zone)
        time.sleep(0.4)

        # Ô chat còn mở nghĩa là ENTER không gửi được lệnh.
        if is_chat_open(window):
            last_reason = "chat"
            close_panel(window)
            time.sleep(0.5)
            continue

        # Chờ dòng chỉ số khu đổi khác.
        deadline = time.time() + timeout
        blocked = False
        changed = False
        while time.time() < deadline:
            if should_stop is not None and should_stop():
                return SwitchResult(False, reader.read(window), False, "stop")
            if has_block_dialog(window):
                dismiss_dialog(window)
                blocked = True
                break
            after = reader.signature(window)
            if reader.text_changed(before, after):
                # Xác nhận lần hai: ảnh chụp đơn lẻ có thể bị nhiễu, còn
                # dòng chỉ số khu thật thì đứng yên.
                time.sleep(0.3)
                confirm = reader.signature(window)
                if (
                    reader.similar(after, confirm)
                    and reader.text_changed(before, confirm)
                    and not has_block_dialog(window)
                ):
                    changed = True
                    # Chỉ học khi ảnh THẬT SỰ đổi: lúc đó chắc chắn ta
                    # vừa tới khu mới, nên nhãn `zone` là đúng.
                    reader.learn(confirm, zone)
                    return SwitchResult(True, zone, True, "changed")
                continue
            if on_status is not None:
                on_status(f"DANG DOI K{zone}")
            time.sleep(confirm_interval)

        if blocked:
            last_reason = "blocked"
            if on_status is not None:
                on_status("BI CHAN DOI KHU")
            if _sleep(retry_wait, should_stop):
                return SwitchResult(False, reader.read(window), False, "stop")
            continue

        if not changed:
            # Không có hộp thoại, cũng không đổi chữ: lệnh không có tác dụng
            # hoặc acc đã đứng đúng khu này từ trước.
            #
            # Nếu biết chắc acc đang ở khu KHÁC thì đây là thất bại: game
            # nuốt lệnh (ví dụ cửa sổ không có focus). Không được báo thành
            # công, nếu không acc sẽ tưởng đã sang khu mới và dò sai chỗ.
            if current is not None and current != zone:
                last_reason = "unchanged"
                if on_status is not None:
                    on_status(f"LENH K{zone} KHONG TAC DUNG")
                continue

            # Chưa biết khu hiện tại (lần đầu chạy): không có gì để đối
            # chiếu, coi như đã tới nơi để acc không kẹt ở khu đầu tiên.
            if on_status is not None:
                on_status(f"COI NHU DA O K{zone}")
            return SwitchResult(True, zone, False, "unchanged")

    return SwitchResult(False, reader.read(window), False, last_reason)

