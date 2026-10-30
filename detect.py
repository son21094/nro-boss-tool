"""
Phát hiện thanh máu boss trong game NroChibi.

Khi gặp boss, game hiện dòng chữ đỏ ở giữa trên màn hình, ví dụ
"Cumber [2.999.999.996 / 3.000.000.000]". Vị trí dòng chữ này thay đổi
theo vị trí boss trên bản đồ, nên tool tự dò vùng chứa chữ đỏ thay vì
cố định toạ độ.

Mọi phát hiện dựa trên màu sắc pixel trên màn hình, không đọc bộ nhớ.
"""

from __future__ import annotations

import cv2
import numpy as np

# Chữ đỏ tươi của thanh máu boss. HSV:
#   Hue ~0-8 (đỏ) hoặc 172-180 (đỏ vùng khuất)
#   Sat >= 120, Value >= 80
BOSS_HP_LOW = np.array([0, 120, 80], dtype=np.uint8)
BOSS_HP_HIGH = np.array([8, 255, 255], dtype=np.uint8)
BOSS_HP_WRAP_LOW = np.array([172, 120, 80], dtype=np.uint8)
BOSS_HP_WRAP_HIGH = np.array([180, 255, 255], dtype=np.uint8)

# Vùng tìm kiếm: phần bản đồ phía dưới thanh máu người chơi.
# Thanh máu/chakra của nhân vật nằm ở góc trên trái (y < 0.14) nên bị loại
# khỏi vùng tìm. Bảng "Thông báo Boss" ở góc phải cũng nằm ngoài vùng.
#
# Vùng để RỘNG: thanh máu boss được vẽ theo vị trí boss trên bản đồ, boss
# đi lên/xuống là dòng chữ đi theo. Nếu cắt vùng quá hẹp thì lúc boss đi
# ra khỏi vùng ta tưởng nhầm là boss đã chết và cả đội bỏ khu.
SEARCH_LEFT = 0.15
SEARCH_RIGHT = 0.85
SEARCH_TOP = 0.14
SEARCH_BOTTOM = 0.62

# Kích thước một cụm chữ hợp lệ (px trên khung 1340x720).
MIN_TEXT_HEIGHT = 8
MAX_TEXT_HEIGHT = 40
MIN_FILL = 0.25

# Hai cụm chữ được coi là cùng một dòng nếu lệch nhau không quá số px này.
ROW_MERGE_TOLERANCE = 6

# Dòng thanh máu boss có dạng "Tên [số / số]" nên khá rộng. Số sát thương
# và các con số đơn lẻ thì hẹp hơn nhiều.
MIN_ROW_WIDTH = 140

# Dòng thanh máu boss luôn được game vẽ ở giữa màn hình (theo tên boss và
# thanh máu của nó). Số sát thương hiện lên ngay cạnh nhân vật nên không
# căn giữa. Đây là cách phân biệt mạnh nhất mà không cần đọc chữ.
#
# Sai lệch được tính theo bề rộng dòng chữ: dòng boss rộng nên cho sai lệch
# tuyệt đối lớn hơn mà vẫn nằm trong ngưỡng.
CENTER_TOLERANCE = 0.25
"""Sai lệch tối đa giữa tâm dòng chữ và tâm màn hình (theo bề rộng dòng)."""


