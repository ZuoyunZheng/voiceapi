import datetime
import enum

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Interval,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


class Session(Base):
    __tablename__ = "sessions"
    session_id = Column(Integer, primary_key=True)
    session_name = Column(String(255), nullable=False)
    session_date = Column(Date, default=datetime.date.today)
    transcripts = relationship("Transcript", back_populates="session")


class Speaker(Base):
    __tablename__ = "speakers"
    speaker_id = Column(Integer, primary_key=True)
    speaker_name = Column(String(255), nullable=False)
    transcripts = relationship("Transcript", back_populates="speaker")


class TranscriptType(enum.Enum):
    transcript = "transcript"
    assistant = "assistant"
    instruction = "instruction"


class Transcript(Base):
    __tablename__ = "transcripts"
    segment_id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.session_id"), nullable=False)
    speaker_id = Column(Integer, ForeignKey("speakers.speaker_id"), nullable=False)
    segment_type = Column(Enum(TranscriptType), nullable=False)
    segment_content = Column(Text, nullable=False)
    start_time = Column(DateTime, nullable=False)
    duration = Column(Interval, nullable=False)

    session = relationship("Session", back_populates="transcripts")
    speaker = relationship("Speaker", back_populates="transcripts")


DATABASE_URL = "postgresql://user:password@db:5432/voiceapi"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.drop_all(bind=engine)  # Drop all tables
    Base.metadata.create_all(bind=engine)
