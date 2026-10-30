"""
Đọc chỉ số khu đang đứng từ góc trên trái cửa sổ game.

Game luôn hiện một dòng như:

    154.Hành tỉnh Bill - K20

trong đó `K20` là khu hiện tại. Đây là nguồn thông tin duy nhất đáng tin:
lệnh chat `k X` có thể bị game chặn ("Không thể đổi khu vực lúc này") mà
không báo lỗi, nên không thể coi "đã gửi lệnh" là "đã đổi khu".

Cách đọc: **không dùng OCR**. Mỗi khu được nhận ra bằng chính hình ảnh
của dòng chữ đó:

* Lần đầu chuyển tới một khu, ta chụp lại dòng chữ và ghi nhớ
  "hình ảnh này = khu X" (X là khu vừa yêu cầu, đã xác nhận là dòng chữ
  đổi khác so với trước khi chuyển).
* Những lần sau, chỉ cần so ảnh hiện tại với các ảnh đã nhớ.

Nhờ vậy tool tự học trong lúc chạy, không cần cài thêm gì và không phụ
thuộc vào font chữ của game.
"""

from __future__ import annotations

import hashlib
import threading

import cv2
import numpy as np

from config import Rect
from game_window import GameWindow

# Kích thước chuẩn để so sánh. Mọi ảnh chụp đều được thu về cùng kích
# thước này nên không bị ảnh hưởng khi cửa sổ game đổi kích thước.
SIGNATURE_WIDTH = 160
SIGNATURE_HEIGHT = 24

# Hai ảnh được coi là CÙNG một dòng chữ khi:
#   - tỉ lệ pixel chữ khác nhau trên toàn dòng nhỏ, VÀ
#   - không có cột nào khác nhau quá nhiều.
# Cùng một khu thì cả hai đều ~0. Khác khu (đổi 1-2 chữ số) thì vùng chữ
# số khác gần như hoàn toàn, nên "cột khác nhiều nhất" rất cao.
SAME_TEXT_THRESHOLD = 0.004
"""Tỉ lệ pixel khác nhau tối đa để coi là cùng một dòng chữ."""

SAME_TEXT_PEAK = 0.10
"""Tỉ lệ pixel khác nhau tối đa ở cột khác nhiều nhất. Cùng một khu thì
gần như 0; khác khu thì thường trên 0,2."""

# Số mẫu tối đa ghi nhớ. Mỗi map tối đa 26 khu, mỗi khu một mẫu là quá đủ.
MAX_SAMPLES = 200