def _boss_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """Lấy mặt nạ các pixel thuộc chữ đỏ của thanh máu boss."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BOSS_HP_LOW, BOSS_HP_HIGH)
    mask = cv2.bitwise_or(
        mask, cv2.inRange(hsv, BOSS_HP_WRAP_LOW, BOSS_HP_WRAP_HIGH)
    )
    return mask


def red_ratio(region_bgr: np.ndarray) -> float:
    """Tỉ lệ pixel đỏ trong vùng ảnh (0..1)."""
    if region_bgr is None or region_bgr.size == 0:
        return 0.0
    mask = _boss_mask(region_bgr)
    return float(mask.sum()) / float(mask.size) / 255.0


def _is_damage_number(area_bgr: np.ndarray, component: np.ndarray) -> bool:
    """Kiểm tra một cụm pixel đỏ có phải số sát thương không.

    Số sát thương (vd "-314685") có đặc điểm rõ ràng: toàn bộ cụm là
    chữ số và dấu gạch, đều là các ký tự hẹp, không có chữ cái. Chữ cái
    trong tên boss (vd "Cumber") rộng hơn và có đường cong.

    Cách phân biệt ổn định nhất: dùng template matching với các mẫu
    chữ số lấy từ chính game. Nếu không có mẫu, ta dùng heuristic:
    cụm chữ số có tỉ lệ chiều rộng trên chiều cao nhỏ và số lượng
    "ký tự" lớn.
    """
    ys, xs = np.where(component)
    if ys.size == 0:
        return True

    box_h = int(ys.max() - ys.min() + 1)
    box_w = int(xs.max() - xs.min() + 1)

    # Đếm số cột có pixel -> xấp xỉ số ký tự.
    col_has = component.any(axis=0)
    columns = int(col_has.sum())

    if box_h <= 0:
        return True

    # Số sát thương: chiều rộng ~ 5-7 lần chiều cao, chia thành nhiều ký tự.
    # Chữ cái: chiều rộng nhỏ hơn nhiều so với chiều cao.
    if box_w / box_h > 2.2 and columns >= 5:
        return True

    return False


def find_boss_text(
    frame_bgr: np.ndarray,
) -> tuple[int, int, int, int] | None:
    """Tìm vùng chứa dòng chữ đỏ của thanh máu boss.

    Phải phân biệt thanh máu boss với số sát thương hiện lên khi đánh
    (ví dụ "-536047"), vì cả hai đều là chữ đỏ.

    Trả về (x, y, w, h) trong toạ độ ảnh, hoặc None nếu không thấy.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return None

    height, width = frame_bgr.shape[:2]
    x1 = int(SEARCH_LEFT * width)
    x2 = int(SEARCH_RIGHT * width)
    y1 = int(SEARCH_TOP * height)
    y2 = int(SEARCH_BOTTOM * height)
    area = frame_bgr[y1:y2, x1:x2]
    if area.size == 0:
        return None

    mask = _boss_mask(area)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    # Thu thập các cụm chữ đỏ hợp lệ, rồi gom những cụm cùng hàng lại:
    # dòng thanh máu boss ("Cumber [2.999.999.996 / 3.000.000.000]") bị
    # tách thành nhiều cụm do khoảng trắng và dấu ngoặc vuông.
    boxes: list[tuple[int, int, int, int, int]] = []
    for index in range(1, count):
        x, y, w, h, pixels = stats[index]
        if not (MIN_TEXT_HEIGHT <= h <= MAX_TEXT_HEIGHT):
            continue
        if pixels / float(max(w * h, 1)) < MIN_FILL:
            continue
        if _is_damage_number(area, labels == index):
            # Số sát thương -> không phải thanh máu boss.
            continue
        boxes.append((x, y, w, h, pixels))

    if not boxes:
        return None

    # Gom theo hàng: hai cụm thuộc cùng dòng nếu chênh lệch Y nhỏ.
    boxes.sort(key=lambda item: (item[1], item[0]))
    rows: list[list[tuple[int, int, int, int, int]]] = []
    for box in boxes:
        placed = False
        for row in rows:
            ref_y = row[0][1] + row[0][3] / 2
            if abs(ref_y - (box[1] + box[3] / 2)) <= ROW_MERGE_TOLERANCE:
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])

    # Chọn dòng dài nhất: thanh máu boss là dòng có nhiều ký tự.
    best = None
    best_width = 0
    # Tâm màn hình, KHÔNG phải tâm vùng tìm (vùng tìm lệch sang trái).
    screen_center = width / 2.0
    for row in rows:
        left = min(item[0] for item in row)
        right = max(item[0] + item[2] for item in row)
        top = min(item[1] for item in row)
        bottom = max(item[1] + item[3] for item in row)
        row_width = right - left
        if row_width < MIN_ROW_WIDTH:
            continue

        # Dòng thanh máu boss luôn căn giữa màn hình. So sánh bằng toạ
        # độ tuyệt đối trên màn hình, không phải tương đối trong vùng tìm.
        row_center = (left + right) / 2.0 + x1
        if abs(row_center - screen_center) / row_width > CENTER_TOLERANCE:
            continue

        if row_width > best_width:
            best_width = row_width
            best = (left + x1, top + y1, row_width, bottom - top)

    return best


