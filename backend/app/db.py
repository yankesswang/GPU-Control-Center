import os
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


url = os.getenv('DATABASE_URL') or URL.create(
    'postgresql+psycopg', username=os.getenv('DB_USER', 'inference'),
    password=os.getenv('DB_PASSWORD', ''), host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '5432')), database=os.getenv('DB_NAME', 'inference'),
)
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
