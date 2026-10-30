"""
Kiem tra nhanh: co doc dung dong chu do thanh mau boss khong.

Cach dung:
    python test_detect.py            # chup 1 lan roi thoat
    python test_detect.py --watch    # lien tuc cap nhat

Che do --watch dung bo phat hien giong het voi tool that, gom ca bo loc
on dinh (nhieu anh lien tiep moi tinh la co boss), nen ket qua do tin
cay hon.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from config import CONFIG_PATH, Config
from detect import BossDetector, find_boss_text, red_ratio
from game_window import GameWindow


def main() -> int:
    """Điểm vào của công cụ kiểm tra."""
    parser = argparse.ArgumentParser(description="Kiem tra phat hien thanh mau boss.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--window-index", type=int, default=0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    config = Config.load(args.config)
    windows = [w for w in GameWindow.list_all() if w.client_rect()[2] > 0]
    if not windows:
        print("Khong tim thay cua so game.")
        return 1
    if args.window_index >= len(windows):
        print(f"Chi so cua so khong hop le (chi co {len(windows)} cua so).")
        return 1

    window = windows[args.window_index]
    detector = BossDetector(
        config.boss_hp_min_ratio, config.detect_stable_frames
    )

    cv2.namedWindow("Boss detect", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Boss detect", 1400, 800)

    while True:
        frame = window.capture()
        if frame.size == 0:
            print("Khong chup duoc man hinh game.")
            return 1

        region = find_boss_text(frame)
        stable = detector.observe(frame)

        canvas = frame.copy()
        ratio = 0.0
        if region:
            x, y, w, h = region
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 0, 255), 2)
            ratio = red_ratio(frame[y : y + h, x : x + w])

        status = (
            f"Vung: {'co' if region else 'khong'} | "
            f"Xac nhan: {'CO' if stable else 'KHONG'} | "
            f"ratio: {ratio:.3f} | frames: {detector._hits}"
            f"/{config.detect_stable_frames}"
        )
        cv2.putText(
            canvas, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2
        )
        cv2.imshow("Boss detect", canvas)
        print(status)

        if not args.watch:
            cv2.waitKey(0)
            break
        if cv2.waitKey(int(args.interval * 1000)) & 0xFF in (27, ord("q")):
            break
        time.sleep(args.interval)

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
