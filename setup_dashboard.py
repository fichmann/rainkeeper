"""Create a local dashboard login without putting credentials in source control."""
import getpass
import json
import os
from pathlib import Path

if __name__ == '__main__':
    folder = Path(__file__).parent / 'data'
    folder.mkdir(mode=0o700, exist_ok=True)
    username = input('Dashboard username: ').strip()
    password = getpass.getpass('Dashboard password: ')
    if not username or ':' in username or not password:
        raise SystemExit('A username without colons and a nonempty password are required.')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match.')
    path = folder / 'auth.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'username': username, 'password': password}, f)
    print('Created data/auth.json. For Docker, set data ownership to UID/GID 10001.')
