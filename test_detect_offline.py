"""
Kiem tra bo phat hien thanh mau boss tren cac anh mau (khong can game).

Chay:
    python test_detect_offline.py

Cac truong hop:
    1. Thanh mau boss binh thuong -> co chu do, khong phai mau 0.
    2. Thanh mau boss da ve 0 -> van con chu do, phai nhan ra mau 0.
    3. Chi co so sat thuong -> khong co chu do.
    4. Man hinh trong -> khong co chu do.
    5. So lon bi tach thanh nhieu cum (nhu do cham) -> van tim dung
       nhom mau hien tai.
"""

from __future__ import annotations

import cv2
import numpy as np

from detect import (
    _boss_mask,
    find_boss_hp_chars,
    find_boss_text,
    is_fighting_boss,
    boss_hp_is_zero,
)

SAMPLES = {
    "boss": "sample_boss.png",
    "hp_zero": "sample_hp_zero.png",
    "damage": "sample_damage.png",
    "empty": "sample_empty.png",
}


def split_max_group(source: str) -> np.ndarray:
    """Tach cac chu so trong nhom mau toi da thanh nhieu cum rieng.

    Mo phong trang thai cua so lon nhu "3.000.000.000" khi dau cham tao
    ra nhung khoang trong du 2 cot, lam so bi tach thanh nhieu cum.
    """
    frame = cv2.imread(source)
    region = find_boss_text(frame)
    if region is None:
        raise SystemExit("Khong tim thay dong chu do")
    x, y, w, h = region
    row = frame[y : y + h, x : x + w]

    mask = _boss_mask(row)
    sub = mask[:, 139:176]
    count, _, stats, _ = cv2.connectedComponentsWithStats(sub, 8)
    parts = []
    for i in range(1, count):
        px, _, pw, _ = stats[i][:4]
        parts.append((139 + px, pw))
    parts.sort()

    pieces: list[np.ndarray] = [row[:, : parts[0][0]]]
    for index, (px, pw) in enumerate(parts):
        pieces.append(row[:, px : px + pw])
        if index < len(parts) - 1:
            pieces.append(np.full((row.shape[0], 4, 3), 0, dtype=row.dtype))
    new_row = np.hstack(pieces)

    out = frame.copy()
    new_w = new_row.shape[1]
    # Giu cho dong chu van canh giua nhu cu.
    new_x = max(0, x - (new_w - w) // 2)
    out[y : y + h, new_x : new_x + new_w] = new_row
    return out


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    return condition


def main() -> int:
    images = {key: cv2.imread(path) for key, path in SAMPLES.items()}
    for key, image in images.items():
        if image is None:
            print(f"Khong doc duoc {SAMPLES[key]}")
            return 1

    results: list[bool] = []

    boss = images["boss"]
    results.append(
        check(
            "thanh mau binh thuong: tim thay chu do",
            find_boss_text(boss) is not None,
        )
    )
    results.append(
        check("thanh mau binh thuong: co boss", is_fighting_boss(boss, 0.25))
    )
    results.append(
        check(
            "thanh mau binh thuong: khong phai mau 0",
            not boss_hp_is_zero(boss),
            f"chars={find_boss_hp_chars(boss)}",
        )
    )

    zero = images["hp_zero"]
    results.append(
        check("thanh mau ve 0: van tim thay chu do", find_boss_text(zero) is not None)
    )
    results.append(check("thanh mau ve 0: nhan ra mau 0", boss_hp_is_zero(zero)))
    results.append(
        check(
            "thanh mau ve 0: so ky tu giam",
            (find_boss_hp_chars(zero) or (0, 0))[0]
            < (find_boss_hp_chars(boss) or (99, 0))[0],
            f"chars={find_boss_hp_chars(zero)}",
        )
    )

    for key in ("damage", "empty"):
        results.append(
            check(
                f"{key}: khong co chu do",
                find_boss_text(images[key]) is None,
            )
        )
        results.append(
            check(
                f"{key}: khong co boss",
                not is_fighting_boss(images[key], 0.25),
            )
        )

    # Truong hop so bi tach thanh nhieu cum.
    split = split_max_group(SAMPLES["boss"])
    cv2.imwrite("sample_boss_split.png", split)
    chars = find_boss_hp_chars(split)
    results.append(
        check(
            "so tach nhieu cum: van tim dung nhom mau hien tai",
            chars is not None and chars[0] == 4,
            f"chars={chars}",
        )
    )
    results.append(
        check("so tach nhieu cum: khong phai mau 0", not boss_hp_is_zero(split))
    )

    split_zero = split_max_group(SAMPLES["hp_zero"])
    results.append(
        check(
            "so tach nhieu cum: van nhan ra mau 0",
            boss_hp_is_zero(split_zero),
            f"chars={find_boss_hp_chars(split_zero)}",
        )
    )

    passed = sum(1 for item in results if item)
    print(f"\n{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
