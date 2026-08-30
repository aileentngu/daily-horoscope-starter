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
    maximum: int,
) -> int:
    """Read and validate a whole-number environment variable."""

    raw_value = required(name)

    try:
        value = int(raw_value)

    except ValueError as error:
        raise RuntimeError(
            f"{name} must be a whole number. Received: {raw_value}"
        ) from error

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}."
        )

    return value


def optional_whole_number(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read an optional whole-number environment variable."""

    raw_value = os.getenv(name, "").strip()

    if not raw_value:
        return default

    try:
        value = int(raw_value)

    except ValueError as error:
        raise RuntimeError(
            f"{name} must be a whole number. Received: {raw_value}"
        ) from error

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}."
        )

    return value


def true_or_false(
    name: str,
    default: bool,
) -> bool:
    """Read a true-or-false environment variable."""

    raw_value = os.getenv(name, "").strip().casefold()

    if not raw_value:
        return default

    if raw_value in {
        "true",
        "1",
        "yes",
        "y",
    }:
        return True

    if raw_value in {
        "false",
        "0",
        "no",
        "n",
    }:
        return False

    raise RuntimeError(
        f"{name} must be true or false. Received: {raw_value}"
    )


def reading_mode() -> str:
    """Return either morning or evening."""

    mode = optional(
        "READING_MODE",
        "morning",
    ).casefold()

    if mode not in {
        "morning",
        "evening",
    }:
        raise RuntimeError(
            "READING_MODE must be morning or evening."
        )

    return mode


# ---------------------------------------------------------
# DATE AND TIME
# ---------------------------------------------------------

def melbourne_timezone() -> ZoneInfo:
    """Return Melbourne's timezone."""

    try:
        return ZoneInfo(
            MELBOURNE_TZ
        )

    except ZoneInfoNotFoundError as error:
        raise RuntimeError(
            "Australia/Melbourne could not be loaded. "
            "Install tzdata from requirements.txt."
        ) from error


def date_label(
    value: datetime,
) -> str:
    """Format a date without a leading zero."""

    return (
        f"{value.strftime('%A')}, "
        f"{value.day} "
        f"{value.strftime('%B %Y')}"
    )


def time_label(
    value: datetime,
) -> str:
    """Format a time such as 7:30 am."""

    hour = (
        value.strftime("%I").lstrip("0")
        or "12"
    )

    return (
        f"{hour}:"
        f"{value.strftime('%M')} "
        f"{value.strftime('%p').lower()}"
    )


# ---------------------------------------------------------
# BIRTH DETAILS
# ---------------------------------------------------------

def birth_details() -> dict[str, Any]:
    """Build the birth-details object for FreeAstroAPI."""

    return {
        "year": whole_number(
            "BIRTH_YEAR",
            1800,
            2200,
        ),
        "month": whole_number(
            "BIRTH_MONTH",
            1,
            12,
        ),
        "day": whole_number(
            "BIRTH_DAY",
            1,
            31,
        ),
        "hour": whole_number(
            "BIRTH_HOUR",
            0,
            23,
        ),
        "minute": whole_number(
            "BIRTH_MINUTE",
            0,
            59,
        ),
        "city": required(
            "BIRTH_CITY"
        ),
        "tz_str": required(
            "BIRTH_TIMEZONE"
        ),
        "time_known": true_or_false(
            "BIRTH_TIME_KNOWN",
            True,
        ),
    }


# ---------------------------------------------------------
# FREEASTROAPI
# ---------------------------------------------------------

def fetch_astrology(
    target_date: str,
) -> dict[str, Any]:
    """Retrieve personalised astrology for the requested date."""

    last_error: Exception | None = None

    for attempt in range(1, 4):

        try:
            response = requests.post(
                FREEASTRO_ENDPOINT,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": required(
                        "FREEASTRO_API_KEY"
                    ),
                },
                json={
                    "birth": birth_details(),
                    "date": target_date,
                    "tz_str": MELBOURNE_TZ,
                    "include_interpretation_blocks": True,
                },
                timeout=90,
            )

        except requests.RequestException as error:

            last_error = error

            if attempt < 3:
                time.sleep(5)
                continue

            raise RuntimeError(
                "FreeAstroAPI could not be reached "
                "after three attempts."
            ) from error

        if response.status_code in {
            401,
            403,
        }:
            raise RuntimeError(
                "FreeAstroAPI rejected FREEASTRO_API_KEY."
            )

        if response.status_code == 429:
            raise RuntimeError(
                "The FreeAstroAPI request allowance "
                "has been reached."
            )

        if (
            response.status_code >= 500
            and attempt < 3
        ):
            time.sleep(5)
            continue

        if not response.ok:

            preview = (
                response.text[:700]
                .replace("\n", " ")
            )

            raise RuntimeError(
                f"FreeAstroAPI returned HTTP "
                f"{response.status_code}: {preview}"
            )

        try:
            result = response.json()

        except ValueError as error:
            raise RuntimeError(
                "FreeAstroAPI returned invalid JSON."
            ) from error

        data = result.get("data")

        if not isinstance(
            data,
            dict,
        ):
            raise RuntimeError(
                "FreeAstroAPI did not return "
                "the expected data."
            )

        returned_date = str(
            data.get(
                "date",
                "",
            )
        ).strip()

        if (
            returned_date
            and returned_date != target_date
        ):
            raise RuntimeError(
                f"FreeAstroAPI returned "
                f"{returned_date}, but "
                f"{target_date} was requested."
            )

        return result

    raise RuntimeError(
        "FreeAstroAPI failed unexpectedly."
    ) from last_error


