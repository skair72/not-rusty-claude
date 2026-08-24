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

Hits inside the LC_CODE_SIGNATURE blob of a THIN Mach-O are dropped, not
patched, and the skip is printed. Measured on the shipped darwin arm64 binary:
`--old com.anthropic` finds 137 hits and 2 of them are the signing identifier
stored inside the CodeDirectory, where re-signing throws the edit away and
--no-sign leaves the signature corrupt. --patch-signature overrides.

That protection reaches exactly as far as this tool can parse the container,
and no further. A fat/universal Mach-O (0xcafebabe) is not parsed - the tool
does not walk architecture slices - and neither is an ELF, a header it will not
trust, or a thin Mach-O that simply carries no LC_CODE_SIGNATURE. In every one
of those cases NOTHING is dropped and every hit is patched. Claude Code ships a
thin darwin binary, so that is not a live hazard here, but it is a real
difference in behaviour, so a run that gets no range now SAYS which of those it
met: silence was indistinguishable from "there was no signature to worry
about".

Re-signing needs `codesign`, so on any other platform this refuses up front and
tells you to use --no-sign; it never leaves a half-made file at the destination.

Usage
-----
  ./patch-claude.py --bin <path> --old <str> --new <str> --dry-run
  ./patch-claude.py --bin <path> --old <str> --new <str> --out <path>
  ./patch-claude.py --bin <path> --old <str> --new <str> --in-place
"""

import argparse
import os
import shutil
import struct
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


# The one Mach-O load command this tool has to understand, from
# <mach-o/loader.h>. LC_REQ_DYLD (0x80000000) is never set on it.
LC_CODE_SIGNATURE = 0x1D

# (magic bytes as they appear on disk) -> (struct byte order, header size).
# A 64-bit Mach-O header is 32 bytes, a 32-bit one 28; the load commands follow
# immediately. Anything not in this table - an ELF, a fat/universal binary, the
# Mach-O-shaped fixtures the test suite uses - is deliberately absent so that
# "I cannot tell" reads as None rather than as a refusal.
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe": ("<", 32),   # MH_MAGIC_64, little endian
    b"\xce\xfa\xed\xfe": ("<", 28),   # MH_MAGIC,    little endian
    b"\xfe\xed\xfa\xcf": (">", 32),   # MH_CIGAM_64, big endian
    b"\xfe\xed\xfa\xce": (">", 28),   # MH_CIGAM,    big endian
}

# The one shape it is worth naming rather than lumping in with "unrecognised",
# because it is a macOS executable that this tool will patch happily while its
# signature guard silently does nothing: a fat/universal container, from
# <mach-o/fat.h>. Parsing it means walking the architecture slices and running
# the load-command walk once per slice, which this tool does not do. (0xcafebabe
# is also a Java class file; either way the answer here is "not something I can
# find a signature in".)
FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": "a fat/universal Mach-O (FAT_MAGIC)",
    b"\xbe\xba\xfe\xca": "a fat/universal Mach-O (FAT_CIGAM)",
    b"\xca\xfe\xba\xbf": "a fat/universal Mach-O, 64-bit offsets (FAT_MAGIC_64)",
    b"\xbf\xba\xfe\xca": "a fat/universal Mach-O, 64-bit offsets (FAT_CIGAM_64)",
}

# A Mach-O header plus its load commands is kilobytes, not megabytes: the
# shipped darwin arm64 binary at /tmp/ccmac/package/claude carries 21 commands
# in 2,704 bytes, read off its header on this host. Anything claiming more than
# this is either not a Mach-O or is corrupt, and either way is not worth
# reading - and fh.read(n) allocates n bytes before it discovers the file is
# shorter, so trusting a garbage field here costs that much RAM to reject.
MAX_SIZEOFCMDS = 1 << 20


def signature_scan(path):
    """((start, end) | None, why-not | None) for the LC_CODE_SIGNATURE blob.

    The range is what the caller acts on. The second element is what it TELLS
    the user when there is no range, because "no range" has several causes and
    only one of them is "this file carries no signature". The fat/universal
    case is the one that matters: 0xcafebabe is deliberately absent from
    MACHO_MAGICS, so on a fat binary nothing is ever dropped - and before this
    note that outcome was silent and looked exactly like a thin binary with a
    clean bill of health.

    Read-only and bounded: it touches the header and the load-command table and
    nothing else, so it costs the same on a 300 MB binary as on a 3 KB one.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            magic = head[:4]
            if magic in FAT_MAGICS:
                return None, (f"{FAT_MAGICS[magic]}, which this tool does not "
                              "parse: it reads thin containers only, so it "
                              "cannot say which bytes are signature bytes")
            order_size = MACHO_MAGICS.get(magic)
            if not order_size:
                return None, ("an empty file" if not magic else
                              f"not a Mach-O this tool parses "
                              f"(magic 0x{magic.hex()})")
            order, hdr_len = order_size
            ncmds, sizeofcmds = struct.unpack_from(order + "II", head, 16)
            if not 0 < sizeofcmds <= MAX_SIZEOFCMDS or ncmds == 0:
                return None, ("a thin Mach-O whose load-command table this "
                              f"tool will not read (ncmds={ncmds}, "
                              f"sizeofcmds={sizeofcmds})")
            fh.seek(hdr_len)
            cmds = fh.read(sizeofcmds)
        off = 0
        for _ in range(ncmds):
            cmd, size = struct.unpack_from(order + "II", cmds, off)
            if size < 8:
                return None, ("a thin Mach-O with a malformed load command "
                              f"(size {size} at offset {off} of the table)")
            if cmd == LC_CODE_SIGNATURE:
                dataoff, datasize = struct.unpack_from(order + "II", cmds, off + 8)
                return (dataoff, dataoff + datasize), None
            off += size
    except (OSError, struct.error, IndexError) as exc:
        return None, f"unreadable as a Mach-O ({type(exc).__name__})"
    return None, ("a thin Mach-O carrying no LC_CODE_SIGNATURE among its "
                  f"{ncmds} load command(s)")


