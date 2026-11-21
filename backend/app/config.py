import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/webproject"
)

MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", "1200"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "3"))
MAX_RADIUS = int(os.getenv("MAX_RADIUS", "15"))
MAX_COMPLEXITY = int(os.getenv("MAX_COMPLEXITY", "1500000000"))

MAX_ACTIVE_PER_USER = int(os.getenv("MAX_ACTIVE_PER_USER", "2"))
MAX_TASKS_PER_USER = int(os.getenv("MAX_TASKS_PER_USER", "15"))

N_STRIPS = int(os.getenv("N_STRIPS", "16"))

ADMIN_USERS = set(os.getenv("ADMIN_USERS", "admin").split(","))
TOKEN_TTL_HOURS = 24

DEFAULT_UNIT_SEC = 5e-9

KEY_TTL = 24 * 3600
