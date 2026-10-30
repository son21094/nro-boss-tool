"""
Sinh ảnh mẫu "thanh máu boss đã về 0" để kiểm thử bộ phát hiện.

Cách làm: lấy dòng chữ đỏ trong ảnh mẫu, rồi thay nhóm số máu hiện tại
bằng đúng một chữ số "0" lấy từ nhóm máu tối đa. Như vậy ảnh sinh ra
giống hệt ảnh thật lúc boss vừa chết (máu = 0) mà vẫn giữ nguyên
font của game.
"""

from __future__ import annotations

import cv2
import numpy as np

from detect import _boss_mask, _count_chars, _split_tokens, find_boss_text


def build_zero_hp(source: str = "sample_boss.png") -> np.ndarray:
    frame = cv2.imread(source)
    if frame is None:
        raise SystemExit(f"Khong doc duoc {source}")

    region = find_boss_text(frame)
    if region is None:
        raise SystemExit("Khong tim thay dong chu do trong anh mau")
    x, y, w, h = region
    row = frame[y : y + h, x : x + w].copy()

    mask = _boss_mask(row)
    tokens = _split_tokens(mask)

    # Tim cum chu hep va cao: dau "/" (hoac ngoac vuong).
    row_h = int(mask.any(axis=1).sum())
    narrow = [
        i
        for i, (s, e) in enumerate(tokens)
        if e - s + 1 <= max(8, int(w * 0.08))
        and int(mask[:, s : e + 1].any(axis=1).sum()) >= row_h * 0.85
    ]
    if not narrow:
        raise SystemExit("Khong tim thay dau tach")
    sep = narrow[len(narrow) // 2]

    hp_group = tokens[sep - 1]
    max_group = tokens[sep + 1]

    def parts(group: tuple[int, int]) -> list[tuple[int, int, int]]:
        sub = mask[:, group[0] : group[1] + 1]
        count, _, stats, _ = cv2.connectedComponentsWithStats(sub, 8)
        out = []
        for i in range(1, count):
            px, _, pw, ph = stats[i][:4]
            out.append((group[0] + px, pw, ph))
        return sorted(out)

    hp_parts = parts(hp_group)
    max_parts = parts(max_group)

    # Lay mot chu so "0" tu nhom mau toi da (chu so cuoi cung truoc dau "]").
    digits = [
        (px, pw, ph)
        for px, pw, ph in max_parts
        if 6 <= pw <= 14 and ph >= row_h * 0.5
    ]
    if not digits:
        raise SystemExit("Khong tim thay chu so 0 de mau")
    zero_x, zero_w, zero_h = digits[-1]

    # Mau nen: mau pho bien nhat cua dong chu.
    flat = row.reshape(-1, 3)
    values, counts = np.unique(flat, axis=0, return_counts=True)
    background = values[int(np.argmax(counts))]

    # Ghi de cum mau hien tai: giu dau "[", dat "0" vao cho cua chu so
    # dau tien, xoa cac chu so con lai.
    keep_bracket = hp_parts[0]
    result = row.copy()
    result[:, hp_group[0] : hp_group[1] + 1] = background
    result[:, keep_bracket[0] : keep_bracket[0] + keep_bracket[1]] = row[
        :, keep_bracket[0] : keep_bracket[0] + keep_bracket[1]
    ]

    target_x = keep_bracket[0] + keep_bracket[1] + 1
    result[:, target_x : target_x + zero_w] = row[
        :, zero_x : zero_x + zero_w
    ]

    out = frame.copy()
    out[y : y + h, x : x + w] = result
    return out


def main() -> None:
    image = build_zero_hp()
    cv2.imwrite("sample_hp_zero.png", image)
    region = find_boss_text(image)
    if region is None:
        raise SystemExit("Khong tim thay dong chu trong anh vua sinh")
    x, y, w, h = region
    cv2.imwrite(
        "sample_hp_zero_row.png",
        cv2.resize(
            image[y : y + h, x : x + w],
            None,
            fx=6,
            fy=6,
            interpolation=cv2.INTER_NEAREST,
        ),
    )
    print("Da sinh sample_hp_zero.png")


if __name__ == "__main__":
    main()
