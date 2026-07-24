import json
import os
import re
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
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
ENV_FILE = BASE_DIR / ".env"

FREEASTRO_ENDPOINT = (
    "https://api.freeastroapi.com/api/v3/horoscope/daily/personal"
)

MELBOURNE_TIMEZONE = "Australia/Melbourne"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

MAX_ASTROLOGY_JSON_CHARACTERS = 50_000
MAX_TELEGRAM_CHARACTERS = 3_800
DEFAULT_MAX_CALENDAR_EVENTS = 20

DEFAULT_PERSONAL_CONTEXT = """
The recipient is a fourth-year mechatronics engineering student in Melbourne.

They are balancing university coursework, a final-year project, internship and
graduate applications, technical skill development, part-time work, finances,
friendships, family, relationships, health and their general routine.

They are ambitious and respond best to direct, practical guidance. They want
to know where to direct their attention, what action to take, when to wait,
how to communicate and what mistakes to avoid.

Only connect the astrology to areas of their life genuinely supported by the
astrological source and their actual calendar. Do not force every part of their
life into each reading.
""".strip()

load_dotenv(ENV_FILE)


# ---------------------------------------------------------
# ENVIRONMENT SETTINGS
# ---------------------------------------------------------

def required_setting(name: str) -> str:
    """Return a required environment setting."""

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to your .env file or GitHub Secrets."
        )

    return value


def optional_setting(name: str, default: str = "") -> str:
    """Return an optional environment setting."""

    value = os.getenv(name, "").strip()
    return value if value else default


