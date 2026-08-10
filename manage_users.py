#!/usr/bin/env python3
"""CLI tool to manage local user accounts stored in users.json."""

import argparse
import sys


def cmd_add(args):
    from app.auth import add_user
    import getpass

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        sys.exit(1)
    try:
        add_user(args.username, password, args.role)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"User '{args.username}' added with role '{args.role}'.")


def cmd_delete(args):
    from app.auth import delete_user

    if delete_user(args.username):
        print(f"User '{args.username}' deleted.")
    else:
        print(f"User '{args.username}' not found.", file=sys.stderr)
        sys.exit(1)


def cmd_list(_):
    from app.auth import list_users

    users = list_users()
    if not users:
        print("No users configured.")
        return
    print(f"{'Username':<20} {'Role':<10}")
    print("-" * 30)
    for u in users:
        print(f"{u['username']:<20} {u['role']:<10}")


def cmd_secret(_):
    from app.auth import generate_secret_key

    key = generate_secret_key()
    print(f"Generated SECRET_KEY:\n{key}")
    print("\nAdd this to your .env file as:\nSECRET_KEY=" + key)


parser = argparse.ArgumentParser(description="4THealth user management")
sub = parser.add_subparsers(dest="command", required=True)

p_add = sub.add_parser("add", help="Add or update a user")
p_add.add_argument("username")
p_add.add_argument("--password", default=None, help="Password (prompted if omitted)")
p_add.add_argument("--role", default="viewer", choices=["viewer", "admin"])
p_add.set_defaults(func=cmd_add)

p_del = sub.add_parser("delete", help="Delete a user")
p_del.add_argument("username")
p_del.set_defaults(func=cmd_delete)

p_list = sub.add_parser("list", help="List all users")
p_list.set_defaults(func=cmd_list)

p_secret = sub.add_parser("secret", help="Generate a random SECRET_KEY")
p_secret.set_defaults(func=cmd_secret)

if __name__ == "__main__":
    args = parser.parse_args()
    args.func(args)
