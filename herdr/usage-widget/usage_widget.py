"""Display quota windows from the local Codex and Claude Code CLIs in Herdr."""

import argparse
import asyncio
import curses
import fcntl
import json
import math
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

REFRESH_SECONDS = 300
PROBE_TIMEOUT = 12
# Reserve the bundled shortcut text, separators, and space for Herdr tab controls.
TAB_BAR_RESERVED_COLUMNS = 125
HERDR_MAX_STATUS_CHARS = 80
USED_PREFIX = "Used · "
PROVIDERS = {"codex": ("CDX", "Codex"), "claude": ("CC", "Claude Code")}


@dataclass(frozen=True)
class Window:
    label: str
    used: float
    resets_at: float | None


@dataclass(frozen=True)
class Reading:
    windows: tuple[Window, ...]
    checked_at: float
    stale: bool = False


def clean_label(value):
    if not isinstance(value, str):
        return ""
    printable = "".join(char for char in value if char.isprintable())
    return " ".join(printable.split())[:64]


def as_timestamp(value):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            value = parsed.timestamp()
        if type(value) in (int, float) and 0 < value < 253402214400:
            return float(value)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def make_window(label, used, resets_at):
    label = clean_label(label)
    if label and type(used) in (int, float) and 0 <= used <= 100:
        return Window(label, used, as_timestamp(resets_at))
    return None


def duration_label(minutes):
    if type(minutes) is not int or minutes <= 0:
        return "limit"
    for unit, size in (("d", 1440), ("h", 60)):
        if minutes % size == 0:
            return f"{minutes // size}{unit}"
    return f"{minutes}m"


def codex_windows(result):
    primary = result.get("rateLimits") or {}
    buckets = dict(result.get("rateLimitsByLimitId") or {})
    if primary:
        buckets[primary.get("limitId") or "codex"] = primary
    windows = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        scope = "" if key == "codex" else clean_label(bucket.get("limitName")) or clean_label(key)
        for field in ("primary", "secondary"):
            data = bucket.get(field)
            if not isinstance(data, dict):
                continue
            label = " ".join(part for part in (scope, duration_label(data.get("windowDurationMins"))) if part)
            window = make_window(label, data.get("usedPercent"), data.get("resetsAt"))
            if window is not None:
                windows.append(window)
    return tuple(windows)


def claude_windows(result):
    if not result.get("rate_limits_available"):
        return ()
    rates = result.get("rate_limits") or {}
    windows = []
    for field, label in (("five_hour", "5h"), ("seven_day", "7d")):
        data = rates.get(field)
        if isinstance(data, dict):
            windows.append(make_window(label, data.get("utilization"), data.get("resets_at")))
    for data in rates.get("model_scoped") or []:
        if isinstance(data, dict):
            windows.append(make_window(data.get("display_name"), data.get("utilization"), data.get("resets_at")))
    return tuple(dict.fromkeys(window for window in windows if window is not None))


def executable(name):
    home_bin = str(Path.home() / ".local" / "bin")
    path = [part for part in os.get_exec_path() if os.path.isabs(part)]
    if name == "herdr":
        candidate = os.environ.get("HERDR_BIN_PATH", "")
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name, path=os.pathsep.join([home_bin, *path, "/opt/homebrew/bin", "/usr/local/bin"]))


async def send(process, message):
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()


async def receive(process, predicate):
    while line := await process.stdout.readline():
        message = json.loads(line)
        if isinstance(message, dict) and predicate(message):
            return message
    return {}


async def query_codex(process):
    await send(process, {
        "method": "initialize", "id": 1,
        "params": {"clientInfo": {"name": "herdr-usage-widget", "version": "0.1.0"}},
    })
    if "result" not in await receive(process, lambda message: message.get("id") == 1):
        return ()
    await send(process, {"method": "initialized"})
    await send(process, {"method": "account/rateLimits/read", "id": 2})
    reply = await receive(process, lambda message: message.get("id") == 2)
    return codex_windows(reply.get("result") or {})


