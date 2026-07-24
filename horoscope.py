import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv
from google import genai


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

DEFAULT_PERSONAL_CONTEXT = """
The recipient is a girl who is a fourth-year mechatronics engineering student in Melbourne.

They are balancing university coursework, a final-year project, internship applications, 
technical skill development, finances, confidence, diet, exercise, sleep,
friendships, family, being single and wanting relationships, health and their general routine.

They are ambitious and respond best to direct, practical guidance. They want
to know where to direct their attention, what action to take, when to wait,
how to communicate and what mistakes to avoid.

They are trying to be more girly and feminine while also being able to be assertive and confident in studies and work.
They are also trying to physically glow-up.

Only connect the astrology to areas of their life genuinely supported by the
astrological source. Do not force every part of their life into each reading.
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

def melbourne_now() -> datetime:
    """Return the current Melbourne date and time."""

    try:
        return datetime.now(
            ZoneInfo(MELBOURNE_TIMEZONE)
        )

    except ZoneInfoNotFoundError:
        print(
            "WARNING: Australia/Melbourne could not be loaded. "
            "Using the computer's local time.",
            file=sys.stderr,
        )

        return datetime.now().astimezone()


def formatted_date(value: datetime) -> str:
    """Format a date without a leading zero."""

    return (
        f"{value.strftime('%A')}, "
        f"{value.day} "
        f"{value.strftime('%B %Y')}"
    )


def determine_target_date(
    mode: str,
    now: datetime,
) -> datetime:
    """
    Morning mode reads today.

    Evening mode reads tomorrow.
    """

    if mode == "evening":
        return now + timedelta(days=1)

    return now


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
# SOURCE PREPARATION
# ---------------------------------------------------------

def astrology_source_text(
    api_result: dict,
) -> str:
    """Convert the API response to readable JSON for Gemini."""

    data = api_result.get(
        "data",
        api_result,
    )

    source = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    return source[:MAX_ASTROLOGY_JSON_CHARACTERS]


# ---------------------------------------------------------
# MORNING PROMPT
# ---------------------------------------------------------

def build_morning_prompt(
    source_text: str,
    date_text: str,
    personal_context: str,
) -> str:
    """Create the morning motivation and advice prompt."""

    return f"""
Prepare a private morning astrology message for one person.

DATE
{date_text}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

SOURCE
The JSON below contains the person's personalised astrology for today,
including natal transits, aspects, interpretation blocks, timing information,
scores, themes and longer-running influences.

Use this source as the sole astrological authority. Do not invent transits,
placements, events or predictions.

PURPOSE
This message will be read at 7:30 in the morning. It must help the person begin
the day with direction, motivation and a clear plan.

Write like a perceptive and trusted friend who understands astrology and knows
the person's circumstances.

The tone must be:
- calm;
- formal but natural;
- decisive;
- specific;
- encouraging without sounding sentimental;
- practical rather than mystical.

Preserve the exact language of the strongest one to three astrological
influences. For example, retain “Mercury square natal Saturn” rather than
reducing it to “communication problems”.

Immediately explain what each relevant influence means in practical terms.

Connect the reading to university, the final-year project, internship
applications, work, finances, relationships or routine only where the source
supports that connection.

Do not use phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- stay positive;
- the universe is telling you;
- step into your power;
- navigate these energies;
- balance is key.

Give direct instructions. Explain precisely what should receive attention and
what behaviour is likely to waste the day.

Do not invent:
- job offers;
- arguments;
- deadlines;
- financial gains or losses;
- health problems;
- named people;
- guaranteed outcomes.

OUTPUT FORMAT

Return only the finished message in this exact structure:

Morning — {date_text}

Direction
Write one paragraph of three or four sentences. Name the strongest astrological
influence, explain its meaning and state the most productive approach to today.

Your priorities
• One specific and realistic action to complete today.
• A second specific action supported by the source.
• A third point only if it adds genuine value.

Avoid
• One specific reaction, distraction or decision to avoid.
• A second point only when supported.

Keep in mind
Write one firm but encouraging sentence that gives the person motivation
without using a cliché.

Keep the complete response between 130 and 200 words.

Do not use emojis, hashtags, lucky numbers, ratings, disclaimers, markdown bold
markers, an introduction or a conclusion.

PERSONALISED ASTROLOGY JSON

{source_text}
""".strip()


# ---------------------------------------------------------
# EVENING PROMPT
# ---------------------------------------------------------

def build_evening_prompt(
    source_text: str,
    date_text: str,
    personal_context: str,
) -> str:
    """Create the next-day preparation prompt."""

    return f"""
Prepare a private evening astrology message for one person.

TOMORROW'S DATE
{date_text}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

SOURCE
The JSON below contains the person's personalised astrology for tomorrow,
including natal transits, aspects, interpretation blocks, timing information,
scores, themes and longer-running influences.

Use this source as the sole astrological authority. Do not invent transits,
placements, events or predictions.

PURPOSE
This message will be read at 8:30 tonight. It must explain tomorrow's strongest
astrological influences and tell the person how to prepare tonight.

Write like a perceptive and trusted friend who understands astrology and knows
the person's circumstances.

