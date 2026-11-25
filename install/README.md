# Installing Revayat Novel

Revayat Novel v3.0.0 ships a single native Rust CLI (`revayat-novel`) with a built-in recoverable installer (`crates/revayat-installer`). You do not need Python, pip, PowerShell scripts, or Bash wrappers.

## Install into an agent

Run from the repository root (or pass `--payload <path-to-skills/revayat-novel>`):

```powershell
.\bin\revayat-novel.exe install --agent claude --scope user
```

```bash
./bin/revayat-novel install --agent codex --scope project
```

Supported `--agent` values: `claude`, `kiro`, `codex`, `cursor`, `cline`, `hermes`, `opencode`, `antigravity`, and `all`.

Supported `--scope` values: `user` and `project`. Pass `--path <dir>` to override the base directory, `--force` to replace an existing install atomically, and `--status` to inspect installed state without writing files.

## Recovery and safety guarantees

- Every install acquires an OS-level lock (`fs2`) and writes a versioned install journal before touching destination files.
- Re-running after an interrupted install recovers cleanly from the backup directory.
- Git-tracked `AGENTS.md` files are never overwritten; the installer refuses rather than clobbering tracked workspace files.
- `payload.json` lists the exact 26 skill files (`SKILL.md`, 20 reference guides, and the bundled Vazirmatn v33.003 font pack + `OFL.txt`) copied into the destination skill folder alongside the `revayat-novel` binary.
