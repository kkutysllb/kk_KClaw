"""KClaw CLI 共享的基于 curses 的 UI 组件。

供 `kclaw tools` 和 `kclaw skills` 使用，用于交互式清单选择。
提供 curses 多选键盘导航功能，以及
针对不支持 curses 的终端的基于文本的编号回退方案。
"""
import sys
from typing import Callable, List, Optional, Set

from kclaw_cli.colors import Colors, color


def curses_checklist(
    title: str,
    items: List[str],
    selected: Set[int],
    *,
    cancel_returns: Set[int] | None = None,
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """Curses 多选清单。返回已选择的索引集合。

    参数:
        title: 显示在清单顶部的标题行。
        items: 每行的显示标签。
        selected: 初始勾选的索引（预选择）。
        cancel_returns: ESC/q 时返回的值。默认为原始 *selected*。
        status_fn: 可选回调函数 ``f(chosen_indices) -> str``，
            返回值渲染在终端底行。适用于实时聚合信息
            （例如预估 token 数量）。
    """
    if cancel_returns is None:
        cancel_returns = set(selected)

    # 安全检查：当 stdin 不是终端时（例如子进程管道），
    # curses 和 input() 都会挂起或空转。立即返回默认值。
    if not sys.stdin.isatty():
        return cancel_returns

    try:
        import curses
        chosen = set(selected)
        result_holder: list = [None]

        def _draw(stdscr):
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

                # 当提供 status_fn 时，保留底行作为状态栏
                footer_rows = 1 if status_fn else 0

                # Header
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    stdscr.addnstr(0, 0, title, max_x - 1, hattr)
                    stdscr.addnstr(
                        1, 0,
                        "  ↑↓ 导航  空格 切换  回车 确认  ESC 取消",
                        max_x - 1, curses.A_DIM,
                    )
                except curses.error:
                    pass

                # Scrollable item list
                visible_rows = max_y - 3 - footer_rows
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + 3
                    if y >= max_y - 1 - footer_rows:
                        break
                    check = "✓" if i in chosen else " "
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

                # 状态栏（底行，右对齐）
                if status_fn:
                    try:
                        status_text = status_fn(chosen)
                        if status_text:
                            # 右对齐到底行
                            sx = max(0, max_x - len(status_text) - 1)
                            sattr = curses.A_DIM
                            if curses.has_colors():
                                sattr |= curses.color_pair(3)
                            stdscr.addnstr(max_y - 1, sx, status_text, max_x - sx - 1, sattr)
                    except curses.error:
                        pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(items)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(items)
                elif key == ord(" "):
                    chosen.symmetric_difference_update({cursor})
                elif key in (curses.KEY_ENTER, 10, 13):
                    result_holder[0] = set(chosen)
                    return
                elif key in (27, ord("q")):
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except Exception:
        return _numbered_fallback(title, items, selected, cancel_returns, status_fn)


def _numbered_fallback(
    title: str,
    items: List[str],
    selected: Set[int],
    cancel_returns: Set[int],
    status_fn: Optional[Callable[[Set[int]], str]] = None,
) -> Set[int]:
    """针对不支持 curses 的终端的基于文本的切换回退。"""
    chosen = set(selected)
    print(color(f"\n  {title}", Colors.YELLOW))
    print(color("  按编号切换，回车确认。\n", Colors.DIM))

    while True:
        for i, label in enumerate(items):
            marker = color("[✓]", Colors.GREEN) if i in chosen else "[ ]"
            print(f"  {marker} {i + 1:>2}. {label}")
        if status_fn:
            status_text = status_fn(chosen)
            if status_text:
                print(color(f"\n  {status_text}", Colors.DIM))
        print()
        try:
            val = input(color("  输入编号切换（或回车确认）: ", Colors.DIM)).strip()
            if not val:
                break
            idx = int(val) - 1
            if 0 <= idx < len(items):
                chosen.symmetric_difference_update({idx})
        except (ValueError, KeyboardInterrupt, EOFError):
            return cancel_returns
        print()

    return chosen
