# pip install click requests icalendar xdg-base-dirs

import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import click
import requests
from dateutil.rrule import rrulestr
from icalendar import Calendar, Event, vText
from xdg_base_dirs import xdg_config_home

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

OUTPUT_PATH = "busy-blocks.ics"
UID_PREFIX = "busy-mirror-"
SUMMARY_TEXT = "Busy"
LOOKAHEAD_DAYS = 60
INCLUDE_ALL_DAY = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    keychain_service: str
    my_emails: set[str]
    ignore_summaries: set[str] = field(default_factory=set)
    lookahead_days: int = LOOKAHEAD_DAYS
    output_path: str = OUTPUT_PATH


def load_config() -> Config:
    config_path = xdg_config_home() / "calthing" / "config.toml"
    if not config_path.exists():
        sys.exit(
            f"Config file not found: {config_path}\n"
            "Create it with at minimum:\n\n"
            '  keychain_service = "gcal-ics-url"\n'
            '  my_emails = ["you@example.com"]\n'
        )
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config(
        keychain_service=data["keychain_service"],
        my_emails=set(data["my_emails"]),
        ignore_summaries=set(data.get("ignore_summaries", [])),
        lookahead_days=int(data.get("lookahead_days", LOOKAHEAD_DAYS)),
        output_path=str(data.get("output_path", OUTPUT_PATH)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_calendar_url(keychain_service: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", keychain_service, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(
            f"Could not read calendar URL from keychain (service: {keychain_service!r}).\n"
            f"Store it with:\n"
            f"  security add-generic-password -s {keychain_service!r} -a \"$(whoami)\" -w '<your iCal URL>'"
        )
    return result.stdout.strip()


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_datetime(val) -> datetime | None:
    """Coerce a DTSTART/DTEND value to an aware datetime, or None for all-day dates."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, date):
        return None  # all-day
    return None


def is_declined_by_me(component, my_emails: set[str]) -> bool:
    raw = component.get("ATTENDEE")
    if raw is None:
        return False
    attendees = raw if isinstance(raw, list) else [raw]
    for attendee in attendees:
        cal_address = str(attendee).lower().removeprefix("mailto:")
        if cal_address not in my_emails:
            continue
        partstat = str(attendee.params.get("PARTSTAT", "")).upper()
        if partstat == "DECLINED":
            return True
    return False


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def process_events(
    ical_bytes: bytes,
    now: datetime,
    lookahead_days: int = LOOKAHEAD_DAYS,
    my_emails: set[str] = frozenset(),
    ignore_summaries: set[str] = frozenset(),
    include_all_day: bool = INCLUDE_ALL_DAY,
) -> tuple[Calendar, dict[str, int], int, list[tuple[datetime, datetime]]]:
    """
    Parse ical_bytes and return:
      (output_cal, skip_counts, included_count, included_times)

    included_times contains one (start, end) per display slot — recurring
    events are expanded so each occurrence in the window gets its own entry.
    """
    window_start = now
    window_end = now + timedelta(days=lookahead_days)

    source_cal = Calendar.from_ical(ical_bytes)

    included_times: list[tuple[datetime, datetime]] = []
    skip_counts: dict[str, int] = {
        "cancelled": 0,
        "transparent": 0,
        "declined": 0,
        "ignored": 0,
        "all_day": 0,
        "outside_window": 0,
    }
    included = 0

    output_cal = Calendar()
    output_cal.add("PRODID", "-//busy-mirror//busy-mirror//EN")
    output_cal.add("VERSION", "2.0")
    output_cal.add("METHOD", "PUBLISH")

    for component in source_cal.walk("VEVENT"):
        # STATUS:CANCELLED
        status = str(component.get("STATUS", "")).upper()
        if status == "CANCELLED":
            skip_counts["cancelled"] += 1
            continue

        # TRANSP:TRANSPARENT (free)
        transp = str(component.get("TRANSP", "")).upper()
        if transp == "TRANSPARENT":
            skip_counts["transparent"] += 1
            continue

        # Declined attendee
        if is_declined_by_me(component, my_emails):
            skip_counts["declined"] += 1
            continue

        # Ignored summary
        summary = str(component.get("SUMMARY", ""))
        if summary in ignore_summaries:
            skip_counts["ignored"] += 1
            continue

        # All-day check
        dtstart_raw = component.get("DTSTART")
        dtend_raw = component.get("DTEND")
        dtstart_val = dtstart_raw.dt if dtstart_raw else None
        dtend_val = dtend_raw.dt if dtend_raw else None

        dtstart = to_datetime(dtstart_val)
        dtend = to_datetime(dtend_val)

        if dtstart is None or dtend is None:
            # All-day event
            if not include_all_day:
                skip_counts["all_day"] += 1
                continue
            # Convert all-day to UTC midnight datetimes so the output is valid
            if isinstance(dtstart_val, date) and not isinstance(dtstart_val, datetime):
                dtstart = datetime(dtstart_val.year, dtstart_val.month, dtstart_val.day, tzinfo=timezone.utc)
            if isinstance(dtend_val, date) and not isinstance(dtend_val, datetime):
                dtend = datetime(dtend_val.year, dtend_val.month, dtend_val.day, tzinfo=timezone.utc)

        # Window filter: skip if fully outside [now, now+lookahead_days].
        # For master recurring events (RRULE present), the DTSTART may be in
        # the past while future occurrences still fall in the window. Keep them
        # if the rule hasn't expired (no UNTIL, or UNTIL >= window_start).
        rrule = component.get("RRULE")
        if rrule:
            until_list = rrule.get("UNTIL", [])
            until = until_list[0] if until_list else None
            if until is not None:
                if isinstance(until, datetime):
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                else:
                    until = datetime(until.year, until.month, until.day, tzinfo=timezone.utc)
            rule_expired = until is not None and until < window_start
            if rule_expired or dtstart >= window_end:
                skip_counts["outside_window"] += 1
                continue
        else:
            effective_end = dtend if dtend else dtstart
            if effective_end <= window_start or dtstart >= window_end:
                skip_counts["outside_window"] += 1
                continue

        # Build sanitised replacement event
        uid_raw = str(component.get("UID", ""))
        new_event = Event()
        new_event.add("UID", vText(f"{UID_PREFIX}{uid_raw}"))
        new_event.add("DTSTART", dtstart)
        new_event.add("DTEND", dtend)
        new_event.add("SUMMARY", vText(SUMMARY_TEXT))
        new_event.add("TRANSP", vText("OPAQUE"))
        new_event.add("CLASS", vText("PRIVATE"))
        new_event.add("DTSTAMP", now_utc())

        # Preserve recurrence properties (assign raw to avoid re-encoding).
        # RECURRENCE-ID must be kept on exception VEVENTs so Outlook knows to
        # replace the corresponding RRULE-generated occurrence rather than
        # treating the exception as a second conflicting event with the same UID.
        # Without it, Outlook imports the exception and immediately removes it.
        for prop in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"):
            if prop in component:
                new_event[prop] = component[prop]

        output_cal.add_component(new_event)
        included += 1

        # For display: expand occurrences within the window.
        # Non-recurring events contribute one slot; recurring events may have
        # multiple (or a past DTSTART whose future occurrences are what matter).
        duration = (dtend - dtstart) if dtend else timedelta(0)
        rrule_prop = component.get("RRULE")
        if rrule_prop:
            try:
                rule = rrulestr(
                    rrule_prop.to_ical().decode(),
                    dtstart=dtstart,
                    ignoretz=False,
                )
                for occ in rule.between(window_start, window_end, inc=True):
                    if occ.tzinfo is None:
                        occ = occ.replace(tzinfo=timezone.utc)
                    included_times.append((occ, occ + duration))
            except Exception:
                included_times.append((dtstart, dtend))
        else:
            included_times.append((dtstart, dtend))

    return output_cal, skip_counts, included, included_times


def format_schedule(
    included_times: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    local_tz,
) -> list[str]:
    """Return sorted M-F schedule lines for events within the window."""
    by_day: dict[date, list[tuple[datetime, datetime]]] = {}
    seen_slots: set[tuple[datetime, datetime]] = set()
    for start, end in included_times:
        slot = (start, end)
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        day = start.astimezone(local_tz).date()
        by_day.setdefault(day, []).append((start, end))

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = []
    for day in sorted(by_day):
        if day.weekday() >= 5:
            continue
        if day < window_start.date() or day >= window_end.date():
            continue
        slots = sorted(by_day[day])
        times = ", ".join(
            f"{s.astimezone(local_tz).strftime('%-I:%M%p').lower()}"
            f"-{e.astimezone(local_tz).strftime('%-I:%M%p').lower()}"
            for s, e in slots
        )
        lines.append(f"  {DAY_NAMES[day.weekday()]} {day.strftime('%b %-d'):>6}  {times}")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--ics-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Use a local .ics file instead of fetching from the keychain URL.",
)
def main(ics_file):
    """Generate busy-block .ics from Google Calendar."""
    import os
    import zoneinfo

    cfg = load_config()
    now = now_utc()

    if ics_file:
        with open(ics_file, "rb") as f:
            ical_bytes = f.read()
    else:
        try:
            response = requests.get(get_calendar_url(cfg.keychain_service), timeout=30)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise click.ClickException(f"Network error fetching calendar: {exc}")
        except requests.exceptions.HTTPError as exc:
            raise click.ClickException(f"HTTP error fetching calendar: {exc}")
        except requests.exceptions.Timeout:
            raise click.ClickException("Request timed out fetching calendar.")
        ical_bytes = response.content

    output_cal, skip_counts, included, included_times = process_events(
        ical_bytes,
        now,
        lookahead_days=cfg.lookahead_days,
        my_emails=cfg.my_emails,
        ignore_summaries=cfg.ignore_summaries,
    )

    output_path = os.path.expanduser(cfg.output_path)
    with open(output_path, "wb") as f:
        f.write(output_cal.to_ical())

    total_skipped = sum(skip_counts.values())
    click.echo(f"Wrote {output_path}")
    click.echo(f"  Included: {included} event(s)")
    click.echo(f"  Skipped:  {total_skipped} event(s)")
    for reason, count in skip_counts.items():
        if count:
            click.echo(f"    {reason}: {count}")

    local_tz = zoneinfo.ZoneInfo("America/New_York")
    window_start = now
    window_end = now + timedelta(days=cfg.lookahead_days)
    schedule = format_schedule(included_times, window_start, window_end, local_tz)
    if schedule:
        click.echo("")
        for line in schedule:
            click.echo(line)



if __name__ == "__main__":
    main()
