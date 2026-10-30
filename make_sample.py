"""
Tao anh mau de kiem chung detector.

Anh nay mo phong game: nen toi, dong chu do thanh mau boss do o giua man
hinh, va so sat thuong bay len canh nhan vat. Dung de kiem tra detector
nham boss / nham sat thuong.

Cach dung:
    python make_sample.py
"""

from __future__ import annotations

import cv2
import numpy as np

WIDTH, HEIGHT = 1340, 720


def _background(seed: int = 11) -> np.ndarray:
    """Nen toi co cham nho, giong nen game."""
    rng = np.random.default_rng(seed)
    img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    img[:, :] = (28, 20, 14)
    for _ in range(700):
        cx = int(rng.integers(0, WIDTH))
        cy = int(rng.integers(100, HEIGHT))
        size = int(rng.integers(2, 7))
        img[cy, cx] = (90, 80, 70)
    return img


def _red_text(img: np.ndarray, text: str, center_x: int, y: int, scale: float = 0.6) -> None:
    """Ve chu do co vien toi, giong chu trong game."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = 2
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x = center_x - tw // 2
    cv2.putText(img, text, (x + 2, y + 2), font, scale, (18, 18, 18), thickness + 2)
    cv2.putText(img, text, (x, y), font, scale, (40, 40, 225), thickness)


def make_samples() -> None:
    """Sinh các ảnh mẫu dùng để kiểm chứng."""
    # 1. Có boss: dong chu do o giua man hinh.
    img = _background()
    _red_text(img, "Tho dai ca [467 / 500]", WIDTH // 2, 200)
    cv2.imwrite("sample_boss.png", img)

    # 2. Chi co so sat thuong bay len canh nhan vat.
    img = _background(seed=21)
    _red_text(img, "-336770", 700, 430, scale=0.75)
    cv2.imwrite("sample_damage.png", img)

    # 3. Khong co gi ca.
    img = _background(seed=31)
    cv2.imwrite("sample_empty.png", img)

    print("Da tao: sample_boss.png, sample_damage.png, sample_empty.png")


if __name__ == "__main__":
    make_samples()
