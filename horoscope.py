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