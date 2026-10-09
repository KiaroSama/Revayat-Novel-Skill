# Offline sandbox validation

The `Sandbox validation` GitHub workflow runs on Ubuntu 24.04. It validates a
finite synthetic corpus, not private books, literary accuracy, live targets or a
complete security audit. The ordinary portable pytest, renderer and OCR lanes
remain separate and retain their existing coverage.

## Admission and execution

1. Trusted preparation builds the digest-pinned Python 3.14.8 runtime using only
   the Dockerfile and existing dependency manifests. No repository module runs
   during preparation. Dependency fetching ends before validation starts.
2. The host controller admits the exact local image ID and inspected container
   configuration. Seven negative **stdlib-only** preflights each alter one control
   and must refuse before any target import.
3. A fresh admitted container proves actual environment, user/capability/seccomp,
   namespace/route, mount, cgroup, file-limit and scratch observations. Only then
   does its trusted parent start the fixed target fixture child with a fresh
   allowlisted environment. Target output cannot overwrite the parent's control
   report.
4. Six fixture families exercise archive path refusal, external XML entity
   refusal, invalid native table spans, populated merge continuations, transaction
   refusal without book mutation, and linked/unlisted artifact refusal. Positive
   controls prevent an implementation that simply refuses every input passing.

The source staging allowlist contains native pipeline Python modules, the fixed
fixture child and the synthetic evidence helper only. Git metadata, host home,
credentials, manuscripts and the Docker socket are not mounted. Both source and
runtime/toolchain are read-only; scratch is disposable and no target output is
copied to artifacts.

## Fixed budgets

| Boundary | Ceiling |
| --- | --- |
| CPU / CPU time | 1 CPU / 60 seconds per process |
| Memory / swap / processes | 768 MiB / no swap / 32 |
| Individual file / scratch tmpfs | 8 MiB / 64 MiB |
| Controller wall / idle / captured output | 180 seconds / 30 seconds / 64 KiB per command |
| Container cleanup / CI job | 20 seconds / 12 minutes |

The scratch tmpfs contains HOME, TMPDIR and cache. `/dev` is a read-only tmpfs
with only the finite standard character devices; `/dev/pts`, `/dev/shm` and
`/dev/mqueue` are empty read-only masks, not active TTY or shared-memory stores.
The generated network configuration files are mounted read-only. Every other
writable filesystem is refused except namespace-local proc observations; proc
kernel-control paths must themselves be read-only. Standard null/random devices,
proc observation and pipes are not manuscript storage. Any failed, missing or
unknown control prevents target execution; timeouts, nonzero exit, missing case
results and unverified container cleanup fail the job. No retries hide failures.
The host force-removes each owned container and verifies its absence; workflow
cancellation also removes only containers carrying this job's ownership label.

## Evidence and limits

The workflow retains only `result.json` and UTF-8 diagnostic file logs for seven
days. Each controller execution creates a unique
`sandbox_YYYY-MM-DD_HH-mm-ss_UTC_<id>.log` with UTC entries of the form
`[timestamp UTC] [LEVEL] [SANDBOX] Message`. INFO records admission, case counts and
cleanup; ERROR records refusal types, never raw target output, environment values,
credentials or manuscript content. There is no verbose payload logging. Logging
initialization failure reports a safe console error and fails validation.

For an already prepared local Linux image, from the repository root:

```bash
python3 tests/sandbox/runner.py --image sha256:<exact-local-image-id> --report .pytest-tmp/sandbox-evidence/result.json
```

The controller contract tests run in ordinary pytest CI without Docker. They are
not proof of kernel enforcement. The actual container lane never substitutes
mocks for control proof. Docker is unavailable on the current Windows development
host, so no local Docker validation is claimed.

Containers share the runner kernel. These bounded controls are not a VM boundary,
a universal container-escape guarantee, or proof that all project source has been
security-audited. Synthetic format fixtures cover only their declared structures.