The tone must be:
- calm;
- formal but natural;
- direct;
- specific;
- practical;
- thoughtful rather than dramatic.

Preserve the exact language of the strongest one to three astrological
influences. For example, retain “Mars trine natal Midheaven” and then explain
what it means for tomorrow's actions.

Separate what should be prepared tonight from what should be done tomorrow.

Where supported by the source, give concrete preparation such as:
- write tomorrow's first task down tonight;
- prepare documents or notes before an important conversation;
- finish a minor loose end so it does not consume tomorrow;
- postpone sending a reactive message;
- arrange focused time for technical or university work;
- decide which application or task deserves priority;
- allow extra time before making a permanent decision.

Only connect the reading to university, the final-year project, internship
applications, work, finances, relationships or routine when the source
genuinely supports it.

Do not use vague phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- stay positive;
- the universe is telling you;
- step into your power;
- navigate these energies;
- balance is key.

Do not invent:
- job offers;
- arguments;
- deadlines;
- financial outcomes;
- health issues;
- named people;
- guaranteed events.

OUTPUT FORMAT

Return only the finished message in this exact structure:

Tomorrow — {date_text}

Outlook
Write one paragraph of three or four sentences. Name tomorrow's strongest
astrological influence, explain what it means and state the best overall
approach.

Prepare tonight
• One specific thing to organise, write, finish or decide tonight.
• A second preparation step only when supported.

Tomorrow's priorities
• One specific action to prioritise tomorrow.
• A second specific action supported by the source.

Avoid tomorrow
• One specific reaction, distraction or decision to avoid.
• A second point only when supported.

Keep the complete response between 140 and 210 words.

Do not use emojis, hashtags, lucky numbers, ratings, disclaimers, markdown bold
markers, an introduction or a conclusion.

PERSONALISED ASTROLOGY JSON

{source_text}
""".strip()


# ---------------------------------------------------------
# GEMINI
# ---------------------------------------------------------

def clean_generated_message(
    message: str,
) -> str:
    """Clean minor formatting errors."""

    message = message.strip()
    message = message.replace("**", "")
    message = re.sub(r"\n{3,}", "\n\n", message)

    return message


def expected_sections(
    mode: str,
) -> tuple[str, ...]:
    """Return the required headings for each reading."""

    if mode == "evening":
        return (
            "Tomorrow",
            "Outlook",
            "Prepare tonight",
            "Tomorrow's priorities",
            "Avoid tomorrow",
        )

    return (
        "Morning",
        "Direction",
        "Your priorities",
        "Avoid",
        "Keep in mind",
    )


def message_has_required_sections(
    message: str,
    mode: str,
) -> bool:
    """Check whether Gemini followed the requested format."""

    lowered = message.casefold()

    return all(
        section.casefold() in lowered
        for section in expected_sections(mode)
    )


def generate_reading(
    api_result: dict,
    mode: str,
    date_text: str,
) -> str:
    """Generate either the morning or evening reading."""

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

    source_text = astrology_source_text(
        api_result
    )

    if mode == "evening":
        prompt = build_evening_prompt(
            source_text=source_text,
            date_text=date_text,
            personal_context=personal_context,
        )

    else:
        prompt = build_morning_prompt(
            source_text=source_text,
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

            if attempt < 2:
                time.sleep(3)
                continue

            break

        if not message_has_required_sections(
            message,
            mode,
        ):
            if attempt < 2:
                prompt += (
                    "\n\nYour previous response did not follow the required "
                    "headings. Repeat the task using every exact heading."
                )
                continue

        return message[:MAX_TELEGRAM_CHARACTERS]

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
                "Telegram could not be reached after three attempts."
            ) from error

        if not result.get("ok"):
            description = result.get(
                "description",
                "Unknown Telegram error",
            )

            raise RuntimeError(
                f"Telegram rejected the message: {description}"
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

    bot_token = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        "",
    ).strip()

    if not bot_token or not chat_id:
        return

    safe_error = str(error).replace(
        "\n",
        " ",
    )[:500]

    try:
        send_telegram(
            f"The {mode} astrology automation failed.\n"
            f"{safe_error}"
        )

    except Exception:
        pass


# ---------------------------------------------------------
# MAIN PROGRAM
# ---------------------------------------------------------

def main() -> None:
    """Run the morning or evening automation."""

    mode = reading_mode()
    now = melbourne_now()

    target_datetime = determine_target_date(
        mode=mode,
        now=now,
    )

    target_date = target_datetime.date().isoformat()
    date_text = formatted_date(target_datetime)

    if mode == "morning":
        print(
            f"Preparing this morning's reading for {target_date}..."
        )

    else:
        print(
            f"Preparing tomorrow's reading for {target_date}..."
        )

    api_result = fetch_personal_horoscope(
        target_date
    )

    print(
        "FreeAstroAPI returned the personalised astrology successfully."
    )

    reading = generate_reading(
        api_result=api_result,
        mode=mode,
        date_text=date_text,
    )

    print()
    print("Generated message:")
    print()
    print(reading)
    print()

    send_telegram(reading)

    print(
        f"The {mode} astrology message was delivered successfully."
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