def code_signature_range(path):
    """(start, end) of the LC_CODE_SIGNATURE blob in `path`, or None.

    Returns None whenever the container is not a thin Mach-O it recognises,
    because the caller turns a range into a refusal and a wrong range would
    refuse a legitimate patch. signature_scan() is this same walk with the
    reason for a None attached; this is the answer on its own.
    """
    return signature_scan(path)[0]


def partition_signature_hits(hits, signature):
    """Split `hits` into (outside, inside) the signature range, in ONE pass.

    The obvious spelling of the filter - `[h for h in hits if h not in
    set(inside)]` - rebuilds that set on every hit, which is
    O(len(hits) x len(inside)). It is not academic: on synthetic thin Mach-O
    fixtures with the hits split half inside the blob and half outside,
    measured on this host end to end (`--old NEEDLE --new z --out ... --no-sign`,
    output to /dev/null, two runs each), the set-per-hit form took 4.51 s and
    4.72 s at 16,000 hits and 16.88 s and 16.90 s at 32,000 - 3.6x the time for
    2x the hits. This form took 0.10 s and 0.12 s on that same 16,000-hit
    fixture and 0.14 s and 0.13 s on the 32,000-hit one, and the patched output
    is md5-identical to the old form's on both. Isolated from the rest of the
    tool the two filters alone are 5.752 s vs 0.0010 s at 16,000 hits and
    20.239 s vs 0.0022 s at 32,000.

    A guard that exists to stop this tool hanging on a 300 MB binary must not
    become the hang, and `--old com.anthropic` on the real binary already
    returns 137 hits, so the shape is reachable from ordinary arguments.
    """
    if signature is None:
        return list(hits), []
    lo, hi = signature
    outside, inside = [], []
    for h in hits:
        if lo <= h < hi:
            inside.append(h)
        else:
            outside.append(h)
    return outside, inside


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


def codesign_path():
    """Where `codesign` lives, or None. One home for the question so the
    pre-flight check and the tests ask it the same way."""
    return shutil.which("codesign")


