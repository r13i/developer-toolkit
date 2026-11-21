# Herdr TUI configuration

Saved configuration and workflow preferences for Herdr TUI, validated with version 0.9.5. Herdr App is a separate product with a different configuration directory.

## Files

- `config.toml`: the active settings snapshot.
- `AGENTS.md`: workflow preferences for agents maintaining the profile.
- `.gitignore`: restricts this directory to the profile and documentation; runtime state and credentials stay outside Git.

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

Direct **Ctrl+Tab** and **Ctrl+Shift+Tab** remain available to the outer terminal, such as iTerm. Their Herdr equivalents use the prefix followed by Tab or Shift+Tab.

Herdr tab-management shortcuts are disabled. Use the outer terminal for tabs and right-click menus for renaming Herdr workspaces or panes.

Closing a workspace leaves its Git checkout on disk. Use the worktree controls separately to delete checkout files.

## Visible shortcut strip

A compact cheat sheet stays visible along the bottom of the terminal area. It starts with `Ctrl+Space >` to show that each action follows the prefix. Uppercase letters mean Shift: `w/W` creates or closes a workspace, and `p/P` splits or closes a pane.

The strip includes workspace cycling, workspace and pane actions, splitting below, worktrees, zoom, search, and help. Press the prefix followed by `?` for the full active keymap.

Herdr TUI 0.9.5 renders this text in the tab row's status area. The row stays visible even with one tab so the cheat sheet remains available; tab shortcuts remain disabled. The status area hides if the terminal becomes too narrow to fit the whole strip.

The Agents section in the left sidebar cannot be replaced with arbitrary text through the TUI's configuration. This profile uses the supported bottom status strip instead.

## Apply or restore

Run these commands from the repository root. They back up existing settings before copying this profile to the default Herdr configuration directory.

```sh
(
set -eu
herdr_config_dir="$HOME/.config/herdr"
herdr_backup_root="$HOME/.local/share/herdr-config-backups"
mkdir -p "$herdr_config_dir" "$herdr_backup_root"
herdr_backup_dir="$(mktemp -d "$herdr_backup_root/restore.XXXXXXXX")"
for herdr_file in config.toml AGENTS.md; do
  if [ -f "$herdr_config_dir/$herdr_file" ]; then
    cp -p "$herdr_config_dir/$herdr_file" "$herdr_backup_dir/"
  fi
done
cp herdr/config.toml herdr/AGENTS.md "$herdr_config_dir/"
herdr server reload-config
)
```

The reload command requires a running Herdr server. If Herdr is stopped, start it normally after copying the files. If you use `HERDR_CONFIG_PATH`, apply the configuration to that path instead of the default location.

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
