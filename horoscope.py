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
The recipient is a fourth-year mechatronics engineering student in Melbourne.

They are balancing university, a final-year project, internship and graduate
applications, technical development, part-time work, finances, wellbeing,
friendships, family, relationships, hobbies, creativity and their general
routine.

They prefer concise and practical advice. Only connect the astrology to an area
of life when the astrology or calendar information genuinely supports it. Do
not mention career, relationships, wellbeing, hobbies or motivation merely to
cover every category.
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
            f"{name} must be a whole number. "
            f"Received: {raw_value}"
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
            f"{name} must be a whole number. "
            f"Received: {raw_value}"
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
        f"{name} must be true or false. "
        f"Received: {raw_value}"
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
    """Return Melbourne's time zone."""

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
            "FreeAstroAPI did not return "
            "the expected data."
        )

    returned_date = str(
        data.get("date", "")
    ).strip()

    if (
        returned_date
        and returned_date != target_date
    ):
        raise RuntimeError(
            f"FreeAstroAPI returned {returned_date}, "
            f"but {target_date} was requested."
        )

    return result


# ---------------------------------------------------------
# GOOGLE CALENDAR
# ---------------------------------------------------------

def download_calendar() -> Calendar | None:
    """
    Download the Google Calendar iCal feed.

    A missing calendar URL is allowed and is treated as an empty calendar.
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
            "The calendar address did not return "
            "an iCalendar feed."
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
        cleaned[: limit - 1].rstrip()
        + "…"
    )


def decoded(
    event: Any,
    name: str,
) -> Any:
    """Decode an iCalendar event property."""

    value = event.get(name)

    if value is None:
        return None

    try:
        return event.decoded(name)

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

    if isinstance(start, datetime):
        start_local = to_melbourne(
            start,
            timezone,
        )

        if isinstance(end, datetime):
            end_local = to_melbourne(
                end,
                timezone,
            )

        else:
            end_local = (
                start_local
                + timedelta(hours=1)
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
        + timedelta(days=1)
    )

    try:
        occurrences = list(
            recurring_ical_events
            .of(calendar)
            .between(start, end)
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

    records: list[dict[str, Any]] = []
    seen: set[
        tuple[str, str, str]
    ] = set()

    for event in occurrences:
        status = clean_text(
            event.get("STATUS")
        ).casefold()

        if status == "cancelled":
            continue

        title = (
            clean_text(
                event.get("SUMMARY")
            )
            or "Untitled event"
        )

        try:
            displayed_time, sort_value = event_time(
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
                event.get("UID"),
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

        record: dict[str, Any] = {
            "time": displayed_time,
            "title": title,
            "_sort": sort_value,
        }

        if include_locations:
            location = clean_text(
                event.get("LOCATION"),
                140,
            )

            if location:
                record["location"] = location

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

    for record in records[:maximum_events]:
        event_output = {
            "time": str(
                record["time"]
            ),
            "title": str(
                record["title"]
            ),
        }

        if record.get("location"):
            event_output["location"] = str(
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
            "Suggest a sensible structure for the "
            "available time, but do not invent "
            "commitments."
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
    )[:MAX_ASTROLOGY_CHARS]


# ---------------------------------------------------------
# GEMINI PROMPTS
# ---------------------------------------------------------

def build_prompt(
    result: dict[str, Any],
    events: list[dict[str, str]],
    mode: str,
    target_label: str,
) -> str:
    """Build either the morning or evening prompt."""

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

    if mode == "evening":
        return f"""
Prepare a private next-day preparation message for one person.

TOMORROW
{target_label}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TOMORROW'S CALENDAR
{calendar_text}

TOMORROW'S PERSONALISED ASTROLOGY JSON
{astrology_text}

PURPOSE
This message is read at 8:30 pm. It must contain only practical preparation
for tomorrow. Do not provide a general horoscope summary, motivational speech,
monthly outlook, retrospective discussion or broad life advice.

SELECTION RULES
- Use only the strongest one to three astrological influences.
- Preserve the exact transit or aspect names when they are useful.
- Include a calendar event only when it materially changes the preparation.
- Do not mention every event or every life area merely to appear comprehensive.
- Select only the advice that is highly relevant to tomorrow's horoscope.
- If tomorrow has no events, prepare the person for an open day without
  inventing commitments.
