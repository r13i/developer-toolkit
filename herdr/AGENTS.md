# Herdr workflow preferences

- Use Ctrl+Space as the primary prefix and retain Ctrl+B as a fallback.
- After Ctrl+Space, release the keys and use Tab or Shift+Tab to cycle forward or backward through workspaces, with wraparound. Unbind the conflicting default pane-cycling shortcuts. Keep direct Ctrl+Tab and Ctrl+Shift+Tab for iTerm, not Herdr. The user prefers this over numbered workspace shortcuts.
- Desired improvement: press the prefix once, then repeat Tab or Shift+Tab to cycle workspaces until exiting navigation. Installed TUI 0.9.5 currently exits prefix mode after one command and has no configurable repeat mode; the existing shortcuts remain single-use.
- The user manages tabs in the outer terminal, not in Herdr. Leave Herdr tab-management shortcuts unbound. Keep one bottom status row visible for a shortcut cheat sheet, including in single-tab workspaces.
- The user does not use the Agents panel and prefers a visible shortcut reference. TUI 0.9.5 cannot replace that panel through configuration; use the bottom status strip for the cheat sheet.
- After the prefix, use W to create a workspace and Shift+W to close it; P to split a pane and Shift+P to close it; minus to split below; N to create a worktree; M or Z to zoom; G to find workspaces and agents.
- Remove conflicting default bindings whenever adding these shortcuts. Use right-click menus for renaming.
- Keep Git worktree deletion separate from closing a workspace; closing a workspace must not delete its checkout files.
- Telegram setup is paused. Resume it only when the user asks.
- Store shared Herdr settings in `r13i/developer-toolkit` under `herdr/`. Prefer an existing repository in the user's `r13i` account; ask the user before creating a new repository.
