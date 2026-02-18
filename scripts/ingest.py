#!/usr/bin/env python3
"""
Ingest a document into the chain of custody: compute SHA256, append to
manifest.json, regenerate manifest.lock, and optionally GPG-sign the manifest.

manifest.lock is a SHA256 hash of the entire manifest.json at the time of
writing. Any out-of-band modification to manifest.json will cause a lockfile
mismatch that parse.py and validate_output.py will detect and report as a
Chain of Custody Breach.

Each manifest entry includes the current Git commit hash ("git_commit"),
binding the data state to the code state. If repository history is rewritten
or the manifest is tampered with, the commit-to-hash link becomes auditable
in the Git log.

GPG signing (Trust Anchor):
  After writing, the script offers to GPG-sign manifest.json, creating a
  detached signature at manifest.json.sig. parse.py will check for this
  signature. An unsigned manifest is treated as untrusted by parse.py.

  Use --sign to sign non-interactively.
  Use --sign-key <KEY_ID> to specify a GPG key.
  Use --no-sign to explicitly skip and suppress the prompt.

Run from the repository root: python scripts/ingest.py <path-to-file>
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _gpg_available() -> bool:
    """Return True if the gpg binary is on PATH."""
    try:
        subprocess.run(["gpg", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _gpg_has_secret_key() -> bool:
    """Return True if at least one secret key is available in the keyring."""
    try:
        r = subprocess.run(
            ["gpg", "--list-secret-keys", "--with-colons"],
            capture_output=True, text=True,
        )
        return "sec" in r.stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _create_local_project_key(project_name: str = "vibe_research") -> str | None:
    """
    Generate a non-expiring, no-passphrase GPG key scoped to this project.
    Returns the key fingerprint on success, None on failure.
    This key is local only — it is never uploaded to a keyserver.
    """
    print(f"[INFO] Creating a local GPG key for project '{project_name}'…")
    batch_params = (
        f"%no-protection\n"
        f"Key-Type: default\n"
        f"Subkey-Type: default\n"
        f"Name-Real: {project_name}\n"
        f"Name-Email: {project_name}@localhost\n"
        f"Expire-Date: 0\n"
        f"%commit\n"
    )
    try:
        r = subprocess.run(
            ["gpg", "--batch", "--gen-key"],
            input=batch_params,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            # Extract the fingerprint from the output
            for line in r.stderr.splitlines():
                if "key" in line.lower() and "marked as ultimately trusted" in line.lower():
                    parts = line.split()
                    for p in parts:
                        if len(p) >= 8 and all(c in "0123456789ABCDEFabcdef" for c in p):
                            print(f"[OK] Local project key created: {p}")
                            return p
            print("[OK] Local project key created (fingerprint not parsed — use default key).")
            return ""  # Empty string → use default key
        else:
            print(f"[WARN] Key creation failed: {r.stderr.strip()}", file=sys.stderr)
            return None
    except FileNotFoundError:
        return None


def gpg_sign_manifest(manifest_path: Path, key_id: str | None = None) -> bool:
    """
    Create a GPG detached ASCII-armored signature for manifest_path.
    Returns True on success, False on failure.
    Never raises — failures are warnings, not errors.
    """
    sig_path = Path(str(manifest_path) + ".sig")
    cmd = ["gpg", "--yes", "--detach-sign", "--armor"]
    if key_id:
        cmd += ["--local-user", key_id]
    cmd.append(str(manifest_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[OK] Manifest signed. Signature: {sig_path.name}")
            return True
        else:
            print(
                f"[WARN] GPG signing failed (exit {result.returncode}): {result.stderr.strip()}\n"
                f"       The manifest is unsigned. parse.py will note UNVERIFIED DATA STATE.",
                file=sys.stderr,
            )
            return False
    except FileNotFoundError:
        print(
            "[INFO] GPG not installed — skipping manifest signing. "
            "Tip: install it later with `brew install gnupg` (macOS) or `sudo apt install gnupg` (Linux).",
            file=sys.stderr,
        )
        return False


def prompt_sign_manifest(manifest_path: Path, key_id: str | None = None) -> None:
    """
    Interactively offer GPG signing. Guides the researcher through creating a
    local project key if no key is configured. Never blocks on failure.
    """
    sig_path = Path(str(manifest_path) + ".sig")

    if not _gpg_available():
        print(
            "[INFO] GPG not installed — manifest signing skipped. "
            "Tip: `brew install gnupg` (macOS) or `sudo apt install gnupg` (Linux) to enable it.",
            file=sys.stderr,
        )
        return

    has_key = _gpg_has_secret_key()

    print(
        "\n┌─ Manifest Trust Anchor (optional) ────────────────────────────┐\n"
        "│  GPG-sign manifest.json to attach your identity to the data. │\n"
        "│  Unsigned manifests receive a WARNING in parse.py, not an    │\n"
        "│  error. Your SHA256 lockfile still provides integrity.        │\n"
        "└───────────────────────────────────────────────────────────────┘"
    )

    if not has_key:
        print(
            "  No GPG key found in your keyring.\n"
            "  Option A: Create a local project key (recommended for new users).\n"
            "  Option B: Skip for now — you can sign later."
        )
        try:
            choice = input("  [a] Create local key  [s] Skip  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "s"

        if choice == "a":
            key_id = _create_local_project_key() or key_id
            if key_id is not None:
                gpg_sign_manifest(manifest_path, key_id or None)
        else:
            print(
                f"[INFO] Signing skipped. parse.py will note UNVERIFIED DATA STATE.\n"
                f"       Sign later: gpg --detach-sign --armor {manifest_path}",
                file=sys.stderr,
            )
        return

    # Key exists — offer to sign
    try:
        choice = input("  Sign manifest.json now? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "n"

    if choice == "y":
        if not key_id:
            try:
                entered = input("  GPG Key ID or email (blank = default key): ").strip()
                key_id = entered or None
            except (EOFError, KeyboardInterrupt):
                key_id = None
        gpg_sign_manifest(manifest_path, key_id)
    else:
        print(
            f"[INFO] Signing skipped.\n"
            f"       Sign later: gpg --detach-sign --armor {manifest_path}\n"
            f"       Expected:   {sig_path}",
            file=sys.stderr,
        )


def get_git_commit() -> str:
    """
    Return the current HEAD commit hash (short+long: full 40-char).
    Returns "NO_GIT_COMMIT" if the repo has no commits yet or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "NO_GIT_COMMIT"


