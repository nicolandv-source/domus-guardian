from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def ping_database() -> dict[str, object]:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT current_database(), current_user,
                       current_setting('server_version')
                """
            )
        ).one()
    return {
        "connected": True,
        "database": result[0],
        "user": result[1],
        "server_version": result[2],
    }


def reset_database_pool() -> None:
    """Drop idle connections so SQLAlchemy creates a fresh connection next time."""
    engine.dispose()