def is_fighting_boss(frame_bgr: np.ndarray, min_ratio: float = 0.02) -> bool:
    """Kiểm tra màn hình có đang hiện thanh máu boss không.

    Tham số:
        frame_bgr: ảnh toàn bộ khung game.
        min_ratio: tỉ lệ pixel đỏ tối thiểu trong vùng tìm thấy.
    """
    region = find_boss_text(frame_bgr)
    if region is None:
        return False
    x, y, w, h = region
    return red_ratio(frame_bgr[y : y + h, x : x + w]) > min_ratio


# ---------------------------------------------------------------------- #
# Đọc máu hiện tại của boss
# ---------------------------------------------------------------------- #

# Dòng thanh máu có dạng "Tên [hp / max]". Khi máu hiện tại về 0, nhóm số
# bên trái dấu "/" chỉ còn 1 chữ số trong khi nhóm số bên phải (máu tối đa)
# vẫn giữ nguyên. Ta đếm số thành phần liên thông của nhóm máu hiện tại:
# nếu không quá 2 (chữ số + dấu ngoặc) thì máu chỉ còn 1 chữ số, tức là
# đã về 0.
HP_DEAD_MAX_CHARS = 2


def _split_tokens(mask: np.ndarray) -> list[tuple[int, int]]:
    """Chia dòng chữ đỏ thành các cụm chữ liên tiếp theo cột.

    Trả về danh sách (cột_bắt_đầu, cột_kết_thúc) của từng cụm. Hai cụm
    được gộp lại nếu chúng cách nhau không quá 2 cột trống, để các chữ số
    viết liền bằng dấu chấm ("3.000.000.000") vẫn là một cụm.
    """
    col_has = mask.any(axis=0)
    tokens: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for index, filled in enumerate(col_has):
        if filled:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 2:
                tokens.append((start, index - gap))
                start = None
                gap = 0
    if start is not None:
        tokens.append((start, len(col_has) - 1))
    return tokens


def _count_chars(mask: np.ndarray, start: int, end: int) -> int:
    """Đếm số ký tự (thành phần liên thông) trong một cụm chữ."""
    sub = mask[:, start : end + 1]
    if sub.size == 0:
        return 0
    count, _ = cv2.connectedComponents(sub, 8)
    return count - 1


def _token_height(mask: np.ndarray, start: int, end: int) -> int:
    """Chiều cao của một cụm chữ (số hàng có pixel đỏ)."""
    sub = mask[:, start : end + 1]
    if sub.size == 0:
        return 0
    return int(sub.any(axis=1).sum())


def find_boss_hp_chars(
    frame_bgr: np.ndarray, region: tuple[int, int, int, int] | None = None
) -> tuple[int, int] | None:
    """Số ký tự của máu hiện tại và máu tối đa trong thanh máu boss.

    Dòng thanh máu có dạng "Tên [hp / max]". Ta tìm dấu "/" (ký tự hẹp
    và cao), rồi lấy nhóm số ngay bên trái làm máu hiện tại và nhóm số
    ngay bên phải làm máu tối đa.

    `region` cho phép truyền vào vùng đã tìm thấy trước đó, tránh phải dò
    lại toàn màn hình.

    Trả về None nếu không phân tích được dòng chữ.
    """
    if region is None:
        region = find_boss_text(frame_bgr)
    if region is None:
        return None
    x, y, w, h = region
    crop = frame_bgr[y : y + h, x : x + w]
    mask = _boss_mask(crop)
    if mask.size == 0:
        return None

    tokens = _split_tokens(mask)
    if len(tokens) < 3:
        return None

    row_h = int(mask.any(axis=1).sum())
    if row_h <= 0:
        return None

    # Dấu "/" và ngoặc vuông "[" "]" đều cao hơn chữ số (chữ số chỉ cao
    # bằng chữ thường). Nhờ vậy lọc theo chiều cao sẽ loại được các chữ
    # số đứng riêng, kể cả khi số lớn bị tách thành nhiều cụm do dấu chấm.
    narrow_limit = max(8, int(w * 0.08))
    candidates: list[int] = []
    for index, (start, end) in enumerate(tokens):
        if end - start + 1 > narrow_limit:
            continue
        if _token_height(mask, start, end) < row_h * 0.85:
            continue
        candidates.append(index)
    if not candidates:
        # Chữ số trong font này có thể cao ngang dấu "/". Thử lại với
        # ngưỡng thấp hơn.
        for index, (start, end) in enumerate(tokens):
            if end - start + 1 > narrow_limit:
                continue
            if _token_height(mask, start, end) < row_h * 0.6:
                continue
            candidates.append(index)
    if not candidates:
        return None

    # Dấu "/" là ký tự hẹp có nhóm số ở cả hai bên. Trong danh sách các
    # ký tự hẹp theo thứ tự ("[", "/", "]"), dấu "/" là ký tự cuối cùng
    # còn có nhóm số ở bên phải.
    sep_idx = -1
    for index in reversed(candidates):
        if index - 1 >= 0 and index + 1 < len(tokens):
            sep_idx = index
            break
    if sep_idx < 0:
        return None

    hp_chars = _count_chars(mask, *tokens[sep_idx - 1])
    max_chars = _count_chars(mask, *tokens[sep_idx + 1])
    if hp_chars <= 0 or max_chars <= 0:
        return None
    return hp_chars, max_chars


