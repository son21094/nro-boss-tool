"""
Kiem tra vong lap "danh boss" cua tool bang anh mau, khong can game.

Chay:
    python test_fight_flow.py

Kiem tra 4 tinh huong:
    1. Boss song, thanh mau bien mat  -> tool ket thuc, bao cho ca doi.
    2. Boss song, thanh mau ve 0       -> tool ket thuc, bao cho ca doi.
    3. Boss song, acc khac bo khu     -> tool dung, khong kiet.
    4. Den khu muon khong thay boss   -> tool tu hang va quay lai do khu.
"""

from __future__ import annotations

import time

import cv2

from boss_hunter import Account, Controller, SharedState
from config import Config


class FakeWindow:
    """Cửa sổ game giả: trả về ảnh có sẵn theo thứ tự."""

    def __init__(self) -> None:
        self.frames: list = []
        self.attacks = 0

    def capture(self):
        if self.frames:
            return self.frames.pop(0)
        return self.default

    def is_minimized(self) -> bool:
        return False

    def press_burst(self, vks, count, interval) -> None:
        self.attacks += 1


def make_account(window, state, controller, zone=5, phase_gap=0.4):
    config = Config()
    config.boss_phase_gap = phase_gap
    account = Account(window, config, [0, 1], 0, state, controller)
    account.running = True
    account.current_zone = zone
    return account


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))
    return condition


