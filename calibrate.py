"""
Kiem chung detector: chup man hinh game va khoanh vung chu do tim duoc.

Cach dung:
    python calibrate.py
    python calibrate.py --watch

Tool se hien anh game kem khung to mau tim duoc, ban nhin va xac nhan
co dung la dong chu do thanh mau boss khong.

Neu khong tim thay gi, hien "KHONG TIM THAY". Luc do co the:
  - Chua vao khu nao co boss -> thu sang khu co boss roi chay lai.
  - Vung tim kiem trong detect.py chua phu het noi dong chu do xuat hien.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from config import CONFIG_PATH, Config
from detect import find_boss_text, is_fighting_boss, red_ratio
from game_window import GameWindow

WINDOW_NAME = "Calibrate NroChibi"


def main() -> int:
    """Điểm vào của công cụ kiểm chứng."""
    parser = argparse.ArgumentParser(
        description="Kiem chung viec phat hien dong chu do thanh mau boss."
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--window-index", type=int, default=0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    config = Config.load(args.config)
    windows = [w for w in GameWindow.list_all() if w.client_rect()[2] > 0]
    if not windows:
        print("Khong tim thay cua so game. Hay mo game truoc.")
        return 1
    if args.window_index >= len(windows):
        print(f"Chi so cua so khong hop le (chi co {len(windows)} cua so).")
        return 1

    window = windows[args.window_index]
    window.focus()
    time.sleep(0.5)

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 1340, 720)

    while True:
        frame = window.capture()
        if frame.size == 0:
            print("Khong chup duoc man hinh game.")
            return 1

        region = find_boss_text(frame)
        fighting = is_fighting_boss(frame, config.boss_hp_min_ratio)

        canvas = frame.copy()
        ratio = 0.0
        if region:
            x, y, w, h = region
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 0, 255), 2)
            ratio = red_ratio(frame[y : y + h, x : x + w])

        status = (
            f"{'TIM THAY' if region else 'KHONG TIM THAY'}"
            f" | Dang danh: {'CO' if fighting else 'KHONG'} | ratio: {ratio:.3f}"
        )
        color = (0, 200, 0) if region else (0, 0, 255)
        cv2.putText(
            canvas, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2
        )
        cv2.imshow(WINDOW_NAME, canvas)
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
