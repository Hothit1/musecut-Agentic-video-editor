"""Settings read from environment / .env. Keys are never logged."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

API_KEY = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY", "")
BASE_URL = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
SPARK_MODEL = os.environ.get("SPARK_MODEL_ID", "muse-spark-1.3")
IMAGE_MODEL = os.environ.get("MUSE_IMAGE_MODEL_ID", "muse-image-1.0")
SAM_MODEL = os.environ.get("SAM_MODEL_ID", "sam-3.1")
ASR_MODEL = os.environ.get("ASR_MODEL_ID", "muse-voice-transcribe-1.0")
SPARK_REASONING = os.environ.get("SPARK_REASONING_EFFORT", "medium")

DATA_DIR = Path(os.environ.get("MUSECUT_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "musecut.db"
SCHEMA_PATH = ROOT / "schema" / "edit-plan.schema.json"
MUSIC_DIR = ROOT / "assets" / "music"
FONT_PATH = os.environ.get("CAPTION_FONT", "/System/Library/Fonts/Supplemental/Impact.ttf")

MAX_DURATION_SEC = 20 * 60
TRACK_FPS = 2.0
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 25

# USD prices from dev.meta.ai/docs/pricing-rate-limits (2026-10-29)
PRICES = {
    "spark_in_per_m": 1.25,
    "spark_out_per_m": 4.25,
    "image_each": 0.01,
    "sam_image_each": 0.0025,
    "asr_per_hour": 0.18,
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
