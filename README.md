# calthing

Mirrors Google Calendar events into an Outlook-importable `.ics` file as private "Busy" blocks. Strips all event details (description, attendees, location), keeping only time blocks.

Outlook calendar subscriptions show as overlays, not on your primary calendar, so colleagues can't see that time as busy. Getting Google Calendar events onto your primary calendar requires importing an `.ics` file.

Imports are snapshots. Events canceled or declined after an import stay in Outlook until you remove them. New invites after the import require another run. Reimporting is safe; Outlook deduplicates by UID so the same event won't appear twice.

## Setup

Install dependencies:

```
uv sync
```

Create a config file at `~/.config/calthing/config.toml`:

```toml
keychain_service = "gcal-ics-url"
my_emails = ["you@example.com"]
```

Store your Google Calendar secret iCal URL (from Calendar Settings → Integrate calendar → Secret address in iCal format) in the keychain:

```
security add-generic-password -s "gcal-ics-url" -a "$(whoami)" -w "<your iCal URL>"
```

## Usage

Generate `busy-blocks.ics` in the current directory:

```
uv run python main.py
```

Use a local `.ics` file instead of fetching from the URL:

```
uv run python main.py --ics-file path/to/calendar.ics
```

Run the self-test against the bundled sanitized fixture:

```
uv run pytest
```

## Importing into Outlook

Import via [Outlook Web App](https://outlook.cloud.microsoft/calendar/view/workweek); the desktop client doesn't support `.ics` import:

1. Open OWA and go to the **Calendar** tab
2. **Add calendar** dropdown → **From file**
3. Select `busy-blocks.ics` and import

Reimport after each regeneration. Outlook deduplicates by UID, so don't change `UID_PREFIX` in `main.py`.

## Configuration

Edit `~/.config/calthing/config.toml`:

| Key | Default | Description |
|---|---|---|
| `keychain_service` | (required) | macOS keychain service name storing the iCal URL |
| `my_emails` | (required) | Your email address(es) for declined-event filtering |
| `ignore_summaries` | `[]` | Event names to exclude entirely (e.g. `["All Hands", "Team Standup"]` for events already on your Outlook calendar) |
| `lookahead_days` | `60` | How many days ahead to include |
| `output_path` | `"busy-blocks.ics"` | Where to write the output |

Example:

```toml
keychain_service = "gcal-ics-url"
my_emails = ["you@example.com"]
ignore_summaries = ["All Hands", "Team Standup"]
lookahead_days = 60
```
