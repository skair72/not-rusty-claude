#!/usr/bin/env python3
"""
patch-claude.py - length-preserving byte patcher + ad-hoc re-signer for the
native (Bun-compiled) Claude Code Mach-O binary, or any signed macOS executable.

Why it works the way it does
----------------------------
The replacement is padded to the EXACT length of the string it replaces. Offsets
in a Mach-O are absolute: load commands, __LINKEDIT, and Bun's embedded-bundle
offsets all break if the file grows or shrinks mid-file. So a patch may be
same-length or shorter, never longer.

Padding lands at the END of the replacement. If you include the surrounding
quotes in --old/--new, the padding falls OUTSIDE the string literal as harmless
JS whitespace and the string value stays exactly what you typed:

    --old '"long original text"'  --new '"short"'
      ->  "short"<12 spaces>          valid JS, identical byte length

Omit the quotes and the padding lands INSIDE the literal as trailing spaces.
That is fine for prose, wrong for a filesystem path.

Modifying the bytes invalidates the Developer ID signature, and under the
hardened runtime macOS SIGKILLs an invalid binary on launch. So this re-signs
ad-hoc, carrying over the original entitlements and keeping runtime hardening.
Without those entitlements the process starts and then dies the moment it JITs.

Usage
-----
  ./patch-claude.py --bin <path> --old <str> --new <str> --dry-run
  ./patch-claude.py --bin <path> --old <str> --new <str> --out <path>
  ./patch-claude.py --bin <path> --old <str> --new <str> --in-place
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

RESET, BOLD, DIM, RED, GRN, YEL, CYN = (
    "\033[0m", "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m"
)


def say(msg, colour=""):
    print(f"{colour}{msg}{RESET}" if colour else msg)


def die(msg):
    say(f"error: {msg}", RED)
    sys.exit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_all(haystack, needle):
    hits, i = [], haystack.find(needle)
    while i != -1:
        hits.append(i)
        i = haystack.find(needle, i + 1)
    return hits


def splice(blob, hits, old_len, padded):
    """Return a copy of blob with `padded` written over old_len bytes at each hit.

    Raises instead of returning a resized buffer. That is the whole invariant:
    Mach-O offsets are absolute, so a result even one byte longer or shorter
    than the input has relocated every load command, __LINKEDIT entry and
    embedded-bundle offset past the first hit. Callers must run this BEFORE
    opening the destination - under --in-place the write destroys the original
    and only the .bak is left.
    """
    out = bytearray(blob)
    for off in hits:
        out[off:off + old_len] = padded
    if len(out) != len(blob):
        raise ValueError(
            f"patch would resize the file ({len(blob):,} -> {len(out):,} bytes): "
            f"{len(padded)} bytes of replacement for a {old_len}-byte span")
    return out


def preview(blob, off, length, width=48):
    lo = max(0, off - width)
    hi = min(len(blob), off + length + width)
    before = blob[lo:off].decode("utf-8", "replace")
    match = blob[off:off + length].decode("utf-8", "replace")
    after = blob[off + length:hi].decode("utf-8", "replace")
    clean = lambda s: s.replace("\n", "\\n").replace("\r", "\\r")
    return f"{DIM}{clean(before)}{RESET}{YEL}{clean(match)}{RESET}{DIM}{clean(after)}{RESET}"


def original_identifier(binary):
    """Read the signing identifier off the original binary.

    codesign otherwise invents one from the filename, which would silently
    rename com.anthropic.claude-code to claude-patched-<hash> and break
    anything keyed on the identifier (TCC grants, the .app bundle logic).
    """
    res = run(["codesign", "-dvvv", binary])
    for line in (res.stdout + res.stderr).splitlines():
        if line.startswith("Identifier="):
            return line.split("=", 1)[1].strip()
    return None


def dump_entitlements(binary, dest):
    """Capture the original entitlements as an XML plist."""
    res = run(["codesign", "-d", "--entitlements", "-", "--xml", binary])
    xml = res.stdout.strip()
    if not xml.startswith("<?xml"):
        # Older codesign writes the plist to stderr, or the binary has none.
        xml = res.stderr.strip() if res.stderr.strip().startswith("<?xml") else ""
    if not xml:
        return None
    with open(dest, "w") as fh:
        fh.write(xml)
    return dest


def resign(binary, entitlements, identifier):
    cmd = ["codesign", "--force", "--options", "runtime"]
    if identifier:
        cmd += ["-i", identifier]
    if entitlements:
        cmd += ["--entitlements", entitlements]
    cmd += ["--sign", "-", binary]
    say(f"  $ {' '.join(cmd)}", DIM)
    res = run(cmd)
    if res.returncode != 0:
        die(f"codesign failed:\n{res.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser(
        description="Length-preserving patch + ad-hoc re-sign for a signed macOS binary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--bin", required=True, help="binary to patch")
    ap.add_argument("--old", required=True, help="exact string to replace")
    ap.add_argument("--new", required=True, help="replacement (must be <= --old in bytes)")
    ap.add_argument("--pad", default=" ", help="pad byte, default space (use '\\0' for C strings)")
    ap.add_argument("--occurrence", default="all",
                    help="'all' (default) or a 1-based index to patch just one hit")
    ap.add_argument("--out", help="write the patched binary here")
    ap.add_argument("--in-place", action="store_true", help="patch --bin directly (keeps a .bak)")
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--no-sign", action="store_true", help="patch but skip re-signing (will not launch)")
    ap.add_argument("--identifier", help="signing identifier; defaults to the original binary's")
    ap.add_argument("--verify", metavar="ARG", action="append",
                    help="run the patched binary with this arg to confirm it launches "
                         "(repeatable; attach dashed args with =, as --verify=--version, "
                         "or argparse reads them as options)")
    args = ap.parse_args()

    src = os.path.abspath(args.bin)
    if not os.path.isfile(src):
        die(f"no such file: {src}")

    pad = args.pad.encode().decode("unicode_escape").encode("utf-8")
    if len(pad) != 1:
        die("--pad must be exactly one byte")

    old = args.old.encode("utf-8")
    new = args.new.encode("utf-8")
    if not old:
        # b"".find() succeeds at every offset, so an empty --old "matches" the
        # whole file. Measured here on a 1 MiB fixture with this guard removed:
        # 1,048,577 reported occurrences, 3,145,742 lines of preview, 55.8 MB
        # peak RSS, then exit 0 claiming a successful patch. Wall clock was 41 s
        # in two runs on this (shared, loaded) host - load-dependent, so read it
        # as "tens of seconds per MiB", not as a fixed cost. On the ~300 MB
        # binary this tool is aimed at, that is a hang.
        die("--old is empty; that matches at every offset in the file")
    if len(new) > len(old):
        die(f"replacement is longer than the original ({len(new)} > {len(old)} bytes). "
            "A Mach-O cannot grow mid-file - shorten --new.")

    say(f"\n{BOLD}Reading{RESET} {src} ({os.path.getsize(src):,} bytes)")
    with open(src, "rb") as fh:
        blob = bytearray(fh.read())

    hits = find_all(bytes(blob), old)
    if not hits:
        die("string not found in the binary")

    if args.occurrence != "all":
        try:
            idx = int(args.occurrence)
        except ValueError:
            die("--occurrence must be 'all' or an integer")
        if not 1 <= idx <= len(hits):
            die(f"--occurrence {idx} out of range (found {len(hits)})")
        hits = [hits[idx - 1]]

    # Overlapping hits clobber each other. find_all advances one byte at a
    # time, so --old aaa matches twice in "aaaa" and the second write lands on
    # top of the first replacement's padding: measured, "PREFIX aaaa SUFFIX"
    # with --new z came out as "PREFIX zz   SUFFIX", a doubled z nobody asked
    # for. The length is still right, so neither splice() nor the on-disk
    # size check can see it.
    # Refuse instead; --occurrence selects a single hit and stays usable.
    touching = [n for n in range(1, len(hits)) if hits[n] - hits[n - 1] < len(old)]
    if touching:
        die(f"--old overlaps itself: {len(touching)} of the {len(hits)} hits start "
            f"within {len(old)} bytes of the previous one, so patching them all "
            "would write over each other's padding. Use --occurrence.")

    padded = new + pad * (len(old) - len(new))
    try:
        patched = splice(blob, hits, len(old), padded)
    except ValueError as exc:
        # Deliberately before --dry-run returns and before the destination is
        # created: a rehearsal should report this, and a real run should not
        # have overwritten anything by the time it is noticed.
        die(str(exc))

    say(f"{BOLD}Found{RESET} {len(hits)} occurrence(s); patching {len(hits)}")
    say(f"  old: {len(old):>5} bytes  {CYN}{args.old[:70]}{RESET}")
    say(f"  new: {len(new):>5} bytes  {CYN}{args.new[:70]}{RESET}")
    say(f"  pad: {len(old) - len(new):>5} bytes of {args.pad!r} appended\n")

    for n, off in enumerate(hits, 1):
        say(f"  [{n}] offset 0x{off:X}")
        say(f"      {preview(bytes(blob), off, len(old))}\n")

    if args.dry_run:
        say("dry run - nothing written.", YEL)
        return

    if args.in_place:
        dest = src
        backup = src + ".bak"
        if not os.path.exists(backup):
            say(f"{BOLD}Backup{RESET} -> {backup}")
            shutil.copy2(src, backup)
    elif args.out:
        dest = os.path.abspath(args.out)
        say(f"{BOLD}Copying{RESET} -> {dest}")
        shutil.copy2(src, dest)
    else:
        die("choose --out <path> or --in-place (or --dry-run)")

    # Ask codesign about the original BEFORE the patch lands. Defensive
    # ordering, and the premise is UNVERIFIED: this was written on a host with
    # no codesign, and `codesign -d` reads the embedded signature superblob
    # rather than validating page hashes, so the reads would very likely return
    # the same identifier and entitlements after the write as before it. What
    # is certain is only that under --in-place src IS dest, so afterwards the
    # single copy on disk carries a signature that no longer covers its own
    # bytes - and these two answers are what the new signature is built from.
    # Reading them first costs nothing and removes the question.
    tmp = None if args.no_sign else tempfile.TemporaryDirectory()
    try:
        ent = ident = None
        if tmp:
            ent = dump_entitlements(src, os.path.join(tmp.name, "ent.plist"))
            ident = args.identifier or original_identifier(src)

        with open(dest, "wb") as fh:
            fh.write(patched)
        os.chmod(dest, 0o755)

        # splice() already guaranteed the buffer's length, so what is left for
        # this to catch is the write itself landing short - a quota or ENOSPC
        # that the buffered write did not surface. Saying "Patched" over a
        # truncated 300 MB binary is the failure mode; recovery is the .bak.
        written = os.path.getsize(dest)
        if written != len(patched):
            die(f"wrote {len(patched):,} bytes but {dest} holds {written:,} - "
                "truncated write, recover from the .bak")
        say(f"{GRN}Patched{RESET} - size unchanged at {written:,} bytes\n")

        if args.no_sign:
            say("--no-sign given; the binary is now unsigned-invalid and will be killed on launch.", YEL)
            return

        if ent:
            say(f"{BOLD}Entitlements{RESET} carried over from the original:")
            with open(ent) as fh:
                body = fh.read()
            for key in sorted(k.split("</key>")[0] for k in body.split("<key>")[1:]):
                say(f"  - {key}", DIM)
        else:
            say("no entitlements found on the original", YEL)

        if ident:
            say(f"\n{BOLD}Identifier{RESET} preserved as {CYN}{ident}{RESET}")
        else:
            say("\ncould not read the original identifier; codesign will derive "
                "one from the filename", YEL)

        say(f"{BOLD}Re-signing{RESET} ad-hoc, hardened runtime preserved")
        resign(dest, ent, ident)
    finally:
        if tmp:
            tmp.cleanup()

    # A quarantine xattr plus an ad-hoc signature is a launch block; drop it.
    run(["xattr", "-d", "com.apple.quarantine", dest])

    say(f"\n{BOLD}Verifying{RESET}")
    res = run(["codesign", "-v", "--verbose=2", dest])
    out = (res.stdout + res.stderr).strip()
    say(f"  {GRN if res.returncode == 0 else RED}{out}{RESET}")
    if res.returncode != 0:
        die("signature verification failed")

    if args.verify:
        say(f"\n{BOLD}Launching{RESET} {' '.join(args.verify)}")
        res = run([dest] + args.verify)
        out = (res.stdout + res.stderr).strip()
        for line in out.splitlines()[:12]:
            say(f"  {line}")
        if res.returncode != 0:
            die(f"binary exited {res.returncode}")
        say(f"  {GRN}launched cleanly{RESET}")

    say(f"\n{GRN}{BOLD}Done.{RESET} {dest}")
    say(f"{DIM}Note: now ad-hoc signed, not notarized. TCC permissions (mic, Apple Events)")
    say(f"reset because the code identity changed. Auto-update will overwrite this.{RESET}\n")


if __name__ == "__main__":
    main()
