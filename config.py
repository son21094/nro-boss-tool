"""
Cấu hình tool săn boss NroChibi.

Toạ độ vùng được khai báo theo phần trăm khung game nên tool vẫn chạy
đúng khi bạn đổi độ phân giải hoặc kéo cửa sổ.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")


@dataclass
class Rect:
    """Vùng hình chữ nhật tính theo phần trăm của cửa sổ game."""

    left: float
    top: float
    right: float
    bottom: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Đổi sang toạ độ pixel trong ảnh cửa sổ game (x, y, w, h)."""
        x1 = int(self.left / 100.0 * width)
        y1 = int(self.top / 100.0 * height)
        x2 = int(self.right / 100.0 * width)
        y2 = int(self.bottom / 100.0 * height)
        x1 = max(0, min(x1, max(0, width - 1)))
        y1 = max(0, min(y1, max(0, height - 1)))
        x2 = max(x1 + 1, min(x2, width))
        y2 = max(y1 + 1, min(y2, height))
        return x1, y1, x2 - x1, y2 - y1

    def center(self, width: int, height: int) -> tuple[int, int]:
        """Toạ độ pixel của tâm vùng."""
        x, y, w, h = self.to_pixels(width, height)
        return x + w // 2, y + h // 2


@dataclass
class Config:
    """Toàn bộ tham số của tool."""

    # ------------------------------------------------------------------ #
    # Phát hiện boss
    # ------------------------------------------------------------------ #
    boss_hp_min_ratio: float = 0.25
    """Tỉ lệ pixel đỏ tối thiểu trong vùng chữ thanh máu để coi là đang đánh boss."""

    detect_stable_frames: int = 2
    """Số ảnh liên tiếp phải thấy dòng chữ đỏ mới coi là có boss.

    Số sát thương bay lên rồi biến mất trong chưa tới 1 giây, còn thanh
    máu boss đứng yên. Yêu cầu nhiều frame liên tiếp loại được số sát
    thương mà không cần đọc chữ.
    """

    detect_stable_interval: float = 0.3
    """Giây nghỉ giữa hai lần chụp khi kiểm tra ổn định."""

    # ------------------------------------------------------------------ #
    # Dò khu
    # ------------------------------------------------------------------ #
    zone_indicator: Rect = field(
        default_factory=lambda: Rect(left=12.0, top=13.2, right=30.0, bottom=16.6)
    )
    """Vùng dòng chỉ số khu ở góc trên trái, dạng '154.Hành tỉnh Bill - K20'.

    Đây là nguồn thông tin duy nhất để biết acc đang đứng ở khu nào. Muốn
    kiểm tra vùng cắt có đúng không thì chạy `python boss_hunter.py
    --dump-zone` rồi xem file `zone_crop_0.png`.
    """

    zone_start: int = 0
    """Khu đầu tiên trong dải mà acc này phụ trách."""
    zone_end: int = 25
    """Khu cuối cùng trong dải mà acc này phụ trách (bao gồm)."""

    zone_dwell: float = 3.0
    """Giây chờ ở mỗi khu để nhân vật tới nơi và kiểm tra có boss không."""

    zone_step_delay: float = 1.5
    """Giây nghỉ giữa hai lần chuyển khu."""

    zone_switch_cooldown: float = 13.0
    """Số giây tối thiểu giữa hai lần chuyển khu. Game hiện hộp thoại
    'không thể đổi khu vực, vui lòng đợi 9 giây' nếu chuyển quá nhanh."""

    zone_switch_timeout: float = 7.0
    """Số giây tối đa chờ chỉ số khu hiện đúng sau khi gửi lệnh đổi khu."""

    zone_switch_retries: int = 3
    """Số lần gửi lại lệnh đổi khu nếu bị game chặn."""

    zone_confirm_interval: float = 0.25
    """Giây nghỉ giữa hai lần đọc chỉ số khu khi chờ xác nhận."""

    zone_missing_confirmations: int = 2
    """Số lần thử đổi sang cùng một khu (ở các vòng dò khác nhau) mà
    chỉ số khu vẫn không đổi, trước khi coi khu đó không tồn tại trên map.

    Một lệnh `k X` có thể bị game nuốt tạm thời dù khu X có thật, nên cần
    xác nhận nhiều lần. Khi đã xác nhận, tool thu hẹp dải khu còn
    `start..X-1`, chia lại khu cho các acc và dò lại từ khu đầu.
    """

    # ------------------------------------------------------------------ #
    # Đánh boss
    # ------------------------------------------------------------------ #
    attack_keys: list[str] = field(default_factory=lambda: ["ENTER"])
    """Các phím bấm liên tục khi đang đánh boss."""

    attack_interval: float = 0.12
    """Giây nghỉ giữa hai lần bấm phím tấn công."""

    attack_burst: int = 5
    """Số phím bấm liên tiếp trong một lần giành focus.

    Với nhiều acc, bấm từng phím một sẽ liên tục chuyển focus giữa các
    cửa sổ. Gom thành loạt giúp ổn định hơn nhiều.
    """

    # ------------------------------------------------------------------ #
    # Đồng bộ nhiều acc
    # ------------------------------------------------------------------ #
    sync_enabled: bool = True
    """Khi một acc tìm thấy boss, các acc khác sẽ chuyển sang khu đó."""

    boss_gone_timeout: float = 15.0
    """Số giây không acc nào thấy boss thì coi như boss đã chết, quay lại dò khu."""

    boss_dead_frames: int = 3
    """Số khung hình liên tiếp không thấy thanh máu boss trước khi bắt đầu
    chờ phase mới.

    Khi acc vừa tới khu, chữ đỏ có thể chưa kịp vẽ ra. Nếu báo chết ngay
    lúc đó, tất cả acc khác sẽ bỏ khu đang có boss.
    """

    boss_phase_gap: float = 10.0
    """Số giây chờ boss hiện lại sau khi thanh máu biến mất/về 0.

    Boss nhiều phase có thể mất một khoảng để hiện phase tiếp theo, và lúc
    mới chuyển phase boss có thể bất tử. Trong khoảng này tool vẫn đánh
    tiếp và chưa coi là boss đã chết; chỉ khi hết thời gian mà thanh máu
    không hiện lại mới reset dò khu.
    """

    boss_arrive_confirm: float = 8.0
    """Sau khi tới khu boss mà không thấy thanh máu trong bao nhiêu giây
    thì coi như chưa vào đúng khu và gửi lại lệnh đổi khu."""

    # ------------------------------------------------------------------ #
    # Gửi phím
    # ------------------------------------------------------------------ #
    post_keys: bool = False
    """Gửi phím thẳng vào cửa sổ game bằng PostMessage thay vì giành focus.

    Mặc định TẮT vì game NroChibi chỉ xử lý phím khi cửa sổ đang có focus.
    Gửi PostMessage cho cửa sổ không focus sẽ bị game bỏ qua: tool tưởng đã
    gõ lệnh đổi khu nhưng nhân vật đứng yên.

    Bật: chỉ dùng khi đã kiểm tra bằng `--test-keys` thấy game THẬT SỰ nhận
    PostMessage; khi đó các cửa sổ không nhảy lên đỉnh màn hình.
    Tắt: dùng keybd_event + giành focus (cách hoạt động ổn định).
    """

    # ------------------------------------------------------------------ #
    # Bật/tắt
    # ------------------------------------------------------------------ #
    toggle_key: str = "F9"
    """Phím bật/tắt tool (hoạt động kể cả khi đang chơi game)."""

    emergency_key: str = "F12"
    """Phím dừng tool ngay lập tức."""

    pause_key: str = "F11"
    """Phím tạm dừng / tiếp tục."""

    # ------------------------------------------------------------------ #
    # Khác
    # ------------------------------------------------------------------ #
    log_file: str = "nro_boss.log"
    verbose: bool = True

    # ------------------------------------------------------------------ #
    def save(self, path: Path = CONFIG_PATH) -> None:
        """Lưu cấu hình ra file JSON."""
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        """Đọc cấu hình từ file JSON, trả về cấu hình mặc định nếu lỗi."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Dựng Config từ dict, bỏ qua các khoá không hợp lệ."""
        config = cls()
        for key, value in (data or {}).items():
            if not hasattr(config, key):
                continue
            current = getattr(config, key)
            if isinstance(current, Rect):
                if isinstance(value, Rect):
                    setattr(config, key, value)
                elif isinstance(value, dict):
                    setattr(
                        config,
                        key,
                        Rect(
                            left=float(value.get("left", current.left)),
                            top=float(value.get("top", current.top)),
                            right=float(value.get("right", current.right)),
                            bottom=float(value.get("bottom", current.bottom)),
                        ),
                    )
            elif isinstance(current, list) and isinstance(value, list):
                setattr(config, key, value)
            elif isinstance(current, bool):
                setattr(config, key, bool(value))
            elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
                setattr(config, key, type(current)(value))
            elif isinstance(current, str) and isinstance(value, str):
                setattr(config, key, value)
        return config
