import json
import os
import re
import sys
from datetime import datetime
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

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
MELBOURNE_TIMEZONE = "Australia/Melbourne"
MAX_ASTROLOGY_JSON_CHARACTERS = 45_000

DEFAULT_PERSONAL_CONTEXT = """
The recipient is a fourth-year mechatronics engineering student in Melbourne.
They are balancing university coursework, a final-year project, internship and
graduate applications, technical skill development, part-time work, finances,
friendships, family, relationships and their general health and routine.

They are ambitious and want practical guidance about where to direct their
attention, when to act, when to wait, how to communicate and what mistakes to
avoid. Mention only the parts of their life that are genuinely supported by the
astrological information supplied.
""".strip()

load_dotenv(ENV_FILE)


# ---------------------------------------------------------
# SETTINGS AND VALIDATION
# ---------------------------------------------------------

def required_setting(name: str) -> str:
    """Return a required value from the .env file."""

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Missing {name}. Open the .env file and add a value for it."
        )

    return value


def optional_setting(name: str, default: str = "") -> str:
    """Return an optional value from the .env file."""

    value = os.getenv(name, "").strip()
    return value if value else default


def integer_setting(
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Read and validate an integer setting."""

    raw_value = required_setting(name)

    try:
        value = int(raw_value)

    except ValueError as error:
        raise RuntimeError(
            f"{name} must be a whole number, but received: {raw_value}"
        ) from error

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}."
        )

    return value


def boolean_setting(name: str, default: bool) -> bool:
    """Read a true/false setting from the .env file."""

    raw_value = os.getenv(name, "").strip().casefold()

    if not raw_value:
        return default

    if raw_value in {"true", "1", "yes", "y"}:
        return True

    if raw_value in {"false", "0", "no", "n"}:
        return False

    raise RuntimeError(
        f"{name} must be true or false, but received: {raw_value}"
    )


# ---------------------------------------------------------
# DATE AND TIME
# ---------------------------------------------------------

def melbourne_now() -> datetime:
    """Return the current Melbourne date and time."""

    try:
        return datetime.now(ZoneInfo(MELBOURNE_TIMEZONE))

    except ZoneInfoNotFoundError:
        print(
            "WARNING: Australia/Melbourne could not be loaded. "
            "Using the computer's local time instead.",
            file=sys.stderr,
        )

        return datetime.now().astimezone()


def formatted_date(now: datetime) -> str:
    """Format the date without a leading zero."""

    return (
        f"{now.strftime('%A')}, "
        f"{now.day} "
        f"{now.strftime('%B %Y')}"
    )


# ---------------------------------------------------------
# FREEASTROAPI REQUEST
# ---------------------------------------------------------

def build_birth_details() -> dict:
    """Build the birth-data object required by FreeAstroAPI."""

    time_known = boolean_setting(
        "BIRTH_TIME_KNOWN",
        default=True,
    )

    birth = {
        "year": integer_setting("BIRTH_YEAR", 1800, 2200),
        "month": integer_setting("BIRTH_MONTH", 1, 12),
        "day": integer_setting("BIRTH_DAY", 1, 31),
        "hour": integer_setting("BIRTH_HOUR", 0, 23),
        "minute": integer_setting("BIRTH_MINUTE", 0, 59),
        "city": required_setting("BIRTH_CITY"),
        "tz_str": required_setting("BIRTH_TIMEZONE"),
        "time_known": time_known,
    }

    return birth


def fetch_personal_horoscope(target_date: str) -> dict:
    """Retrieve the personalised daily horoscope from FreeAstroAPI."""

    api_key = required_setting("FREEASTRO_API_KEY")

    payload = {
        "birth": build_birth_details(),
        "date": target_date,
        "tz_str": MELBOURNE_TIMEZONE,
        "include_interpretation_blocks": True,
    }

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
        raise RuntimeError(
            "FreeAstroAPI could not be reached. "
            "Check your internet connection."
        ) from error

    if response.status_code in {401, 403}:
        raise RuntimeError(
            "FreeAstroAPI rejected the API key. "
            "Check FREEASTRO_API_KEY."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "FreeAstroAPI's request limit has been reached for today."
        )

    if not response.ok:
        response_preview = response.text[:500].replace("\n", " ")

        raise RuntimeError(
            "FreeAstroAPI returned "
            f"HTTP {response.status_code}: {response_preview}"
        )

    try:
        result = response.json()

    except ValueError as error:
        raise RuntimeError(
            "FreeAstroAPI returned a response that was not valid JSON."
        ) from error

    data = result.get("data")

    if not isinstance(data, dict):
        raise RuntimeError(
            "FreeAstroAPI did not return the expected horoscope data."
        )

    returned_date = str(data.get("date", "")).strip()

    if returned_date and returned_date != target_date:
        raise RuntimeError(
            "FreeAstroAPI returned the wrong date. "
            f"Requested {target_date}, received {returned_date}."
        )

    return result


# ---------------------------------------------------------
# GEMINI INTERPRETATION
# ---------------------------------------------------------

def astrology_source_text(api_result: dict) -> str:
    """Convert the structured astrology response into compact JSON."""

    data = api_result.get("data", api_result)

    source = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    return source[:MAX_ASTROLOGY_JSON_CHARACTERS]


def build_prompt(
    api_result: dict,
    date_text: str,
    personal_context: str,
) -> str:
    """Build the instructions for Gemini."""

    source_text = astrology_source_text(api_result)

    return f"""
Prepare a private morning astrology reading for one person.

DATE
{date_text}, Melbourne, Australia.

PERSONAL CONTEXT
{personal_context}

SOURCE
The JSON below is a personalised Western astrology horoscope calculated from
this person's birth details. It can include a daily theme, scores, top natal
transits, exact transit times, active windows, interpretation blocks, focus
areas and broader monthly patterns.

Use the source as the sole astrological authority. Do not calculate placements
or invent transits yourself.

VOICE
Write like a perceptive friend who understands astrology and knows the person's
circumstances. Use formal, natural and direct language. The reading should feel
considered and personally useful, not like an AI summary, motivational post or
newspaper horoscope.

ASTROLOGICAL LANGUAGE
Retain the exact names of the one to three most important transits, aspects,
planets, natal points or chart patterns from the source. For example, preserve
"Mars Conjunction Natal Midheaven" rather than reducing it to "career energy".
Explain the practical meaning in ordinary language immediately afterwards.

When the source provides an exact time or active window, mention it only when it
materially changes what the person should do. Convert technical UTC timing into
plain Melbourne terms only when you can do so confidently from the supplied
information. Otherwise, describe it as morning, afternoon, evening or an active
background influence without inventing precision.

PRACTICAL ADVICE
Give specific advice about what the person should do and avoid today. Where the
source supports it, connect the reading to university work, the final-year
project, internship applications, technical development, part-time work,
finances, communication, relationships or routine.

Choose only the life areas genuinely supported by the transits. Do not mention
every area merely because it appears in the personal context.

Good advice is concrete. Prefer language such as:
- finish the draft before beginning another application;
- ask for clarification before agreeing;
- send the prepared message rather than revising it repeatedly;
- postpone a permanent decision until the emotional pressure settles;
- use the productive period for concentrated technical work;
- do not turn one difficult conversation into a judgement about the entire
  relationship.

Do not use vague phrases such as:
- trust the process;
- embrace change;
- protect your energy;
- stay positive;
- be mindful;
- the universe is telling you;
- step into your power;
- balance is key;
- navigate these energies.

Do not invent events, arguments, job offers, financial outcomes, health issues,
deadlines or named people. Treat astrology as guidance, not certainty.

OUTPUT
Return only the finished reading in this exact structure:

{date_text}

Today
Write one paragraph of three or four sentences. Name the strongest daily
transit or pattern, explain its practical meaning and state the principal way
the person should approach the day.

Do
• One specific action.
• A second specific action only when clearly supported.

Avoid
• One specific reaction, behaviour or decision to avoid.
• A second point only when clearly supported.

This month
Write one paragraph of two or three sentences using the source's dominant
monthly topics, active windows or background patterns. State what deserves
sustained attention and what longer-term mistake should be avoided.

Keep the full reading between 120 and 190 words. Do not add emojis, hashtags,
rating scores, lucky numbers, disclaimers, an introduction or a conclusion.
Do not use Markdown bold markers.

PERSONALISED ASTROLOGY JSON
{source_text}
""".strip()


def clean_generated_message(message: str) -> str:
    """Clean minor formatting errors from Gemini's response."""

    message = message.strip().replace("**", "")
    message = re.sub(r"\n{3,}", "\n\n", message)

    return message


def validate_generated_message(message: str) -> None:
    """Confirm that the expected sections are present."""

    required_sections = (
        "Today",
        "Do",
        "Avoid",
        "This month",
    )

    missing = [
        section
        for section in required_sections
        if section.casefold() not in message.casefold()
    ]

    if missing:
        raise RuntimeError(
            "Gemini omitted required sections: "
            + ", ".join(missing)
        )

    if len(message) < 300:
        raise RuntimeError(
            "Gemini returned an unexpectedly short reading."
        )


def generate_reading(
    api_result: dict,
    date_text: str,
) -> str:
    """Turn structured astrology data into practical advice."""

    api_key = required_setting("GEMINI_API_KEY")

    model_name = optional_setting(
        "GEMINI_MODEL",
        DEFAULT_GEMINI_MODEL,
    )

    personal_context = optional_setting(
        "PERSONAL_CONTEXT",
        DEFAULT_PERSONAL_CONTEXT,
    )

    prompt = build_prompt(
        api_result=api_result,
        date_text=date_text,
        personal_context=personal_context,
    )

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )

    except Exception as error:
        raise RuntimeError(
            f"Gemini could not create the reading using "
            f"'{model_name}'. Check GEMINI_API_KEY and the "
            "free-tier quota."
        ) from error

    message = clean_generated_message(
        response.text or ""
    )

    if not message:
        raise RuntimeError(
            "Gemini returned an empty reading."
        )

    validate_generated_message(message)

    return message[:3_500]


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send the completed reading through Telegram."""

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

    except requests.RequestException as error:
        raise RuntimeError(
            "Telegram could not be reached. Check your internet "
            "connection, bot token and chat ID."
        ) from error

    if not result.get("ok"):
        description = result.get(
            "description",
            "Unknown Telegram error",
        )

        raise RuntimeError(
            f"Telegram rejected the message: {description}"
        )


def send_failure_message(error: Exception) -> None:
    """Attempt to send a brief Telegram warning after a failure."""

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

    safe_error = str(error).replace("\n", " ")[:500]

    try:
        send_telegram(
            "Horoscope automation failed.\n"
            f"{safe_error}"
        )

    except Exception:
        pass


# ---------------------------------------------------------
# MAIN PROGRAM
# ---------------------------------------------------------

def main() -> None:
    """Run the complete horoscope process."""

    now = melbourne_now()
    target_date = now.date().isoformat()
    date_text = formatted_date(now)

    print(
        f"Requesting personalised astrology for {target_date}..."
    )

    api_result = fetch_personal_horoscope(
        target_date
    )

    print(
        "FreeAstroAPI returned the personalised horoscope successfully."
    )

    reading = generate_reading(
        api_result=api_result,
        date_text=date_text,
    )

    print()
    print("Generated reading:")
    print()
    print(reading)
    print()

    send_telegram(reading)

    print(
        "Reading delivered successfully through Telegram."
    )


if __name__ == "__main__":
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

        send_failure_message(error)

        sys.exit(1)