# Offline CLI sandbox

`Sandbox validation` builds `revayat-novel` with Rust 1.100.0 and copies the
executable, skills and evaluation cases into a Debian Bookworm runtime image.
The runtime runs as UID and GID 65532. It does not contain the build toolchain
or the optional OCR and Word rendering tools. The **build** needs network access
to fetch images, build tools and dependencies. The **checks** run without network
access, with a read-only root filesystem, no Linux capabilities, and no new
privileges.

Run from the repository root on a machine with Docker in Linux container mode,
Bash, Git, and `jq`. The build context comes from committed workspace paths;
local secrets, logs and build outputs stay out of it.

```bash
set -euo pipefail
context=$(mktemp -d)
answers=$(mktemp)
trap 'rm -rf -- "$context"; rm -f -- "$answers"' EXIT
git archive HEAD Cargo.toml Cargo.lock rust-toolchain.toml crates skills evaluation/cases.json \
  | tar -xf - -C "$context"
docker build --file - --tag revayat-novel:sandbox "$context" < tests/sandbox/Dockerfile
sandbox=(docker run --rm --network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges)
"${sandbox[@]}" revayat-novel:sandbox doctor --json
printf '%s\n' 'این کتاب را می‌خوانم.' | "${sandbox[@]}" -i revayat-novel:sandbox persian-lint --check --style
jq '.cases | map({key: .id, value: .accepts[0]}) | from_entries' "$context/evaluation/cases.json" > "$answers"
chmod a+r "$answers"
"${sandbox[@]}" --mount "type=bind,src=$answers,dst=/answers.json,readonly" \
  revayat-novel:sandbox eval-score --answers /answers.json --json
```

CI asserts the non-root image user, the font and optional-tool report, UTF-8
normalization, and a passing result for all 28 recorded controls. The answers
come from the accepted variants on purpose: this proves the scorer runs in the
sandbox. It does not assess new translations, prove isolation for other runs, or
replace a security audit. Mount only the generated answers file, never a private
book or a working directory.
