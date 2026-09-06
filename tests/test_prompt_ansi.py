"""Regression test for readline mangling ANSI codes in input() prompts.

The cli imports `readline` for line editing; colored prompts must bracket their
escape sequences in \001..\002 so readline measures width correctly instead of
displaying a literal "[1m".
"""

from crm import display
from crm.display import _rl_prompt


def test_ansi_runs_are_guarded():
    s = f"  Confirm? [{display.BOLD}y{display.RESET}/{display.DIM}N{display.RESET}] "
    out = _rl_prompt(s)
    # Each ANSI escape must be wrapped by \001 (start) .. \002 (end).
    assert out.count("\001") == out.count("\002")
    assert out.count("\001") == out.count("\033[")
    # No ESC byte may appear outside a guard pair.
    for esc in [display.BOLD, display.DIM, display.RESET]:
        if esc:  # empty when colors are disabled
            assert f"\001{esc}\002" in out


def test_no_ansi_prompt_unchanged():
    assert _rl_prompt("Search: ") == "Search: "


def test_plain_text_between_codes_preserved():
    out = _rl_prompt(f"{display.BOLD}hi{display.RESET}")
    # Visible text stays outside the guards.
    assert "hi" in out
    assert "\001hi" not in out
