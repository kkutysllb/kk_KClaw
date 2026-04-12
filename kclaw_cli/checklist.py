"""KClaw CLI 共享的基于 curses 的多选清单。

供 ``kclaw tools`` 和 ``kclaw skills`` 使用，用于显示可切换的列表项。
当 curses 不可用时（Windows 无 curses、stdin 被管道等）
回退到带编号的文本界面。
"""

import sys
from typing import List, Set

from kclaw_cli.colors import Colors, color


def curses_checklist(
    title: str,
    items: List[str],
    pre_selected: Set[int],
) -> Set[int]:
    """多选清单。返回 **已选择** 的索引集合。

    参数:
        title: 显示在清单顶部的标题文本。
        items: 每行的显示标签。
        pre_selected: 初始勾选的索引。

    返回:
        用户确认勾选的索引集合。取消（ESC/q）时，
        返回 ``pre_selected`` 不变。
    """
    # 安全检查：当 stdin 不是终端时返回默认值。
    if not sys.stdin.isatty():
        return set(pre_selected)

    try:
        import curses
        selected = set(pre_selected)
        result = [None]

        def _ui(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, 8, -1)  # dim gray
            cursor = 0
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                # Header
                try:
                    hattr = curses.A_BOLD | (curses.color_pair(2) if curses.has_colors() else 0)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ 导航  空格 切换  回车 确认  ESC 取消",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                # Scrollable item list
                visible_rows = max_y - 3
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1:
                        break
                    check = "✓" if i in selected else " "
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} [{check}] {items[i]}"

                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(items)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(items)
                elif key == ord(" "):
                    selected.symmetric_difference_update({cursor})
                elif key in (curses.KEY_ENTER, 10, 13):
                    result[0] = set(selected)
                    return
                elif key in (27, ord("q")):
                    result[0] = set(pre_selected)
                    return

        curses.wrapper(_ui)
        return result[0] if result[0] is not None else set(pre_selected)

    except Exception:
        pass  # 回退到带编号的文本模式

    # ── 带编号的文本回退 ────────────────────────────────────────────
    selected = set(pre_selected)
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color("  按编号切换，回车确认。\n", Colors.DIM))

    while True:
        for i, label in enumerate(items):
            check = "✓" if i in selected else " "
            print(f"    {i + 1:3}. [{check}] {label}")
        print()

        try:
            raw = input(color("  输入编号切换，'s' 保存，'q' 取消: ", Colors.DIM)).strip()
        except (KeyboardInterrupt, EOFError):
            return set(pre_selected)

        if raw.lower() == "s" or raw == "":
            return selected
        if raw.lower() == "q":
            return set(pre_selected)
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                selected.symmetric_difference_update({idx})
        except ValueError:
            print(color("  无效输入", Colors.DIM))
