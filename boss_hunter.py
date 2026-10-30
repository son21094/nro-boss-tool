"""
Tool săn boss tự động cho game NroChibi.

Luồng làm việc:
  1. Mỗi acc phụ trách một dải khu, dò khu bằng lệnh chat `k X`.
  2. Khi khu đó có boss, game hiện dòng chữ đỏ to đùng ở giữa màn hình
     (ví dụ "Thỏ đại ca [467/500]"). Tool nhận ra dòng chữ đó.
  3. Acc tìm thấy báo khu đó cho các acc khác, mọi acc chuyển sang khu đó.
  4. Tất cả liên tục ấn ENTER đánh boss cho tới khi dòng chữ đỏ biến mất.

Tool chỉ đọc pixel trên màn hình và gửi input như người dùng thật.
Không đọc bộ nhớ tiến trình, không can thiệp gói tin mạng của game.

Phím điều khiển:
  F9  : bật/tắt tool
  F11 : tạm dừng / tiếp tục
  F12 : dừng khẩn cấp
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import cv2

from config import CONFIG_PATH, Config
from detect import BossDetector, boss_bar_state as detect_boss_bar_state, is_fighting_boss
from game_window import VK, GameWindow, count_focus_steals, reset_focus_steals, set_post_keys
from zone_control import close_panel, is_chat_open, switch_to_zone
from zone_reader import ZoneReader

LOGGER = logging.getLogger("nro_boss")

# Giữ handle của mutex trong suốt thời gian chạy để Windows không giải phóng.
_SINGLETON_HANDLE = None


def acquire_single_instance(name: str = "NroBossTool_Singleton") -> bool:
    """Đảm bảo chỉ có một tool chạy tại một thời điểm.

    Hai instance cùng chạy sẽ giành focus và gõ phím vào cùng ô chat của
    game, tạo ra lệnh rác kiểu "ak 15" và làm cả hai cùng sai.
    """
    global _SINGLETON_HANDLE
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _SINGLETON_HANDLE = handle
    return True


def setup_logging(config: Config, name: str = "nro_boss") -> logging.Logger:
    """Cấu hình ghi log ra file và (tuỳ chọn) ra màn hình."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")

    file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if config.verbose:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


