"""Owner tools (there is no email, so forgotten passwords are reset here).
Run against the same database as the website:  DATABASE_URL=... python manage_users.py <command>
  list                          show accounts
  reset-password NAME           set a new password (asks for it; signs that user out everywhere)
  make-admin NAME               make NAME an owner (can connect Upstox)
  delete NAME                   delete the account and all of its saved data
"""
import getpass
import sys
import time

import accounts as acc


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "reset-password", "make-admin", "delete"):
        print(__doc__)
        return 1
    cmd = argv[1]
    try:
        if cmd == "list":
            for u in acc.list_users():
                last = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login_at"])) if u["last_login_at"] else "never"
                print(f"{u['id']:>4}  {u['username']:<24} {'owner' if u['is_admin'] else 'user ':<6} last login {last}")
            return 0
        if len(argv) < 3:
            print("Missing username.")
            return 1
        name = argv[2]
        if cmd == "reset-password":
            pw = getpass.getpass("New password: ")
            if pw != getpass.getpass("Repeat: "):
                print("Passwords differ.")
                return 1
            acc.reset_password(name, pw)
            print("Password changed; the user was signed out everywhere.")
        elif cmd == "make-admin":
            acc.make_admin(name)
            print(f"{name} is now an owner.")
        elif cmd == "delete":
            if input(f"Type {name} again to delete the account and its data: ").strip() != name:
                print("Cancelled.")
                return 1
            row = next((u for u in acc.list_users() if u["username"] == acc.normalise_username(name)), None)
            if not row:
                print("No such user.")
                return 1
            acc.delete_user(row["id"], check_password=False)
            print("Deleted.")
    except acc.AccountError as exc:
        print("Error:", exc.message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