class ZoneReader:
    """Nhận diện khu hiện tại bằng cách so ảnh dòng chỉ số khu."""

    def __init__(self, region: Rect | None = None):
        self.region = region or Rect(left=12.0, top=13.2, right=30.0, bottom=16.6)
        self.lock = threading.Lock()
        # Danh sách (signature, zone, đã xác nhận).
        self._samples: list[tuple[np.ndarray, int, bool]] = []
        self._last_zone: int | None = None

    # ------------------------------------------------------------------ #
    # Ảnh
    # ------------------------------------------------------------------ #
    def crop(self, window: GameWindow) -> np.ndarray | None:
        """Cắt đúng vùng dòng chỉ số khu từ ảnh cửa sổ game."""
        frame = window.capture()
        if frame is None or frame.size == 0:
            return None
        height, width = frame.shape[:2]
        x, y, w, h = self.region.to_pixels(width, height)
        piece = frame[y : y + h, x : x + w]
        if piece.size == 0:
            return None
        return piece

    def signature(self, window: GameWindow) -> np.ndarray | None:
        """Chữ ký ảnh của dòng chỉ số khu (ảnh xám đã chuẩn hoá)."""
        piece = self.crop(window)
        if piece is None:
            return None
        return self._normalize(piece)

    @staticmethod
    def _normalize(piece: np.ndarray) -> np.ndarray:
        """Chuẩn hoá ảnh dòng chữ để so sánh ổn định.

        Các bước:
          1. Chuyển xám, làm mờ nhẹ để bớt nhiễu.
          2. Nhị phân hoá: chỉ giữ lại **chữ**, bỏ nền (nền có thể đổi
             màu theo bản đồ hoặc theo hiệu ứng).
          3. Cắt sát vùng có chữ, rồi thu về kích thước chuẩn. Nhờ vậy
             dòng chữ cùng nội dung luôn cho ra cùng một ảnh.
        """
        gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        # Chữ sáng hơn nền khá nhiều. Otsu tự chọn ngưỡng theo từng ảnh.
        _level, mask = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
        )
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            return np.zeros((SIGNATURE_HEIGHT, SIGNATURE_WIDTH), dtype=np.float32)
        # Cắt sát chữ nhưng chừa 1 px mỗi phía cho ổn định.
        y1 = max(0, int(rows[0]) - 1)
        y2 = min(mask.shape[0], int(rows[-1]) + 2)
        x1 = max(0, int(cols[0]) - 1)
        x2 = min(mask.shape[1], int(cols[-1]) + 2)
        text = mask[y1:y2, x1:x2]
        text = cv2.resize(
            text, (SIGNATURE_WIDTH, SIGNATURE_HEIGHT), interpolation=cv2.INTER_AREA
        )
        # Ảnh nhị phân: so sánh chính là tỉ lệ pixel chữ khác nhau.
        return (text.astype(np.float32) / 255.0)

    @staticmethod
    def _distance(a: np.ndarray, b: np.ndarray) -> float:
        """Tỉ lệ pixel khác nhau giữa hai chữ ký ảnh (0..1)."""
        if a is None or b is None or a.shape != b.shape:
            return 1.0
        return float(np.abs(a - b).mean())

    @staticmethod
    def _peak_distance(a: np.ndarray, b: np.ndarray) -> float:
        """Tỉ lệ pixel khác nhau ở cột khác nhiều nhất (0..1)."""
        if a is None or b is None or a.shape != b.shape:
            return 1.0
        diff = np.abs(a - b)
        columns = diff.mean(axis=0)
        # Gộp 2 cột liền nhau: chữ số co giãn 1 px vẫn bị bắt.
        columns = np.convolve(columns, np.ones(2) / 2.0, mode="same")
        return float(columns.max())

    def similar(self, a: np.ndarray | None, b: np.ndarray | None) -> bool:
        """Hai chữ ký ảnh có phải cùng một dòng chữ không."""
        if a is None or b is None or a.shape != b.shape:
            return False
        return (
            self._distance(a, b) < SAME_TEXT_THRESHOLD
            and self._peak_distance(a, b) < SAME_TEXT_PEAK
        )

    def text_changed(self, before: np.ndarray | None, after: np.ndarray | None) -> bool:
        """Dòng chỉ số khu có thay đổi giữa hai lần chụp không."""
        if before is None or after is None:
            return False
        return not self.similar(before, after)

    # ------------------------------------------------------------------ #
    # Học và đọc
    # ------------------------------------------------------------------ #
    def learn(self, signature: np.ndarray, zone: int) -> None:
        """Ghi nhớ: dòng chữ này là khu `zone`."""
        if signature is None:
            return
        with self.lock:
            for sample, sample_zone, verified in self._samples:
                if self.similar(sample, signature):
                    if sample_zone == zone:
                        return
                    # Ngược lại: mẫu cũ ghi sai (hiếm), thay bằng mẫu mới
                    # nhưng chỉ khi mẫu mới đã được xác nhận.
                    self._samples.remove((sample, sample_zone, verified))
                    break
            self._samples.append((signature, zone, True))
            if len(self._samples) > MAX_SAMPLES:
                self._samples.pop(0)
            self._last_zone = zone

    def read(self, window: GameWindow) -> int | None:
        """Đọc khu hiện tại, trả về None nếu chưa từng thấy dòng chữ này."""
        return self.read_signature(self.signature(window))

    def read_signature(self, signature: np.ndarray | None) -> int | None:
        """Tra khu từ một chữ ký ảnh đã có."""
        if signature is None:
            return None
        with self.lock:
            best_zone: int | None = None
            best_distance = 1.0
            for sample, zone, _verified in self._samples:
                if not self.similar(sample, signature):
                    continue
                distance = self._distance(sample, signature)
                if distance < best_distance:
                    best_distance = distance
                    best_zone = zone
            if best_zone is not None:
                self._last_zone = best_zone
            return best_zone

    def last_zone(self) -> int | None:
        """Khu đọc được gần nhất (không chụp lại)."""
        with self.lock:
            return self._last_zone

    def forget_all(self) -> None:
        """Xoá toàn bộ mẫu đã học (dùng khi đổi sang map khác hẳn)."""
        with self.lock:
            self._samples.clear()
            self._last_zone = None

    def sample_count(self) -> int:
        """Số mẫu đã học, để hiện trong log/panel."""
        with self.lock:
            return len(self._samples)

    # ------------------------------------------------------------------ #
    # Chẩn đoán
    # ------------------------------------------------------------------ #
    @staticmethod
    def digest(signature: np.ndarray | None) -> str:
        """Mã băm ngắn của chữ ký, tiện ghi log."""
        if signature is None:
            return "-"
        data = np.clip(signature, 0, 255).astype(np.uint8).tobytes()
        return hashlib.md5(data).hexdigest()[:8]
