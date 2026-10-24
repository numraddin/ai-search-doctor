"""Pages of the bundled demo sites (used by the preset buttons and by the evaluation scripts)."""
from . import config

DEMO_PAGES = {
    "a": ["", "products.html", "contact.html"],
    "b": ["", "more.html", "info.html"],
    "b_fixed": ["", "pricing.html", "tutors.html"],
    "c": ["", "pricing.html", "private/members-pricing.html"],
    "d": ["", "pricing.html", "team.html"],
}


def demo_urls(site: str) -> list[str]:
    return [f"{config.APP_BASE_URL}/demo/{site}/{p}" for p in DEMO_PAGES[site]]
