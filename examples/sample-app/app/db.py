"""SQLite access through SQLAlchemy."""
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)
Base = declarative_base()


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    password = Column(String, nullable=False, default="")


def init_db() -> None:
    Base.metadata.create_all(engine)


def search_items(term: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT name FROM items WHERE name LIKE '%{term}%'"))
        return [r[0] for r in rows]
