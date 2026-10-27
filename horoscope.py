import json
import os
import re
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import recurring_ical_events
import requests
from dotenv import load_dotenv
from google import genai
from icalendar import Calendar


# ---------------------------------------------------------
# UTF-8 OUTPUT
# ---------------------------------------------------------

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ---------------------------------------------------------
# PROGRAM SETTINGS
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

FREEASTRO_ENDPOINT = (
    "https://api.freeastroapi.com/api/v3/horoscope/daily/personal"
)

MELBOURNE_TZ = "Australia/Melbourne"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

MAX_ASTROLOGY_CHARS = 50_000
MAX_TELEGRAM_CHARS = 3_800

DEFAULT_PERSONAL_CONTEXT = """
The recipient has a varied life including university, career development,
friendships, family, relationships, wellbeing, fitness, hobbies, creativity,
finances, personal expression, rest and ordinary day-to-day responsibilities.

Do not assume career, university or productivity is the most important part of
every reading.

Let the astrology determine the focus.

If the strongest transits point towards relationships, emotional wellbeing,
home life, creativity, communication, confidence, identity, money, rest,
motivation, hobbies, physical energy or another area, centre the reading there.

Calendar events are useful practical context, but they are secondary to the
astrology.

The reading should first explain the planetary picture and what it means
personally, then apply that selectively to the person's actual day.
""".strip()


# ---------------------------------------------------------
# ENVIRONMENT SETTINGS
# ---------------------------------------------------------

def required(name: str) -> str:
    """Return a required environment variable."""

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to .env and GitHub Secrets."
        )

    return value


def optional(
    name: str,
    default: str = "",
) -> str:
    """Return an optional environment variable."""

    return os.getenv(name, "").strip() or default


def whole_number(
    name: str,
    minimum: int,