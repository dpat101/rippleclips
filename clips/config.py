"""Settings. Secrets come from ~/.rippleclips-env on the droplet, never from git."""
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
ENV_FILE = HOME / ".rippleclips-env"


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines into os.environ (values never printed)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

# Google Drive
DRIVE_ROOT_NAME = os.environ.get("DRIVE_ROOT_NAME", "The Ripple Effect")
DRIVE_ROOT_ID = os.environ.get("DRIVE_ROOT_ID", "")  # optional: the folder id, if the name is ambiguous
EPISODE_FOLDER_PATTERN = r"^TRE\s*Ep\s*(\d+)\b"
CLIPS_FOLDER_NAME = "Clips"
GOOGLE_CLIENT_FILE = HOME / ".rippleclips-google-client.json"
GOOGLE_TOKEN_FILE = HOME / ".rippleclips-google-token.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Only start an episode once nothing in its folder has changed for this long (uploads finished).
SETTLE_HOURS = float(os.environ.get("SETTLE_HOURS", "2"))
# Episodes before this number are ignored (0 = all). Set to 51 to start from TRE Ep51.
START_EPISODE = int(os.environ.get("START_EPISODE", "0"))

# Claude
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
STYLE_FILE = Path(__file__).resolve().parent.parent / "style" / "examples.md"

# Clips
MIN_CLIP_SECONDS = 12.0
MAX_CLIP_SECONDS = 90.0
MAX_CLIPS_PER_VIDEO = 8
OUT_WIDTH, OUT_HEIGHT = 1080, 1920

# Transcription (runs on the droplet, free)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")

# Working files and memory
WORK_DIR = HOME / "rippleclips-work"
STATE_FILE = HOME / "rippleclips-state.json"
LOCK_FILE = HOME / ".rippleclips.lock"
LOG_FILE = HOME / "rippleclips.log"
DISK_WARN_PERCENT = 70
