#!/usr/bin/env bash
# Sets up the rippleclips droplet. Run as root on the droplet (SETUP.md, step 4). Safe to run again.
#   bash /home/clips/rippleclips/deploy/install.sh          # packages, Python, settings file
#   bash /home/clips/rippleclips/deploy/install.sh --cron   # also switch on the hourly run
set -euo pipefail
APP_USER=clips
HOME_DIR=/home/$APP_USER
REPO=$HOME_DIR/rippleclips

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-venv python3-pip ffmpeg fonts-dejavu-core git

# 2 GB of swap, so transcription never runs out of memory
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

id -u $APP_USER >/dev/null 2>&1 || useradd -m -s /bin/bash $APP_USER

sudo -u $APP_USER -H bash -c "
  set -e
  cd $REPO
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  if [ ! -f $HOME_DIR/.rippleclips-env ]; then
    printf '%s\n' '# Ripple Effect clips settings. Never commit or share this file.' \
      'ANTHROPIC_API_KEY=' 'DRIVE_ROOT_NAME=The Ripple Effect' 'START_EPISODE=51' > $HOME_DIR/.rippleclips-env
  fi
  chmod 600 $HOME_DIR/.rippleclips-env
"

if [ "${1:-}" = "--cron" ]; then
  LINE="7 * * * * cd $REPO && git pull --ff-only -q && .venv/bin/pip install -q -r requirements.txt && .venv/bin/python -m clips.run >> $HOME_DIR/rippleclips-cron.log 2>&1 # rippleclips"
  ( sudo -u $APP_USER crontab -l 2>/dev/null | grep -v '# rippleclips' ; echo "$LINE" ) | sudo -u $APP_USER crontab -
  echo "Hourly run switched on (7 minutes past each hour)."
fi
echo "== Done"