# ---------------------------------------------------------
# GOOGLE CALENDAR
# ---------------------------------------------------------

def download_calendar() -> Calendar | None:
    """
    Download the Google Calendar iCal feed.

    A missing calendar URL is allowed and is treated
    as an empty calendar.
    """

    calendar_url = optional(
        "GOOGLE_CALENDAR_ICAL_URL"
    )

    if not calendar_url:

        print(
            "No calendar URL supplied. "
            "Continuing without events."
        )

        return None

    response = requests.get(
        calendar_url,
        timeout=45,
    )

    if response.status_code in {
        401,
        403,
        404,
        410,
    }:
        raise RuntimeError(
            "Google Calendar rejected "
            "the secret iCal address."
        )

    if not response.ok:
        raise RuntimeError(
            f"Google Calendar returned HTTP "
            f"{response.status_code}."
        )

    if (
        b"BEGIN:VCALENDAR"
        not in response.content[:5000]
    ):
        raise RuntimeError(
            "The calendar address did not "
            "return an iCalendar feed."
        )

    try:
        return Calendar.from_ical(
            response.content
        )

    except Exception as error:
        raise RuntimeError(
            "The Google Calendar feed "
            "could not be parsed."
        ) from error


def clean_text(
    value: object,
    limit: int = 160,
) -> str:
    """Clean text read from a calendar event."""

    if value is None:
        return ""

    cleaned = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    if len(cleaned) <= limit:
        return cleaned

    return (
        cleaned[
            :limit - 1
        ].rstrip()
        + "…"
    )


def decoded(
    event: Any,
    name: str,
) -> Any:
    """Decode an iCalendar event property."""

    value = event.get(
        name
    )

    if value is None:
        return None

    try:
        return event.decoded(
            name
        )

    except Exception:
        return getattr(
            value,
            "dt",
            value,
        )


def to_melbourne(
    value: datetime,
    timezone: ZoneInfo,
) -> datetime:
    """Convert an event time to Melbourne time."""

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone
        )

    return value.astimezone(
        timezone
    )


def event_time(
    event: Any,
    target: date,
    timezone: ZoneInfo,
) -> tuple[str, datetime]:
    """Return an event's displayed and sortable time."""

    start = decoded(
        event,
        "DTSTART",
    )

    if start is None:
        raise ValueError(
            "Event has no start time."
        )

    end = decoded(
        event,
        "DTEND",
    )

    if isinstance(
        start,
        datetime,
    ):

        start_local = to_melbourne(
            start,
            timezone,
        )

        if isinstance(
            end,
            datetime,
        ):
            end_local = to_melbourne(
                end,
                timezone,
            )

        else:
            end_local = (
                start_local
                + timedelta(
                    hours=1
                )
            )

        displayed_time = (
            f"{time_label(start_local)}"
            f"–{time_label(end_local)}"
        )

        return (
            displayed_time,
            start_local,
        )

    all_day_time = datetime.combine(
        target,
        dt_time.min,
        tzinfo=timezone,
    )

    return (
        "All day",
        all_day_time,
    )