async def query_claude(process):
    await send(process, {
        "type": "control_request", "request_id": "usage",
        "request": {"subtype": "get_usage", "skip_behaviors": True},
    })
    reply = await receive(process, lambda message: (
        message.get("type") == "control_response"
        and isinstance(message.get("response"), dict)
        and message["response"].get("request_id") == "usage"
    ))
    response = reply.get("response") or {}
    if response.get("subtype") != "success":
        return ()
    return claude_windows(response.get("response") or {})


async def probe(name, args, query, env=None):
    binary = executable(name)
    if not binary:
        return None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            binary, *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=Path.home(), env=env,
            limit=1024 * 1024,
        )
        return await asyncio.wait_for(query(process), timeout=PROBE_TIMEOUT) or None
    except (OSError, asyncio.TimeoutError, ValueError, TypeError, AttributeError, KeyError):
        return None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


async def fetch():
    claude_env = dict(os.environ, DISABLE_AUTOUPDATER="1", DISABLE_ERROR_REPORTING="1", DISABLE_TELEMETRY="1")
    return await asyncio.gather(
        probe("codex", ["app-server"], query_codex),
        probe("claude", [
            "-p", "--safe-mode", "--no-session-persistence", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
        ], query_claude, claude_env),
    )


def update_readings(previous, results, now):
    readings = {}
    for provider, windows in zip(PROVIDERS, results):
        if windows:
            readings[provider] = Reading(tuple(windows), now)
        elif provider in previous:
            readings[provider] = replace(previous[provider], stale=True)
    return readings


def cache_path():
    configured_root = Path(os.environ.get("XDG_STATE_HOME", ""))
    root = configured_root if configured_root.is_absolute() else Path.home() / ".local" / "state"
    return root / "herdr" / "usage-widget" / "usage.json"


