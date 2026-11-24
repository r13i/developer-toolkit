# Herdr TUI configuration

Saved configuration and workflow preferences for Herdr TUI, validated with version 0.9.5. Herdr App is a separate product with a different configuration directory.

## Files

- `config.toml`: the active settings snapshot.
- `AGENTS.md`: workflow preferences for agents maintaining the profile.
- `usage-widget/` contains the plugin manifest, Python script, and tests.
- [`.env.example`](.env.example) lists the optional environment settings.
- `.gitignore` keeps runtime state and credentials outside Git.

## Shortcuts

Press **Ctrl+Space**, release the keys, then press an action key. **Ctrl+B** remains a fallback prefix.

| Key after the prefix | Action |
| --- | --- |
| Tab / Shift+Tab | Next / previous workspace, with wraparound |
| w / Shift+w | Create / close a workspace |
| p / Shift+p | Split right / close a pane |
| - | Split below |
| n | Create a Git worktree |
| m or z | Maximize / restore the pane |
| g | Find workspaces and agents |
| ? | Show the active shortcuts |
| u | Open account usage |

Direct **Ctrl+Tab** and **Ctrl+Shift+Tab** remain available to the outer terminal, such as iTerm. Their Herdr equivalents use the prefix followed by Tab or Shift+Tab.

Herdr tab-management shortcuts are disabled. Use the outer terminal for tabs and right-click menus for renaming Herdr workspaces or panes.

Closing a workspace leaves its Git checkout on disk. Use the worktree controls separately to delete checkout files.

## Visible shortcut strip

A compact cheat sheet stays visible along the bottom of the terminal area. It starts with `Ctrl+Space >` to show that each action follows the prefix. Uppercase letters mean Shift. `w/W` creates or closes a workspace, and `p/P` splits or closes a pane.

The strip includes workspace cycling, workspace and pane actions, splitting below, worktrees, zoom, search, and help. Press the prefix followed by `?` for the full active keymap.

Herdr renders the shortcuts in the tab row's status area. The row stays visible with one tab.

## Account usage

The expanded sidebar shows a progress bar and percent used for each main quota beneath the active workspace. Fable has a separate weekly bar when Claude reports it. Press **Ctrl+Space**, then **u**, for larger bars, all reported quota windows, full local reset dates, countdowns, and the installed Herdr plugin list. Use the arrow keys to scroll. Press **q** or **Esc** to close.

The widget refreshes every five minutes through the signed-in CLIs. It shows only the quota windows each account reports. It flags stale readings and waits for new data after a reset. Herdr plugins and other installed integrations without a quota feed show `quota unavailable`.

The widget requires Herdr 0.9.5 or later and Python 3.10 or later on macOS or Linux. It uses Codex's App Server and Claude Code's experimental usage request. The Claude probe runs in safe mode with telemetry, update checks, and error reporting disabled. Both CLIs use their existing sign-ins. The widget stores usage windows, their timestamps, and the last workspace ID it updated for each local Herdr session in `$XDG_STATE_HOME/herdr/usage-widget/`, which defaults to `~/.local/state/herdr/usage-widget/`. It does not read credentials, browser cookies, or transcripts.

## Apply or restore

Run these commands from the repository root. They back up existing settings before copying this profile to the default Herdr configuration directory.

```sh
(
set -eu
herdr_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/herdr"
herdr_backup_root="$HOME/.local/share/herdr-config-backups"
mkdir -p "$herdr_config_dir" "$herdr_backup_root"
herdr_backup_dir="$(mktemp -d "$herdr_backup_root/restore.XXXXXXXX")"
for herdr_file in config.toml AGENTS.md; do
  if [ -f "$herdr_config_dir/$herdr_file" ]; then
    cp -p "$herdr_config_dir/$herdr_file" "$herdr_backup_dir/"
  fi
done
if [ -d "$herdr_config_dir/usage-widget" ]; then
  cp -pR "$herdr_config_dir/usage-widget" "$herdr_backup_dir/"
fi
HERDR_CONFIG_PATH="$PWD/herdr/config.toml" herdr config check
install -d -m 700 "$herdr_config_dir/usage-widget"
install -m 644 herdr/usage-widget/herdr-plugin.toml herdr/usage-widget/usage_widget.py "$herdr_config_dir/usage-widget/"
herdr plugin link "$herdr_config_dir/usage-widget" --enabled
cp herdr/config.toml herdr/AGENTS.md "$herdr_config_dir/"
herdr server reload-config
)
```

Start Herdr before applying the profile so the link and reload commands can reach the server. If you use `HERDR_CONFIG_PATH`, copy `config.toml` to that path. Keep the plugin directory at `${XDG_CONFIG_HOME:-$HOME/.config}/herdr/usage-widget` for the scheduled refresh.

Run the widget checks from the repository root with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s herdr/usage-widget -v`.

## Save later changes

From the repository root in a task worktree, copy the live files back before reviewing and committing the changes:

```sh
cp "$HOME/.config/herdr/config.toml" "$HOME/.config/herdr/AGENTS.md" herdr/
git diff -- herdr/
```

## Limits and paused work

The prefix handles one action at a time. Repeated workspace cycling still requires repeating the prefix. Keeping a workspace navigation mode active until Esc is a desired improvement; it is not enabled by this profile. See the [community request for persistent modes](https://github.com/herdrdev/herdr/discussions/784).

This directory does not install or configure Telegram. Telegram setup remains paused. Do not add bot tokens, chat credentials, terminal logs, or session snapshots to the tracked files.

Reference: [Herdr TUI keyboard guide](https://herdr.dev/tui/docs/keyboard/) and [configuration guide](https://herdr.dev/tui/docs/configuration/).