def main() -> int:
    boss = cv2.imread("sample_boss.png")
    zero = cv2.imread("sample_hp_zero.png")
    empty = cv2.imread("sample_empty.png")
    if boss is None or zero is None or empty is None:
        print("Thieu anh mau, chay: python make_sample.py")
        return 1

    results: list[bool] = []
    controller = Controller()
    controller.set_enabled(True)

    # 1. Boss sống rồi chết (thanh máu biến mất hẳn).
    window = FakeWindow()
    window.default = empty
    window.frames = [boss] * 3 + [empty] * 8
    state = SharedState()
    account = make_account(window, state, controller)
    state.report(5)
    result = account.fight()
    results.append(check("thanh mau bien mat: ket thuc dung", result == "dead", result))
    results.append(
        check("thanh mau bien mat: bao cho ca doi", state.get() is None)
    )

    # 2. Boss sống rồi chết (thanh máu đứng lại ở giá trị 0).
    window = FakeWindow()
    window.default = zero
    window.frames = [boss] * 3 + [zero] * 8
    state = SharedState()
    account = make_account(window, state, controller)
    state.report(5)
    result = account.fight()
    results.append(check("thanh mau ve 0: ket thuc dung", result == "dead", result))
    results.append(check("thanh mau ve 0: bao cho ca doi", state.get() is None))

    # 3. Cả đội đã bỏ khu: acc phải dừng đánh.
    window = FakeWindow()
    window.default = boss
    window.frames = [boss] * 50
    state = SharedState()
    account = make_account(window, state, controller)
    state.report(5)

    def release() -> None:
        time.sleep(1.5)
        state.mark_dead(5, force=True)

    import threading

    threading.Thread(target=release, daemon=True).start()
    result = account.fight()
    results.append(check("ca doi bo khu: acc dung danh", result == "left", result))

    # 4. Acc tới khu muộn, không thấy boss: phải tự hết hạn rồi rời khu.
    window = FakeWindow()
    window.default = empty
    window.frames = [empty] * 200
    state = SharedState(expire_after=2.0)
    account = make_account(window, state, controller)
    state.boss_zone = 5
    state.last_seen = time.time()
    before_epoch = state.scan_epoch()
    result = account.fight()
    results.append(check("khong thay boss: tu roi khu", result == "left", result))
    results.append(
        check(
            "khong thay boss: co tin hieu do lai tu dau",
            state.scan_epoch() > before_epoch,
            f"{before_epoch}->{state.scan_epoch()}",
        )
    )

    # 5. Bấm BOSS DA CHET: con trỏ dò khu phải quay về khu đầu tiên.
    window = FakeWindow()
    window.default = empty
    state = SharedState()
    controller2 = Controller()
    controller2.set_enabled(True)
    account = make_account(window, state, controller2)
    account.zone_index = 2
    controller2.request_reset()
    pending = account._reset_pending()
    results.append(check("reset: nhan duoc yeu cau", pending))
    results.append(
        check("reset: quay ve khu dau", account.zone_index == 0, str(account.zone_index))
    )

    # 6. Boss chết tự phát tín hiệu để mọi acc dò lại từ đầu.
    state.report(5)
    account.zone_index = 2
    account_other = make_account(window, state, controller2)
    account_other.zone_index = 2
    before = state.scan_epoch()
    state.mark_dead(5, force=True)
    after = state.scan_epoch()
    results.append(
        check("boss chet: co tin hieu reset", after > before, f"{before}->{after}")
    )
    applied_a = account._apply_scan_reset()
    applied_b = account_other._apply_scan_reset()
    results.append(
        check(
            "boss chet: ca 2 acc quay ve khu dau",
            applied_a
            and applied_b
            and account.zone_index == 0
            and account_other.zone_index == 0,
            f"{account.zone_index}/{account_other.zone_index}",
        )
    )

    # 7. Xoá trạng thái boss (nút BOSS DA CHET) cũng phát tín hiệu reset.
    state2 = SharedState()
    account2 = make_account(window, state2, controller2)
    account2.zone_index = 2
    before = state2.scan_epoch()
    state2.clear("test nut BOSS DA CHET")
    after = state2.scan_epoch()
    results.append(
        check("xoa trang thai: co tin hieu reset", after > before, f"{before}->{after}")
    )
    applied = account2._apply_scan_reset()
    results.append(
        check(
            "xoa trang thai: ap dung quay ve khu dau",
            applied and account2.zone_index == 0,
            str(account2.zone_index),
        )
    )

    # 8. Lệnh clear nội bộ của acc không tăng epoch lần nữa.
    before = state2.scan_epoch()
    state2.clear("acc xu ly reset", bump_epoch=False)
    results.append(
        check(
            "clear noi bo: khong tang epoch",
            state2.scan_epoch() == before,
            f"{before}->{state2.scan_epoch()}",
        )
    )

    # 9. Boss nhiều phase: thanh máu mất một lúc rồi hiện lại.
    window = FakeWindow()
    window.default = empty
    window.frames = [boss, empty, empty, boss]
    state = SharedState()
    account = make_account(window, state, controller, phase_gap=1.0)
    account.config.boss_dead_frames = 1
    state.report(5)
    result = account.fight()
    results.append(
        check(
            "phase moi: khong ket luan chet som",
            len(window.frames) == 0,
            str(len(window.frames)),
        )
    )
    results.append(
        check("phase moi: ket thuc khi that su chet", result == "dead", result)
    )
    results.append(
        check("phase moi: bao cho ca doi khi chet that", state.get() is None)
    )

    # 9b. hold() phải giữ trạng thái boss sống trong lúc chờ phase.
    held = SharedState(expire_after=0.2)
    held.report(5)
    time.sleep(0.3)
    held.hold(5)
    results.append(
        check("hold: giu boss song khi cho phase", held.get() == 5)
    )

    # 10. Như trên nhưng thanh máu đứng ở 0 rồi mới hiện phase tiếp theo.
    window = FakeWindow()
    window.default = zero
    window.frames = [boss, zero, zero, boss]
    state = SharedState()
    account = make_account(window, state, controller, phase_gap=1.0)
    account.config.boss_dead_frames = 1
    state.report(5)
    result = account.fight()
    results.append(
        check(
            "phase moi (mau 0): khong ket luan chet som",
            len(window.frames) == 0,
            str(len(window.frames)),
        )
    )
    results.append(
        check("phase moi (mau 0): ket thuc khi that su chet", result == "dead", result)
    )

    passed = sum(1 for item in results if item)
    print(f"\n{passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
