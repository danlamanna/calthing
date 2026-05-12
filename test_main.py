import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path

from main import format_schedule, process_events

FIXTURE_PATH = Path(__file__).parent / "basic-sanitized.ics"
FIXTURE_NOW = datetime(2026, 5, 12, 12, 39, 20, tzinfo=timezone.utc)
FIXTURE_EMAILS = {"user@example.com"}


def test_process_and_format():
    ical_bytes = FIXTURE_PATH.read_bytes()

    _, skip_counts, included, included_times = process_events(
        ical_bytes,
        FIXTURE_NOW,
        lookahead_days=30,
        my_emails=FIXTURE_EMAILS,
    )

    assert included == 29
    assert skip_counts == {
        "cancelled": 0,
        "transparent": 2,
        "declined": 10,
        "ignored": 0,
        "all_day": 0,
        "outside_window": 39,
    }

    local_tz = zoneinfo.ZoneInfo("America/New_York")
    window_end = FIXTURE_NOW + timedelta(days=30)
    schedule = format_schedule(included_times, FIXTURE_NOW, window_end, local_tz)

    assert schedule == [
        "  Tue May 12  11:00am-11:30am, 12:00pm-12:50pm, 3:30pm-4:00pm",
        "  Thu May 14  1:00pm-2:00pm",
        "  Fri May 15  9:30am-10:30am, 10:30am-10:55am, 11:00am-12:00pm, 1:00pm-2:00pm",
        "  Mon May 18  1:00pm-2:00pm, 3:00pm-4:00pm",
        "  Tue May 19  1:00pm-1:25pm, 3:30pm-4:00pm",
        "  Wed May 20  12:00pm-1:00pm",
        "  Fri May 22  9:30am-10:30am, 10:30am-11:00am, 1:00pm-2:00pm",
        "  Mon May 25  3:00pm-4:00pm",
        "  Tue May 26  3:30pm-4:00pm",
        "  Wed May 27  12:00pm-1:00pm",
        "  Fri May 29  9:30am-10:30am, 11:00am-12:00pm, 1:00pm-2:00pm",
        "  Mon  Jun 1  1:00pm-2:00pm, 3:00pm-4:00pm",
        "  Tue  Jun 2  1:00pm-1:25pm, 3:30pm-4:00pm",
        "  Fri  Jun 5  9:30am-10:30am, 10:30am-10:55am, 1:00pm-2:00pm",
        "  Mon  Jun 8  3:00pm-4:00pm",
        "  Tue  Jun 9  11:00am-11:30am, 3:30pm-4:00pm",
    ]