def is_key_pressed(vk: int) -> bool:
    """Kiểm tra phím đang được giữ, không cần cửa sổ game có focus."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Controller:
    """Trạng thái bật/tắt dùng chung cho toàn bộ acc.

    `enabled` quyết định tool có gửi input vào game hay không. Khi tắt, các
    acc vẫn chạy nhưng chỉ nằm chờ, không gõ phím nào vào game.
    """

    def __init__(self) -> None:
        self.enabled = threading.Event()
        self.stop = threading.Event()
        self.paused = False
        self._lock = threading.Lock()

        # Đang ở chế độ ĐỒNG BỘ (nhân bản thao tác): tool không được gõ
        # phím nào vào game, nhường toàn bộ input cho người dùng.
        self.input_suspended = False

        # Đặt lại toàn bộ: các acc đang đánh boss sẽ quay về dò khu.
        # Panel dùng khi bấm nút "BOSS DA CHET".
        #
        # Dùng số đếm (`reset_epoch`) chứ không dùng cờ bật/tắt: nhiều acc
        # cùng đọc một lúc, nếu ai cũng xoá cờ thì acc đọc sau sẽ không
        # biết là đã có yêu cầu reset.
        self.reset = threading.Event()
        self.reset_epoch = 0

    def request_reset(self) -> None:
        """Yêu cầu mọi acc quay lại dò khu ngay."""
        with self._lock:
            self.reset_epoch += 1
        self.reset.set()

    def set_enabled(self, on: bool) -> bool:
        """Bật hoặc tắt tool, trả về trạng thái mới."""
        with self._lock:
            if on:
                self.enabled.set()
            else:
                self.enabled.clear()
            return self.enabled.is_set()

    def toggle(self) -> bool:
        """Đảo trạng thái bật/tắt, trả về trạng thái mới."""
        with self._lock:
            if self.enabled.is_set():
                self.enabled.clear()
            else:
                self.enabled.set()
            return self.enabled.is_set()

    def is_enabled(self) -> bool:
        return self.enabled.is_set()

    def request_stop(self) -> None:
        """Yêu cầu dừng toàn bộ tool."""
        self.stop.set()
        self.enabled.clear()


class SharedState:
    """Trạng thái chung cho tất cả acc.

    Giữ khu đang có boss, để các acc đồng bộ sang khu đó.
    """

    def __init__(self, expire_after: float = 15.0):
        self.lock = threading.Lock()
        self.boss_zone: int | None = None
        self.last_seen: float = 0.0
        self.expire_after = expire_after
        self.log = LOGGER

        # Khu vừa được tuyên bố "boss đã chết". Trong vài giây sau đó,
        # không cho acc nào đó hồi sinh lại trạng thái này chỉ vì nó còn
        # thấy thanh máu sót lại trên màn hình (thanh máu boss chết thường
        # đứng thêm một lúc mới biến mất).
        self.resurrect_guard = 5.0
        self._dead_zone: int | None = None
        self._dead_at: float = 0.0

        # Mỗi lần boss chết / người dùng reset, số này tăng lên. Các acc
        # thấy số mới thì đưa con trỏ dò khu về khu đầu tiên của mình.
        # Dùng số đếm thay vì cờ để nhiều acc cùng đọc không xoá mất tín hiệu.
        self._scan_epoch = 0

    def scan_epoch(self) -> int:
        """Số hiệu lần reset dò khu gần nhất."""
        with self.lock:
            return self._scan_epoch

    def report(self, zone: int) -> None:
        """Ghi nhận: đang thấy boss ở khu `zone`.

        Khi đã có boss ở khu khác thì giữ nguyên khu cũ. Lý do: các acc
        đang ở khu khác nhau có thể cùng nhìn thấy boss ở những khu khác
        nhau (ví dụ boss vừa chết ở khu 0 nhưng thanh máu còn sót lại).
        Nếu ai cũng ghi đè thì các acc liên tục đổi khu loạn xạ.
        """
        with self.lock:
            now = time.time()
            if (
                self.boss_zone is None
                and self._dead_zone == zone
                and now - self._dead_at < self.resurrect_guard
            ):
                # Vừa tuyên bố boss khu này chết, chưa cho hồi sinh ngay.
                return
            if self.boss_zone is None:
                self.log.info(">>> Tim thay boss o khu %d!", zone)
                self.boss_zone = zone
            elif self.boss_zone != zone:
                # Có acc khác đã tìm thấy boss trước, giữ khu đó.
                return
            self.last_seen = time.time()

    def hold(self, zone: int) -> None:
        """Gia hạn trạng thái boss trong lúc chờ phase mới.

        Khác với `report`: hàm này không đánh thức boss đã hết hạn và
        không đổi khu. Nó chỉ cập nhật `last_seen` nếu boss vẫn đang ở
        đúng khu, để khoảng chờ phase không bị hiểu nhầm là boss biến mất.
        """
        with self.lock:
            if self.boss_zone == zone:
                self.last_seen = time.time()

    def get(self) -> int | None:
        """Lấy khu đang có boss.

        Nếu đã lâu không ai thấy boss nữa thì coi như boss đã chết và trả
        về None để các acc quay lại dò khu.
        """
        with self.lock:
            if self.boss_zone is None:
                return None
            if time.time() - self.last_seen > self.expire_after:
                self.log.info(">>> Boss da bien mat, quay lai do khu.")
                self.boss_zone = None
                self.last_seen = 0.0
                self._scan_epoch += 1
                return None
            return self.boss_zone

    def current(self) -> int | None:
        """Lấy khu boss hiện tại mà không tự hết hạn."""
        with self.lock:
            return self.boss_zone

    def clear(
        self, reason: str = "nguoi dung yeu cau", bump_epoch: bool = True
    ) -> None:
        """Xoá trạng thái boss, buộc mọi acc quay lại dò khu.

        Dùng cho nút "BOSS DA CHET": khi bạn thấy boss đã chết mà tool
        vẫn tưởng còn sống, bấm nút là cả đội reset ngay.

        `bump_epoch=False` dùng khi chính acc xử lý một yêu cầu reset đã
        được panel phát tín hiệu, để không tăng số hiệu reset lần nữa.
        """
        with self.lock:
            if self.boss_zone is not None:
                self.log.info(
                    ">>> Xoa trang thai boss o khu %d (%s).", self.boss_zone, reason
                )
                self._dead_zone = self.boss_zone
                self._dead_at = time.time()
            self.boss_zone = None
            self.last_seen = 0.0
            if bump_epoch:
                self._scan_epoch += 1

    def mark_dead(self, zone: int, force: bool = False) -> None:
        """Ghi nhận: boss ở khu `zone` đã chết, quay lại dò khu.

        Với `force=True` (do acc đang đánh gọi khi nó thấy thanh máu biến
        mất liên tục nhiều khung hình) thì xoá ngay, vì chính acc đó là
        nguồn tin đáng tin nhất.

        Không `force` thì chỉ xoá khi đã lâu không ai thấy boss nữa, tránh
        việc một acc nhìn thấy thanh máu biến mất trong khoảnh khắc làm cả
        đội bỏ khu đang có boss.
        """
        with self.lock:
            if self.boss_zone != zone:
                return
            if not force and time.time() - self.last_seen < self.expire_after:
                # Vừa mới có ai đó còn thấy boss, chưa chắc đã chết.
                return
            self.log.info(">>> Boss o khu %d da chet, quay lai do khu.", zone)
            self._dead_zone = zone
            self._dead_at = time.time()
            self.boss_zone = None
            self.last_seen = 0.0
            self._scan_epoch += 1


class ZonePlan:
    """Dải khu dùng chung cho tất cả acc, tự thu hẹp theo map thật.

    Một số map của game chỉ có ít khu hơn dải tối đa `--zones`. Khi gửi
    `k X` mà chỉ số khu không đổi, rất có thể X không tồn tại trên map.
    Tuy nhiên lệnh cũng có thể bị game nuốt tạm thời, nên chỉ kết luận sau
    `missing_confirmations` lần thất bại ở các vòng dò khác nhau. Lúc đó
    dải khu được thu về `start..X-1`, chia lại cho các acc và mọi acc dò
    lại từ khu đầu tiên.
    """

    def __init__(
        self,
        start: int,
        end: int,
        account_count: int,
        missing_confirmations: int = 2,
    ):
        if start > end:
            start, end = end, start
        self.start = start
        self.configured_end = end
        self.account_count = max(1, account_count)
        self.missing_confirmations = max(1, int(missing_confirmations))
        self._end = end
        # Khu cao nhất đã thực sự đổi tới được. Không bao giờ thu hẹp dải
        # xuống dưới khu này, kể cả khi một lần đổi khu bị lỗi tạm thời.
        self._highest_valid = start - 1
        self._failures: dict[int, int] = {}
        self._generation = 0
        self.lock = threading.Lock()
        self.log = LOGGER

    def generation(self) -> int:
        """Số hiệu lần thay đổi dải khu gần nhất."""
        with self.lock:
            return self._generation

    def end(self) -> int:
        """Khu cuối cùng thật sự dùng để chia việc."""
        with self.lock:
            return self._end

    def zones_for(self, index: int) -> list[int]:
        """Danh sách khu của acc `index`, chia vòng tròn trong dải hiện tại."""
        with self.lock:
            end = self._end
            count = self.account_count
        total = end - self.start + 1
        if total <= 0 or count <= 0:
            return []
        return [
            self.start + offset
            for offset in range(total)
            if offset % count == index
        ]

    def note_valid(self, zone: int) -> None:
        """Ghi nhận một khu đã đổi tới được (không phải đoán)."""
        with self.lock:
            if zone < self.start or zone > self.configured_end:
                return
            if zone > self._highest_valid:
                self._highest_valid = zone
            # Khu này có thật, nên mọi nghi ngờ trước đó ở các khu thấp hơn
            # cũng không còn ý nghĩa.
            for suspect in [z for z in self._failures if z <= zone]:
                self._failures.pop(suspect, None)

    def note_failed_switch(
        self, zone: int, reason: str, account_index: int | None = None
    ) -> bool:
        """Ghi nhận một lần đổi khu thất bại. True nếu dải khu vừa thu hẹp.

        Chỉ tính lỗi `unchanged`: game không báo bị chặn, không đổi chữ.
        Lỗi `blocked` là game đang bắt chờ giữa hai lần đổi khu, không liên
        quan tới việc khu có tồn tại hay không.
        """
        if reason != "unchanged":
            return False

        with self.lock:
            if zone < self.start or zone > self._end:
                return False
            # Đã từng đổi tới khu này hoặc khu cao hơn: chắc chắn nó có
            # thật, đây chỉ là lỗi tạm thời.
            if zone <= self._highest_valid:
                return False

            attempts = self._failures.get(zone, 0) + 1
            self._failures[zone] = attempts
            if attempts < self.missing_confirmations:
                self.log.info(
                    "Acc %s: khu %d chua doi duoc (lan %d/%d), se thu lai "
                    "truoc khi gioi han dai khu.",
                    "?" if account_index is None else account_index,
                    zone,
                    attempts,
                    self.missing_confirmations,
                )
                return False

            new_end = max(
                self.start, self._highest_valid, min(self._end, zone - 1)
            )
            if new_end >= self._end:
                return False

            self._end = new_end
            # Giữ lại số lần lỗi của các khu vẫn còn trong dải mới: có thể
            # acc khác đã thử khu đó trước khi dải thu hẹp.
            self._failures = {
                suspect: attempts
                for suspect, attempts in self._failures.items()
                if suspect <= new_end
            }
            self._generation += 1
            self.log.warning(
                ">>> Map chi con khu %d-%d: khu %d khong ton tai. "
                "Chia lai khu va do lai tu dau.",
                self.start,
                new_end,
                zone,
            )
            return True

    def reset(self) -> bool:
        """Xoá giới hạn đã học (dùng khi đổi map). True nếu có thay đổi."""
        with self.lock:
            changed = (
                self._end != self.configured_end
                or self._highest_valid != self.start - 1
                or bool(self._failures)
            )
            self._end = self.configured_end
            self._highest_valid = self.start - 1
            self._failures.clear()
            if changed:
                self._generation += 1
                self.log.info(
                    ">>> Xoa gioi han khu da hoc, dai khu tro lai %d-%d.",
                    self.start,
                    self.configured_end,
                )
            return changed


class Account:
    """Một acc: một cửa sổ game, một dải khu."""

    def __init__(
        self,
        window: GameWindow,
        config: Config,
        zones: list[int],
        index: int,
        state: SharedState,
        controller: Controller,
        zone_reader: ZoneReader | None = None,
        zone_plan: ZonePlan | None = None,
    ):
        self.window = window
        self.config = config
        self.zones = zones
        self.index = index
        self.state = state
        self.controller = controller
        self.zone_reader = zone_reader or ZoneReader(config.zone_indicator)
        self.zone_plan = zone_plan
        self._plan_generation = (
            zone_plan.generation() if zone_plan is not None else 0
        )
        self._scanned_any = False
        self._was_suspended = controller.input_suspended
        self.paused = False
        self.running = False
        self.zone_index = 0
        self.current_zone = zones[0] if zones else 0
        self.last_switch = 0.0
        self.detector = BossDetector(
            config.boss_hp_min_ratio, config.detect_stable_frames
        )
        self.log = setup_logging(config, f"nro_boss_acc{index}")

        # Trạng thái hiển thị cho cửa sổ điều khiển.
        self.status = "CHO SAN"
        self.status_zone = -1
        self._seen_reset = controller.reset_epoch
        self._seen_scan_epoch = state.scan_epoch()

    # ------------------------------------------------------------------ #
    # Tiện ích
    # ------------------------------------------------------------------ #
    def set_status(self, status: str, zone: int = -1) -> None:
        """Cập nhật trạng thái hiển thị cho panel."""
        self.status = status
        self.status_zone = zone

    def attack_vks(self) -> list[int]:
        """Danh sách mã phím tấn công."""
        return [
            VK[name.upper()]
            for name in self.config.attack_keys
            if name.upper() in VK
        ]

    def boss_hp_visible(self) -> bool:
        """Kiểm tra thanh máu boss đang hiện không."""
        frame = self.window.capture()
        if frame.size == 0:
            return False
        return is_fighting_boss(frame, self.config.boss_hp_min_ratio)

    # ------------------------------------------------------------------ #
    # Hành động
    # ------------------------------------------------------------------ #
    def press_attack(self) -> None:
        """Bấm loạt phím tấn công, chỉ giành focus cửa sổ game một lần."""
        vks = self.attack_vks()
        if not vks:
            return
        self.window.press_burst(
            vks, self.config.attack_burst, self.config.attack_interval
        )

    def _refresh_zones(self) -> bool:
        """Cập nhật dải khu nếu dùng chung vừa phát hiện map ngắn hơn.

        Trả về True nếu dải khu vừa thay đổi. Khi đó con trỏ dò khu được
        đưa về đầu dải mới để acc không bỏ sót các khu vừa được chia lại.
        """
        if self.zone_plan is None:
            return False
        generation = self.zone_plan.generation()
        if generation == self._plan_generation:
            return False

        self._plan_generation = generation
        self.zones = self.zone_plan.zones_for(self.index)
        self.zone_index = 0
        self._scanned_any = False
        self.detector.reset()
        if self.zones:
            self.log.info(
                "Acc %d: dai khu moi %d-%d (%d khu), do lai tu khu %d.",
                self.index,
                self.zones[0],
                self.zones[-1],
                len(self.zones),
                self.zones[0],
            )
            self.set_status("DO LAI TU DAU", self.zones[0])
        else:
            self.log.warning(
                "Acc %d: map chi con it khu hon so acc, acc nay khong co khu.",
                self.index,
            )
            self.set_status("KHONG CO KHU")
        return True

    def _rewind_scan(self) -> None:
        """Đưa con trỏ dò khu về khu đầu tiên của acc này."""
        self._refresh_zones()
        self.zone_index = 0
        self._scanned_any = False
        self.detector.reset()
        self._seen_scan_epoch = self.state.scan_epoch()
        first = self.zones[0] if self.zones else -1
        self.set_status("DA RESET", first)
        self.log.info("Acc %d: do lai tu khu %d.", self.index, first)

    def _apply_scan_reset(self) -> bool:
        """Áp dụng tín hiệu reset dò khu do boss chết / người dùng."""
        if self.state.scan_epoch() == self._seen_scan_epoch:
            return False
        self._rewind_scan()
        return True

    def _reset_pending(self) -> bool:
        """Có yêu cầu reset chưa xử lý không (nút "BOSS DA CHET")."""
        if self.controller.reset_epoch != self._seen_reset:
            self._seen_reset = self.controller.reset_epoch
            self._rewind_scan()
            return True
        return False

    def _sleep(self, seconds: float, check_enabled: bool = False) -> bool:
        """Ngủ `seconds` giây nhưng ngắt được khi tool bị dừng/tắt/reset.

        Trả về True nếu bị ngắt.
        """
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if (
                self.controller.stop.is_set()
                or self.controller.reset_epoch != self._seen_reset
                or self.state.scan_epoch() != self._seen_scan_epoch
            ):
                return True
            if check_enabled and not self.controller.is_enabled():
                return True
            if self.controller.input_suspended:
                return True
            time.sleep(min(0.2, max(0.0, deadline - time.time())))
        return False

    def _should_interrupt(self) -> bool:
        """Có nên bỏ dở thao tác đang làm không."""
        return (
            self.controller.stop.is_set()
            or self.controller.reset_epoch != self._seen_reset
            or self.state.scan_epoch() != self._seen_scan_epoch
            or not self.controller.is_enabled()
            or self.controller.input_suspended
        )

    def move_to_zone(self, zone: int) -> bool:
        """Chuyển sang khu `zone` và xác nhận bằng chỉ số khu thật.

        Game giới hạn tần suất chuyển khu, nên cần chờ đủ lâu giữa hai
        lần chuyển, nếu không sẽ bị chặn bằng hộp thoại.

        Trả về True nếu đã tới khu (hoặc nghi đã tới). Trả về False nếu bị
        game chặn — lúc đó `current_zone` được cập nhật theo khu THẬT đọc
        được, không phải khu vừa yêu cầu.
        """
        wait = self.config.zone_switch_cooldown - (time.time() - self.last_switch)
        if wait > 0:
            self.set_status(f"CHO DOI KHU ({wait:.0f}s)", zone)
            if self._sleep(wait, check_enabled=True):
                return False

        self.set_status("DANG DOI KHU", zone)
        result = switch_to_zone(
            self.window,
            zone,
            self.zone_reader,
            timeout=self.config.zone_switch_timeout,
            retries=self.config.zone_switch_retries,
            confirm_interval=self.config.zone_confirm_interval,
            should_stop=self._should_interrupt,
            on_status=lambda text: self.set_status(text, zone),
        )
        self.last_switch = time.time()
        self.detector.reset()

        if result.ok:
            real = result.zone if result.zone is not None else zone
            self.current_zone = real
            self.status_zone = real
            if result.verified:
                if self.zone_plan is not None:
                    self.zone_plan.note_valid(real)
                self.log.info(
                    "Acc %d: da o khu %d (%s).", self.index, real, result.reason
                )
            else:
                self.log.info(
                    "Acc %d: gui lenh khu %d, chua xac nhan duoc (%s).",
                    self.index,
                    real,
                    result.reason,
                )
            return True

        # Bị chặn: giữ đúng khu thật, KHÔNG ghi đè bằng khu vừa yêu cầu.
        self.current_zone = result.zone if result.zone is not None else self.current_zone
        self.status_zone = self.current_zone
        self.set_status("KHONG DOI DUOC KHU", self.current_zone)
        self.log.warning(
            "Acc %d: KHONG DOI DUOC khu %d (%s), dang o khu %d.",
            self.index,
            zone,
            result.reason,
            self.current_zone,
        )
        if self.zone_plan is not None and self.zone_plan.note_failed_switch(
            zone, result.reason, self.index
        ):
            self.log.warning(
                "Acc %d: khu %d khong ton tai, dai khu da duoc thu hep.",
                self.index,
                zone,
            )
            self._refresh_zones()
        return False

    def next_zone(self) -> int:
        """Lấy khu tiếp theo trong dải, quay vòng về khu đầu khi hết."""
        self._refresh_zones()
        if not self.zones:
            return -1
        if self.zone_index >= len(self.zones):
            self.zone_index = 0
            if self._scanned_any:
                self.log.info(
                    "Acc %d: da het vong khu, quay lai khu %d.",
                    self.index,
                    self.zones[0],
                )
            self._scanned_any = False
        zone = self.zones[self.zone_index]
        self.zone_index += 1
        self._scanned_any = True
        return zone

    # ------------------------------------------------------------------ #
    # Vòng lặp
    # ------------------------------------------------------------------ #
    def check_hotkeys(self) -> bool:
        """Kiểm tra trạng thái dừng. Trả về False nếu cần dừng hẳn.

        Phím tạm dừng được xử lý ở thread theo dõi phím, nên ở đây chỉ
        cần kiểm tra cờ dừng. Nếu kiểm tra phím ở cả hai nơi thì sẽ đảo
        trạng thái hai lần và tạo cảm giác phím bị bấm kép.
        """
        if self.controller.stop.is_set():
            return False
        if self.controller.paused:
            self.set_status("TAM DUNG")
            time.sleep(0.2)
        return True

    def scan(self) -> bool:
        """Dò một khu. Trả về True nếu thấy boss.

        Cách dò: vào khu, spam ENTER để đánh. Nếu khu đó có boss thì game
        hiện thanh máu đỏ ở giữa màn hình, ta nhận ra và báo cho cả đội.

        Cần cẩn thận: khi rời khu cũ, thanh máu boss của khu cũ vẫn có
        thể còn trên màn hình trong một khoảnh khắc. Vì vậy ta chờ cho nó
        biến mất hẳn rồi mới kiểm tra khu mới.
        """
        if self._should_interrupt():
            return False

        zone = self.next_zone()
        if zone < 0:
            self.set_status("KHONG CO KHU")
            self._sleep(1.0, check_enabled=True)
            return False
        self.set_status("DANG DOI KHU", zone)
        if not self.move_to_zone(zone):
            # Bị chặn hoặc bị ngắt: thử khu khác ở vòng sau.
            return False

        # Acc khác vừa tìm thấy boss: bỏ dò ngay, để vòng chính đi đánh.
        if self.state.current() is not None:
            return False

        # Chờ cho thanh máu của khu cũ (nếu có) biến mất trước.
        if self.boss_hp_visible():
            self.set_status("CHO KHU CU BIEN MAT", zone)
            gone_deadline = time.time() + self.config.zone_dwell
            while time.time() < gone_deadline:
                if self._should_interrupt() or self.state.current() is not None:
                    return False
                if not self.boss_hp_visible():
                    break
                time.sleep(0.25)

        # Giờ mới thật sự kiểm tra khu mới: đánh thử và xem có boss không.
        # Dùng bộ phát hiện nhiều khung hình liên tiếp để tránh nhầm với
        # số sát thương bay lên khi đánh.
        self.set_status("DANG DO KHU", zone)
        deadline = (
            time.time()
            + self.config.zone_dwell
            + self.config.detect_stable_frames * self.config.detect_stable_interval
            + 1.0
        )
        while time.time() < deadline:
            # Cắt ngang ngay khi acc khác tìm thấy boss.
            if self._should_interrupt() or self.state.current() is not None:
                return False
            frame = self.window.capture()
            if frame.size and self.detector.observe(frame):
                self.log.info("Acc %d: khu %d CO BOSS!", self.index, zone)
                self.current_zone = zone
                return True
            self.press_attack()
            time.sleep(self.config.detect_stable_interval)

        return False

    def boss_bar_state(self, frame) -> str:
        """Trạng thái thanh máu boss trong ảnh.

        Trả về "none", "zero" hoặc "alive" (xem `detect.boss_bar_state`).
        """
        if frame is None or frame.size == 0:
            return "none"
        return detect_boss_bar_state(frame, self.config.boss_hp_min_ratio)

    @staticmethod
    def frame_valid(frame) -> bool:
        """Kiểm tra ảnh chụp có dùng được không.

        Lỡ có lần chụp lỗi (cửa sổ vừa đóng, hay chụp trả về ảnh trống)
        thì không được tính là "mất boss", kẻo cả đội bỏ nhầm khu đang
        có boss.
        """
        if frame is None or frame.size == 0:
            return False
        return float(frame.std()) > 2.0

    def fight(self) -> str:
        """Ở lại khu hiện tại và liên tục spam ENTER để đánh boss.

        Boss nhiều phase có thể biến mất một lúc ngắn sau khi hết phase
        trước. Tool chờ tối đa `boss_phase_gap` giây; nếu thanh máu hiện
        lại thì đánh tiếp, hết thời gian mới coi là boss đã chết.

        Trả về:
            "dead"  : boss đã chết hẳn, khu này xong
            "left"  : cả đội đã bỏ khu này
            "stop"  : tool bị dừng
        """
        zone = self.current_zone
        phase_gap = max(0.0, self.config.boss_phase_gap)
        missing_frames = 0
        zero_frames = 0
        saw_boss = False
        phase_deadline = 0.0
        while self.running:
            if self.controller.stop.is_set():
                return "stop"
            if self._reset_pending():
                # Người dùng bấm "BOSS DA CHET": bỏ khu này ngay.
                self.state.clear("boss da chet", bump_epoch=False)
                self.set_status("DA RESET", zone)
                self.log.info("Acc %d: reset theo yeu cau, quay lai do khu.", self.index)
                return "left"
            if self.controller.input_suspended:
                # Đang ở chế độ ĐỒNG BỘ: nhường input cho người dùng.
                self.set_status("DONG BO", zone)
                time.sleep(0.2)
                continue
            if not self.controller.is_enabled():
                self.set_status("TAM DUNG", zone)
                time.sleep(0.2)
                continue

            # Cả đội đã bỏ khu này rồi thì thôi đánh, quay lại dò khu.
            if self.state.get() != zone:
                self.set_status("DA BO KHU", zone)
                return "left"

            frame = self.window.capture()
            if not self.frame_valid(frame):
                # Ảnh chụp lỗi, không tính vào việc mất boss.
                time.sleep(0.3)
                continue

            state = self.boss_bar_state(frame)
            if state == "alive":
                if phase_deadline:
                    self.log.info(
                        "Acc %d: boss khu %d hien lai (phase moi), tiep tuc danh.",
                        self.index,
                        zone,
                    )
                phase_deadline = 0.0
                missing_frames = 0
                zero_frames = 0
                saw_boss = True
                # Báo cho các acc khác biết boss còn sống ở khu này.
                # Chỉ gọi report khi THẤY thanh máu, để last_seen phản ánh
                # đúng lần cuối còn thấy boss. Nếu gọi vô điều kiện thì
                # trạng thái chung không bao giờ hết hạn và các acc kẹt
                # mãi ở chế độ đánh boss.
                self.state.report(zone)
                self.set_status("DANG DANH BOSS", zone)
            elif phase_deadline:
                # Đang chờ phase mới: giữ trạng thái chung sống để acc
                # khác không reset, đồng thời vẫn đánh tiếp.
                self.state.hold(zone)
                self.set_status("CHO PHASE MOI", zone)
                if time.time() >= phase_deadline:
                    self.state.mark_dead(zone, force=True)
                    self.set_status("BOSS DA CHET", zone)
                    self.log.info(
                        "Acc %d: het %.1fs cho phase moi, coi nhu boss khu %d da chet.",
                        self.index,
                        phase_gap,
                        zone,
                    )
                    return "dead"
            elif state == "zero":
                # Thanh máu còn hiện nhưng máu đã về 0. Có thể boss vừa
                # chết, cũng có thể sắp hiện phase tiếp theo.
                missing_frames = 0
                zero_frames += 1
                if zero_frames >= self.config.boss_dead_frames:
                    if saw_boss:
                        self.state.hold(zone)
                        phase_deadline = time.time() + phase_gap
                        self.set_status("CHO PHASE MOI", zone)
                        self.log.info(
                            "Acc %d: thanh mau boss khu %d ve 0, cho phase moi toi da %.1fs.",
                            self.index,
                            zone,
                            phase_gap,
                        )
                    # Chưa từng thấy boss sống ở khu này nên chưa dám
                    # kết luận: có thể chỉ là đọc sai dòng chữ. Cứ đánh
                    # tiếp, khi nào cả đội hết hạn thì tự quay lại dò khu.
                    else:
                        zero_frames = 0
            else:
                # Không thấy thanh máu nữa. Chờ thêm vài khung hình rồi
                # mới xét, vì lúc mới tới nơi chữ đỏ có thể chưa kịp vẽ ra.
                zero_frames = 0
                missing_frames += 1
                if missing_frames >= self.config.boss_dead_frames:
                    if saw_boss:
                        # Chính acc này đã nhìn thấy boss sống rồi, nên
                        # khi thanh máu biến mất thì có thể boss đã chết
                        # hoặc đang chuyển phase. Chờ thêm một khoảng.
                        self.state.hold(zone)
                        phase_deadline = time.time() + phase_gap
                        self.set_status("CHO PHASE MOI", zone)
                        self.log.info(
                            "Acc %d: mat thanh mau boss khu %d, cho phase moi toi da %.1fs.",
                            self.index,
                            zone,
                            phase_gap,
                        )
                    # Chưa từng thấy boss ở khu này (có thể tới trễ hoặc
                    # boss chết trước khi tới). Chỉ báo chết nếu lâu rồi
                    # không acc nào thấy boss.
                    else:
                        self.state.mark_dead(zone)
                        if self.state.get() != zone:
                            self.set_status("DA BO KHU", zone)
                            return "left"

            self.press_attack()
            time.sleep(0.2)

        return "stop"

    def _wait_for_boss_bar(self, zone: int, seconds: float) -> bool:
        """Đánh thử và chờ thanh máu boss hiện ra. True nếu thấy."""
        self.set_status("TIM THANH MAU BOSS", zone)
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self._should_interrupt():
                return False
            if self.state.get() != zone:
                return False
            frame = self.window.capture()
            if self.frame_valid(frame) and self.boss_bar_state(frame) != "none":
                return True
            self.press_attack()
            time.sleep(0.25)
        return False

    def follow_boss_zone(self, boss_zone: int) -> str:
        """Chuyển tới khu đang có boss, xác nhận rồi spam ENTER.

        Sau khi tới nơi, phải thấy thanh máu boss thật thì mới yên tâm.
        Nếu không thấy (lệnh đổi khu bị chặn, hoặc acc đứng nhầm khu) thì
        gửi lại lệnh `k X` — tối đa vài lần — thay vì đánh vào chỗ trống.
        """
        attempts = 0
        while True:
            if self._should_interrupt():
                return "left"
            if self.state.get() != boss_zone:
                self.set_status("DA BO KHU", boss_zone)
                return "left"
            if self._reset_pending():
                self.state.clear("boss da chet", bump_epoch=False)
                return "left"

            if self.current_zone != boss_zone:
                self.set_status("CHUYEN DEN KHU BOSS", boss_zone)
                if not self.move_to_zone(boss_zone):
                    # Không đổi được khu: nhường vòng khác xử lý.
                    return "left"
                if self.current_zone != boss_zone:
                    return "left"
                self.set_status("DA TOI KHU BOSS", boss_zone)

            if self._wait_for_boss_bar(boss_zone, self.config.boss_arrive_confirm):
                return self.fight()

            if self.state.get() != boss_zone:
                return "left"
            attempts += 1
            self.log.info(
                "Acc %d: khong thay thanh mau o khu %d (lan %d), gui lai lenh.",
                self.index,
                boss_zone,
                attempts,
            )
            if attempts >= 3:
                # Đã thử lại nhiều lần: cứ đánh tại chỗ, boss có thể đang
                # ở ngoài tầm nhìn của acc này.
                return self.fight()
            # Đánh dấu là chưa ở khu này để vòng sau gửi lại lệnh (vẫn
            # phải chờ đủ thời gian chống chặn của game).
            self.current_zone = -1

    def _on_manual_sync_finished(self) -> None:
        """DONG BO vừa tắt: có thể người dùng đã đổi sang map khác.

        Xoá giới hạn khu đã học để lần dò sau tự nhận lại số khu của map
        mới, rồi đưa acc về khu đầu tiên.
        """
        if self.zone_plan is not None:
            self.zone_plan.reset()
        self._rewind_scan()
        self.log.info(
            "Acc %d: ket thuc DONG BO, do lai tu khu dau.", self.index
        )

    def run(self) -> None:
        """Vòng lặp chính của acc."""
        self._refresh_zones()
        if self.zones:
            self.log.info(
                "Acc %d: phu trach %d khu (%s).",
                self.index,
                len(self.zones),
                f"{self.zones[0]}-{self.zones[-1]}",
            )
        else:
            self.log.warning(
                "Acc %d: khong co khu nao (map it khu hon so acc).",
                self.index,
            )
        self.running = True

        while self.running and not self.controller.stop.is_set():
            if not self.check_hotkeys():
                break

            # DONG BO kết thúc: có thể map vừa được đổi, nên xoá giới hạn
            # khu đã học và dò lại từ đầu.
            suspended = self.controller.input_suspended
            if self._was_suspended and not suspended:
                self._on_manual_sync_finished()
            self._was_suspended = suspended

            # Người dùng bấm "BOSS DA CHET": quay lại khu đầu tiên ngay,
            # kể cả khi tool đang tạm dừng, tắt hoặc ở chế độ đồng bộ.
            if self._reset_pending():
                self.state.clear("boss da chet", bump_epoch=False)
                time.sleep(0.2)
                continue

            # Boss vừa chết hoặc hết hạn: dò lại từ khu đầu tiên của acc,
            # không dò tiếp từ khu đang dở.
            self._apply_scan_reset()

            if self.paused:
                self.set_status("TAM DUNG")
                time.sleep(0.2)
                continue

            # Đang ở chế độ ĐỒNG BỘ: không gõ gì vào game.
            if self.controller.input_suspended:
                self.set_status("DONG BO")
                time.sleep(0.2)
                continue

            # Tắt tool -> acc nằm chờ, không gõ phím nào vào game.
            if not self.controller.is_enabled():
                self.set_status("DA TAT")
                time.sleep(0.2)
                continue

            if self.window.is_minimized():
                self.set_status("CUA SO THU NHAT")
                time.sleep(0.5)
                continue

            # Đã biết boss ở đâu: đi tới khu đó nếu chưa ở đó.
            boss_zone = self.state.get()
            if boss_zone is not None:
                result = self.follow_boss_zone(boss_zone)
                if result == "left":
                    # Khu đã xong, chuyển sang vòng dò khu mới.
                    time.sleep(self.config.zone_step_delay)
                continue

            # Chưa biết boss ở đâu: dò khu.
            if self.scan():
                # Báo khu này có boss cho cả đội, rồi ở lại đánh.
                self.state.report(self.current_zone)
                self.fight()
            elif self.state.current() is None:
                # Chỉ nghỉ khi thật sự không có boss. Nếu vừa cắt ngang
                # vì acc khác tìm thấy boss thì vào đánh luôn.
                time.sleep(self.config.zone_step_delay)

        self.log.info("Acc %d: ket thuc.", self.index)


def split_zones(total: int, account_count: int) -> list[list[int]]:
    """Chia danh sách khu cho các acc, chia deu theo vong tron.

    Ví dụ 26 khu cho 3 acc:
        acc 0: 0, 3, 6, 9,...
        acc 1: 1, 4, 7, 10,...
        acc 2: 2, 5, 8, 11,...
    Cách chia vong tron giup moi acc luon co viec de lam, khong acc nao
    phai ngoi cho.

    Nếu số khu ít hơn số acc, chỉ những acc thực sự có khu mới được
    trả về, tránh rỗng.
    """
    if account_count <= 0 or total <= 0:
        return []
    plans = [
        [zone for zone in range(total) if zone % account_count == index]
        for index in range(account_count)
    ]
    return [zones for zones in plans if zones]


def start_hotkey_watcher(controller: Controller, config: Config) -> threading.Thread:
    """Theo dõi phím bật/tắt và dừng khẩn cấp ở nền.

    Dùng `RegisterHotKey` để Windows gửi thông báo phím riêng cho tiến
    trình này. Cách này nhận phím ổn định kể cả khi giữ phím (không bị
    bật/tắt liên tục như cách dò trạng thái phím), và hoạt động kể cả
    khi cửa sổ game đang có focus.

    Lưu ý: phải đăng ký phím nằm trong ĐÚNG thread lắng nghe message,
    vì Windows gửi WM_HOTKEY vào hàng đợi của thread đã đăng ký.
    Phím nào đăng ký không được (app khác đã chiếm) sẽ được theo dõi
    bằng cách dò trạng thái phím làm dự phòng.
    """

    def watch() -> None:
        user32 = ctypes.windll.user32
        WM_HOTKEY = 0x0312

        HOTKEY_ID_TOGGLE = 0xB051
        HOTKEY_ID_EMERGENCY = 0xB052
        HOTKEY_ID_PAUSE = 0xB053

        toggle_vk = VK.get(config.toggle_key.upper(), VK["F9"])
        emergency_vk = VK.get(config.emergency_key.upper(), VK["F12"])
        pause_vk = VK.get(config.pause_key.upper(), VK["F11"])

        # MOD_NOREPEAT: mỗi lần nhấn chỉ báo một lần.
        MOD_NOREPEAT = 0x4000

        registered = []
        for hotkey_id, vk in (
            (HOTKEY_ID_TOGGLE, toggle_vk),
            (HOTKEY_ID_EMERGENCY, emergency_vk),
            (HOTKEY_ID_PAUSE, pause_vk),
        ):
            if user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, vk):
                registered.append((hotkey_id, vk))
            else:
                LOGGER.warning(
                    "Khong dang ky duoc phim %s (app khieu dung), dung cach do trang thai.",
                    vk,
                )

        # Phím nào đăng ký thành công thì do WM_HOTKEY xử lý.
        # Phím nào không được thì dò trạng thái phím làm dự phòng.
        # Tuyệt đối không xử lý cả hai cho cùng một phím, nếu không sẽ
        # bật/tắt hai lần và thành tắt luôn.
        handled_by_hotkey = {vk for _, vk in registered}
        fallback_keys = [
            vk
            for vk in (toggle_vk, emergency_vk, pause_vk)
            if vk not in handled_by_hotkey
        ]
        if fallback_keys:
            LOGGER.info(
                "Dung cach do trang thai cho phim: %s",
                ", ".join(str(vk) for vk in fallback_keys),
            )
        last_down: dict[int, bool] = {}

        message = wintypes.MSG()
        try:
            while not controller.stop.is_set():
                # Xử lý thông báo phím.
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == WM_HOTKEY:
                        if message.wParam == HOTKEY_ID_TOGGLE:
                            on = controller.toggle()
                            LOGGER.info(
                                "Nhan %s -> tool %s.",
                                config.toggle_key,
                                "BAT" if on else "TAT",
                            )
                        elif message.wParam == HOTKEY_ID_EMERGENCY:
                            LOGGER.info("Nhan %s -> dung tool.", config.emergency_key)
                            controller.request_stop()
                            return
                        elif message.wParam == HOTKEY_ID_PAUSE:
                            controller.paused = not controller.paused
                            LOGGER.info(
                                "Nhan %s -> %s.",
                                config.pause_key,
                                "tam dung" if controller.paused else "tiep tuc",
                            )

                # Dự phòng cho phím không đăng ký được.
                for vk in fallback_keys:
                    down = is_key_pressed(vk)
                    if down and not last_down.get(vk, False):
                        if vk == toggle_vk:
                            on = controller.toggle()
                            LOGGER.info(
                                "Nhan %s -> tool %s.",
                                config.toggle_key,
                                "BAT" if on else "TAT",
                            )
                        elif vk == emergency_vk:
                            LOGGER.info("Nhan %s -> dung tool.", config.emergency_key)
                            controller.request_stop()
                            return
                        elif vk == pause_vk:
                            controller.paused = not controller.paused
                            LOGGER.info(
                                "Nhan %s -> %s.",
                                config.pause_key,
                                "tam dung" if controller.paused else "tiep tuc",
                            )
                    last_down[vk] = down

                time.sleep(0.05)
        finally:
            for hotkey_id, _ in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def build_accounts(
    windows: list[GameWindow],
    config: Config,
    state: SharedState,
    controller: Controller,
    start: int,
    end: int,
    zone_reader: ZoneReader | None = None,
) -> list[Account]:
    """Chia khu và tạo acc cho từng cửa sổ game."""
    zone_plan = ZonePlan(
        start,
        end,
        len(windows),
        missing_confirmations=config.zone_missing_confirmations,
    )
    accounts: list[Account] = []
    for index, window in enumerate(windows):
        zones = zone_plan.zones_for(index)
        # Mỗi acc có file log riêng để dễ theo dõi khi chạy nhiều acc.
        acc_log = Path(config.log_file).with_name(
            f"{Path(config.log_file).stem}_acc{index}{Path(config.log_file).suffix}"
        )
        acc_config = Config.from_dict({**config.__dict__, "log_file": str(acc_log)})
        accounts.append(
            Account(
                window,
                acc_config,
                zones,
                index,
                state,
                controller,
                zone_reader=zone_reader,
                zone_plan=zone_plan,
            )
        )
    return accounts


def main() -> int:
    """Điểm vào của chương trình."""
    parser = argparse.ArgumentParser(
        description="Tool tu dong do khu va san boss cho game NroChibi."
    )
    parser.add_argument(
        "--accounts",
        type=int,
        default=0,
        help="So acc can chay (0 = tat ca cua so game dang mo).",
    )
    parser.add_argument(
        "--zones",
        type=str,
        default="0-25",
        help="Dai khu toi da can chia, dinh dang 'dau-cuoi'. "
        "Tool tu thu hep neu map co it khu hon.",
    )
    parser.add_argument(
        "--window-list",
        type=str,
        default="",
        help="Danh sach chi so cua so game dung cho tung acc, vi du '0,2,3,4,5'.",
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument(
        "--no-panel",
        action="store_true",
        help="Khong mo cua so dieu khien, chi chay qua terminal.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Khong dong bo giua cac acc, moi acc tu do khu rieng.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chi xem ke hoach chia khu roi thoat.",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="Chi liet ke cac cua so game dang mo roi thoat.",
    )
    parser.add_argument(
        "--focus-keys",
        action="store_true",
        help="Gui phim kieu cu (gianh focus). Day la mac dinh.",
    )
    parser.add_argument(
        "--post-keys",
        action="store_true",
        help="Thu gui phim bang PostMessage (khong gianh focus). Chi dung khi "
        "--test-keys bao game NHAN phim.",
    )
    parser.add_argument(
        "--dump-zone",
        action="store_true",
        help="Chup vung chi so khu cua cua so dau tien ra file de kiem tra roi thoat.",
    )
    parser.add_argument(
        "--test-keys",
        action="store_true",
        help="Kiem tra game co nhan phim gui bang PostMessage khong roi thoat.",
    )
    args = parser.parse_args()

    if args.list_windows:
        windows = GameWindow.list_all()
        if not windows:
            print("Khong tim thay cua so game.")
            return 1
        for index, win in enumerate(windows):
            left, top, width, height = win.client_rect()
            print(
                f"[{index}] hwnd={win.hwnd} "
                f"vi tri=({left},{top}) kich thuoc={width}x{height}"
            )
        return 0

    try:
        start, end = (int(part) for part in args.zones.split("-", 1))
    except ValueError:
        print("Sai dinh dang --zones, vi du: --zones 0-25")
        return 1
    if start > end:
        start, end = end, start

    config = Config.load(args.config)
    if args.no_sync:
        config.sync_enabled = False
    if args.post_keys:
        config.post_keys = True
    if args.focus_keys:
        config.post_keys = False
    # Áp dụng chế độ gửi phím trước khi bất kỳ acc nào bắt đầu.
    set_post_keys(config.post_keys)
    setup_logging(config)

    windows = [w for w in GameWindow.list_all() if w.client_rect()[2] > 0]
    if not windows:
        LOGGER.error("Khong tim thay cua so game. Hay mo game truoc.")
        return 1

    # Chẩn đoán: game có nhận phím gửi bằng PostMessage không?
    if args.test_keys:
        set_post_keys(True)
        print("Kiem tra: gui phim bang PostMessage (KHONG gianh focus)...")
        ok_count = 0
        for index, win in enumerate(windows):
            if is_chat_open(win):
                close_panel(win)
                time.sleep(0.6)
            win.key(VK["R"])
            time.sleep(0.7)
            opened = is_chat_open(win)
            # Đóng chat nếu đã mở, tránh để lại ô chat trên màn hình.
            win.key(VK["ESC"])
            time.sleep(0.3)
            print(
                f"  [{index}] hwnd={win.hwnd}: "
                f"{'NHAN phim (chat mo duoc)' if opened else 'KHONG nhan phim'}"
            )
            ok_count += 1 if opened else 0
        print()
        if windows and ok_count == len(windows):
            print("KET LUAN: game NHAN PostMessage. Cac cua so se dung yen.")
        elif ok_count:
            print(
                f"KET LUAN: chi {ok_count}/{len(windows)} cua so nhan PostMessage. "
                "Khong dung --post-keys; de mac dinh (gianh focus) cho chac chan."
            )
        else:
            print(
                "KET LUAN: game KHONG nhan PostMessage. "
                "Tool mac dinh da dung kieu gianh focus, cu chay binh thuong."
            )
        return 0

    # Chẩn đoán vùng chỉ số khu: chụp ra file để xem có cắt đúng không.
    if args.dump_zone:
        reader = ZoneReader(config.zone_indicator)
        for index, win in enumerate(windows):
            piece = reader.crop(win)
            if piece is None:
                print(f"[{index}] khong chup duoc.")
                continue
            out = Path(f"zone_crop_{index}.png")
            cv2.imwrite(str(out), piece)
            sig = reader.signature(win)
            print(
                f"[{index}] luu {out} ({piece.shape[1]}x{piece.shape[0]}) "
                f"digest={ZoneReader.digest(sig)}"
            )
        print(
            f"Vung dang dung: {config.zone_indicator} "
            f"({config.zone_indicator.to_pixels(*windows[0].client_rect()[2:])})"
        )
        return 0

    # Cho phép chon cua so cua tung acc.
    if args.window_list:
        try:
            picks = [int(part) for part in args.window_list.split(",") if part.strip()]
        except ValueError:
            print("Sai dinh dang --window-list, vi du: --window-list 0,1,2,3,4")
            return 1
        bad = [p for p in picks if p < 0 or p >= len(windows)]
        if bad:
            print(
                f"Chi so cua so khong hop le: {bad}. "
                f"Co {len(windows)} cua so game dang mo."
            )
            return 1
        windows = [windows[p] for p in picks]

    if args.accounts:
        windows = windows[: args.accounts]

    total_zones = end - start + 1
    if total_zones < len(windows):
        print(
            f"Dai khu {start}-{end} chi co {total_zones} khu, khong du cho "
            f"{len(windows)} acc. Giam so acc hoac tang dai khu."
        )
        return 1

    print(f"So acc: {len(windows)} | Dai khu: {start}-{end}")
    plans = split_zones(total_zones, len(windows))
    for index in range(len(windows)):
        zones = [start + z for z in plans[index]]
        span = f"{zones[0]}-{zones[-1]}" if len(zones) > 1 else str(zones[0])
        print(f"  acc {index}: {len(zones)} khu ({span})")

    if args.dry_run:
        return 0

    # Chặn chạy trùng: hai tool cùng gõ phím sẽ phá lệnh của nhau.
    if not acquire_single_instance():
        print(
            "Da co mot tool khac dang chay. "
            "Hay tat tool do (F12 hoac dong cua so dieu khien) roi chay lai."
        )
        return 1

    controller = Controller()
    state = SharedState(config.boss_gone_timeout)
    # Một bộ đọc khu dùng chung: acc nào học được khu nào thì acc khác
    # nhận ra ngay, không phải học lại.
    zone_reader = ZoneReader(config.zone_indicator)
    accounts = build_accounts(
        windows, config, state, controller, start, end, zone_reader=zone_reader
    )
    if not accounts:
        print("Khong co acc nao de chay.")
        return 1

    start_hotkey_watcher(controller, config)

    threads = []
    for account in accounts:
        thread = threading.Thread(target=account.run, daemon=True)
        thread.start()
        threads.append(thread)
        time.sleep(0.2)

    panel = None
    if not args.no_panel:
        try:
            from panel import ControlPanel

            panel = ControlPanel(controller, accounts, config)
            panel.start()
        except Exception as exc:  # noqa: BLE001 - panel là tu chọn
            LOGGER.warning("Khong mo duoc cua so dieu khien: %s", exc)

    print(
        f"\nTool da sanh sang. Nhan {config.toggle_key} de bat/tat, "
        f"{config.pause_key} de tam dung, {config.emergency_key} de dung han."
    )

    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nDang dung...")
    finally:
        controller.request_stop()
        if panel is not None:
            panel.close()
        time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
