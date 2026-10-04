# Recoverable skill installation

Both native wrappers require Python 3.10+ **before installation**, with no third-party
installer dependencies. Keep `installer.py`, `install_files.py`,
`install_transaction.py` and `payload.json` beside the wrappers. Python 3.10 remains
a tested compatibility floor, not a recommendation to use an EOL interpreter.

```bash
bash install/install.sh --agent claude --scope project --path /path/to/project
```

PowerShell equivalent: `./install/install.ps1 -Agent claude -Scope project -Path
<project>`. User scope is the default; `all` selects only existing agent config
roots. OpenCode user skills are under `.config/opencode/skills`, project skills
under `.opencode/skills`. The other seven mappings are unchanged.

## Replacement and instructions

Automation must explicitly supply `--force` / `-Force` to replace an existing copy.
Interactive replacement requires `y` or `yes`; Enter, EOF and unavailable input are
**No**. Force does not bypass malformed instructions, unsafe paths or recovery
conflicts. No prompt occurs after publication begins.

Only the exact standalone BEGIN/END Revayat pointer region in `AGENTS.md` is owned.
Unowned prefix/suffix bytes, UTF-8 BOM, line endings and final-newline behavior are
preserved. Malformed/duplicate/nested markers or invalid UTF-8 refuse before skill
replacement. One combined pointer describes installed pointer agents; repeating an
unchanged installation does not accumulate blank lines.

## Recovery and retained backups

The selected base contains a reserved `.revayat-novel-installer` state directory.
On POSIX it must be owner-only (0700); Windows uses the base's inherited ACLs.
Its internal `.gitignore` excludes all contents. **Never force-add, publish, upload
or package this state**: backups can contain previous private instructions.

A stable OS-held lock covers recovery and publication; lock age/PID are not consent
to steal it. All selected payloads are staged and verified on the same filesystem
before a prepared journal is written. Previous objects are renamed into retained
backups, never deleted to make room. Recovery validates every participant before
changing any: prepared work restores originals, committed work finalizes a receipt.
Recovery can be interrupted and resumed. If a target, stage or backup differs from
both recorded states, the installer refuses and preserves conflicting evidence.
Do not delete or edit the pending journal to make a refusal disappear.

Run the same installer against the same base to recover; afterward it may install
again if replacement consent is supplied. A committed operation with receipt
housekeeping failure reports `committed: true`, `cleanup_pending: true`, exit 2.
This is not a rollback. Inspect retained originals before any owner-directed manual
cleanup. There is no automatic backup pruning or age-based pending-state deletion.

Recovery does **not** provide simultaneous visibility across several agent folders:
unlocked readers can see a temporary missing name or mixed old/new installations.
Writable file handles are flushed before permissions are restored; supported POSIX
directories are synchronized. No universal power-loss, network-filesystem, ACL/ADS
copying or equally privileged hostile-race guarantee is made. Windows permissions
verification covers its native writable/read-only bit; POSIX ordinary mode bits.

## Explicit payload

`payload.json` lists exactly the sorted tracked files under `skills/revayat-novel`.
Only those files install, not untracked manuscripts, config, caches or virtualenvs.
CI compares the inventory to Git. When intentionally adding/removing a tracked skill
file, update the inventory and its `INVENTORY_SHA256` identity in `installer.py`
in the same change. The identity rejects an accidentally edited/incomplete manifest;
it is not authentication against somebody who can also modify the installer. Unsafe paths, case collisions,
missing files or source changes while staging refuse; recursive copying is not a
fallback. Links/reparse points, special files, Git-managed replaced content,
source overlap and cross-filesystem destinations are refused even with force.

## Diagnostic logs

Each engine invocation writes a unique UTF-8 log in protected installer state:
`installer_YYYY-MM-DD_HH-mm-ss_UTC_<collision-suffix>.log`. Entries are UTC
`[YYYY-MM-DD HH:mm:ss UTC] [LEVEL] [installer] Message`; INFO records startup,
participant transitions, commit/rollback, duration and exit, ERROR records sanitized
failure type. Instruction/payload bodies, credentials and environment values are
never logged. There is no verbose body-dump mode. Logging initialization failure
uses a console warning; handlers are closed. Logs are retained without automatic
rotation; manually share only a reviewed, sanitized diagnostic log, never backups
or the state directory. Pre-runtime wrapper errors appear on stderr only.

## Verification

The existing full CI matrix runs public transaction/pointer/payload controls.
Windows runs actual PowerShell, native OS locks and junction tests; POSIX runs
actual Bash and links. Tests use disposable project-owned bases, never real profiles.
Actual process termination covers publication/recovery boundaries. Mechanical
installation safety does not certify literary translation quality.
