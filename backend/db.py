import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


class Speaker(Base):
    __tablename__ = "speaker"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    transcripts = relationship("Transcript", back_populates="speaker")


class Transcript(Base):
    __tablename__ = "transcript"
    segment_id = Column(Integer, primary_key=True)
    speaker_id = Column(Integer, ForeignKey("speaker.id"), nullable=False)
    content = Column(Text, nullable=False)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, default=datetime.datetime.utcnow)

    speaker = relationship("Speaker", back_populates="transcripts")


DATABASE_URL = "postgresql://user:password@db:5432/voiceapi"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