def integer_setting(
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Read and validate a whole-number setting."""

    raw_value = required_setting(name)

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


def optional_integer_setting(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read an optional whole-number setting."""

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


def boolean_setting(name: str, default: bool) -> bool:
    """Read a true-or-false environment setting."""

    raw_value = os.getenv(name, "").strip().casefold()

    if not raw_value:
        return default

    if raw_value in {"true", "1", "yes", "y"}:
        return True

    if raw_value in {"false", "0", "no", "n"}:
        return False

    raise RuntimeError(
        f"{name} must be true or false. Received: {raw_value}"
    )


def reading_mode() -> str:
    """Return either morning or evening."""

    mode = optional_setting(
        "READING_MODE",
        "morning",
    ).casefold()

    if mode not in {"morning", "evening"}:
        raise RuntimeError(
            "READING_MODE must be either morning or evening."
        )

    return mode


# ---------------------------------------------------------
# DATE AND TIME
# ---------------------------------------------------------

def melbourne_timezone() -> ZoneInfo:
    """Return the Melbourne time-zone object."""

    try:
        return ZoneInfo(MELBOURNE_TIMEZONE)

    except ZoneInfoNotFoundError as error:
        raise RuntimeError(
            "Australia/Melbourne could not be loaded. "
            "Install the tzdata package from requirements.txt."
        ) from error


def melbourne_now() -> datetime:
    """Return the current Melbourne date and time."""

    return datetime.now(
        melbourne_timezone()
    )


def formatted_date(value: datetime) -> str:
    """Format a date without a leading zero."""

    return (
        f"{value.strftime('%A')}, "
        f"{value.day} "
        f"{value.strftime('%B %Y')}"
    )


def determine_target_datetime(
    mode: str,
    now: datetime,
) -> datetime:
    """Use today in the morning and tomorrow in the evening."""

    if mode == "evening":
        return now + timedelta(days=1)

    return now


def format_clock_time(value: datetime) -> str:
    """Format a time as 7:30 am rather than 07:30 AM."""

    hour = value.strftime("%I").lstrip("0") or "12"

    return (
        f"{hour}:"
        f"{value.strftime('%M')} "
        f"{value.strftime('%p').lower()}"
    )


# ---------------------------------------------------------
# BIRTH DETAILS
# ---------------------------------------------------------

def build_birth_details() -> dict:
    """Build the birth-information object sent to FreeAstroAPI."""

    return {
        "year": integer_setting(
            "BIRTH_YEAR",
            1800,
            2200,
        ),
        "month": integer_setting(
            "BIRTH_MONTH",
            1,
            12,
        ),
        "day": integer_setting(
            "BIRTH_DAY",
            1,
            31,
        ),
        "hour": integer_setting(
            "BIRTH_HOUR",
            0,
            23,
        ),
        "minute": integer_setting(
            "BIRTH_MINUTE",
            0,
            59,
        ),
        "city": required_setting(
            "BIRTH_CITY"
        ),
        "tz_str": required_setting(
            "BIRTH_TIMEZONE"
        ),
        "time_known": boolean_setting(
            "BIRTH_TIME_KNOWN",
            default=True,
        ),
    }


# ---------------------------------------------------------
# FREEASTROAPI
# ---------------------------------------------------------

def fetch_personal_horoscope(
    target_date: str,
) -> dict:
    """Request the personalised horoscope for a specified date."""

    api_key = required_setting(
        "FREEASTRO_API_KEY"
    )

    payload = {
        "birth": build_birth_details(),
        "date": target_date,
        "tz_str": MELBOURNE_TIMEZONE,
        "include_interpretation_blocks": True,
    }

    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = requests.post(
                FREEASTRO_ENDPOINT,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                json=payload,
                timeout=90,
            )

        except requests.RequestException as error:
            last_error = error

            if attempt < 3:
                time.sleep(5)
                continue

            raise RuntimeError(
                "FreeAstroAPI could not be reached after three attempts."
            ) from error

        if response.status_code in {401, 403}:
            raise RuntimeError(
                "FreeAstroAPI rejected FREEASTRO_API_KEY."
            )

        if response.status_code == 429:
            raise RuntimeError(
                "The FreeAstroAPI request allowance has been reached."
            )

        if response.status_code >= 500 and attempt < 3:
            time.sleep(5)
            continue

        if not response.ok:
            preview = response.text[:700].replace(
                "\n",
                " ",
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

        if not isinstance(data, dict):
            raise RuntimeError(
                "FreeAstroAPI did not return the expected "
                "personal horoscope data."
            )

        returned_date = str(
            data.get("date", "")
        ).strip()

        if returned_date and returned_date != target_date:
            raise RuntimeError(
                "FreeAstroAPI returned the wrong date. "
                f"Requested {target_date}, received {returned_date}."
            )

        return result

    raise RuntimeError(
        "FreeAstroAPI failed unexpectedly."
    ) from last_error


# ---------------------------------------------------------
# GOOGLE CALENDAR ICAL
# ---------------------------------------------------------

def fetch_calendar_bytes() -> bytes:
    """Download the private read-only Google Calendar iCal feed."""

    calendar_url = required_setting(
        "GOOGLE_CALENDAR_ICAL_URL"
    )

    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                calendar_url,
                timeout=45,
            )

        except requests.RequestException as error:
            last_error = error

            if attempt < 3:
                time.sleep(5)
                continue

            raise RuntimeError(
                "Google Calendar could not be reached "
                "after three attempts."
            ) from error

        if response.status_code in {
            401,
            403,
            404,
            410,
        }:
            raise RuntimeError(
                "Google Calendar rejected the private iCal address. "
                "Copy a fresh Secret address in iCal format and "
                "update GOOGLE_CALENDAR_ICAL_URL."
            )

        if response.status_code >= 500 and attempt < 3:
            time.sleep(5)
            continue

        if not response.ok:
            preview = response.text[:300].replace(
                "\n",
                " ",
            )

            raise RuntimeError(
                f"Google Calendar returned HTTP "
                f"{response.status_code}: {preview}"
            )

        if b"BEGIN:VCALENDAR" not in response.content[:5000]:
            raise RuntimeError(
                "The Google Calendar address did not return "
                "an iCalendar feed."
            )

        return response.content

    raise RuntimeError(
        "Google Calendar failed unexpectedly."
    ) from last_error


def clean_event_text(
    value: object,
    maximum_length: int = 140,
) -> str:
    """Clean text taken from a calendar event."""

    if value is None:
        return ""

    text = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    if len(text) > maximum_length:
        text = (
            text[: maximum_length - 1].rstrip()
            + "…"
        )

    return text


def normalise_datetime(
    value: datetime,
    timezone: ZoneInfo,
) -> datetime:
    """Convert an event datetime to Melbourne time."""

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone
        )

    return value.astimezone(
        timezone
    )


def decoded_event_value(
    event,
    property_name: str,
):
    """Decode an iCalendar property if it exists."""

    property_value = event.get(
        property_name
    )

    if property_value is None:
        return None

    try:
        return event.decoded(
            property_name
        )

    except Exception:
        return getattr(
            property_value,
            "dt",
            property_value,
        )


