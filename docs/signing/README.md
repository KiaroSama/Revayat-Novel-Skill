# Verify a Revayat Novel release

Download the binary for your system, `SHA256SUMS.txt`, and `SHA256SUMS.txt.sig` from the release. For Linux, also download the binary's `.sig` file. Get the public verification keys from this directory in a trusted checkout, not from the release assets you are checking.

## Windows Authenticode

Run these commands in PowerShell, replacing the file name if you downloaded the ARM64 build:

```powershell
$signature = Get-AuthenticodeSignature -LiteralPath ./revayat-novel-v3.0.0-windows-x64.exe
$signature | Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
$signature.SignerCertificate.Thumbprint
```

The signer thumbprint must be `9914D665F5EE65433E098B84A6F3E50891B18087`, matching [`revayat-novel-codesign.cer`](revayat-novel-codesign.cer). `HashMismatch` means the file changed after signing. Windows can report an untrusted signer for a self-signed certificate. Do not treat that status as `Valid`; verify the signed checksums and provenance independently. You do not need to install the certificate to check the thumbprint.

## Linux Ed25519 and checksums

Use OpenSSL 3 with [`revayat-novel-release-ed25519.pub.pem`](revayat-novel-release-ed25519.pub.pem) from this directory. Verify the signature on the checksum list before relying on it. If you downloaded the Linux ARM64 build, replace both `revayat-novel-v3.0.0-linux-x64` and `revayat-novel-v3.0.0-linux-x64.sig` with `revayat-novel-v3.0.0-linux-arm64` and `revayat-novel-v3.0.0-linux-arm64.sig`:

```bash
openssl pkeyutl -verify -pubin -inkey revayat-novel-release-ed25519.pub.pem -rawin -in SHA256SUMS.txt -sigfile SHA256SUMS.txt.sig
sha256sum --check --ignore-missing SHA256SUMS.txt
openssl pkeyutl -verify -pubin -inkey revayat-novel-release-ed25519.pub.pem -rawin -in revayat-novel-v3.0.0-linux-x64 -sigfile revayat-novel-v3.0.0-linux-x64.sig
```

Confirm that your binary is listed as `OK`. On Windows, run `Get-FileHash -Algorithm SHA256 -LiteralPath <binary>` in PowerShell and compare it with the matching line in the verified list. Stop if a signature or checksum fails.

## GitHub build provenance

Use an authenticated GitHub CLI to check that the binary came from this repository's release workflow:

```bash
gh attestation verify revayat-novel-v3.0.0-linux-x64 --repo KiaroSama/Revayat-Novel-Skill --signer-workflow KiaroSama/Revayat-Novel-Skill/.github/workflows/release.yml
```

Replace the file name with the binary you downloaded. Provenance covers the binaries, not the detached signature files.
