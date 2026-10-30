"""
Cửa sổ điều khiển cho tool săn boss.

Cửa sổ luôn hiện trên cùng, gồm:
  - Nút BẬT/TẮT lớn (tương đương phím F9).
  - Nút DỪNG KHẨN CẤP (tương đương phím F12).
  - Bảng trạng thái từng acc: đang ở khu nào, đang làm gì.
  - Dòng log cuối cùng.

Tkinter chạy ở thread riêng với một hàng đợi, còn phần tool chính chạy ở
thread khác. Hai bên giao tiếp qua `Controller` và các thuộc tính trạng
thái của acc.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

LOGGER = logging.getLogger("nro_boss.panel")

# Màu theo thiết kế của game: cam chủ đạo, nền sáng.
BG = "#FAF7F2"
SURFACE = "#FFFFFF"
BORDER = "#EBE4D8"
TEXT = "#1F1D1A"
TEXT_SOFT = "#706B65"
TEXT_FAINT = "#A8A199"
PRIMARY = "#FF6B35"
PRIMARY_DARK = "#E85924"
DANGER = "#F0546A"
OK_GREEN = "#22C55E"

# Trạng thái -> màu chữ.
STATE_COLORS = {
    "DANG DO KHU": TEXT_SOFT,
    "DANG DOI KHU": TEXT_SOFT,
    "CHO DOI KHU": TEXT_FAINT,
    "DANG DANH BOSS": DANGER,
    "CHUYEN DEN KHU BOSS": PRIMARY_DARK,
    "DA TOI KHU BOSS": PRIMARY_DARK,
    "TIM THANH MAU BOSS": PRIMARY_DARK,
    "CHO PHASE MOI": PRIMARY_DARK,
    "CHUA THAY BOSS": TEXT_SOFT,
    "BOSS DA CHET": TEXT_FAINT,
    "DA RESET": TEXT_FAINT,
    "DO LAI TU DAU": TEXT_FAINT,
    "KHONG CO KHU": DANGER,
    "DA BO KHU": TEXT_FAINT,
    "DONG BO": PRIMARY_DARK,
    "GUI LENH K": TEXT_SOFT,
    "DANG DOI K": TEXT_SOFT,
    "COI NHU DA O K": TEXT_SOFT,
    "KHONG DOI DUOC KHU": DANGER,
    "BI CHAN DOI KHU": DANGER,
    "TAM DUNG": TEXT_FAINT,
    "DA TAT": TEXT_FAINT,
    "CHO SAN": TEXT_FAINT,
}


class QueueLogHandler(logging.Handler):
    """Đẩy dòng log vào hàng đợi để cửa sổ điều khiển hiển thị."""

    def __init__(self, sink: queue.Queue):
        super().__init__(level=logging.INFO)
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait(self.format(record))
        except Exception:
            pass


class ControlPanel:
    """Cửa sổ điều khiển."""

    def __init__(self, controller, accounts, config):
        self.controller = controller
        self.accounts = accounts
        self.config = config
        self._queue: queue.Queue = queue.Queue()
        self._root: tk.Tk | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._overlay = None
        self._log_handler: QueueLogHandler | None = None
        self._mirror = None
        self._log_lines = 0
        self._status = None
        self.toggle_btn = None
        self.mirror_btn = None
        self.dead_btn = None

    # ------------------------------------------------------------------ #
    # Vòng đời
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Mở cửa sổ ở thread riêng."""
        self._attach_log_handler()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _attach_log_handler(self) -> None:
        """Nối log của tool vào hàng đợi để hiện trong cửa sổ."""
        if self._log_handler is not None:
            return
        self._log_handler = QueueLogHandler(self._queue)
        logging.getLogger().addHandler(self._log_handler)

    def _detach_log_handler(self) -> None:
        if self._log_handler is not None:
            try:
                logging.getLogger().removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler = None

    def close(self) -> None:
        """Đóng cửa sổ."""
        self._closed = True
        if self._mirror is not None:
            try:
                self._mirror.stop()
            except Exception:
                pass
        self._detach_log_handler()
        if self._root is not None:
            try:
                self._root.after(0, self._root.quit)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Giao diện
    # ------------------------------------------------------------------ #
    def _build(self, root: tk.Tk) -> None:
        root.title("Fooday - Tool San Boss")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        pad = tkfont.nametofont("TkDefaultFont")
        title_font = tkfont.Font(root=root, family="Segoe UI", size=13, weight="bold")
        label_font = tkfont.Font(root=root, family="Segoe UI", size=9)
        mono_font = tkfont.Font(root=root, family="Consolas", size=9)
        btn_font = tkfont.Font(root=root, family="Segoe UI", size=10, weight="bold")
        big_font = tkfont.Font(root=root, family="Segoe UI", size=11, weight="bold")
        del pad

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(
            header,
            text="TOOL SAN BOSS",
            font=title_font,
            bg=BG,
            fg=TEXT,
        ).pack(side="left")
        tk.Label(
            header,
            text=f"{self.config.toggle_key} bat/tat  ·  {self.config.pause_key} tam dung  ·  "
            f"{self.config.emergency_key} dung han",
            font=label_font,
            bg=BG,
            fg=TEXT_FAINT,
        ).pack(side="right")

        # Nút bật/tắt.
        buttons = tk.Frame(root, bg=BG)
        buttons.pack(fill="x", padx=16, pady=(0, 10))

        self.toggle_btn = tk.Button(
            buttons,
            text="BAT",
            font=big_font,
            bg=OK_GREEN,
            fg="#FFFFFF",
            activebackground=OK_GREEN,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            width=14,
            height=1,
            cursor="hand2",
            command=self._on_toggle,
        )
        self.toggle_btn.pack(side="left", fill="x", expand=True)

        tk.Button(
            buttons,
            text="DUNG KHAN CAP",
            font=btn_font,
            bg=SURFACE,
            fg=DANGER,
            activebackground=SURFACE,
            activeforeground=DANGER,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            height=1,
            cursor="hand2",
            command=self._on_stop,
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Hàng nút phụ: đồng bộ phím, báo boss đã chết, xoá log.
        tools = tk.Frame(root, bg=BG)
        tools.pack(fill="x", padx=16, pady=(0, 10))

        self.mirror_btn = tk.Button(
            tools,
            text="DONG BO",
            font=btn_font,
            bg=SURFACE,
            fg=PRIMARY_DARK,
            activebackground=SURFACE,
            activeforeground=PRIMARY_DARK,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            height=1,
            cursor="hand2",
            command=self._on_mirror,
        )
        self.mirror_btn.pack(side="left", fill="x", expand=True)

        self.dead_btn = tk.Button(
            tools,
            text="BOSS DA CHET",
            font=btn_font,
            bg=SURFACE,
            fg=TEXT_SOFT,
            activebackground=SURFACE,
            activeforeground=TEXT_SOFT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            height=1,
            cursor="hand2",
            command=self._on_boss_dead,
        )
        self.dead_btn.pack(side="left", padx=(8, 0), fill="x", expand=True)

        tk.Button(
            tools,
            text="XOA LOG + RESET",
            font=btn_font,
            bg=SURFACE,
            fg=TEXT_SOFT,
            activebackground=SURFACE,
            activeforeground=TEXT_SOFT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            height=1,
            cursor="hand2",
            command=self._on_clear_log,
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Bảng trạng thái từng acc.
        table = tk.Frame(root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        table.pack(fill="x", padx=16)

        cols = ("acc", "khu", "trang thai")
        widths = (52, 56, 190)
        for index, (name, width) in enumerate(zip(cols, widths)):
            tk.Label(
                table,
                text=name.upper(),
                font=label_font,
                bg=SURFACE,
                fg=TEXT_FAINT,
                anchor="w",
                width=width // 8,
            ).grid(row=0, column=index, sticky="w", padx=10, pady=(8, 4))

        self._rows: list[dict] = []
        for index, account in enumerate(self.accounts):
            tk.Label(
                table,
                text=f"acc {index}",
                font=mono_font,
                bg=SURFACE,
                fg=TEXT,
                anchor="w",
                width=52 // 8,
            ).grid(row=index + 1, column=0, sticky="w", padx=10, pady=3)

            zone_label = tk.Label(
                table,
                text="-",
                font=mono_font,
                bg=SURFACE,
                fg=TEXT_SOFT,
                anchor="w",
                width=56 // 8,
            )
            zone_label.grid(row=index + 1, column=1, sticky="w", padx=10, pady=3)

            state_label = tk.Label(
                table,
                text="CHO SAN",
                font=mono_font,
                bg=SURFACE,
                fg=TEXT_FAINT,
                anchor="w",
                width=190 // 8,
            )
            state_label.grid(row=index + 1, column=2, sticky="w", padx=10, pady=3)

            self._rows.append(
                {"zone": zone_label, "state": state_label}
            )

        # Khung log cuộn được.
        log_frame = tk.Frame(
            root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER
        )
        log_frame.pack(fill="both", padx=16, pady=(10, 6))

        self._log = tk.Text(
            log_frame,
            height=9,
            font=mono_font,
            bg=SURFACE,
            fg=TEXT_SOFT,
            bd=0,
            wrap="none",
            state="disabled",
        )
        self._log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)

        scroll = tk.Scrollbar(log_frame, command=self._log.yview)
        scroll.pack(side="right", fill="y")
        self._log.config(yscrollcommand=scroll.set)

        # Dòng trạng thái nhanh ở đáy.
        self._status = tk.Label(
            root,
            text="",
            font=label_font,
            bg=BG,
            fg=TEXT_SOFT,
            anchor="w",
            justify="left",
        )
        self._status.pack(fill="x", padx=16, pady=(0, 12))

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ #
    # Sự kiện
    # ------------------------------------------------------------------ #
    def _on_toggle(self) -> None:
        on = self.controller.toggle()
        LOGGER.info("Nut bat/tat -> tool %s.", "BAT" if on else "TAT")
        self._restore_game_focus()

    def _on_stop(self) -> None:
        LOGGER.info("Nut dung khan cap.")
        if self._mirror is not None:
            self._mirror.stop()
        self.controller.request_stop()

    def _on_mirror(self) -> None:
        """Bật/tắt chế độ đồng bộ phím sang mọi acc."""
        if self._mirror is None:
            try:
                from mirroring import Mirror

                self._mirror = Mirror(
                    [account.window for account in self.accounts],
                    self.config,
                    self.controller,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Khong bat duoc che do dong bo: %s", exc)
                return

        on = self._mirror.toggle()
        if on:
            self.mirror_btn.config(
                text="DANG DONG BO - TAT",
                bg=PRIMARY,
                fg="#FFFFFF",
                highlightbackground=PRIMARY,
            )
            self._set_status("Go phim o mot cua so game de dieu khien ca doi.")
        else:
            self.mirror_btn.config(
                text="DONG BO",
                bg=SURFACE,
                fg=PRIMARY_DARK,
                highlightbackground=BORDER,
            )
            self._set_status("Da tat che do dong bo.")

    def _reset_hunt(self, reason: str) -> None:
        """Xoá trạng thái boss và yêu cầu mọi acc dò lại từ khu đầu."""
        state = getattr(self.accounts[0], "state", None) if self.accounts else None
        if state is not None:
            state.clear(reason)
        self.controller.request_reset()

    def _on_boss_dead(self) -> None:
        """Xoá trạng thái boss + reset mọi acc + xoá log hiển thị."""
        LOGGER.info("Nut BOSS DA CHET -> reset tat ca acc.")
        self._reset_hunt("nguoi dung bam BOSS DA CHET")
        self._clear_log_view()
        self._set_status("Da reset: cac acc do lai tu khu dau.")

    def _on_clear_log(self) -> None:
        """Xoá log và reset dò khu về khu đầu của mỗi acc.

        Người dùng thường bấm nút này sau khi boss chết để bắt đầu lượt
        mới, nên nó phải reset luôn chứ không chỉ xoá chữ.
        """
        LOGGER.info("Nut XOA LOG + RESET -> reset tat ca acc.")
        self._reset_hunt("nguoi dung bam XOA LOG + RESET")
        self._clear_log_view()
        self._set_status("Da xoa log: cac acc do lai tu khu dau.")

    def _clear_log_view(self) -> None:
        """Xoá nội dung khung log."""
        if self._log is not None:
            self._log.config(state="normal")
            self._log.delete("1.0", "end")
            self._log.config(state="disabled")
            self._log_lines = 0

    def _set_status(self, text: str, color: str = TEXT_SOFT) -> None:
        """Đặt dòng trạng thái nhanh ở đáy cửa sổ."""
        if self._status is not None:
            self._status.config(text=text, fg=color)

    def _on_close(self) -> None:
        """Đóng cửa sổ điều khiển: dừng tool luôn, tránh tool chạy ngầm."""
        self._closed = True
        if self._mirror is not None:
            try:
                self._mirror.stop()
            except Exception:
                pass
        self.controller.request_stop()
        self._detach_log_handler()
        if self._root is not None:
            self._root.quit()

    def _restore_game_focus(self) -> None:
        """Trả focus về cửa sổ game sau khi bấm nút trên panel.

        Ở chế độ gửi phím bằng PostMessage (mặc định) thì không cần và
        KHÔNG nên làm: mỗi lần trả focus là một lần cửa sổ game bị kéo lên
        đỉnh, khiến các cửa sổ nhảy loạn.
        """
        try:
            from game_window import post_keys_enabled

            if post_keys_enabled():
                return
        except Exception:
            pass
        if not self.accounts:
            return
        try:
            self.accounts[0].window.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Vòng lặp giao diện
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        root = tk.Tk()
        self._root = root
        self._build(root)
        self._build_overlay(root)
        self._refresh()
        root.mainloop()

    def _build_overlay(self, root: tk.Tk) -> None:
        """Tạo lớp phủ trạng thái nằm trên cửa sổ game."""
        try:
            from overlay import GameOverlay

            self._overlay = GameOverlay(root, self.controller, self.accounts, self.config)
        except Exception as exc:  # noqa: BLE001 - overlay là tuỳ chọn
            LOGGER.warning("Khong mo duoc overlay tren man hinh game: %s", exc)
            self._overlay = None

    def _refresh(self) -> None:
        """Cập nhật nội dung bảng và nút.

        Mỗi phần được bọc lỗi riêng: một chỗ hỏng (ví dụ overlay không
        theo kịp cửa sổ game) không được làm đứng cả khung log.
        """
        if self._root is None or self._closed:
            return

        try:
            # Bám theo cửa sổ game để overlay luôn nằm đúng chỗ.
            if self._overlay is not None and self.accounts:
                self._overlay.follow(self.accounts[0].window.client_rect())
                self._overlay.update_state()
        except Exception:
            pass

        try:
            for index, account in enumerate(self.accounts):
                if index >= len(self._rows):
                    break
                zone = (
                    f"K{account.status_zone}"
                    if account.status_zone >= 0
                    else "-"
                )
                self._rows[index]["zone"].config(text=zone)
                self._rows[index]["state"].config(
                    text=account.status,
                    fg=STATE_COLORS.get(account.status, TEXT_SOFT),
                )
        except Exception:
            pass

        try:
            if self.controller.is_enabled():
                self.toggle_btn.config(text="TAT", bg=PRIMARY, fg="#FFFFFF")
            else:
                self.toggle_btn.config(text="BAT", bg=OK_GREEN, fg="#FFFFFF")

            # Đồng bộ trạng thái nút ĐỒNG BỘ với thực tế (có thể bị tắt từ
            # nơi khác, ví dụ dừng khẩn cấp).
            if self.mirror_btn is not None:
                active = bool(self._mirror is not None and self._mirror.active)
                if active:
                    self.mirror_btn.config(
                        text="DANG DONG BO - TAT",
                        bg=PRIMARY,
                        fg="#FFFFFF",
                        highlightbackground=PRIMARY,
                    )
                else:
                    self.mirror_btn.config(
                        text="DONG BO",
                        bg=SURFACE,
                        fg=PRIMARY_DARK,
                        highlightbackground=BORDER,
                    )
        except Exception:
            pass

        try:
            self._drain_log()
        except Exception:
            pass

        try:
            boss_zone = None
            boss_dead = False
            phase_wait = False
            for account in self.accounts:
                if account.status == "DANG DANH BOSS":
                    boss_zone = account.status_zone
                    phase_wait = False
                    break
                if account.status == "CHO PHASE MOI" and boss_zone is None:
                    boss_zone = account.status_zone
                    phase_wait = True
                if account.status == "BOSS DA CHET":
                    boss_dead = True
            if boss_zone is not None:
                if phase_wait:
                    self._set_status(
                        f"Boss khu K{boss_zone} dang chuyen phase - cho hien lai.",
                        PRIMARY_DARK,
                    )
                else:
                    self._set_status(
                        f"Boss dang o khu K{boss_zone} - tat ca acc cung danh.",
                        PRIMARY_DARK,
                    )
            elif boss_dead:
                self._set_status("Boss da chet - cac acc do lai tu khu dau.", TEXT_SOFT)
            elif self._mirror is not None and self._mirror.active:
                self._set_status(
                    "DONG BO PHIM: go phim o mot cua so game, cac acc khac lam theo.",
                    PRIMARY_DARK,
                )
            else:
                self._set_status("Dang do khu...", TEXT_SOFT)
        except Exception:
            pass

        if self._root is not None and not self._closed:
            self._root.after(300, self._refresh)

    def _drain_log(self) -> None:
        """Đưa các dòng log đang chờ vào khung log."""
        if self._log is None:
            return
        lines: list[str] = []
        while True:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return
        if not self._log.winfo_exists():
            return
        self._log.config(state="normal")
        for line in lines:
            self._log.insert("end", line + "\n")
            self._log_lines += 1
        # Giới hạn số dòng để cửa sổ không phình mãi.
        too_many = self._log_lines - 800
        if too_many > 0:
            self._log.delete("1.0", f"{too_many + 1}.0")
            self._log_lines -= too_many
        self._log.see("end")
        self._log.config(state="disabled")