def boss_hp_is_zero(
    frame_bgr: np.ndarray,
    max_chars: int = HP_DEAD_MAX_CHARS,
    region: tuple[int, int, int, int] | None = None,
) -> bool:
    """True nếu thanh máu boss đang hiện nhưng máu đã về 0.

    Trường hợp này xảy ra khi boss vừa chết: thanh máu còn đứng lại ở
    giá trị 0 thay vì biến mất hẳn khỏi màn hình.
    """
    chars = find_boss_hp_chars(frame_bgr, region)
    if chars is None:
        return False
    hp_chars, _ = chars
    return hp_chars <= max_chars


# Trạng thái thanh máu boss trong một khung hình.
BOSS_BAR_NONE = "none"
BOSS_BAR_ZERO = "zero"
BOSS_BAR_ALIVE = "alive"


def boss_bar_state(frame_bgr: np.ndarray, min_ratio: float = 0.25) -> str:
    """Trạng thái thanh máu boss trong ảnh.

    Trả về:
        "none"  : không thấy thanh máu (boss chết hoặc ngoài tầm)
        "zero"  : thanh máu còn hiện nhưng máu đã về 0
        "alive" : boss còn sống

    Chỉ dò vùng chứa dòng chữ một lần rồi dùng lại cho cả hai phép kiểm
    tra, để đỡ phải quét toàn màn hình hai lượt.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return BOSS_BAR_NONE
    region = find_boss_text(frame_bgr)
    if region is None:
        return BOSS_BAR_NONE
    x, y, w, h = region
    if red_ratio(frame_bgr[y : y + h, x : x + w]) <= min_ratio:
        return BOSS_BAR_NONE
    if boss_hp_is_zero(frame_bgr, region=region):
        return BOSS_BAR_ZERO
    return BOSS_BAR_ALIVE


class BossDetector:
    """Phát hiện boss có xuất hiện ổn định hay không.

    Số sát thương hiện lên khi đánh (ví dụ "-314685") cũng là chữ đỏ, và
    nó bay lên rồi biến mất trong chưa tới 1 giây. Thanh máu boss thì
    đứng yên suốt khi boss còn sống.

    Vì vậy ta yêu cầu dòng chữ đỏ xuất hiện liên tục nhiều ảnh liên tiếp
    mới coi là thật. Cách này không cần đọc chữ, chỉ cần so sánh ảnh.
    """

    def __init__(self, min_ratio: float = 0.25, stable_frames: int = 2):
        self.min_ratio = min_ratio
        self.stable_frames = max(1, stable_frames)
        self._hits = 0

    def reset(self) -> None:
        """Xoá trạng thái, dùng khi vừa đổi khu."""
        self._hits = 0

    def observe(self, frame_bgr: np.ndarray) -> bool:
        """Đưa một ảnh vào bộ phát hiện, trả về True nếu chắc chắn có boss.

        Gọi liên tiếp với các ảnh chụp cách nhau `interval` giây. Chỉ khi
        dòng chữ đỏ xuất hiện đủ số ảnh liên tiếp thì mới trả về True.
        """
        if is_fighting_boss(frame_bgr, self.min_ratio):
            self._hits += 1
        else:
            self._hits = 0
        return self._hits >= self.stable_frames

    def confirmed(self) -> bool:
        """True nếu đã đủ số ảnh liên tiếp thấy boss."""
        return self._hits >= self.stable_frames