- Treat the calendar as factual and astrology as reflective guidance.
- Do not invent events, deadlines, attendees, travel times, outcomes, health
  problems, arguments, job offers or financial results.

WHAT COUNTS AS USEFUL PREPARATION
Examples include preparing documents, setting out equipment, writing the first
task, deciding the order of work, planning how to approach a conversation,
setting a boundary, scheduling rest, removing a distraction, postponing a
reactive message or protecting a focused work period. Use only examples
supported by the source.

STYLE
Write like a perceptive friend: concise, direct, formal but natural. Avoid
generic AI language, mystical claims and filler. Do not use phrases such as
“trust the process”, “embrace change”, “protect your energy”, “stay positive”,
“step into your power” or “balance is key”.

RETURN ONLY THIS FORMAT

Prepare for tomorrow — {target_label}

• [Concrete preparation step tied to the strongest relevant transit.]
• [Second concrete preparation step.]
• [Third concrete preparation step.]
• [A fourth or fifth bullet only if it is genuinely useful.]

Each bullet must tell the person what to do tonight. Where useful, name the
relevant transit or tomorrow's event within the bullet. Keep the complete
message between 70 and 130 words. Do not add any other heading, paragraph,
conclusion, emoji, hashtag, rating, disclaimer or Markdown bold markers.
""".strip()

    return f"""
Prepare a concise private morning astrology-and-calendar message for one person.

TODAY
{target_label}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

TODAY'S CALENDAR
{calendar_text}

TODAY'S PERSONALISED ASTROLOGY JSON
{astrology_text}

PURPOSE
This message is read at 7:30 am. It must give specific, useful direction for
today and a short monthly outlook. Use concise dot points rather than broad
paragraphs.

SELECTION RULES
- Identify only the strongest one to three daily influences.
- Preserve exact transit or aspect names when they improve the advice.
- Include only advice that is clearly supported by the astrology.
- Mention a calendar event only when it is materially relevant to the advice.
- Do not mention every event, topic or area of life merely to cover it.
- Choose from wellbeing, connections, career or study, hobbies or expression,
  and motivation only when the source clearly supports that area.
- If there are no calendar events, give useful open-day advice without saying
  that the calendar is empty unless that fact matters.
- Treat calendar entries as factual and astrology as reflective guidance.
- Do not invent events, deadlines, attendees, outcomes, health problems,
  arguments, job offers or financial results.

MONTHLY RULES
- Use only slow-moving, longer-running or multi-week influences actually present
  in the source.
- Give two or three concise monthly points where supported.
- Do not turn a one-day transit into a month-long prediction.
- If only one genuine longer-running theme exists, give one monthly bullet
  rather than inventing more.

STYLE
Write like a perceptive friend: concise, specific, formal but natural. Every
bullet should tell the person what to do, what to prioritise or what to avoid.
Avoid generic AI language, motivational filler and mystical claims. Do not use
phrases such as “trust the process”, “embrace change”, “protect your energy”,
“stay positive”, “step into your power” or “balance is key”.

RETURN ONLY THIS FORMAT

Morning — {target_label}

Main influence
• [Name the strongest transit or aspect and state its practical meaning in one
  concise sentence.]

Do today
• [Highest-value specific action.]
• [Second specific action only if clearly supported.]
• [Third action only if it adds genuine value.]

Avoid today
• [Most relevant reaction, distraction or decision to avoid.]
• [Second point only if clearly supported.]

This month
• [Specific longer-term priority tied to a genuine longer-running influence.]
• [Second monthly point where supported.]
• [Third monthly point only if genuinely supported.]

Keep the complete message between 90 and 160 words. Do not add paragraphs, a
conclusion, emoji, hashtag, rating, disclaimer or Markdown bold markers.
""".strip()


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
        "Main influence",
        "Do today",
        "Avoid today",
        "This month",
    )


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
            .replace("**", ""),
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
        or not result.get("ok")
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
        .replace("\n", " ")[:500]
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
            + timedelta(days=1)
        )

    else:
        target_datetime = now

    target = target_datetime.date()
    target_string = target.isoformat()
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

        print(reading)
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

        sys.exit(130)

    except Exception as error:
        print(
            f"\nERROR: {error}",
            file=sys.stderr,
        )

        send_failure(
            error,
            current_mode,
        )

        sys.exit(1)