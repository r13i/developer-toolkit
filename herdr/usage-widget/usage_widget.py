"""Display quota windows from the local Codex and Claude Code CLIs in Herdr."""

import argparse
import asyncio
import curses
import fcntl
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

REFRESH_SECONDS = 300
PROBE_TIMEOUT = 12
PROVIDERS = {"codex": "Codex", "claude": "Claude Code"}
SIDEBAR_WINDOWS = {
    "codex": (
        ("5h", "usage_codex_5h", "Codex 5h"),
        ("7d", "usage_codex_7d", "Codex 7d"),
    ),
    "claude": (
        ("5h", "usage_claude_5h", "Claude 5h"),
        ("7d", "usage_claude_7d", "Claude 7d"),
        ("fable", "usage_fable", "Fable 7d"),
    ),
}
SIDEBAR_TOKEN_NAMES = ("usage_heading", *(
    token for windows in SIDEBAR_WINDOWS.values() for _, token, _ in windows
))
SIDEBAR_TTL_MS = 45000


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
        # Named Herdr sessions share the provider cache.
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


def progress_bar(used, width):
    filled = math.floor(used * width / 100 + 0.5)
    return "█" * filled + "░" * (width - filled)


def sidebar_tokens(readings, now):
    tokens = {"usage_heading": "Quota · % used"}
    for provider, slots in SIDEBAR_WINDOWS.items():
        reading = readings.get(provider)
        if not reading or not reading.windows:
            tokens[slots[1][1]] = f"{PROVIDERS[provider].split()[0]} unavailable"
            continue
        windows = {window.label.casefold(): window for window in reading.windows}
        for key, token, label in slots:
            window = windows.get(key)
            if not window:
                continue
            if window.resets_at is not None and window.resets_at <= now:
                value = "reset due"
            elif is_stale(reading, now):
                value = "stale"
            else:
                value = f"{math.floor(window.used)}% {progress_bar(window.used, 5)}"
            tokens[token] = f"{label} {value}"
    return tokens


def report_sidebar(binary, workspace, tokens):
    args = [binary, "workspace", "report-metadata", workspace, "--source", "herdr-usage-widget",
            "--ttl-ms", str(SIDEBAR_TTL_MS)]
    for name in SIDEBAR_TOKEN_NAMES:
        args.extend(("--token", f"{name}={tokens[name]}") if name in tokens else ("--clear-token", name))
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2).returncode == 0


def publish_sidebar(path):
    binary = executable("herdr")
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not binary or not socket:
        return
    # Workspace IDs repeat between named sessions, so track focus per Herdr socket.
    session = hashlib.sha256(os.fsencode(socket)).hexdigest()[:16]
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path.parent / f"sidebar-{session}.workspace", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as state:
            fcntl.flock(state, fcntl.LOCK_EX)
            previous = state.read().strip()
            result = subprocess.check_output([binary, "workspace", "list"], stderr=subprocess.DEVNULL, timeout=2)
            workspaces = json.loads(result)["result"]["workspaces"]
            active = next((row["workspace_id"] for row in workspaces if row.get("focused")), None)
            if previous and previous != active:
                report_sidebar(binary, previous, {})
            if active and report_sidebar(binary, active, sidebar_tokens(read_cache(path), time.time())):
                state.seek(0)
                state.write(active)
                state.truncate()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return


def countdown(seconds):
    minutes = math.ceil(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return " ".join(f"{qty}{unit}" for qty, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if qty) or "now"


def detail_window(window, now):
    names = {"5h": "Five-hour window", "7d": "Weekly", "fable": "Fable weekly"}
    title = f"  {names.get(window.label.casefold(), window.label)}"
    meter = f"  {progress_bar(window.used, 24)}  {usage_percent(window.used)} used"
    if window.resets_at is None:
        return (title, meter, "  Reset time unavailable", "")
    reset = local_reset(window.resets_at)
    stamp = f"{reset:%a %d %b %H:%M %Z}"
    remaining = window.resets_at - now
    if remaining <= 0:
        return (title, f"  Reset passed at {stamp}", "  Waiting for a new reading", "")
    return (title, meter, f"  Resets {stamp} (in {countdown(remaining)})", "")


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
    for provider, name in PROVIDERS.items():
        reading = readings.get(provider)
        if not reading or not reading.windows:
            lines.extend((f"{name} · quota unavailable", ""))
            continue
        checked = datetime.fromtimestamp(reading.checked_at).astimezone()
        state = "Stale · " if is_stale(reading, now) else ""
        lines.append(f"{name} · {state}checked {checked:%d %b %H:%M %Z}")
        for window in reading.windows:
            lines.extend(detail_window(window, now))
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
                style = curses.A_BOLD if line and (not line.startswith(" ") or line.lstrip().startswith(("█", "░"))) else 0
                screen.addnstr(row, 0, line, width - 1, style)
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
    parser.add_argument("view", choices=("refresh", "sidebar", "details", "popup"))
    args = parser.parse_args()
    path = cache_path()
    if args.view in ("refresh", "sidebar"):
        if args.view == "refresh":
            try:
                refresh_cache(path)
            except OSError:
                pass
        publish_sidebar(path)
        return

    inventory, integrations = herdr_inventory()
    if args.view == "popup":
        curses.wrapper(popup, path, inventory, integrations)
    else:
        print("\n".join(detail_lines(read_cache(path), inventory, time.time(), integrations)))


if __name__ == "__main__":
    main()