def event_time_details(
    event,
    target_date: date,
    timezone: ZoneInfo,
) -> tuple[str, datetime]:
    """Create the displayed time and sorting time for one event."""

    start_value = decoded_event_value(
        event,
        "DTSTART",
    )

    if start_value is None:
        raise ValueError(
            "Calendar event has no DTSTART."
        )

    end_value = decoded_event_value(
        event,
        "DTEND",
    )

    if end_value is None:
        duration = decoded_event_value(
            event,
            "DURATION",
        )

        if duration is not None:
            end_value = start_value + duration

        elif isinstance(start_value, datetime):
            end_value = (
                start_value
                + timedelta(hours=1)
            )

        else:
            end_value = (
                start_value
                + timedelta(days=1)
            )

    if isinstance(start_value, datetime):
        local_start = normalise_datetime(
            start_value,
            timezone,
        )

        if isinstance(end_value, datetime):
            local_end = normalise_datetime(
                end_value,
                timezone,
            )
        else:
            local_end = (
                local_start
                + timedelta(hours=1)
            )

        start_text = format_clock_time(
            local_start
        )
        end_text = format_clock_time(
            local_end
        )

        if local_end > local_start:
            time_text = (
                f"{start_text}–{end_text}"
            )
        else:
            time_text = start_text

        return time_text, local_start

    all_day_sort = datetime.combine(
        target_date,
        dt_time.min,
        tzinfo=timezone,
    )

    return "All day", all_day_sort

def extract_calendar_events(
    target_date: date,
) -> list[dict[str, str]]:
    """
    Return the target day's events, including recurring events.

    An empty calendar or a day with no events returns an empty list.
    It is not treated as an error.
    """

    calendar_bytes = fetch_calendar_bytes()

    try:
        calendar = Calendar.from_ical(
            calendar_bytes
        )

    except Exception as error:
        raise RuntimeError(
            "The Google Calendar iCal feed could not be parsed."
        ) from error

    # Some completely empty calendars contain no VEVENT components.
    # recurring_ical_events can raise IndexError in this situation,
    # so return an ordinary empty list before querying it.
    calendar_components = [
        component
        for component in calendar.walk()
        if getattr(component, "name", "") == "VEVENT"
    ]

    if not calendar_components:
        print(
            "Google Calendar contains no events. "
            "Continuing with an open-day reading."
        )

        return []

    timezone = melbourne_timezone()

    day_start = datetime.combine(
        target_date,
        dt_time.min,
        tzinfo=timezone,
    )

    day_end = day_start + timedelta(days=1)

    try:
        occurrences = recurring_ical_events.of(
            calendar
        ).between(
            day_start,
            day_end,
        )

    except IndexError:
        # This can occur when the calendar contains no usable events.
        return []

    except Exception as error:
        raise RuntimeError(
            "Recurring calendar events could not be expanded."
        ) from error

    # A date with no matching events is normal.
    if not occurrences:
        return []

    include_locations = boolean_setting(
        "INCLUDE_EVENT_LOCATIONS",
        default=False,
    )

    maximum_events = optional_integer_setting(
        "MAX_CALENDAR_EVENTS",
        default=DEFAULT_MAX_CALENDAR_EVENTS,
        minimum=1,
        maximum=50,
    )

    extracted: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()

    for event in occurrences:
        status = clean_event_text(
            event.get("STATUS")
        ).casefold()

        if status == "cancelled":
            continue

        title = clean_event_text(
            event.get("SUMMARY"),
            maximum_length=160,
        )

        if not title:
            title = "Untitled event"

        try:
            time_text, sort_time = event_time_details(
                event,
                target_date,
                timezone,
            )

        except (TypeError, ValueError):
            continue

        uid = clean_event_text(
            event.get("UID"),
            maximum_length=200,
        )

        duplicate_key = (
            uid,
            sort_time.isoformat(),
            title,
        )

        if duplicate_key in seen:
            continue

        seen.add(
            duplicate_key
        )

        record: dict[str, object] = {
            "time": time_text,
            "title": title,
            "_sort_time": sort_time,
        }

        if include_locations:
            location = clean_event_text(
                event.get("LOCATION"),
                maximum_length=140,
            )

            if location:
                record["location"] = location

        extracted.append(
            record
        )

    extracted.sort(
        key=lambda item: (
            item["_sort_time"],
            str(item["title"]).casefold(),
        )
    )

    visible_events: list[dict[str, str]] = []

    for record in extracted[:maximum_events]:
        visible_event = {
            "time": str(record["time"]),
            "title": str(record["title"]),
        }

        if "location" in record:
            visible_event["location"] = str(
                record["location"]
            )

        visible_events.append(
            visible_event
        )

    return visible_events

