#!/usr/bin/env python3
"""
Reset a single user's password in the Capelle Platform Postgres store.

Why this script exists
----------------------
Passwords are stored as bcrypt hashes (see
``backend/capelle_platform/auth/impl_jwt.py::JWTAuthenticator.hash_password``)
so an admin cannot retrieve a user's existing password — only replace it.
This tool generates (or accepts) a new password, hashes it with the *same*
bcrypt parameters the app uses, and atomically swaps the user's
``hashed_password`` column.

What it does
------------
1. Locate exactly one ``users`` row by email (case-insensitive).
2. Generate a 20-char URL-safe random password unless ``--password`` is given.
3. Hash via ``bcrypt.hashpw`` with auto-generated salt — same call the
   running app makes during registration.
4. ``UPDATE users SET hashed_password = $1 WHERE id = $2`` inside a
   transaction. Abort if rowcount != 1.
5. Print the new credentials so you can forward them out-of-band.

Safety
------
- Refuses to run if no user matches (would silently fail).
- Refuses to run if multiple users match (ambiguous; data hygiene issue).
- Dry-run by default — pass ``--apply`` to actually write.
- Never reads or logs the existing hash.

Usage
-----
    # Dry run (default) — shows what *would* happen
    python3 scripts/reset_user_password.py --email saleh@example.com

    # Actually reset, with auto-generated password
    python3 scripts/reset_user_password.py --email saleh@example.com --apply

    # Provide your own password
    python3 scripts/reset_user_password.py \\
        --email saleh@example.com --password 'something-strong' --apply

    # Point at a non-default DSN (otherwise reads $CAPELLE_POSTGRES_DSN)
    python3 scripts/reset_user_password.py \\
        --email saleh@example.com --apply \\
        --dsn postgresql://capelle:<pw>@aoi-todo:5432/capelle
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from typing import Final

import asyncpg
import bcrypt

_DEFAULT_DSN_ENV: Final[str] = "CAPELLE_POSTGRES_DSN"
_GENERATED_PASSWORD_BYTES: Final[int] = 15  # → ~20 URL-safe chars
_MIN_PASSWORD_LEN: Final[int] = 6  # matches UserCreate.password.min_length


def _generate_password() -> str:
    """Return a fresh URL-safe random password.

    20 URL-safe chars ≈ 120 bits of entropy — well above brute-force
    feasibility against bcrypt.
    """
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


def _hash_password(raw: str) -> str:
    """Bcrypt-hash exactly the way ``JWTAuthenticator.hash_password`` does."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(raw.encode("utf-8"), salt).decode("utf-8")


def _resolve_dsn(cli_dsn: str | None) -> str:
    if cli_dsn:
        return cli_dsn
    env_dsn = os.environ.get(_DEFAULT_DSN_ENV, "").strip()
    if env_dsn:
        return env_dsn
    raise SystemExit(
        f"No DSN: pass --dsn or set ${_DEFAULT_DSN_ENV} "
        f"(shape: postgresql://capelle:<pw>@aoi-todo:5432/capelle)."
    )


def _mask_dsn(dsn: str) -> str:
    """Hide the password portion when echoing the DSN."""
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    creds, hostpath = rest.split("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        creds = f"{user}:<redacted>"
    return f"{scheme}://{creds}@{hostpath}"


async def _reset(
    *,
    dsn: str,
    email: str,
    new_password: str,
    apply: bool,
) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, email FROM users WHERE LOWER(email) = LOWER($1)",
            email,
        )
        if not rows:
            print(f"ERROR: no user matches email={email!r}", file=sys.stderr)
            return 2
        if len(rows) > 1:
            print(
                f"ERROR: {len(rows)} users match email={email!r} "
                f"(ambiguous, refusing to act): "
                f"{[(r['id'], r['email']) for r in rows]}",
                file=sys.stderr,
            )
            return 3

        user_id = rows[0]["id"]
        stored_email = rows[0]["email"]
        print(f"matched user: id={user_id} email={stored_email}")

        new_hash = _hash_password(new_password)
        print(f"new hash prefix: {new_hash[:7]}…  (algorithm: bcrypt)")

        if not apply:
            print("DRY-RUN — no change written. Re-run with --apply to commit.")
            return 0

        async with conn.transaction():
            status = await conn.execute(
                "UPDATE users SET hashed_password = $1 WHERE id = $2",
                new_hash, user_id,
            )
            # asyncpg returns e.g. "UPDATE 1" — the trailing int is rowcount.
            rowcount = int(status.rsplit(" ", 1)[-1])
            if rowcount != 1:
                raise RuntimeError(
                    f"expected exactly 1 row to update, got {rowcount} — "
                    f"transaction rolled back"
                )
        print(f"UPDATE confirmed: 1 row written.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--email", required=True,
        help="Email of the user whose password to reset.",
    )
    parser.add_argument(
        "--password", default=None,
        help=f"Replacement password (min {_MIN_PASSWORD_LEN} chars). "
             f"If omitted, a random 20-char password is generated.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the change. Without this flag the script "
             "runs as a dry-run and reports what *would* happen.",
    )
    parser.add_argument(
        "--dsn", default=None,
        help=f"Postgres DSN. If omitted, reads ${_DEFAULT_DSN_ENV}.",
    )
    args = parser.parse_args()

    new_password = args.password if args.password else _generate_password()
    if len(new_password) < _MIN_PASSWORD_LEN:
        print(
            f"ERROR: password must be at least {_MIN_PASSWORD_LEN} characters "
            f"(matches the app's UserCreate validator).",
            file=sys.stderr,
        )
        return 2

    dsn = _resolve_dsn(args.dsn)
    print(f"target: {_mask_dsn(dsn)}")
    print(f"email:  {args.email}")
    print()

    exit_code = asyncio.run(_reset(
        dsn=dsn, email=args.email, new_password=new_password, apply=args.apply,
    ))

    if exit_code == 0 and args.apply:
        print()
        print("=" * 60)
        print("New credentials — forward to the user via a side channel")
        print("(Signal/Slack DM/etc., not email). They can log in and")
        print("change it themselves on next sign-in.")
        print("=" * 60)
        print(f"  email:    {args.email}")
        print(f"  password: {new_password}")
        print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
