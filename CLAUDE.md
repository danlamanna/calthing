# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run python main.py                            # fetch from keychain URL and write busy-blocks.ics
uv run python main.py --ics-file foo.ics         # use a local .ics instead
uv run pytest                                    # run the self-test
```

## Architecture

All logic lives in `main.py`. There are no external services, databases, or frameworks beyond the dependency list.

**Data flow:** fetch iCal bytes → `process_events()` → write sanitized `.ics` + print schedule.

`process_events(ical_bytes, now, lookahead_days)` is the core function. It returns `(output_cal, skip_counts, included_count, included_times)`. Keeping `now` injectable is what makes the self-test deterministic — the test freezes it to `2026-05-12T12:39:20Z`.

`format_schedule(included_times, window_start, window_end, local_tz)` expands recurring events via `dateutil.rrule` so each occurrence in the window appears as its own display slot. This is display-only; the output `.ics` contains the master event with the original RRULE intact so Outlook expands it.

**Recurring event handling:** Google exports open-ended recurring series as a single master VEVENT with an RRULE rather than expanding future instances. The window filter therefore skips the `effective_end <= window_start` check for events with an active RRULE, keeping them even when their DTSTART is in the past.

**Exception overrides (RECURRENCE-ID):** When a single occurrence of a recurring series is modified (different time, attendees, location, etc.), Google exports it as a separate VEVENT alongside the master. That exception VEVENT carries a `RECURRENCE-ID` property whose value matches the original occurrence's date/time. Both VEVENTs share the same UID. `RECURRENCE-ID` must be preserved in the output — without it, Outlook sees two VEVENTs with the same UID where one has RRULE and one looks like a standalone event on the same date, causing it to import the event and then immediately remove it as an irreconcilable conflict. With `RECURRENCE-ID` present, RFC 5545-compliant clients (including modern Outlook) automatically suppress the RRULE-generated occurrence and replace it with the exception; no explicit `EXDATE` on the master is required.

**Declined-event detection:** uses `component.get("ATTENDEE")` (not `component.walk()`—walk only traverses sub-components, not properties). Returns a list when multiple attendees are present.

**Test fixture:** `basic-sanitized.ics` is a stripped, anonymized version of a real calendar feed, kept in git. It covers a ±30-day window around 2026-05-12, with summaries replaced by `Event N` and attendee emails anonymized to `attendeeN@example.com` except the fixture owner (`user@example.com`). The pytest test hardcodes `my_emails={"user@example.com"}` to match.

**Keychain:** the Google Calendar secret iCal URL is stored in the macOS keychain under a service name configured in `~/.config/calthing/config.toml`. `get_calendar_url()` retrieves it via `subprocess` + the `security` CLI.