def calendar_source_text(
    events: list[dict[str, str]],
) -> str:
    """
    Create a calendar summary for Gemini.

    A day without events is explicitly described as an open day.
    """

    if not events:
        return (
            "There are no scheduled calendar events for this date. "
            "Treat this as an open day. Give practical advice about how "
            "to structure the available time, but do not invent meetings, "
            "appointments, shifts, classes, deadlines or commitments."
        )

    lines = []

    for event in events:
        line = (
            f"- {event['time']}: "
            f"{event['title']}"
        )

        if event.get("location"):
            line += (
                f" — Location: "
                f"{event['location']}"
            )

        lines.append(line)

    return "\n".join(lines)
# ---------------------------------------------------------
# ASTROLOGY SOURCE
# ---------------------------------------------------------

def astrology_source_text(
    api_result: dict,
) -> str:
    """Convert the astrology response into readable JSON."""

    data = api_result.get(
        "data",
        api_result,
    )

    source = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    return source[
        :MAX_ASTROLOGY_JSON_CHARACTERS
    ]


# ---------------------------------------------------------
# MORNING PROMPT
# ---------------------------------------------------------

def build_morning_prompt(
    astrology_text: str,
    calendar_text: str,
    date_text: str,
    personal_context: str,
) -> str:
    """Create the morning calendar-aware astrology prompt."""

    return f"""
Prepare a private morning astrology and calendar message for one person.

DATE
{date_text}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TODAY'S CALENDAR
{calendar_text}

ASTROLOGY SOURCE
The JSON below contains the person's personalised astrology for today,
including natal transits, aspects, interpretation blocks, timing information,
scores, themes and longer-running influences.

{astrology_text}

PURPOSE
This message is read at 7:30 am. Combine the astrology with the actual calendar
to give a realistic plan for today.

SOURCE RULES
- Treat the calendar as factual.
- Treat the astrology as reflective guidance, not certainty.
- Preserve the exact names of the strongest one to three astrological
  influences.
- Mention calendar event titles exactly as supplied.
- Do not invent event details, preparation requirements, attendees, outcomes,
  deadlines or travel times.
- Do not claim that a transit guarantees what will happen at an event.
- If no events are scheduled, use that open time constructively rather than
  inventing commitments.

ADVICE
Give specific advice about:
- which scheduled event deserves preparation;
- where a gap in the calendar should be used for focused work or recovery;
- whether a meeting, class, shift or appointment requires patience,
  confidence, restraint or directness;
- the most useful task to complete before or after an event;
- what behaviour is likely to undermine the day.

Use the person's actual event titles. Explain why the relevant transit makes a
particular approach sensible, but do not overstate astrology.

STYLE
Write like a perceptive friend who knows the person well. Be calm, formal,
natural, direct and practical. Do not sound like an AI summary, life coach,
advertisement or mystical prediction.

Do not use phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- stay positive;
- the universe is telling you;
- step into your power;
- navigate these energies;
- balance is key.

OUTPUT FORMAT

Return only the finished message in this exact structure:

Morning — {date_text}

Direction
Write one paragraph of three to five sentences. Name the principal transit,
explain it briefly and connect it to today's most important events or open
work periods.

Before your first commitment
• Give one specific preparation step.
• Give a second point only if useful.

Plan for today
• Name the highest-priority event or task and state what to do.
• Give one practical use for a gap between events, or for open time.
• Add a third point only if strongly supported.

Avoid
• Give one specific reaction, distraction or decision to avoid.
• Give a second point only if strongly supported.

Keep in mind
Write one firm, encouraging sentence that is specific to today's schedule.

Keep the complete response between 160 and 240 words. Do not add emojis,
hashtags, lucky numbers, ratings, disclaimers, Markdown bold markers, an
introduction or a conclusion.
""".strip()


# ---------------------------------------------------------
# EVENING PROMPT
# ---------------------------------------------------------

