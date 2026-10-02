"""Set up a local admin login without placing a password in Git or command arguments."""

import argparse
import getpass
import json
import os
import secrets
from pathlib import Path

from backend.app.admin import hash_password
from backend.app.config import ROOT, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--interactive", action="store_true", help="Enter your own password securely"
    )
    parser.add_argument("--rotate", action="store_true", help="Replace the existing admin login")
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 128 or "\n" in args.username or "=" in args.username:
        parser.error("Use a username of 1–128 characters without newlines or =")
    settings = Settings()
    if settings.admin_password_hash and settings.admin_session_secret and not args.rotate:
        print("Admin login is already configured. Use --rotate to replace it.")
        return
    password = (
        getpass.getpass("Admin password (at least 12 characters): ")
        if args.interactive
        else secrets.token_urlsafe(24)
    )
    if not 12 <= len(password) <= 256:
        parser.error("The password must contain 12–256 characters")
    values = {
        "ADMIN_USERNAME": args.username,
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "ADMIN_SESSION_SECRET": secrets.token_urlsafe(48),
    }
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0] not in values]
    lines.extend(f"{key}={json.dumps(value)}" for key, value in values.items())
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)
    private_dir = Path.home() / ".config" / "amaze-go"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_dir.chmod(0o700)
    credentials = private_dir / "admin-credentials.txt"
    fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(
            "Admin URL: http://127.0.0.1:8000/admin\n"
            f"Username: {args.username}\nPassword: {password}\n"
        )
    credentials.chmod(0o600)
    print(f"Admin username: {args.username}. Login credentials saved privately to {credentials}.")
    print("Restart the backend to enable the new login.")


if __name__ == "__main__":
    main()
