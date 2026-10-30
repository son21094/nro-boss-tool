"""
Test phần đổi khu có xác nhận, không cần mở game.

Mô phỏng một cửa sổ game bằng ảnh tự vẽ: dòng chỉ số khu ở góc trên trái
đổi nội dung theo khu. Nhờ vậy kiểm được đúng thứ từng gây lỗi "acc vào
nhầm khu": lệnh bị game chặn nhưng tool vẫn tưởng đã đổi khu thành công.
"""

from __future__ import annotations

import cv2
import numpy as np

import zone_control
from game_window import VK
from zone_control import switch_to_zone
from zone_reader import ZoneReader

WIDTH, HEIGHT = 1340, 720


def render(zone: int) -> np.ndarray:
    """Vẽ một khung game giả, chỉ có dòng chỉ số khu."""
    frame = np.full((HEIGHT, WIDTH, 3), 40, dtype=np.uint8)
    text = f"154.Hanh tinh Bill - K{zone}"
    cv2.putText(
        frame,
        text,
        (165, 113),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return frame


def chat_box_frame(*, placeholder: bool = False, text: str = "") -> np.ndarray:
    """Vẽ khung game giả có ô chat mở.

    `placeholder=True` mô phỏng ô trống (chữ mờ "chat" màu xám).
    `text` mô phỏng chữ đã gõ (màu gần đen, chữ mờ biến mất).
    """
    frame = render(0)
    x1 = int(zone_control.CHAT_INPUT_BOX[0] * WIDTH)
    y1 = int(zone_control.CHAT_INPUT_BOX[1] * HEIGHT)
    x2 = int(zone_control.CHAT_INPUT_BOX[2] * WIDTH)
    y2 = int(zone_control.CHAT_INPUT_BOX[3] * HEIGHT)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (166, 243, 171), -1)

    tx = int(zone_control.CHAT_TEXT_BOX[0] * WIDTH) + 3
    ty = int(zone_control.CHAT_TEXT_BOX[3] * HEIGHT) - 5
    if placeholder:
        cv2.putText(
            frame, "chat", (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
            (152, 152, 152), 1, cv2.LINE_AA,
        )
    if text:
        cv2.putText(
            frame, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
            (40, 40, 40), 1, cv2.LINE_AA,
        )
    return frame


class FakeWindow:
    """Cửa sổ game giả: đổi khu được, có thể bị chặn."""

    def __init__(self, zone: int = 3):
        self.zone = zone
        self.reject = False
        self.dialog = False
        self.chat_open = False
        self.commands: list[int] = []

    def capture(self):
        return render(self.zone)

    def client_rect(self):
        return 0, 0, WIDTH, HEIGHT

    # Các hàm input không làm gì; test thay `send_command` bên dưới.
    def key(self, *args, **kwargs):
        pass

    def type_text(self, *args, **kwargs):
        pass

    def key_combo(self, *args, **kwargs):
        pass

    def click(self, *args, **kwargs):
        pass

    def focus(self):
        pass


def install_fakes(window: FakeWindow) -> None:
    """Thay các hàm thao tác thật bằng hành vi giả lập."""

    def fake_send_command(win, command: str):
        win.chat_open = False
        zone = int(command.split()[-1])
        win.commands.append(zone)
        if win.reject:
            win.dialog = True
            return
        win.zone = zone

    zone_control.send_command = fake_send_command
    zone_control.is_chat_open = lambda win: win.chat_open
    zone_control.has_block_dialog = lambda win: win.dialog
    zone_control.dismiss_dialog = lambda win: setattr(win, "dialog", False) or True
    zone_control.close_panel = lambda win: None


RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    RESULTS.append((bool(ok), name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def main() -> int:
    # 0. Ô chat phải được nhận ra bằng đúng hệ màu HSV.
    #    (Đã từng bị lỗi: khai báo màu BGR nhưng lại đem so trong HSV, nên
    #     is_chat_open luôn False -> tool không biết chat đang mở.)
    chat_frame = chat_box_frame()
    check(
        zone_control.is_chat_open(_FrameWindow(chat_frame)),
        "nhan dien o chat: dang mo",
    )
    check(
        not zone_control.is_chat_open(_FrameWindow(render(0))),
        "nhan dien o chat: dang dong",
    )

    # 0b. Ô chat trống có chữ mờ "chat"; ô đã gõ thì chữ mờ biến mất.
    empty_frame = chat_box_frame(placeholder=True)
    text_frame = chat_box_frame(text="k 7")
    check(
        zone_control.chat_input_state(empty_frame) == "empty",
        "o chat trong: nhan ra trang thai empty",
    )
    check(
        zone_control.chat_input_state(text_frame) == "text",
        "o chat co chu: nhan ra trang thai text",
    )

    # 0c. Ô trống thì không được bấm phím nào (bấm mù dễ làm đóng ô chat).
    key_window = _KeyWindow(empty_frame)
    zone_control.clear_chat_input(key_window)
    check(
        key_window.keys == [],
        "o chat trong: khong bam phim nao",
    )

    # 0d. Có chữ thì chỉ bấm BACKSPACE, dừng ngay khi ô trống trở lại,
    #     tuyệt đối không gõ thêm chữ "a" (lỗi Ctrl+A của game).
    clear_window = _KeyWindow(text_frame, text_frame, empty_frame)
    zone_control.clear_chat_input(clear_window)
    check(
        clear_window.keys == [VK["BACKSPACE"]],
        "xoa o chat: chi BACKSPACE va dung khi het chu",
    )

    window = FakeWindow(zone=3)
    install_fakes(window)
    reader = ZoneReader()
    kwargs = dict(timeout=0.4, retries=3, confirm_interval=0.02, retry_wait=0.0)

    # 1. Đổi khu bình thường -> xác nhận được, học được khu mới.
    result = switch_to_zone(window, 7, reader, **kwargs)
    check(result.ok and result.verified, "doi khu binh thuong: thanh cong")
    check(result.zone == 7, "doi khu binh thuong: dung khu 7")
    check(reader.read(window) == 7, "doi khu binh thuong: doc lai ra khu 7")
    check(reader.sample_count() == 1, "doi khu binh thuong: hoc duoc 1 mau")

    # 2. Đã đứng đúng khu -> không gửi lệnh, không bị chặn.
    before = len(window.commands)
    result = switch_to_zone(window, 7, reader, **kwargs)
    check(result.ok and result.reason == "already", "da o dung khu: khong gui lenh")
    check(len(window.commands) == before, "da o dung khu: khong ton them lenh")

    # 3. Lệnh bị game chặn -> phải thử lại và KHÔNG báo nhầm là thành công.
    window.reject = True
    result = switch_to_zone(window, 12, reader, **kwargs)
    check(not result.ok, "bi chan: bao that bai")
    check(result.zone == 7, "bi chan: khu that van la 7, khong phai 12")
    check(result.reason == "blocked", "bi chan: ly do la blocked")
    check(reader.read(window) == 7, "bi chan: khong hoc nham mau")

    # 4. Bị chặn lần đầu, lần sau được -> thử lại thành công.
    window.reject = False
    window.dialog = False
    calls = {"n": 0}

    def flaky_send(win, command: str):
        calls["n"] += 1
        win.chat_open = False
        if calls["n"] == 1:
            win.dialog = True
            return
        win.zone = int(command.split()[-1])

    zone_control.send_command = flaky_send
    result = switch_to_zone(window, 15, reader, **kwargs)
    check(result.ok and result.verified, "chan lan dau: thu lai thanh cong")
    check(calls["n"] >= 2, "chan lan dau: co gui lai lenh")
    check(reader.read(window) == 15, "chan lan dau: hoc duoc khu 15")

    # 5. Lệnh không có tác dụng (không hộp thoại, không đổi chữ) -> coi như
    #    đã ở khu đó, nhưng KHÔNG học mẫu (vì không chắc).
    zone_control.send_command = lambda win, command: None
    fresh = ZoneReader()
    result = switch_to_zone(window, 15, fresh, **kwargs)
    check(result.ok and not result.verified, "lenh khong tac dung: coi nhu da o khu")
    check(result.reason == "unchanged", "lenh khong tac dung: ly do unchanged")
    check(fresh.sample_count() == 0, "lenh khong tac dung: khong hoc mau")

    # 6. Ô chat còn mở (lệnh chưa gửi được) -> thử lại.
    window2 = FakeWindow(zone=2)
    install_fakes(window2)
    state = {"n": 0}

    def chat_stuck(win, command: str):
        state["n"] += 1
        if state["n"] == 1:
            win.chat_open = True
            return
        win.chat_open = False
        win.zone = int(command.split()[-1])

    zone_control.send_command = chat_stuck
    reader2 = ZoneReader()
    result = switch_to_zone(window2, 9, reader2, **kwargs)
    check(result.ok and result.verified, "chat ket: thu lai thanh cong")
    check(reader2.read(window2) == 9, "chat ket: hoc duoc khu 9")

    # 7. Bộ đọc phân biệt được các khu khác nhau.
    frames = {zone: ZoneReader().signature(_WindowWith(zone)) for zone in range(0, 26)}
    sigs = list(frames.values())
    distinct = True
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            if ZoneReader().similar(sigs[i], sigs[j]):
                distinct = False
    check(distinct, "doc khu: 26 khu cho ra 26 chu ky khac nhau")

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


class _WindowWith:
    """Cửa sổ giả tối giản chỉ để lấy chữ ký ảnh."""

    def __init__(self, zone: int):
        self.zone = zone

    def capture(self):
        return render(self.zone)


class _FrameWindow:
    """Cửa sổ giả trả về đúng một khung ảnh cho trước."""

    def __init__(self, frame):
        self.frame = frame

    def capture(self):
        return self.frame


class _KeyWindow:
    """Cửa sổ giả ghi lại các phím được bấm.

    Có thể truyền nhiều khung ảnh: mỗi lần `capture()` trả khung kế tiếp,
    khung cuối được lặp lại. Dùng để mô phỏng ô chat đang có chữ rồi trở
    về trống sau khi bấm BACKSPACE.
    """

    def __init__(self, *frames):
        self.frames = list(frames)
        self.index = 0
        self.keys: list[int] = []

    def capture(self):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame

    def key(self, vk, hold: float = 0.03):
        self.keys.append(vk)

    def key_combo(self, modifier, vk):
        self.keys.append(modifier)
        self.keys.append(vk)


if __name__ == "__main__":
    raise SystemExit(main())