def build_evening_prompt(
    astrology_text: str,
    calendar_text: str,
    date_text: str,
    personal_context: str,
) -> str:
    """Create the next-day calendar-aware astrology prompt."""

    return f"""
Prepare a private evening astrology and calendar message for one person.

TOMORROW'S DATE
{date_text}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TOMORROW'S CALENDAR
{calendar_text}

ASTROLOGY SOURCE
The JSON below contains the person's personalised astrology for tomorrow,
including natal transits, aspects, interpretation blocks, timing information,
scores, themes and longer-running influences.

{astrology_text}

PURPOSE
This message is read at 8:30 pm. Explain tomorrow's principal astrology and use
the actual calendar to tell the person what to organise tonight and how to
approach tomorrow.

SOURCE RULES
- Treat the calendar as factual.
- Treat the astrology as reflective guidance, not certainty.
- Preserve the exact names of the strongest one to three astrological
  influences.
- Mention calendar event titles exactly as supplied.
- Do not invent event details, attendees, deadlines, preparation materials,
  travel times or outcomes.
- Do not claim that a transit guarantees what will happen at an event.
- If no events are scheduled, identify a sensible focus for the open day
  without inventing commitments.

ADVICE
Give specific advice about:
- what should be prepared tonight for the earliest or most important event;
- which event tomorrow deserves the most mental attention;
- how to use gaps between commitments;
- which communication style or emotional reaction is most useful;
- what should be postponed or avoided tomorrow.

STYLE
Write like a perceptive friend who knows the person well. Be calm, formal,
natural, direct and practical. Do not sound like an AI summary, life coach,
advertisement or mystical prediction.

Do not use phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- stay positive;
- the universe is telling you;
- step into your power;
- navigate these energies;
- balance is key.

OUTPUT FORMAT

Return only the finished message in this exact structure:

Tomorrow — {date_text}

Outlook
Write one paragraph of three to five sentences. Name the principal transit,
explain it briefly and connect it to tomorrow's actual commitments.

Prepare tonight
• Give one concrete preparation step for a named event.
• Give a second preparation step only if useful.

Tomorrow's plan
• Name the highest-priority event or task and state the best approach.
• Explain how to use a gap between events, or open time.
• Add a third point only if strongly supported.

Avoid tomorrow
• Give one specific reaction, distraction or decision to avoid.
• Give a second point only if strongly supported.

Keep the complete response between 170 and 250 words. Do not add emojis,
hashtags, lucky numbers, ratings, disclaimers, Markdown bold markers, an
introduction or a conclusion.
""".strip()


# ---------------------------------------------------------
# GEMINI
# ---------------------------------------------------------

def clean_generated_message(
    message: str,
) -> str:
    """Clean minor formatting errors."""

    message = message.strip()
    message = message.replace(
        "**",
        "",
    )
    message = re.sub(
        r"\n{3,}",
        "\n\n",
        message,
    )

    return message


def expected_sections(
    mode: str,
) -> tuple[str, ...]:
    """Return the required headings for each message."""

    if mode == "evening":
        return (
            "Tomorrow",
            "Outlook",
            "Prepare tonight",
            "Tomorrow's plan",
            "Avoid tomorrow",
        )

    return (
        "Morning",
        "Direction",
        "Before your first commitment",
        "Plan for today",
        "Avoid",
        "Keep in mind",
    )


def message_has_required_sections(
    message: str,
    mode: str,
) -> bool:
    """Check that Gemini followed the requested format."""

    lowered = message.casefold()

    return all(
        section.casefold() in lowered
        for section in expected_sections(mode)
    )


