#!/usr/bin/env bash
# Google Drive sign-in, run through the ssh tunnel in SETUP.md step 6.
set -euo pipefail
exec sudo -u clips -H bash -c 'cd /home/clips/rippleclips && .venv/bin/python -m clips.google_signin'