def read_cache(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    readings = {}
    for provider in PROVIDERS:
        entry = data.get(provider)
        if not isinstance(entry, dict):
            continue
        checked = as_timestamp(entry.get("checked_at"))
        raw_windows = entry.get("windows")
        if checked is None or not isinstance(raw_windows, list):
            continue
        windows = (
            make_window(row.get("label"), row.get("used"), row.get("resets_at"))
            for row in raw_windows if isinstance(row, dict)
        )
        valid = tuple(window for window in windows if window is not None)
        if valid:
            readings[provider] = Reading(valid, checked, entry.get("stale") is not False)
    return readings


def write_cache(path, readings, attempted_at=None):
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump({provider: asdict(reading) for provider, reading in readings.items()}, output)
        if attempted_at is not None:
            os.utime(temp, (attempted_at, attempted_at))
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def refresh_cache(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as lock:
        # Both Herdr status entries share one refresh and render the same snapshot.
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time()
        try:
            age = now - path.stat().st_mtime
        except FileNotFoundError:
            age = REFRESH_SECONDS
        previous = read_cache(path)
        if 0 <= age < REFRESH_SECONDS - 5:
            return previous
        readings = update_readings(previous, asyncio.run(fetch()), time.time())
        # Schedule from the start of the last probe, even when a provider was slow.
        write_cache(path, readings, attempted_at=now)
        return readings


def is_stale(reading, now):
    return reading.stale or now - reading.checked_at > 2 * REFRESH_SECONDS


def usage_percent(used):
    return f"{used:.1f}".rstrip("0").rstrip(".") + "%"


def local_reset(resets_at):
    return datetime.fromtimestamp(math.floor((resets_at + 30) / 60) * 60).astimezone()


def bar_window(window, now, max_chars=math.inf, max_columns=math.inf):
    if window.resets_at and window.resets_at <= now:
        detail = "reset due"
    else:
        detail = usage_percent(window.used)
        if window.resets_at is not None:
            reset = local_reset(window.resets_at)
            fmt = "%H:%M" if reset.date() == datetime.fromtimestamp(now).date() else "%a %H:%M"
            detail += f" @ {reset:{fmt}}"

    label = window.label
    full = f"{label} {detail}"
    if len(full) <= max_chars and entry_width(full) <= max_columns:
        return full

    scope, _, duration = label.rpartition(" ")
    has_duration = scope and (
        duration == "limit" or (duration.endswith(("m", "h", "d")) and duration[:-1].isdigit())
    )
    head = scope if has_duration else label
    tail = f"… {duration} {detail}" if has_duration else f"… {detail}"
    chars_left = max_chars - len(tail)
    columns_left = max_columns - entry_width(tail)
    shortened = []
    for char in head:
        width = entry_width(char)
        if chars_left < 1 or columns_left < width:
            break
        shortened.append(char)
        chars_left -= 1
        columns_left -= width
    return "".join(shortened).rstrip() + tail if shortened else full


def standard_windows(provider, windows):
    labels = ("5h", "7d", "fable") if provider == "claude" else ("5h", "7d")
    return [row for row in windows if row.label.casefold() in labels]


def bar_line(readings, now, selected_provider=None, max_columns=math.inf):
    entries = []
    providers = (selected_provider,) if selected_provider else tuple(PROVIDERS)
    for provider in providers:
        short, _ = PROVIDERS[provider]
        reading = readings.get(provider)
        if reading is None or not reading.windows:
            entries.append(f"{short} unavailable")
            continue
        standard = standard_windows(provider, reading.windows)
        suffix = " (stale)" if is_stale(reading, now) else ""
        if standard:
            windows = " ".join(bar_window(row, now) for row in standard)
        else:
            padding = (USED_PREFIX if provider == "codex" else "") + f"{short} {suffix}"
            windows = bar_window(
                reading.windows[0], now,
                max_chars=HERDR_MAX_STATUS_CHARS - len(padding),
                max_columns=max_columns - entry_width(padding),
            )
        entries.append(f"{short} {windows}{suffix}")
    prefix = USED_PREFIX if selected_provider != "claude" else ""
    return prefix + " · ".join(entries)


def entry_width(entry):
    # Wide glyphs can take two terminal columns even when Herdr counts one character.
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in entry)


def status_entry(readings, now, provider, columns):
    remaining = columns - TAB_BAR_RESERVED_COLUMNS
    entries = {name: bar_line(readings, now, name) for name in PROVIDERS}
    visible = {}
    def priority(name):
        reading = readings.get(name)
        if not reading or not reading.windows:
            return 2
        return 0 if standard_windows(name, reading.windows) else 1

    # Reserve the fixed windows first. Scoped names can shorten to fit what remains.
    for name in sorted(entries, key=priority):
        entry = entries[name]
        if entry_width(entry) > remaining:
            entry = bar_line(readings, now, name, max_columns=remaining)
        width = entry_width(entry)
        if width <= remaining and len(entry) <= HERDR_MAX_STATUS_CHARS:
            visible[name] = entry
            remaining -= width
    if provider == "claude" and provider in visible and "codex" not in visible:
        if remaining >= len(USED_PREFIX) and len(visible[provider]) + len(USED_PREFIX) <= HERDR_MAX_STATUS_CHARS:
            return USED_PREFIX + visible[provider]
    return visible.get(provider, "")


def herdr_tab_columns():
    binary = executable("herdr")
    pane = os.environ.get("HERDR_ACTIVE_PANE_ID")
    if not binary or not pane:
        return 0
    try:
        raw = subprocess.check_output([binary, "pane", "layout", "--pane", pane],
                                      stderr=subprocess.DEVNULL, timeout=2)
        width = json.loads(raw)["result"]["layout"]["area"]["width"]
        return width if type(width) is int else 0
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return 0


def countdown(seconds):
    minutes = math.ceil(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return " ".join(f"{qty}{unit}" for qty, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if qty) or "now"


def detail_window(window, now):
    if window.resets_at is None:
        return f"  {window.label}  {usage_percent(window.used)} used · reset time unavailable"
    reset = local_reset(window.resets_at)
    remaining = window.resets_at - now
    if remaining <= 0:
        return f"  {window.label}  Reset passed at {reset:%a %d %b %H:%M %Z} · waiting for new reading"
    return f"  {window.label}  {usage_percent(window.used)} used · resets {reset:%a %d %b %H:%M %Z} (in {countdown(remaining)})"


def herdr_inventory():
    binary = executable("herdr")
    if not binary:
        return None, []
    inventory = []
    try:
        raw = subprocess.check_output([binary, "plugin", "list", "--json"],
                                      stderr=subprocess.DEVNULL, timeout=2)
        for plugin in json.loads(raw)["result"]["plugins"]:
            name = clean_label(plugin.get("name")) or clean_label(plugin.get("id"))
            state = "enabled" if plugin.get("enabled") else "disabled"
            if name:
                inventory.append((name, state))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        inventory = None

    # Built-in integrations report lifecycle state, so surface their missing quota too.
    integrations = []
    try:
        raw = subprocess.check_output([binary, "integration", "status"],
                                      stderr=subprocess.DEVNULL, timeout=2, text=True)
        for line in raw.splitlines():
            agent, _, status = line.partition(": ")
            if status.startswith(("current ", "outdated ")) and agent not in PROVIDERS:
                integrations.append(clean_label(agent))
    except (OSError, subprocess.SubprocessError):
        pass
    return inventory, integrations


def detail_lines(readings, inventory, now, integrations=()):
    lines = ["Account usage", ""]
    for provider, (_, name) in PROVIDERS.items():
        reading = readings.get(provider)
        if not reading or not reading.windows:
            lines.extend((f"{name} · quota unavailable", ""))
            continue
        checked = datetime.fromtimestamp(reading.checked_at).astimezone()
        state = "Stale · " if is_stale(reading, now) else ""
        lines.append(f"{name} · {state}checked {checked:%d %b %H:%M %Z}")
        lines.extend(detail_window(window, now) for window in reading.windows)
        lines.append("")
    lines.append("Herdr plugins")
    if inventory is None:
        lines.append("  Plugin inventory unavailable")
    elif not inventory:
        lines.append("  No plugins installed")
    else:
        lines.extend(f"  {name} ({state}) · quota unavailable" for name, state in inventory)
    if integrations:
        lines.extend(("", "Other Herdr integrations"))
        lines.extend(f"  {agent} · quota unavailable" for agent in integrations)
    return lines


def popup(screen, path, inventory, integrations):
    screen.keypad(True)
    screen.timeout(1000)
    offset = 0
    while True:
        lines = detail_lines(read_cache(path), inventory, time.time(), integrations)
        height, width = screen.getmaxyx()
        if width > 8:
            lines = [part for line in lines
                     for part in (textwrap.wrap(line, width - 2, subsequent_indent="  ") or [""])]
        visible = max(0, height - 1)
        offset = min(max(0, offset), max(0, len(lines) - visible))
        screen.erase()
        if width > 1:
            for row, line in enumerate(lines[offset:offset + visible]):
                screen.addnstr(row, 0, line, width - 1)
            screen.addnstr(height - 1, 0, "↑/↓ scroll · q or Esc close", width - 1, curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27):
            return
        if key == curses.KEY_UP:
            offset -= 1
        elif key == curses.KEY_DOWN:
            offset += 1
        elif key == curses.KEY_PPAGE:
            offset -= visible
        elif key == curses.KEY_NPAGE:
            offset += visible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", choices=("bar", "details", "popup"))
    parser.add_argument("--provider", choices=tuple(PROVIDERS), help="render a single Herdr bar entry")
    parser.add_argument("--fit", action="store_true", help="leave room for the Herdr shortcuts")
    args = parser.parse_args()
    if args.fit and not args.provider:
        parser.error("--fit requires --provider")
    path = cache_path()
    if args.view == "bar":
        try:
            readings = refresh_cache(path)
        except OSError:
            readings = read_cache(path)
        now = time.time()
        line = (status_entry(readings, now, args.provider, herdr_tab_columns())
                if args.fit else bar_line(readings, now, args.provider))
        print(line)
        return

    inventory, integrations = herdr_inventory()
    if args.view == "popup":
        curses.wrapper(popup, path, inventory, integrations)
    else:
        print("\n".join(detail_lines(read_cache(path), inventory, time.time(), integrations)))


if __name__ == "__main__":
    main()