def get_calendar_events(
    target: date,
) -> list[dict[str, str]]:
    """
    Return calendar events for the requested date.

    No events is normal and returns an empty list.
    """

    calendar = download_calendar()

    if calendar is None:
        return []

    has_event_components = any(
        getattr(
            component,
            "name",
            "",
        ) == "VEVENT"
        for component in calendar.walk()
    )

    if not has_event_components:
        return []

    timezone = melbourne_timezone()

    start = datetime.combine(
        target,
        dt_time.min,
        tzinfo=timezone,
    )

    end = (
        start
        + timedelta(
            days=1
        )
    )

    try:
        occurrences = list(
            recurring_ical_events
            .of(calendar)
            .between(
                start,
                end,
            )
        )

    except IndexError:
        return []

    except Exception as error:
        raise RuntimeError(
            "Recurring calendar events "
            "could not be expanded."
        ) from error

    if not occurrences:
        return []

    include_locations = true_or_false(
        "INCLUDE_EVENT_LOCATIONS",
        False,
    )

    maximum_events = optional_whole_number(
        "MAX_CALENDAR_EVENTS",
        20,
        1,
        50,
    )

    records: list[
        dict[str, Any]
    ] = []

    seen: set[
        tuple[str, str, str]
    ] = set()

    for event in occurrences:

        status = clean_text(
            event.get(
                "STATUS"
            )
        ).casefold()

        if status == "cancelled":
            continue

        title = (
            clean_text(
                event.get(
                    "SUMMARY"
                )
            )
            or "Untitled event"
        )

        try:
            (
                displayed_time,
                sort_value,
            ) = event_time(
                event,
                target,
                timezone,
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        duplicate_key = (
            clean_text(
                event.get(
                    "UID"
                ),
                200,
            ),
            sort_value.isoformat(),
            title,
        )

        if duplicate_key in seen:
            continue

        seen.add(
            duplicate_key
        )

        record: dict[
            str,
            Any,
        ] = {
            "time": displayed_time,
            "title": title,
            "_sort": sort_value,
        }

        if include_locations:

            location = clean_text(
                event.get(
                    "LOCATION"
                ),
                140,
            )

            if location:
                record[
                    "location"
                ] = location

        records.append(
            record
        )

    records.sort(
        key=lambda item: (
            item["_sort"],
            str(
                item["title"]
            ).casefold(),
        )
    )

    output: list[
        dict[str, str]
    ] = []

    for record in records[
        :maximum_events
    ]:

        event_output = {
            "time": str(
                record["time"]
            ),
            "title": str(
                record["title"]
            ),
        }

        if record.get(
            "location"
        ):
            event_output[
                "location"
            ] = str(
                record["location"]
            )

        output.append(
            event_output
        )

    return output


# ---------------------------------------------------------
# PROMPT SOURCE FORMATTING
# ---------------------------------------------------------

def calendar_for_prompt(
    events: list[dict[str, str]],
) -> str:
    """Format calendar events for Gemini."""

    if not events:
        return (
            "There are no scheduled calendar events "
            "for this date. Treat it as an open day. "
            "Do not invent commitments."
        )

    lines = []

    for event in events:

        line = (
            f"- {event['time']}: "
            f"{event['title']}"
        )

        if event.get(
            "location"
        ):
            line += (
                f" — Location: "
                f"{event['location']}"
            )

        lines.append(
            line
        )

    return "\n".join(
        lines
    )


def astrology_for_prompt(
    result: dict[str, Any],
) -> str:
    """Convert the astrology data into readable JSON."""

    return json.dumps(
        result.get(
            "data",
            result,
        ),
        ensure_ascii=False,
        indent=2,
    )[
        :MAX_ASTROLOGY_CHARS
    ]


# ---------------------------------------------------------
# MORNING PROMPT
# ---------------------------------------------------------

def build_morning_prompt(
    result: dict[str, Any],
    events: list[dict[str, str]],
    target_label: str,
) -> str:
    """
    Build an astrology-first morning reading.

    The astrology determines the life-area focus.
    The calendar is secondary practical context.
    """

    personal_context = optional(
        "PERSONAL_CONTEXT",
        DEFAULT_PERSONAL_CONTEXT,
    )

    calendar_text = calendar_for_prompt(
        events
    )

    astrology_text = astrology_for_prompt(
        result
    )

    return f"""
Prepare a concise personalised morning astrology reading.

DATE
{target_label}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TODAY'S CALENDAR
{calendar_text}

PERSONALISED ASTROLOGY JSON
{astrology_text}

CORE PURPOSE

The reading must begin with the astrology itself, not with work, productivity
or the calendar.

Help the person understand what the current planetary positions, transits and
aspects mean for them personally.

After explaining the strongest astrology, apply it selectively to their actual
day.

ASTROLOGY-FIRST RULES

1. Identify only the two to four most significant influences in the source.

2. Preserve the real astrological language provided by the source wherever
   useful, including:
   - transiting planet;
   - natal planet or point;
   - conjunction;
   - opposition;
   - square;
   - trine;
   - sextile;
   - retrograde status;
   - sign placement;
   - house placement.

3. Mention signs or houses only if they are actually supplied by the source.
   Never invent them.

4. Briefly explain what the relevant planets represent so the person gradually
   learns astrology.

   Examples of the level of explanation wanted:
   - Mercury concerns thinking, communication and learning.
   - Venus concerns relationships, attraction, pleasure and values.
   - Mars concerns drive, action, desire and assertion.
   - Jupiter concerns expansion, opportunity and perspective.
   - Saturn concerns limits, responsibility and structure.
   - Uranus concerns disruption, independence and change.
   - Neptune concerns imagination, sensitivity and ambiguity.
   - Pluto concerns intensity, power and deep change.

   Only explain planets that are actually important today.

5. Briefly explain the aspect when useful.

   For example:
   - a conjunction combines and intensifies two influences;
   - a trine tends to allow easier expression;
   - a sextile tends to present a constructive opportunity;
   - a square tends to create tension requiring active adjustment;
   - an opposition tends to highlight competing needs or perspectives.

   Do not include these definitions unless that aspect appears in today's
   reading.

6. When the source distinguishes a transiting planet from a natal planet or
   point, explain the difference naturally.

7. Do not invent placements, aspects, houses, signs, meanings or predictions
   that are not supported by the source.

LIFE-AREA SELECTION

Do not default to:
- career;
- university;
- applications;
- productivity;
- getting more work done.

Let the astrology determine today's main focus.

Possible areas include:
- romantic relationships;
- friendships;
- family;
- social connection;
- emotional wellbeing;
- mental wellbeing;
- rest;
- home and private life;
- communication;
- learning;
- confidence;
- identity;
- self-expression;
- creativity;
- hobbies;
- enjoyment;
- money and values;
- motivation;
- exercise and physical drive;
- career;
- university;
- practical responsibilities.

If one area is clearly dominant, allow most of the reading to focus on that
area.

Do not mention unrelated life categories just to make the reading appear
comprehensive.

CALENDAR RULES

The calendar is secondary context.

Use it only after interpreting the astrology.

- Mention an event only when today's astrology gives useful advice for how to
  approach it.
- Do not mention every event merely because it appears in the calendar.
- Do not turn ordinary appointments into dramatic astrological predictions.
- If an event has no meaningful connection to the strongest astrology, omit it.
- If there are no events, simply give useful guidance for the open day.

SPECIFICITY

Advice should be concrete.

Good examples:
- have the conversation directly rather than rehearsing it repeatedly;
- make time for a hobby if Venus or creative themes are particularly strong;
- do not answer a difficult message while frustrated;
- use a Mars influence for exercise or decisive action;
- leave more recovery time if Saturn or emotionally demanding influences are
  dominant;
- approach a social event with curiosity rather than trying to control how it
  goes.

These are examples only. Use the actual horoscope.

STYLE

Use concise dot points.

Write like a knowledgeable friend who is teaching the person how astrology
works while also helping them use the reading practically.

Be:
- clear;
- grounded;
- natural;
- specific;
- relatively concise.

Do not sound like:
- a newspaper horoscope;
- a motivational poster;
- a generic AI summary;
- an astrologer making guaranteed predictions.

Treat astrology as reflective guidance rather than certainty.

Avoid phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- step into your power;
- the universe is telling you;
- stay positive;
- balance is key;
- navigate these energies.

Do not invent:
- arguments;
- opportunities;
- health problems;
- deadlines;
- financial outcomes;
- romantic events;
- job offers;
- guaranteed outcomes.

RETURN ONLY THIS FORMAT

Morning — {target_label}

Planetary picture
• [Strongest transit or aspect. Explain what the relevant planets represent,
  what the aspect means and how this combination affects the person today.]
• [Second genuinely important influence, explained in the same concise way.]
• [Third influence only if it adds meaningful information.]

Main focus
• [Identify the single life area or psychological theme most strongly
  emphasised today and give practical guidance.]

Your day
• [One highly relevant action or approach based on today's astrology.]
• [One calendar-specific point only if an actual event genuinely connects to
  the astrology.]
• [One additional point only if strongly useful.]

Watch for
• [The most relevant emotional reaction, communication habit, behavioural
  pattern or decision to avoid today.]

This month
• [One genuine longer-running or slow-moving influence and what it means.]
• [Second monthly theme only if genuinely supported.]
• [Third point only if distinct and genuinely useful.]

Keep the complete message between 120 and 190 words.

Prefer fewer strong bullets over filling every section with weak material.

Do not add:
- emojis;
- hashtags;
- lucky numbers;
- ratings;
- disclaimers;
- Markdown bold markers;
- an introduction;
- a conclusion.
""".strip()


# ---------------------------------------------------------
# EVENING PROMPT
# ---------------------------------------------------------

def build_evening_prompt(
    result: dict[str, Any],
    events: list[dict[str, str]],
    target_label: str,
) -> str:
    """
    Build the preparation-only evening message.
    """

    personal_context = optional(
        "PERSONAL_CONTEXT",
        DEFAULT_PERSONAL_CONTEXT,
    )

    calendar_text = calendar_for_prompt(
        events
    )

    astrology_text = astrology_for_prompt(
        result
    )

    return f"""
Prepare a private next-day preparation message.

TOMORROW
{target_label}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TOMORROW'S CALENDAR
{calendar_text}

TOMORROW'S PERSONALISED ASTROLOGY JSON
{astrology_text}

PURPOSE

This message is read at 8:30 pm.

It must contain only practical preparation for tomorrow.

Do not provide:
- a general horoscope summary;
- a monthly outlook;
- a motivational speech;
- a retrospective discussion.

RULES

- Use only the strongest one to three influences for tomorrow.
- Mention a transit or aspect only when it helps explain why a preparation
  step is useful.
- Calendar events matter only when they genuinely change what should be
  prepared tonight.
- Do not default to work or career.
- If tomorrow's strongest theme concerns relationships, wellbeing, creativity,
  communication, hobbies, motivation or another area, preparation should
  reflect that.
- If tomorrow has no calendar events, prepare the person for the strongest
  astrological theme without inventing commitments.

Do not invent:
- deadlines;
- events;
- travel requirements;
- people;
- arguments;
- health issues;
- financial events;
- guaranteed outcomes.

Useful preparation may include:
- organising something needed tomorrow;
- deciding tomorrow's first priority;
- writing down a thought before a communication-heavy day;
- planning a difficult conversation;
- setting a boundary;
- making room for rest;
- removing a distraction;
- preparing for a social interaction;
- setting aside time for creativity;
- preparing for exercise;
- delaying a reactive decision.

RETURN ONLY THIS FORMAT

Prepare for tomorrow — {target_label}

• [Most useful concrete preparation step.]
• [Second concrete preparation step.]
• [Third step only if genuinely useful.]
• [Fourth step only if strongly supported.]

Keep the message between 60 and 120 words.

Every bullet must tell the person something they can actually do tonight.

Do not add:
- another heading;
- paragraph commentary;
- conclusion;
- emoji;
- hashtag;
- rating;
- disclaimer;
- Markdown bold markers.
""".strip()


# ---------------------------------------------------------
# PROMPT SELECTION
# ---------------------------------------------------------

def build_prompt(
    result: dict[str, Any],
    events: list[dict[str, str]],
    mode: str,
    target_label: str,
) -> str:
    """Build the correct prompt for morning or evening mode."""

    if mode == "evening":
        return build_evening_prompt(
            result,
            events,
            target_label,
        )

    return build_morning_prompt(
        result,
        events,
        target_label,
    )


def required_sections(
    mode: str,
) -> tuple[str, ...]:
    """Return the headings required for each mode."""

    if mode == "evening":
        return (
            "Prepare for tomorrow",
        )

    return (
        "Morning",
        "Planetary picture",
        "Main focus",
        "Your day",
        "Watch for",
        "This month",
    )


# ---------------------------------------------------------
# GEMINI
# ---------------------------------------------------------

def generate_reading(
    result: dict[str, Any],
    events: list[dict[str, str]],
    mode: str,
    target_label: str,
) -> str:
    """Ask Gemini to produce the final message."""

    prompt = build_prompt(
        result,
        events,
        mode,
        target_label,
    )

    client = genai.Client(
        api_key=required(
            "GEMINI_API_KEY"
        )
    )

    model = optional(
        "GEMINI_MODEL",
        DEFAULT_GEMINI_MODEL,
    )

    last_error: Exception | None = None

    for attempt in range(2):

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )

        except Exception as error:

            last_error = error

            if attempt == 0:
                time.sleep(5)
                continue

            raise RuntimeError(
                f"Gemini could not create the "
                f"{mode} reading using '{model}'."
            ) from error

        message = re.sub(
            r"\n{3,}",
            "\n\n",
            (
                response.text
                or ""
            )
            .strip()
            .replace(
                "**",
                "",
            ),
        )

        if not message:

            last_error = RuntimeError(
                "Gemini returned an empty response."
            )

            continue

        missing_sections = [
            section
            for section in required_sections(
                mode
            )
            if (
                section.casefold()
                not in message.casefold()
            )
        ]

        if missing_sections:

            last_error = RuntimeError(
                "Gemini omitted: "
                + ", ".join(
                    missing_sections
                )
            )

            prompt += (
                "\n\nRepeat the answer using every "
                "exact required heading."
            )

            continue

        return message[
            :MAX_TELEGRAM_CHARS
        ]

    raise RuntimeError(
        "Gemini did not return "
        "a usable reading."
    ) from last_error


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram(
    message: str,
) -> None:
    """Send the message through Telegram."""

    response = requests.post(
        (
            "https://api.telegram.org/bot"
            f"{required('TELEGRAM_BOT_TOKEN')}"
            "/sendMessage"
        ),
        json={
            "chat_id": required(
                "TELEGRAM_CHAT_ID"
            ),
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    try:
        result = response.json()

    except ValueError as error:
        raise RuntimeError(
            "Telegram returned invalid data."
        ) from error

    if (
        not response.ok
        or not result.get(
            "ok"
        )
    ):

        description = result.get(
            "description",
            f"HTTP {response.status_code}",
        )

        raise RuntimeError(
            f"Telegram rejected the message: "
            f"{description}"
        )


def send_failure(
    error: Exception,
    mode: str,
) -> None:
    """Attempt to report an automation failure."""

    if not os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip():
        return

    if not os.getenv(
        "TELEGRAM_CHAT_ID",
        "",
    ).strip():
        return

    error_text = (
        str(error)
        .replace(
            "\n",
            " ",
        )[:500]
    )

    try:
        send_telegram(
            f"The {mode} astrology "
            f"automation failed.\n"
            f"{error_text}"
        )

    except Exception:
        pass


# ---------------------------------------------------------
# MAIN PROGRAM
# ---------------------------------------------------------

def main() -> None:
    """Run the morning or evening automation."""

    mode = reading_mode()

    now = datetime.now(
        melbourne_timezone()
    )

    if mode == "evening":

        target_datetime = (
            now
            + timedelta(
                days=1
            )
        )

    else:

        target_datetime = now

    target = target_datetime.date()

    target_string = (
        target.isoformat()
    )

    target_label = date_label(
        target_datetime
    )

    print(
        f"Preparing the {mode} reading "
        f"for {target_string}..."
    )

    astrology = fetch_astrology(
        target_string
    )

    print(
        "FreeAstroAPI returned "
        "the personalised astrology."
    )

    try:

        events = get_calendar_events(
            target
        )

    except Exception as error:

        print(
            "WARNING: Calendar could not be read. "
            "Continuing without events.",
            file=sys.stderr,
        )

        print(
            f"Calendar error: {error}",
            file=sys.stderr,
        )

        events = []

    if events:

        print(
            f"Google Calendar returned "
            f"{len(events)} event(s)."
        )

    else:

        print(
            "No calendar events were found. "
            "Continuing with open-day advice."
        )

    reading = generate_reading(
        astrology,
        events,
        mode,
        target_label,
    )

    if true_or_false(
        "PRINT_READING_TO_LOG",
        False,
    ):

        print(
            "\nGenerated message:\n"
        )

        print(
            reading
        )

        print()

    send_telegram(
        reading
    )

    print(
        f"The {mode} message "
        f"was delivered successfully."
    )


if __name__ == "__main__":

    current_mode = os.getenv(
        "READING_MODE",
        "morning",
    ).strip().casefold()

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nThe program was stopped.",
            file=sys.stderr,
        )

        sys.exit(
            130
        )

    except Exception as error:

        print(
            f"\nERROR: {error}",
            file=sys.stderr,
        )

        send_failure(
            error,
            current_mode,
        )

        sys.exit(
            1
        )