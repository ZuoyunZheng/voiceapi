import asyncio
import datetime
import enum
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class TranscriptType(enum.Enum):
    TRANSCRIPT = "transcript"
    ASSISTANT = "assistant"
    INSTRUCTION = "instruction"


class DatabaseManager:
    """Manages all database interactions using psycopg3 with async functionality."""

    def __init__(self, connection_string: str, min_size: int = 1, max_size: int = 10):
        """Initialize the database manager with connection pool.

        Args:
            connection_string: PostgreSQL connection string
            min_size: Minimum connections in the pool
            max_size: Maximum connections in the pool
        """
        self.connection_string = connection_string
        self.pool = AsyncConnectionPool(
            conninfo=connection_string,
            min_size=min_size,
            max_size=max_size,
            open=False,  # Don't open immediately
        )

    async def initialize(self) -> None:
        """Initialize the connection pool and ensure the database is set up."""
        await self.pool.open()
        await self.create_tables()

    async def close(self) -> None:
        """Close all database connections."""
        await self.pool.close()

    async def create_tables(self) -> None:
        """Create all necessary database tables if they don't exist."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # Create sessions table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id SERIAL PRIMARY KEY,
                        session_name VARCHAR(255) NOT NULL,
                        session_date DATE DEFAULT CURRENT_DATE
                    )
                """)

                # Create speakers table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS speakers (
                        speaker_id SERIAL PRIMARY KEY,
                        speaker_name VARCHAR(255) NOT NULL
                    )
                """)

                # Create session_speakers table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS session_speakers (
                        id SERIAL PRIMARY KEY,
                        session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                        speaker_id INTEGER NOT NULL REFERENCES speakers(speaker_id) ON DELETE CASCADE,
                        local_speaker_id INTEGER NOT NULL,
                        UNIQUE(session_id, local_speaker_id)
                    )
                """)
                # Add indexes for foreign keys in session_speakers
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_speakers_session_id ON session_speakers(session_id);
                """)
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_speakers_speaker_id ON session_speakers(speaker_id);
                """)

                # Create transcript_types enum
                await cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'transcript_type') THEN
                            CREATE TYPE transcript_type AS ENUM ('transcript', 'assistant', 'instruction');
                        END IF;
                    END
                    $$;
                """)

                # Create transcripts table
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS transcripts (
                        segment_id SERIAL PRIMARY KEY,
                        session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                        session_speaker_id INTEGER NOT NULL REFERENCES session_speakers(id) ON DELETE CASCADE,
                        segment_index FLOAT NOT NULL,
                        segment_type transcript_type NOT NULL,
                        segment_content TEXT NOT NULL,
                        start_time TIMESTAMP NOT NULL,
                        duration INTERVAL NOT NULL,
                        UNIQUE(session_id, segment_index)
                    )
                """)
                # Add index for foreign key in transcripts
                # Note: Index on (session_id, segment_index) is likely created automatically by the UNIQUE constraint.
                # Indexing session_id separately might be redundant.
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_transcripts_session_speaker_id ON transcripts(session_speaker_id);
                """)

    async def reset_database(self) -> None:
        """Drop all tables and recreate them - CAUTION: destroys all data."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    DROP TABLE IF EXISTS transcripts;
                    DROP TABLE IF EXISTS session_speakers;
                    DROP TABLE IF EXISTS speakers;
                    DROP TABLE IF EXISTS sessions;
                    DROP TYPE IF EXISTS transcript_type;
                """)

        await self.create_tables()

    # Session operations
    async def create_session(
        self, session_name: str, session_date: Optional[datetime.date] = None
    ) -> int:
        """Create a new session and return its ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if session_date:
                    await cur.execute(
                        "INSERT INTO sessions (session_name, session_date) VALUES (%s, %s) RETURNING session_id",
                        (session_name, session_date),
                    )
                else:
                    await cur.execute(
                        "INSERT INTO sessions (session_name) VALUES (%s) RETURNING session_id",
                        (session_name,),
                    )

                return (await cur.fetchone())[0]

    async def get_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get session details by ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM sessions WHERE session_id = %s", (session_id,)
                )
                return await cur.fetchone()

    async def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all sessions."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM sessions ORDER BY session_date DESC")
                return await cur.fetchall()

    # Speaker operations
    async def create_speaker(self, speaker_name: str) -> int:
        """Create a new speaker and return their ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO speakers (speaker_name) VALUES (%s) RETURNING speaker_id",
                    (speaker_name,),
                )
                return (await cur.fetchone())[0]

    async def get_speaker(self, speaker_id: int) -> Optional[Dict[str, Any]]:
        """Get speaker details by ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM speakers WHERE speaker_id = %s", (speaker_id,)
                )
                return await cur.fetchone()

    async def get_all_speakers(self) -> List[Dict[str, Any]]:
        """Get all speakers."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM speakers ORDER BY speaker_name")
                return await cur.fetchall()

    # Session-Speaker operations
    async def add_speaker_to_session(
        self, session_id: int, speaker_id: int, local_speaker_id: Optional[int] = None
    ) -> int:
        """Add a speaker to a session with an optional local ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # If local_speaker_id is not provided, find the next available one
                if local_speaker_id is None:
                    await cur.execute(
                        "SELECT COALESCE(MAX(local_speaker_id), 0) + 1 FROM session_speakers WHERE session_id = %s",
                        (session_id,),
                    )
                    local_speaker_id = (await cur.fetchone())[0]

                await cur.execute(
                    """
                    INSERT INTO session_speakers (session_id, speaker_id, local_speaker_id)
                    VALUES (%s, %s, %s) RETURNING id
                    """,
                    (session_id, speaker_id, local_speaker_id),
                )
                return (await cur.fetchone())[0]

    async def get_session_speakers(self, session_id: int) -> List[Dict[str, Any]]:
        """Get all speakers for a specific session."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT ss.id, ss.local_speaker_id, s.speaker_id, s.speaker_name
                    FROM session_speakers ss
                    JOIN speakers s ON ss.speaker_id = s.speaker_id
                    WHERE ss.session_id = %s
                    ORDER BY ss.local_speaker_id
                    """,
                    (session_id,),
                )
                return await cur.fetchall()

    # Transcript operations
    async def add_transcript(
        self,
        session_id: int,
        session_speaker_id: int,
        segment_type: Union[TranscriptType, str],
        segment_content: str,
        start_time: datetime.datetime,
        duration: datetime.timedelta,
        segment_index: Optional[float] = None,
    ) -> int:
        """Add a transcript segment to a session."""
        # Convert enum to string if needed
        if isinstance(segment_type, TranscriptType):
            segment_type = segment_type.value

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # If segment_index is not provided, find the next available one
                if segment_index is None:
                    await cur.execute(
                        "SELECT COALESCE(MAX(segment_index), 0) + 1 FROM transcripts WHERE session_id = %s",
                        (session_id,),
                    )
                    segment_index = (await cur.fetchone())[0]

                await cur.execute(
                    """
                    INSERT INTO transcripts
                    (session_id, session_speaker_id, segment_index, segment_type, segment_content, start_time, duration)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING segment_id
                    """,
                    (
                        session_id,
                        session_speaker_id,
                        segment_index,
                        segment_type,
                        segment_content,
                        start_time,
                        duration,
                    ),
                )
                return (await cur.fetchone())[0]

    async def get_session_transcripts(self, session_id: int) -> List[Dict[str, Any]]:
        """Get all transcripts for a specific session, ordered by segment index."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT t.*, ss.local_speaker_id, s.speaker_name
                    FROM transcripts t
                    JOIN session_speakers ss ON t.session_speaker_id = ss.id
                    JOIN speakers s ON ss.speaker_id = s.speaker_id
                    WHERE t.session_id = %s
                    ORDER BY t.segment_index
                    """,
                    (session_id,),
                )
                return await cur.fetchall()

    async def get_transcript(self, segment_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific transcript segment by ID."""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT t.*, ss.local_speaker_id, s.speaker_name
                    FROM transcripts t
                    JOIN session_speakers ss ON t.session_speaker_id = ss.id
                    JOIN speakers s ON ss.speaker_id = s.speaker_id
                    WHERE t.segment_id = %s
                    """,
                    (segment_id,),
                )
                return await cur.fetchone()

    # Advanced operations
    async def split_transcript(
        self, segment_id: int, split_position: int, new_segment_content: str
    ) -> Tuple[int, int]:
        """
        Split a transcript segment into two parts.

        Args:
            segment_id: ID of the segment to split
            split_position: Character position to split at
            new_segment_content: Content for the new segment

        Returns:
            Tuple of (original_segment_id, new_segment_id)
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    # Get original segment
                    await cur.execute(
                        "SELECT * FROM transcripts WHERE segment_id = %s", (segment_id,)
                    )
                    original = await cur.fetchone()

                    if not original:
                        raise ValueError(f"Segment with ID {segment_id} not found")

                    # Calculate new index (halfway between the original and the next one)
                    await cur.execute(
                        """
                        SELECT segment_index FROM transcripts
                        WHERE session_id = %s AND segment_index > %s
                        ORDER BY segment_index ASC LIMIT 1
                        """,
                        (original["session_id"], original["segment_index"]),
                    )
                    next_segment = await cur.fetchone()

                    if next_segment:
                        new_index = (
                            original["segment_index"] + next_segment["segment_index"]
                        ) / 2
                    else:
                        new_index = original["segment_index"] + 1

                    # Calculate the proportion of the duration based on content length
                    original_content = original["segment_content"]
                    original_length = len(original_content)
                    split_ratio = (
                        split_position / original_length if original_length > 0 else 0.5
                    )

                    # Calculate new durations
                    original_duration = original["duration"]
                    new_duration = original_duration * (1 - split_ratio)
                    updated_duration = original_duration * split_ratio

                    # Update original segment
                    truncated_content = original_content[:split_position]
                    await cur.execute(
                        """
                        UPDATE transcripts
                        SET segment_content = %s, duration = %s
                        WHERE segment_id = %s
                        """,
                        (truncated_content, updated_duration, segment_id),
                    )

                    # Calculate new start time
                    new_start_time = original["start_time"] + updated_duration

                    # Insert new segment
                    await cur.execute(
                        """
                        INSERT INTO transcripts
                        (session_id, session_speaker_id, segment_index, segment_type,
                         segment_content, start_time, duration)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING segment_id
                        """,
                        (
                            original["session_id"],
                            original["session_speaker_id"],
                            new_index,
                            original["segment_type"],
                            new_segment_content,
                            new_start_time,
                            new_duration,
                        ),
                    )
                    new_segment_id = (await cur.fetchone())["segment_id"]

                    return segment_id, new_segment_id

    async def merge_transcripts(
        self, segment_ids: List[int], keep_index: Optional[float] = None
    ) -> int:
        """
        Merge multiple transcript segments into one.

        Args:
            segment_ids: List of segment IDs to merge, in order
            keep_index: Which index to keep for the merged segment (defaults to first segment's index)

        Returns:
            ID of the merged segment
        """
        if not segment_ids or len(segment_ids) < 2:
            raise ValueError("At least two segment IDs must be provided to merge")

        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    # Get all segments
                    segments = []
                    for seg_id in segment_ids:
                        await cur.execute(
                            "SELECT * FROM transcripts WHERE segment_id = %s", (seg_id,)
                        )
                        segment = await cur.fetchone()
                        if not segment:
                            raise ValueError(f"Segment with ID {seg_id} not found")
                        segments.append(segment)

                    # Sort segments by index
                    segments.sort(key=lambda x: x["segment_index"])

                    # Check they are all from the same session
                    session_id = segments[0]["session_id"]
                    if not all(seg["session_id"] == session_id for seg in segments):
                        raise ValueError("All segments must belong to the same session")

                    # Determine which segment's index to keep
                    if keep_index is None:
                        keep_index = segments[0]["segment_index"]

                    # Calculate combined properties
                    combined_content = "".join(
                        seg["segment_content"] for seg in segments
                    )
                    start_time = min(seg["start_time"] for seg in segments)

                    # Calculate total duration
                    total_duration = datetime.timedelta()
                    for seg in segments:
                        total_duration += seg["duration"]

                    # Get the speaker from the first segment (this is a policy decision)
                    session_speaker_id = segments[0]["session_speaker_id"]
                    segment_type = segments[0]["segment_type"]

                    # Delete all segments except the first one
                    for seg in segments[1:]:
                        await cur.execute(
                            "DELETE FROM transcripts WHERE segment_id = %s",
                            (seg["segment_id"],),
                        )

                    # Update the first segment with merged content
                    await cur.execute(
                        """
                        UPDATE transcripts
                        SET segment_content = %s,
                            start_time = %s,
                            duration = %s,
                            segment_index = %s,
                            session_speaker_id = %s
                        WHERE segment_id = %s
                        RETURNING segment_id
                        """,
                        (
                            combined_content,
                            start_time,
                            total_duration,
                            keep_index,
                            session_speaker_id,
                            segments[0]["segment_id"],
                        ),
                    )

                    merged_id = (await cur.fetchone())["segment_id"]
                    return merged_id


# Usage example in FastAPI
async def setup_database():
    db_url = "postgresql://user:password@db:5432/voiceapi"
    db_manager = DatabaseManager(db_url)
    await db_manager.initialize()
    return db_manager


# In your FastAPI app
"""
from fastapi import FastAPI, Depends

app = FastAPI()

db_manager: DatabaseManager = None

@app.on_event("startup")
async def startup_event():
    global db_manager
    db_manager = await setup_database()

@app.on_event("shutdown")
async def shutdown_event():
    if db_manager:
        await db_manager.close()

# Database dependency
async def get_db():
    db = await setup_database()
    try:
        yield db
    finally:
        await db.close()

@app.get("/sessions")
async def list_sessions(db: DatabaseManager = Depends(get_db)):
    return await db.get_all_sessions()
"""
