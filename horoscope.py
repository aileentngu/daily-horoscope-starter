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
Write today's personalised horoscope using only the supplied astrology data.

DATE: {target_label} (Australia/Melbourne)
PERSONAL CONTEXT: {personal_context}
ASTROLOGY DATA (source of truth):
{astrology_text}

TODAY'S CALENDAR (use ONLY in the second half; never in planetary listing):
{calendar_text}

DATA INTEGRITY — IMPORTANT
- List ALL distinct planetary positions, movements, sign changes, house entries and
  natal/transit aspects actually supplied in the astrology data, not merely three highlights.
- Name the actual planets, sign, degree, houses, aspect and orb where PROVIDED.
- For each, give precisely ONE plain-language sentence: the affected life area
  according to its VERIFIED natal house and what that transit could signify.
- House meanings may explain verified house values but NEVER infer personal houses
  from zodiac signs, a generic solar chart, or birth details alone.
- Do NOT turn a planet being in a sign into a claim that it is entering a house.
- 'Pluto entering my 1st house in Aquarius' is allowed ONLY when the data shows
  that the house entry occurred; otherwise do not claim it.
- Do not conflate sign ingresses, natal aspects, transiting aspects or house ingresses.
- Dates, exact transit periods, stations, retrogrades and durations must come from
  the source. If absent, write 'duration not supplied' for timing where useful.
- Do not invent any positions, relationships, calendar events, movement dates or outcomes.
- If source data offers only a summary instead of an exhaustive ephemeris,
  explicitly say 'Only transits provided by the source are listed.'
- Astrology is interpretative, not factual prediction; use 'could' rather than
  claiming events will happen.

STYLE AND PRIORITY
- Be clear, compact, practical, matter-of-fact, specific, and use concise dot points.
- Avoid AI clichés ('embrace', 'energy', 'navigate', 'the universe wants', etc.).
- Don't arbitrarily prioritise career, romance, self-improvement or study.
- Let the strongest VERIFIED astrological influences determine the life areas.
- Explain dominance by supported reasons (exact aspect, orb, personal house,
  slow planet or repeated emphasis), not unsupported rankings.
- Tell me how long influences might last ONLY when dates or periods are provided.
- Explain an actionable way to use supportive themes or handle hard ones.
- Do not sacrifice comprehensiveness to meet an arbitrary word count.

RETURN THIS EXACT ORDER AND THESE HEADINGS

Morning — {target_label}

Planetary movements and positions
• [EVERY distinct source-supported planet placement, house movement or transit;
  verified specifics, then one interpretation sentence and affected life area.]

Aspects and personal transits
• [EVERY distinct source-supported aspect to a natal planet or significant point;
  verified specifics and one sentence explaining its personal significance.]

What's dominant today
• [Name the most consequential supported influences, WHY they are dominant,
  relevant houses/life areas and their supplied timing or 'duration not supplied'.]

How to use it
• [Concrete action for important supportive or challenging themes grounded in
  above data, without generic motivational advice.]

Your calendar and practical plan
• [ONLY HERE mention actual calendar commitments and their verified times;
  match specific appropriate transit themes to them without forcing connections.]
• [If no event is supplied, offer a useful open-day plan without inventing one.]

Five fun things to try today
• [One easy, enjoyable activity, explicitly linked to an influence discussed above.]
• [A second distinct activity, grounded in a discussed influence.]
• [A third distinct activity, grounded in a discussed influence.]
• [A fourth distinct activity, grounded in a discussed influence.]
• [A fifth distinct activity, grounded in a discussed influence.]

RULES FOR THE FIVE ACTIVITIES
- Exactly FIVE, and this MUST be the very last section.
- Specific, varied, enjoyable, and easy to do with minimal money or preparation.
- Briefly explain which ALREADY-LISTED verified transit/house theme makes each apt.
- Never add random suggestions just to fill a quota; use verified broad influences
  if source detail is limited, without mislabelling them as transits.
- Do not repeat calendar items as though they were planetary movements.
- No opening preamble, invented precision, promises or fluff.
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
Write a personalised evening reading to prepare for TOMORROW.

TOMORROW: {target_label} (Australia/Melbourne)
PERSONAL CONTEXT: {personal_context}
TOMORROW'S VERIFIED ASTROLOGY DATA:
{astrology_text}
TOMORROW'S CALENDAR (reserved for SECOND HALF only):
{calendar_text}

