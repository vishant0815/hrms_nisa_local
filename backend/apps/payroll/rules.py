"""Pure rules for the payslip template, kept free of Django so they are easy to test."""
import re

HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_CONTRAST = 4.5  # white text on the accent colour must stay readable (WCAG AA)

LOGO_TYPES = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg")}
MAX_LOGO_BYTES = 1024 * 1024
MAX_LOGO_SIDE = 2000


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_with_white(colour: str) -> float:
    return 1.05 / (luminance(colour) + 0.05)


def accent_problem(colour: str):
    """Why this accent colour cannot be used, or None when it is fine."""
    if not HEX_COLOUR.match(colour or ""):
        return "Use a colour like #1d5c3a (a # and six letters or digits)."
    if contrast_with_white(colour) < MIN_CONTRAST:
        return "That colour is too light: the white net-pay text would be hard to read. Pick a darker one."
    return None