def get_file_sha256(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_manifest_sha256(manifest_path: Path) -> str:
    """Return the SHA256 of the manifest file as written on disk."""
    return get_file_sha256(manifest_path)


def check_lockfile_integrity(manifest_path: Path, lock_path: Path) -> None:
    """
    Compare the current manifest.json hash against manifest.lock.
    If they differ and a lockfile exists, someone edited manifest.json
    outside of ingest.py — flag a Chain of Custody Breach and exit.
    """
    if not lock_path.exists():
        return  # First run; no lock yet

    try:
        with open(lock_path) as f:
            lock_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  CRITICAL: CHAIN OF CUSTODY BREACH DETECTED                 ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  manifest.lock is corrupted or unreadable.                  ║\n"
            f"║  Error: {str(exc)[:54]:<54}║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  The lockfile may have been damaged or tampered with.       ║\n"
            "║  If you trust the current manifest.json, delete             ║\n"
            "║  manifest.lock and re-run ingest.py to regenerate it.       ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        sys.exit(1)

    recorded_hash = lock_data.get("manifest_sha256", "")
    actual_hash = get_manifest_sha256(manifest_path)

    if recorded_hash != actual_hash:
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  CRITICAL: CHAIN OF CUSTODY BREACH DETECTED                 ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  manifest.json was modified outside of ingest.py.           ║\n"
            f"║  Expected: {recorded_hash:<50}║\n"
            f"║  Found   : {actual_hash:<50}║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  Do not add entries to manifest.json by hand.               ║\n"
            "║  Investigate the modification before proceeding.            ║\n"
            "║  If intentional, delete manifest.lock and re-run ingest.py. ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        sys.exit(1)


def write_lockfile(manifest_path: Path, lock_path: Path) -> str:
    """Write manifest.lock with the SHA256 of the just-written manifest."""
    digest = get_manifest_sha256(manifest_path)
    lock_data = {
        "generated_by": "ingest.py",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "manifest_sha256": digest,
        "note": (
            "This file is generated automatically by ingest.py. "
            "Do not edit it by hand. Any manual change to manifest.json "
            "will be detected as a Chain of Custody Breach."
        ),
    }
    with open(lock_path, "w") as f:
        json.dump(lock_data, f, indent=2)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a document and add it to the manifest (chain of custody)."
    )
    parser.add_argument("file", type=Path, help="Path to the document (e.g. data/raw/myfile.pdf)")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest.json"),
        help="Path to manifest file (default: data/manifest.json)",
    )

    sign_group = parser.add_mutually_exclusive_group()
    sign_group.add_argument(
        "--sign",
        action="store_true",
        default=False,
        help="GPG-sign the manifest after writing (non-interactive).",
    )
    sign_group.add_argument(
        "--no-sign",
        action="store_true",
        default=False,
        help="Skip GPG signing and suppress the interactive prompt.",
    )
    parser.add_argument(
        "--sign-key",
        type=str,
        default=None,
        metavar="KEY_ID",
        help="GPG key ID or email to use for signing (optional; defaults to GPG default key).",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    manifest_path = args.manifest.resolve()
    lock_path = manifest_path.with_suffix(".lock")

    # ── Step 1: Check lockfile before touching anything ────────────────────
    if manifest_path.exists():
        check_lockfile_integrity(manifest_path, lock_path)

    # ── Step 2: Compute SHA256 of the incoming file ────────────────────────
    digest = get_file_sha256(path)

    # ── Step 3: Load or initialise manifest ───────────────────────────────
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = []

    # Warn if this file (by hash) is already in the manifest
    duplicate = next((e for e in manifest if e.get("sha256") == digest), None)
    if duplicate:
        print(
            f"[WARN] A file with this SHA256 is already in the manifest "
            f"(ingested as '{duplicate['filename']}' at {duplicate['ingested_at']}). "
            f"Proceeding will add a second entry.",
            file=sys.stderr,
        )

    # ── Step 4: Capture git commit (git-bind) ─────────────────────────────
    git_commit = get_git_commit()
    if git_commit == "NO_GIT_COMMIT":
        print(
            "[WARN] No Git commit found. The 'git_commit' field will be recorded as "
            "'NO_GIT_COMMIT'. Commit your work to enable the git-bind audit trail.",
            file=sys.stderr,
        )

    # ── Step 5: Append entry ───────────────────────────────────────────────
    entry = {
        "filename": path.name,
        "sha256": digest,
        "ingested_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_path": str(path),
        "git_commit": git_commit,
    }
    manifest.append(entry)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # ── Step 6: Regenerate lockfile ────────────────────────────────────────
    lock_digest = write_lockfile(manifest_path, lock_path)

    print(f"[OK] Ingested: {path.name}")
    print(f"     File SHA256     : {digest}")
    print(f"     Git commit      : {git_commit}")
    print(f"     Manifest        : {manifest_path}")
    print(f"     Manifest SHA256 : {lock_digest}  (written to {lock_path.name})")

    # ── Step 7: GPG-sign the manifest (Trust Anchor) ───────────────────────
    if args.sign:
        gpg_sign_manifest(manifest_path, args.sign_key)
    elif args.no_sign:
        print(
            "[INFO] Manifest signing skipped (--no-sign). "
            "parse.py will note UNVERIFIED DATA STATE (warning only).",
            file=sys.stderr,
        )
    else:
        # Interactive prompt only when attached to a real terminal
        if sys.stdin.isatty():
            prompt_sign_manifest(manifest_path, args.sign_key)
        else:
            print(
                "[INFO] Non-interactive session: skipping GPG prompt.\n"
                f"       To sign: gpg --detach-sign --armor {manifest_path}\n"
                f"       Or rerun with: python scripts/ingest.py --sign {path}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