def generate_reading(
    api_result: dict,
    calendar_events: list[dict[str, str]],
    mode: str,
    date_text: str,
) -> str:
    """Generate the calendar-aware morning or evening message."""

    gemini_key = required_setting(
        "GEMINI_API_KEY"
    )

    model_name = optional_setting(
        "GEMINI_MODEL",
        DEFAULT_GEMINI_MODEL,
    )

    personal_context = optional_setting(
        "PERSONAL_CONTEXT",
        DEFAULT_PERSONAL_CONTEXT,
    )

    astrology_text = astrology_source_text(
        api_result
    )

    calendar_text = calendar_source_text(
        calendar_events
    )

    if mode == "evening":
        prompt = build_evening_prompt(
            astrology_text=astrology_text,
            calendar_text=calendar_text,
            date_text=date_text,
            personal_context=personal_context,
        )

    else:
        prompt = build_morning_prompt(
            astrology_text=astrology_text,
            calendar_text=calendar_text,
            date_text=date_text,
            personal_context=personal_context,
        )

    client = genai.Client(
        api_key=gemini_key
    )

    last_error: Exception | None = None

    for attempt in range(1, 3):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

        except Exception as error:
            last_error = error

            if attempt < 2:
                time.sleep(5)
                continue

            raise RuntimeError(
                f"Gemini could not create the {mode} reading "
                f"using '{model_name}'."
            ) from error

        message = clean_generated_message(
            response.text or ""
        )

        if not message:
            last_error = RuntimeError(
                "Gemini returned an empty response."
            )
            continue

        if not message_has_required_sections(
            message,
            mode,
        ):
            last_error = RuntimeError(
                "Gemini omitted one or more required headings."
            )

            prompt += (
                "\n\nRepeat the task using every exact "
                "required heading."
            )
            continue

        return message[
            :MAX_TELEGRAM_CHARACTERS
        ]

    raise RuntimeError(
        "Gemini did not return a usable reading."
    ) from last_error


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram(
    message: str,
) -> None:
    """Send the completed message through Telegram."""

    bot_token = required_setting(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = required_setting(
        "TELEGRAM_CHAT_ID"
    )

    endpoint = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            response = requests.post(
                endpoint,
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )

            response.raise_for_status()
            result = response.json()

        except (
            requests.RequestException,
            ValueError,
        ) as error:
            last_error = error

            if attempt < 3:
                time.sleep(5)
                continue

            raise RuntimeError(
                "Telegram could not be reached "
                "after three attempts."
            ) from error

        if not result.get("ok"):
            description = result.get(
                "description",
                "Unknown Telegram error",
            )

            raise RuntimeError(
                f"Telegram rejected the message: "
                f"{description}"
            )

        return

    raise RuntimeError(
        "Telegram delivery failed."
    ) from last_error


def send_failure_message(
    error: Exception,
    mode: str,
) -> None:
    """Attempt to report an automation failure through Telegram."""

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

    safe_error = (
        str(error)
        .replace("\n", " ")[:500]
    )

    try:
        send_telegram(
            f"The {mode} astrology and calendar "
            f"automation failed.\n{safe_error}"
        )

    except Exception:
        pass


# ---------------------------------------------------------
# MAIN PROGRAM
# ---------------------------------------------------------

# ---------------------------------------------------------
# MAIN PROGRAM
# ---------------------------------------------------------

def main() -> None:
    """Run the morning or evening automation."""

    mode = reading_mode()
    now = melbourne_now()

    # Morning mode uses today.
    # Evening mode uses tomorrow.
    target_datetime = determine_target_datetime(
        mode=mode,
        now=now,
    )

    target_date = target_datetime.date()
    target_date_string = target_date.isoformat()
    date_text = formatted_date(target_datetime)

    print(
        f"Preparing the {mode} reading "
        f"for {target_date_string}..."
    )

    # -----------------------------------------------------
    # GET THE ASTROLOGY
    # -----------------------------------------------------

    api_result = fetch_personal_horoscope(
        target_date_string
    )

    print(
        "FreeAstroAPI returned the personalised "
        "astrology successfully."
    )

    # -----------------------------------------------------
    # GET THE CALENDAR EVENTS
    # -----------------------------------------------------

    try:
        calendar_events = extract_calendar_events(
            target_date
        )

    except Exception as calendar_error:
        print(
            "WARNING: Google Calendar could not be read. "
            "The horoscope will continue without calendar events.",
            file=sys.stderr,
        )

        print(
            f"Calendar error: {calendar_error}",
            file=sys.stderr,
        )

        calendar_events = []

    if calendar_events:
        print(
            f"Google Calendar returned "
            f"{len(calendar_events)} event(s) "
            f"for the target date."
        )

    else:
        print(
            "No calendar events were found for the target date. "
            "Continuing with open-day advice."
        )

    # -----------------------------------------------------
    # CREATE THE MESSAGE
    # -----------------------------------------------------

    reading = generate_reading(
        api_result=api_result,
        calendar_events=calendar_events,
        mode=mode,
        date_text=date_text,
    )

    if boolean_setting(
        "PRINT_READING_TO_LOG",
        default=False,
    ):
        print()
        print("Generated message:")
        print()
        print(reading)
        print()

    # -----------------------------------------------------
    # SEND THROUGH TELEGRAM
    # -----------------------------------------------------

    send_telegram(
        reading
    )

    print(
        f"The {mode} astrology and calendar "
        "message was delivered successfully."
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

        sys.exit(130)

    except Exception as error:
        print(
            f"\nERROR: {error}",
            file=sys.stderr,
        )

        send_failure_message(
            error=error,
            mode=current_mode,
        )

        sys.exit(1)