def require_codesign():
    """Refuse a signing run on a host that cannot sign, before anything exists.

    dump_entitlements() and original_identifier() shell out to `codesign`,
    which does not exist off macOS, and subprocess raises FileNotFoundError
    rather than returning non-zero. Measured on this Linux host with the
    fixture `HEAD NEEDLE TAIL` and `--old NEEDLE --new N --out out.bin`: the
    run ended in an unhandled FileNotFoundError traceback, exit 1, and left
    out.bin holding `HEAD NEEDLE TAIL` - a verbatim UNPATCHED copy of the
    input under the name the user asked for, with nothing in the output saying
    so. This is the pre-flight that turns that into one sentence, and main()
    reads the signing metadata before it creates anything so that a failure
    here or in codesign itself cannot leave a file behind at all.
    """
    if codesign_path() is None:
        die("codesign not found - re-signing is the macOS half of this tool. "
            "Use --no-sign to patch the bytes only, or --dry-run to rehearse.")


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
                    help="'all' (default) or a 1-based index to patch just one hit; "
                         "indexes the hits as listed below, i.e. after any inside "
                         "the code signature have been dropped")
    ap.add_argument("--patch-signature", action="store_true",
                    help="also patch hits that land inside the Mach-O code-signature "
                         "blob (refused by default; see the note below)")
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
        # whole file. Re-measured on a 1 MiB fixture with this guard replaced
        # by `pass`, output redirected to a file: 1,048,577 reported
        # occurrences and 3,145,739 lines of preview under --dry-run,
        # 3,145,742 under --no-sign, 54.3-54.5 MiB peak RSS, 5-6 s wall on
        # this shared host. --dry-run and --no-sign both exit 0 claiming a
        # successful patch; the default signing path here (Linux, no codesign)
        # prints the same flood and only then refuses, exit 1, having paid the
        # whole cost first. What that costs on the ~300 MB binary this tool is
        # aimed at, at three lines of preview per byte, is a hang.
        die("--old is empty; that matches at every offset in the file")
    if len(new) > len(old):
        die(f"replacement is longer than the original ({len(new)} > {len(old)} bytes). "
            "A Mach-O cannot grow mid-file - shorten --new.")

    # One immutable buffer, read once. Nothing below mutates blob - splice()
    # takes its own bytearray() copy and preview() only slices - so holding it
    # as a bytearray buys nothing and forces a full bytes(blob) copy at the
    # find and at every preview line. Measured with a wait4 peak-RSS wrapper
    # on a 126,877,716-byte fixture, identical arguments either way:
    # bytearray + bytes(blob) = 375.0 MiB (--dry-run) / 375.3 MiB
    # (--out --no-sign); this version = 254.2-254.4 MiB across three runs of
    # each, and the --out bytes compare equal between the two. What is left is
    # blob plus splice()'s result, which is the minimum for a tool that has to
    # keep the pre-patch bytes alive to print the previews. On the ~325 MB
    # Mach-O this is aimed at, the copy that was removed is most of a gigabyte
    # of RSS to change a handful of bytes.
    say(f"\n{BOLD}Reading{RESET} {src} ({os.path.getsize(src):,} bytes)")
    with open(src, "rb") as fh:
        blob = fh.read()

    hits = find_all(blob, old)
    if not hits:
        die("string not found in the binary")
    found = len(hits)

    # A hit inside LC_CODE_SIGNATURE is never a hit worth patching, and on the
    # real binary it is not hypothetical. Measured on the shipped darwin arm64
    # Mach-O (/tmp/ccmac/package/claude, 324,973,552 bytes, LC_CODE_SIGNATURE
    # = 324,320,704..324,973,552 read with code_signature_range above):
    # --old com.anthropic finds 137 hits, 135 in __BUN and 2 at 0x1354BE54 and
    # 0x135E69E5, inside the signature. Both are the signing Identifier
    # "com.anthropic.claude-code" stored as a literal C string in the
    # CodeDirectory. Patching them is pointless in one direction and
    # corrupting in the other: the re-sign below runs `codesign --force
    # --sign -`, which rebuilds the superblob and discards the edit, while
    # --no-sign leaves a CodeDirectory whose own identifier no longer matches
    # the hashes around it. Either way the count this tool printed would not
    # be the count that survives, which is the reason to drop them here rather
    # than to warn and patch anyway.
    signature, why_no_signature = signature_scan(src)
    if signature is None:
        # Not a warning and not an error: on the ELF this repo actually ships
        # it is the normal answer. It is printed because the alternative is
        # silence, and silence here reads as "checked, nothing inside the
        # signature" when what happened may be "never looked" - a fat/universal
        # binary takes this branch and has its signature patched like any other
        # bytes. See signature_scan().
        say(f"note: no code-signature range to protect - {why_no_signature}. "
            "No hit will be dropped as signature bytes.", DIM)
    hits, inside = partition_signature_hits(hits, signature)
    if inside:
        where = ", ".join(f"0x{h:X}" for h in inside)
        span = f"{signature[0]:,}..{signature[1]:,}"
        if args.patch_signature:
            # partition_signature_hits() already took them out; put them back
            # in offset order, which is what the listing and --occurrence and
            # the overlap check below all assume.
            hits = sorted(hits + inside)
            say(f"warning: --patch-signature given; patching {len(inside)} hit(s) "
                f"inside LC_CODE_SIGNATURE ({span}): {where}. Re-signing will "
                "discard these; --no-sign will leave the signature corrupt.", YEL)
        else:
            say(f"warning: skipping {len(inside)} of {found} hit(s) that land "
                f"inside LC_CODE_SIGNATURE ({span}): {where}. "
                "Pass --patch-signature to include them anyway.", YEL)
            if not hits:
                die("every hit is inside the code-signature blob, so there is "
                    "nothing to patch that would survive re-signing. Use "
                    "--patch-signature if you really mean to edit the signature.")

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

    # `found` is what the search returned, len(hits) what will actually be
    # written: --occurrence and the code-signature filter both narrow it, and
    # printing the narrowed number twice hid that.
    say(f"{BOLD}Found{RESET} {found} occurrence(s); patching {len(hits)}")
    say(f"  old: {len(old):>5} bytes  {CYN}{args.old[:70]}{RESET}")
    say(f"  new: {len(new):>5} bytes  {CYN}{args.new[:70]}{RESET}")
    say(f"  pad: {len(old) - len(new):>5} bytes of {args.pad!r} appended\n")

    for n, off in enumerate(hits, 1):
        say(f"  [{n}] offset 0x{off:X}")
        say(f"      {preview(blob, off, len(old))}\n")

    if args.dry_run:
        say("dry run - nothing written.", YEL)
        return

    # Decide where the bytes go, but create NOTHING yet. Everything that can
    # fail - the codesign reads below - has to fail before the destination
    # exists, or a failure leaves a file under the name the user asked for
    # that is not the patched output.
    if args.in_place:
        dest = src
    elif args.out:
        dest = os.path.abspath(args.out)
        if os.path.exists(dest) and os.path.samefile(dest, src):
            # --out <the input> is a plausible way to ask for --in-place, and
            # answering it as one would be an in-place patch with no .bak.
            # samefile, not a string compare, so a symlink or hard link to the
            # input is caught too.
            die("--out names the input; use --in-place, which keeps a .bak")
    else:
        die("choose --out <path> or --in-place (or --dry-run)")

    tmp = None if args.no_sign else tempfile.TemporaryDirectory()
    try:
        # Ask codesign about the original BEFORE anything is written, and
        # before the destination is even created. Two distinct reasons, both
        # load-bearing:
        #
        # 1. Under --in-place src IS dest, so after the write the single copy
        #    on disk carries a signature that no longer covers its own bytes -
        #    and these two answers are what the new signature is built from.
        #    Whether `codesign -d` would in fact answer differently off a
        #    patched file is UNVERIFIED here (no codesign on this host), but
        #    for at least one real invocation it demonstrably would: the
        #    Identifier it returns, "com.anthropic.claude-code", is itself
        #    stored as a literal C string in the CodeDirectory, so
        #    `--old com.anthropic --in-place --patch-signature` rewrites the
        #    very bytes this read is about.
        # 2. These calls raise rather than return non-zero when codesign is
        #    missing. Doing them here, with require_codesign() in front, is
        #    what keeps a non-macOS host from ending up with an unpatched copy
        #    of the input sitting at --out. See require_codesign().
        ent = ident = None
        if tmp:
            require_codesign()
            ent = dump_entitlements(src, os.path.join(tmp.name, "ent.plist"))
            ident = args.identifier or original_identifier(src)

        if args.in_place:
            backup = src + ".bak"
            if not os.path.exists(backup):
                say(f"{BOLD}Backup{RESET} -> {backup}")
                shutil.copy2(src, backup)
        else:
            say(f"{BOLD}Writing{RESET} -> {dest}")

        try:
            with open(dest, "wb") as fh:
                fh.write(patched)
        except OSError as exc:
            die(f"could not write {dest}: {exc}")
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
