"""
Lớp phủ trên màn hình game: hiện trạng thái tool ngay trên cửa sổ game.

Cửa sổ overlay:
  - Trong suốt, chỉ ô trạng thái là đục màu.
  - Luôn nằm trên cùng, bám theo vị trí cửa sổ game.
  - Bấm vào ô trạng thái để bật/tắt tool.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont

LOGGER = logging.getLogger("nro_boss.overlay")

# Màu nền trong suốt của overlay. Mọi pixel đúng màu này sẽ trong suốt.
TRANSPARENT = "#010203"

# Màu ô trạng thái.
ON_BG = "#1F9D55"
OFF_BG = "#7A7A7A"
TEXT = "#FFFFFF"
ALERT = "#FF6B35"
DIM = "#D8D8D8"


class GameOverlay:
    """Overlay trạng thái bám theo cửa sổ game."""

    WIDTH = 250
    HEIGHT = 62

    def __init__(self, master: tk.Misc, controller, accounts, config):
        self.controller = controller
        self.accounts = accounts
        self.config = config

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.window.configure(bg=TRANSPARENT)

        self.font = tkfont.Font(root=master, family="Segoe UI", size=9, weight="bold")
        self.big_font = tkfont.Font(root=master, family="Segoe UI", size=10, weight="bold")

        # Canvas trong suốt, chỉ vẽ ô trạng thái.
        self.canvas = tk.Canvas(
            self.window,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()

        self._badge = self.canvas.create_rectangle(
            0, 0, self.WIDTH, 26, fill=OFF_BG, outline=""
        )
        self._title = self.canvas.create_text(
            10, 13, anchor="w", text="TOOL: TAT", fill=TEXT, font=self.font
        )
        self._hint = self.canvas.create_text(
            10, 44, anchor="w", text="", fill=ALERT, font=self.font
        )
        self._key = self.canvas.create_text(
            self.WIDTH - 10, 44, anchor="e", text="", fill=DIM, font=self.font
        )

        # Bấm vào ô trạng thái để bật/tắt.
        self.canvas.bind("<Button-1>", self._on_click)

    # ------------------------------------------------------------------ #
    def _on_click(self, _event=None) -> None:
        """Bấm vào overlay thì bật/tắt tool."""
        on = self.controller.toggle()
        LOGGER.info("Nut tren man hinh -> tool %s.", "BAT" if on else "TAT")
        # Trả focus về game để các phím sau gõ đúng cửa sổ.
        # Không làm khi đang gửi phím bằng PostMessage: giành focus chỉ
        # khiến cửa sổ game nhảy lên đỉnh.
        try:
            from game_window import post_keys_enabled

            if post_keys_enabled():
                return
        except Exception:
            pass
        if self.accounts:
            try:
                self.accounts[0].window.focus()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def follow(self, rect: tuple[int, int, int, int]) -> None:
        """Bám theo vị trí cửa sổ game (left, top, width, height)."""
        left, top, width, height = rect
        if width <= 0 or height <= 0:
            self.window.withdraw()
            return
        # Góc trên phải của cửa sổ game, ngay dưới thanh tiêu đề.
        x = left + width - self.WIDTH - 8
        y = top + 30
        try:
            self.window.geometry(f"+{x}+{y}")
            if not self.window.winfo_ismapped():
                self.window.deiconify()
        except Exception:
            pass

    def update_state(self) -> None:
        """Cập nhật nội dung hiển thị."""
        try:
            if self.controller.is_enabled():
                self.canvas.itemconfig(self._badge, fill=ON_BG)
                self.canvas.itemconfig(self._title, text="TOOL: DANG CHAY")
            else:
                self.canvas.itemconfig(self._badge, fill=OFF_BG)
                self.canvas.itemconfig(self._title, text="TOOL: TAT")

            fighting = False
            phase_wait = False
            boss_dead = False
            boss_zone = None
            scanning = []
            switch_failed = False
            for account in self.accounts:
                if account.status == "DANG DANH BOSS":
                    fighting = True
                    boss_zone = account.status_zone
                elif account.status == "CHO PHASE MOI":
                    phase_wait = True
                    boss_zone = account.status_zone
                elif account.status == "BOSS DA CHET":
                    boss_dead = True
                elif account.status == "KHONG DOI DUOC KHU":
                    switch_failed = True
                if account.status_zone >= 0:
                    scanning.append(f"K{account.status_zone}")

            if self.controller.input_suspended:
                self.canvas.itemconfig(
                    self._hint, text="DONG BO PHIM - GO PHIM", fill=ALERT
                )
                self.canvas.itemconfig(self._key, text="")
            elif fighting and boss_zone is not None:
                self.canvas.itemconfig(
                    self._hint, text=f"DA DANH BOSS - KHU K{boss_zone}", fill=ALERT
                )
                self.canvas.itemconfig(self._key, text="")
            elif phase_wait and boss_zone is not None:
                self.canvas.itemconfig(
                    self._hint, text=f"CHO PHASE MOI - KHU K{boss_zone}", fill=ALERT
                )
                self.canvas.itemconfig(self._key, text="")
            elif switch_failed:
                self.canvas.itemconfig(
                    self._hint, text="KHONG DOI DUOC KHU - THU LAI", fill=ALERT
                )
                self.canvas.itemconfig(self._key, text=self.config.toggle_key)
            elif boss_dead:
                self.canvas.itemconfig(
                    self._hint, text="BOSS DA CHET - QUAY LAI DO KHU", fill=DIM
                )
                self.canvas.itemconfig(self._key, text=self.config.toggle_key)
            else:
                text = "DANG DO: " + " ".join(scanning[:6]) if scanning else "CHO SAN"
                self.canvas.itemconfig(self._hint, text=text, fill=DIM)
                self.canvas.itemconfig(self._key, text=self.config.toggle_key)
        except Exception:
            pass

    def destroy(self) -> None:
        """Đóng overlay."""
        try:
            self.window.destroy()
        except Exception:
            pass
