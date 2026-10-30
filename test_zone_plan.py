"""
Test việc tự thu hẹp dải khu khi map có ít hơn 25 khu.

Chạy:
    python test_zone_plan.py

Không cần mở game. Các test kiểm tra:
  - chia khu vòng tròn phủ đủ dải;
  - một lần `k X` bị nuốt tạm thời không làm mất các khu thật;
  - chỉ sau đủ số lần xác nhận mới thu hẹp dải và chia lại;
  - khi hết khu cuối, acc quay lại khu đầu tiên;
  - acc tự cập nhật danh sách khu sau khi dải thay đổi.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np

import boss_hunter
from boss_hunter import Account, Controller, SharedState, ZonePlan
from config import Config
from zone_control import SwitchResult

RESULTS: list[tuple[bool, str]] = []


class FakeWindow:
    """Cửa sổ tối giản; các test này không chụp màn hình thật."""

    def capture(self):
        return np.zeros((0, 0, 3), dtype=np.uint8)

    def is_minimized(self) -> bool:
        return False


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((bool(condition), name))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def make_account(plan: ZonePlan, index: int = 0) -> Account:
    config = Config()
    config.zone_switch_cooldown = 0.0
    config.log_file = str(
        Path(tempfile.gettempdir()) / f"nro_zone_plan_test_{os.getpid()}_{index}.log"
    )
    state = SharedState()
    controller = Controller()
    controller.set_enabled(True)
    account = Account(
        FakeWindow(),
        config,
        plan.zones_for(index),
        index,
        state,
        controller,
        zone_plan=plan,
    )
    account.running = True
    return account


def all_zones(plan: ZonePlan, account_count: int) -> list[int]:
    return sorted(
        zone for index in range(account_count) for zone in plan.zones_for(index)
    )


def main() -> int:
    # 1. Chia vòng tròn phủ đủ dải tối đa.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    check(
        "chia khu: 2 acc phu du 0-25",
        all_zones(plan, 2) == list(range(26)),
        str(all_zones(plan, 2)),
    )
    check(
        "chia khu: acc 0 nhan khu chan",
        plan.zones_for(0) == list(range(0, 26, 2)),
        str(plan.zones_for(0)),
    )

    # 2. Một lần lỗi unchanged chỉ là nghi ngờ, chưa thu hẹp.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    plan.note_valid(0)
    changed_first = plan.note_failed_switch(15, "unchanged", 0)
    check("mot lan loi: chua thu hep", not changed_first and plan.end() == 25)

    # 3. Lỗi lần hai ở cùng khu mới xác nhận khu đó không tồn tại.
    changed_second = plan.note_failed_switch(15, "unchanged", 0)
    check("hai lan loi: thu hep dung", changed_second and plan.end() == 14)
    check(
        "thu hep: chia lai du 0-14",
        all_zones(plan, 2) == list(range(15)),
        str(all_zones(plan, 2)),
    )
    check("thu hep: tang so hieu dai khu", plan.generation() == 1)

    # 4. Lỗi blocked là game bắt chờ, không phải khu không tồn tại.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    plan.note_failed_switch(20, "blocked", 0)
    plan.note_failed_switch(20, "blocked", 0)
    check("bi chan doi khu: khong thu hep", plan.end() == 25)

    # 5. Lỗi tạm thời ở khu có thật: nếu sau đó đổi được thì xoá nghi ngờ.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    plan.note_valid(7)
    plan.note_failed_switch(12, "unchanged", 0)
    plan.note_valid(12)
    plan.note_failed_switch(12, "unchanged", 0)
    check("loi tam thoi o khu that: khong thu hep", plan.end() == 25)

    # 6. Không bao giờ thu hẹp xuống dưới khu đã đổi tới được.
    plan = ZonePlan(0, 25, 2, missing_confirmations=1)
    plan.note_valid(20)
    plan.note_failed_switch(15, "unchanged", 0)
    check("giu khu da xac nhan: khong thu hep duoi 20", plan.end() == 25)

    # 7. Reset (đổi map bằng DONG BO) đưa dải về mức tối đa.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    plan.note_valid(0)
    plan.note_failed_switch(15, "unchanged", 0)
    plan.note_failed_switch(15, "unchanged", 0)
    before_generation = plan.generation()
    did_reset = plan.reset()
    check(
        "reset: dua dai khu ve 0-25",
        did_reset and plan.end() == 25,
        str(plan.end()),
    )
    check(
        "reset: tang so hieu de acc cap nhat",
        plan.generation() == before_generation + 1,
    )

    # 8. Acc tự cập nhật danh sách và dò lại từ đầu sau khi dải thu hẹp.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    account = make_account(plan, 0)
    first = account.next_zone()
    second = account.next_zone()
    plan.note_valid(first)
    plan.note_failed_switch(9, "unchanged", 0)
    plan.note_failed_switch(9, "unchanged", 0)
    after_shrink = account.next_zone()
    check("acc: doc khu dau truoc khi thu hep", (first, second) == (0, 2))
    check(
        "acc: cap nhat dai moi va do lai tu dau",
        after_shrink == 0 and account.zones == [0, 2, 4, 6, 8],
        f"{after_shrink} {account.zones}",
    )

    # 9. Hết khu cuối mà không thấy boss thì quay lại khu đầu.
    plan = ZonePlan(0, 5, 1, missing_confirmations=2)
    account = make_account(plan, 0)
    sequence = [account.next_zone() for _ in range(6)]
    wrapped = account.next_zone()
    check("quet: du 0-5 roi quay lai", sequence == list(range(6)), str(sequence))
    check("quet: khu tiep theo la khu dau", wrapped == 0, str(wrapped))

    # 10. Acc không có khu (map ít khu hơn số acc) phải đứng yên an toàn.
    plan = ZonePlan(0, 0, 3, missing_confirmations=2)
    account = make_account(plan, 2)
    check("acc khong co khu: next_zone tra -1", account.next_zone() == -1)

    # 11. Lỗi đổi khu đi qua Account cũng cập nhật dải dùng chung.
    plan = ZonePlan(0, 25, 2, missing_confirmations=2)
    account = make_account(plan, 0)
    original_switch = boss_hunter.switch_to_zone
    boss_hunter.switch_to_zone = lambda *args, **kwargs: SwitchResult(
        False, 0, False, "unchanged"
    )
    try:
        first_ok = account.move_to_zone(9)
        end_after_first = plan.end()
        second_ok = account.move_to_zone(9)
    finally:
        boss_hunter.switch_to_zone = original_switch
    check(
        "acc doi khu loi: chua thu hep sau lan dau",
        not first_ok and end_after_first == 25,
    )
    check(
        "acc doi khu loi: thu hep sau lan hai",
        not second_ok and plan.end() == 8,
        str(plan.end()),
    )
    check(
        "acc doi khu loi: danh sach khu da cap nhat",
        account.zones == [0, 2, 4, 6, 8],
        str(account.zones),
    )

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
