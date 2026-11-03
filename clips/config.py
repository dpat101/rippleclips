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

# Clips. CLIP_MIX: "funny" or "funny_and_powerful" (Dave's choice, 3 Nov 2026)
CLIP_MIX = os.environ.get("CLIP_MIX", "funny_and_powerful")
MIN_CLIP_SECONDS = 12.0
MAX_CLIP_SECONDS = 90.0
MAX_CLIPS_PER_VIDEO = 8
OUT_WIDTH, OUT_HEIGHT = 1080, 1920

# Full episodes (Dave, 3 Nov 2026): each video also becomes a finished episode for Spotify and YouTube, with
# the warm-up cut off, the sound levelled and the intro video from the main Drive folder in front.
FULL_EPISODES = os.environ.get("FULL_EPISODES", "1") == "1"
FULL_EPISODE_FOLDER_NAME = "Full episode"
INTRO_NAME = "Intro"
OUTRO_NAME = "Outro"  # a video called Outro in the main folder goes at the end; without one, the intro does

# Posting through ContentStudio (stage 2). POSTING is off / draft / on, in the env file.
# Dave, 4 Nov 2026: two clips a day, each platform at its own time, never the exact same minute. Each post goes
# up at a random time within POST_JITTER_MINUTES either side of its slot (Brisbane time).
CLIPS_PER_DAY = 2
SLOT_TIMES = {  # platform: (clip 1, clip 2)
    "facebook": ("08:00", "12:00"),
    "youtube": ("11:00", "16:00"),
    "instagram": ("07:00", "18:00"),
    "tiktok": ("13:00", "20:00"),
}
POST_JITTER_MINUTES = 30
POST_DAYS_AHEAD = int(os.environ.get("POST_DAYS_AHEAD", "3"))  # hand clips over at most this far ahead
APPROVAL_DAYS = 14  # first 2 weeks: only clips Dave drags into Approved are posted
POST_FROM_EPISODE = int(os.environ.get("POST_FROM_EPISODE", "52"))  # Dave, 3 Nov 2026: post from TRE Ep52
CS_TIMES_IN_UTC = os.environ.get("CONTENTSTUDIO_TIMES_IN_UTC", "0") == "1"

# Transcription (runs on the droplet, free)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")

# Working files and memory
WORK_DIR = HOME / "rippleclips-work"
STATE_FILE = HOME / "rippleclips-state.json"
LOCK_FILE = HOME / ".rippleclips.lock"
LOG_FILE = HOME / "rippleclips.log"
DISK_WARN_PERCENT = 70