DATA ACCURACY
- Name ALL distinct source-supported planetary movements, placements, transits,
  natal aspects and house changes in concise bullets; no cherry-picking.
- For each, clearly state VERIFIED planet/sign/house/aspect specifics followed by
  one plain-language interpretation about the relevant house/life area.
- Never invent natal houses or house ingress from signs alone; an example such
  as Pluto entering the 1st house is allowed ONLY if explicitly established.
- Give duration or entry/exit times only where present in the data; otherwise
  say 'duration not supplied' if timing needs clarification.
- Distinguish dominant influences using supported factors (precision, personal
  house, repetitions, or slow-moving transit), with advice for benefits/risks.
- Make no unsupported claims or predictions. Treat astrology as reflective guidance.
- Source may not include ALL astronomical transits; if so say only available ones
  are listed. Never pretend an incomplete list is exhaustive.
- Keep calendar events ENTIRELY out of the planetary listing and interpretation.
- Bring actual events/times into the second half only and connect them to
  specific, already-listed transits when useful, not by force.
- No assumed focus on work, romance, productivity or any fixed life area.
- Use short, helpful bullets with concrete actions and no vague astrology jargon.

RETURN EXACTLY THESE HEADINGS AND THIS ORDER

Prepare for tomorrow — {target_label}

Tomorrow's planetary movements and positions
• [List EVERY separate source-supported movement/position; its verified
  planet/sign/house and ONE sentence interpretation/house life area.]

Tomorrow's aspects and personal transits
• [List EVERY distinct source-supported aspect or natal transit and its verified
  particulars and one sentence interpretation.]

What will matter most
• [Strongest source-supported influences, why, affected areas, duration if known,
  and precise advice for possible benefits/difficulties.]

Tomorrow's calendar and practical plan
• [Mention actual calendar items/times ONLY HERE where they affect preparation,
  with concrete actions linked to applicable VERIFIED transits.]
• [If no scheduled items, do not invent any; use an open morning plan.]

Two enjoyable things to do tonight
• [Exactly one specific enjoyable 5–30 minute activity before/during bedtime,
  grounded in a named VERIFIED influence from above, and if relevant a real
  tomorrow calendar event; explain briefly why it could help tomorrow.]
• [A SECOND distinct, pleasant, low-effort bedtime activity with a similarly
  specific supported link to tomorrow. No tedious chores.]

Two things to do early tomorrow morning
• [Exactly one specific, enjoyable, realistic morning action BEFORE the first
  real calendar event where applicable; link it to a named VERIFIED transit,
  and to a real calendar commitment if genuinely relevant.]
• [A SECOND distinct, manageable morning action linked to another relevant
  transit or supported theme and real schedule when appropriate.]

FINAL FOUR ACTIVITIES RULES
- Exactly TWO tonight and TWO tomorrow morning. This MUST end the message.
- No five-activity ending here; that is MORNING ONLY.
- Every one of the four must be easy and meaningful, not random or expensive.
- Avoid invented commute times or claiming the user can fit an activity
  before an early appointment if its available time is unknown.
- If verified astrological detail is sparse, ground suggestions in the available
  themes without fabricating degrees, houses or transit labels.
- Do not repeat the same activity or recommend generic self-help slogans.
- Use concise dot points. No added headings, introductory or closing paragraphs.
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


def required_sections(mode: str) -> tuple[str, ...]:
    """Headings Gemini must include in each reading."""
    if mode == "evening":
        return (
            "Prepare for tomorrow",
            "Tomorrow's planetary movements and positions",
            "Tomorrow's aspects and personal transits",
            "What will matter most",
            "Tomorrow's calendar and practical plan",
            "Two enjoyable things to do tonight",
            "Two things to do early tomorrow morning",
        )
    return (
        "Morning",
        "Planetary movements and positions",
        "Aspects and personal transits",
        "What's dominant today",
        "How to use it",
        "Your calendar and practical plan",
        "Five fun things to try today",
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

        return message

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



def send_long_telegram(message: str) -> None:
    """Send the entire reading as Telegram-sized pieces, never truncate it."""
    remaining = message.strip()
    while remaining:
        if len(remaining) <= MAX_TELEGRAM_CHARS:
            part, remaining = remaining, ""
        else:
            cut = remaining.rfind("\n", 0, MAX_TELEGRAM_CHARS)
            if cut < MAX_TELEGRAM_CHARS // 2:
                cut = MAX_TELEGRAM_CHARS
            part = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip("\n")
        send_telegram(part)


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

    send_long_telegram(reading)

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