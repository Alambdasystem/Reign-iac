"""Prepare only the dedicated vault mount, then run Core without root privileges.

Render attaches disks at runtime, after image ownership has been established.
The entrypoint may fix the mount directory owner; it never creates a fallback
directory or recursively changes existing files. Vault validates its own files.
"""
import os
import pwd
import sys


VAULT_MOUNT = "/var/data/reign-vault"


def prepare_mount(uid, gid):
    if os.environ.get("REIGN_VAULT_DIR") != VAULT_MOUNT:
        return
    if os.path.realpath(VAULT_MOUNT) != VAULT_MOUNT or not os.path.ismount(VAULT_MOUNT):
        return  # Core remains usable, but vault operations fail closed.
    descriptor = os.open(VAULT_MOUNT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Core startup command is required")
    account = pwd.getpwnam("reign")
    if os.geteuid() == 0:
        prepare_mount(account.pw_uid, account.pw_gid)
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    if os.geteuid() != account.pw_uid or os.getegid() != account.pw_gid:
        raise SystemExit("Core must run as the dedicated service account")
    os.umask(0o077)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
