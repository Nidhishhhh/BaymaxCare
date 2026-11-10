import os
import logging
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("baymaxcare.db")

# Load environment variables
load_dotenv()

# Read Aiven or standard DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Aiven gives postgres:// which SQLAlchemy 1.4+ expects as postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Fallback to local SQLite if DATABASE_URL is not yet configured in .env
if not DATABASE_URL:
    logger.warning("DATABASE_URL is not set in .env. Falling back to local baymaxcare.db SQLite database.")
    DATABASE_URL = "sqlite:///./baymaxcare.db"

# Engine configuration
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
else:
    # Aiven PostgreSQL requires SSL
    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"

try:
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        pool_pre_ping=True,  # Automatically reconnect dead pool connections
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info(f"Database engine initialized for {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else 'local SQLite'}")
except Exception as e:
    logger.error(f"Failed to connect to database: {e}")
    # In case of remote connection error, provide local sqlite fallback
    DATABASE_URL = "sqlite:///./baymaxcare.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """FastAPI dependency to yield database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

