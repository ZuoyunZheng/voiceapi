import argparse
import asyncio
import datetime
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List

import uvicorn
import zmq
import zmq.asyncio
from config import config, update_config
from db import DatabaseManager, TranscriptType
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from utils import ASRResult

context = zmq.asyncio.Context()
app = FastAPI(
    title="VoiceAPI", description="Real-time voice processing with PostgreSQL storage"
)

# Global database manager
db_manager: DatabaseManager = None

# frontend:mic -ws:asr-> app -8001-\
#     /----------------------------/
#     \-> vad -8002-> asr -8003-> app -ws:asr-> frontend
#             \8002-> sid -8004/      \
#             \8002-> kws -8005/       --8007-> agent -8008-> app -ws:agent-> frontend
#             \8002-> dia -8006/      /
# frontend:txt -ws:agent-------------

logger = logging.getLogger(__file__)

# New session everytime websocket is re-connected
# TODO: Persistant counters from DB
session_id_counter = 0
# speaker_id_counter = 0


@app.on_event("startup")
async def startup_event():
    """Initialize database connection on startup."""
    global db_manager, config

    # Set up logging
    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=getattr(logging, config.log_level, logging.INFO),
    )

    logger.info(
        f"Starting VoiceAPI in {'Docker' if config.is_docker else 'local'} mode"
    )
    logger.info(f"Database configuration: {config.database}")

    try:
        db_manager = DatabaseManager(config.database.get_url())
        await db_manager.initialize()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connections on shutdown."""
    global db_manager
    if db_manager:
        await db_manager.close()
        logger.info("Database connections closed")


@app.websocket("/asr")
async def websocket_asr(
    websocket: WebSocket,
    sample_rate: int = Query(
        16000, title="Sample Rate", description="The sample rate of the audio."
    ),
):
    await websocket.accept()

    # Set up ZeroMQ sockets
    audio_push_port = f"tcp://{config.audio_address}:{config.audio_port}"
    asr_pull_port = f"tcp://{config.asr_address}:{config.asr_port}"
    sid_pull_port = f"tcp://{config.sid_address}:{config.sid_port}"
    kws_pull_port = f"tcp://{config.kws_address}:{config.kws_port}"
    trans_push_port = f"tcp://{config.trans_address}:{config.trans_port}"
    agent_pull_port = f"tcp://{config.agent_address}:{config.agent_port}"
    audio_push_socket = context.socket(zmq.PUSH)
    audio_push_socket.bind(audio_push_port)
    asr_pull_socket = context.socket(zmq.PULL)
    asr_pull_socket.connect(asr_pull_port)
    sid_pull_socket = context.socket(zmq.PULL)
    sid_pull_socket.connect(sid_pull_port)
    kws_pull_socket = context.socket(zmq.PULL)
    kws_pull_socket.connect(kws_pull_port)
    trans_push_socket = context.socket(zmq.PUSH)
    trans_push_socket.bind(trans_push_port)
    agent_pull_socket = context.socket(zmq.PULL)
    agent_pull_socket.connect(agent_pull_port)
    name_2_id = {"Assistant": 0, "Unknown Speaker": -1}
    intermediate_result = defaultdict(
        lambda: {
            "speaker_id": -1,
            "speaker_name": "Unknown Speaker",
            "segment_type": "transcript",  # transcript, assistant, instruction
            "segment_content": "",
            "asr_finished": False,
            "sid_finished": False,
            "kws_finished": False,
        }
    )
    result_queue = asyncio.Queue()
    logger.info(
        f"App ports: {audio_push_port}, {asr_pull_port}, {sid_pull_port}, {kws_pull_port}, {trans_push_port}, {agent_pull_port}"
    )

    # Message passing pipeline
    # Send raw bytes -> VAD
    async def task_send_pcm():
        while True:
            pcm_bytes = await websocket.receive_bytes()
            # TODO: implement interrupt mechanism for downstream
            if not pcm_bytes:
                logging.info("Received zero bytes, returning")
                return
            await audio_push_socket.send_pyobj(pcm_bytes)

    async def queue_if_ready(idx):
        ir = intermediate_result[idx]
        if ir["asr_finished"] and ir["sid_finished"] and ir["kws_finished"]:
            del ir["asr_finished"]
            del ir["sid_finished"]
            del ir["kws_finished"]
            await result_queue.put((idx, ir))
            logger.info(
                f"Enqueued results for segment {idx} ({ir['speaker_name']}, {ir['segment_type']}): {ir['segment_content']}"
            )
            del ir

    async def task_recv_asr():
        while True:
            asr_result: ASRResult = await asr_pull_socket.recv_pyobj()
            # logger.info(f"Received ASR results for segment {asr_result.idx}")
            if not asr_result:
                return
            ir = intermediate_result[asr_result.idx]
            ir["segment_content"] += asr_result.text
            ir["asr_finished"] = asr_result.finished
            await queue_if_ready(asr_result.idx)

    async def task_recv_sid():
        while True:
            sid_result: dict = await sid_pull_socket.recv_pyobj()
            # logger.info(f"Received SID results for segment {sid_result['idx']}")
            if not sid_result:
                return
            ir = intermediate_result[sid_result["idx"]]
            ir["speaker_name"] = sid_result["name"]
            if sid_result["name"] not in name_2_id:
                name_2_id[sid_result["name"]] = len(name_2_id)
            ir["speaker_id"] = name_2_id[sid_result["name"]]
            ir["sid_finished"] = sid_result["finished"]
            await queue_if_ready(sid_result["idx"])

    async def task_recv_kws():
        while True:
            kws_result: dict = await kws_pull_socket.recv_pyobj()
            # logger.info(f"Received KWS results for segment {kws_result['idx']}")
            if not kws_result:
                return
            ir = intermediate_result[kws_result["idx"]]
            ir["segment_type"] = kws_result["type"]
            ir["kws_finished"] = kws_result["finished"]
            await queue_if_ready(kws_result["idx"])

    async def task_recv_agent():
        while True:
            agent_result: dict = await agent_pull_socket.recv_pyobj()
            # logger.info(f"Received Agent response for segment {agent_result['id']}")
            if not agent_result:
                return
            await result_queue.put(agent_result)

    # Send result
    async def task_send_result():
        # Prepare DB for new session
        global session_id_counter, db_manager
        current_session_id = None

        # Initialize session if it's the first result
        if session_id_counter == 0:
            # Optionally wipe all data for development (remove in production)
            # await db_manager.reset_database()
            pass

        # Create session and speaker mapping
        speaker_name_to_db_id = {}  # Maps speaker names to database speaker IDs
        session_speaker_ids = {}  # Maps speaker names to session-speaker IDs

        while True:
            # Reply result to frontend via websocket
            segment_id, result = await result_queue.get()
            await websocket.send_json(result)

            # Send result to agent module
            if result.get("segment_type") == "instruction":
                await trans_push_socket.send_pyobj(result)

            # Write to DB
            try:
                # Create session on first transcript
                if current_session_id is None:
                    current_session_id = await db_manager.create_session(
                        session_name=f"meeting_{session_id_counter}",
                        session_date=datetime.date.today(),
                    )
                    session_id_counter += 1
                    logger.info(f"Created new session with ID: {current_session_id}")

                speaker_name = result["speaker_name"]
                segment_content = result["segment_content"]
                segment_type = result["segment_type"]

                # Create or get speaker
                if speaker_name not in speaker_name_to_db_id:
                    # Check if speaker already exists
                    existing_speakers = await db_manager.get_all_speakers()
                    existing_speaker = next(
                        (
                            s
                            for s in existing_speakers
                            if s["speaker_name"] == speaker_name
                        ),
                        None,
                    )

                    if existing_speaker:
                        speaker_db_id = existing_speaker["speaker_id"]
                    else:
                        speaker_db_id = await db_manager.create_speaker(speaker_name)

                    speaker_name_to_db_id[speaker_name] = speaker_db_id

                    # Add speaker to session
                    session_speaker_id = await db_manager.add_speaker_to_session(
                        session_id=current_session_id, speaker_id=speaker_db_id
                    )
                    session_speaker_ids[speaker_name] = session_speaker_id
                    logger.info(f"Added speaker '{speaker_name}' to session")

                # Create transcript segment
                start_time = datetime.datetime.now()
                duration = datetime.timedelta(seconds=5)  # Simulate duration

                transcript_id = await db_manager.add_transcript(
                    session_id=current_session_id,
                    session_speaker_id=session_speaker_ids[speaker_name],
                    segment_type=segment_type,
                    segment_content=segment_content,
                    start_time=start_time,
                    duration=duration,
                    segment_index=float(segment_id),
                )
                logger.info(
                    f"Added transcript segment {transcript_id} for speaker '{speaker_name}': {segment_content[:50]}..."
                )

            except Exception as e:
                logger.error(f"Error storing transcript: {e}")
                # In async context, we don't need manual rollback - transactions are handled automatically

    try:
        await asyncio.gather(
            task_send_pcm(),
            task_recv_asr(),
            task_recv_sid(),
            task_recv_kws(),
            task_recv_agent(),
            task_send_result(),
        )
    except WebSocketDisconnect as e:
        logger.info(f"asr ws disconnected: {str(e)}")
    finally:
        audio_push_socket.close()
        asr_pull_socket.close()
        sid_pull_socket.close()
        kws_pull_socket.close()
        trans_push_socket.close()
        agent_pull_socket.close()


# REST API endpoints for database interaction
@app.get("/sessions", response_model=List[Dict[str, Any]])
async def get_sessions():
    """Get all sessions."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return await db_manager.get_all_sessions()


@app.get("/sessions/{session_id}", response_model=Dict[str, Any])
async def get_session(session_id: int):
    """Get a specific session."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    session = await db_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/sessions/{session_id}/transcripts", response_model=List[Dict[str, Any]])
async def get_session_transcripts(session_id: int):
    """Get all transcripts for a session."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    transcripts = await db_manager.get_session_transcripts(session_id)
    return transcripts


@app.get("/sessions/{session_id}/speakers", response_model=List[Dict[str, Any]])
async def get_session_speakers(session_id: int):
    """Get all speakers for a session."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    speakers = await db_manager.get_session_speakers(session_id)
    return speakers


@app.get("/speakers", response_model=List[Dict[str, Any]])
async def get_speakers():
    """Get all speakers."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return await db_manager.get_all_speakers()


@app.get("/transcripts/{transcript_id}", response_model=Dict[str, Any])
async def get_transcript(transcript_id: int):
    """Get a specific transcript."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    transcript = await db_manager.get_transcript(transcript_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return transcript


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: int):
    """Delete a session and all its transcripts."""
    global db_manager
    if not db_manager:
        raise HTTPException(status_code=500, detail="Database not initialized")
    # The database will handle cascading deletes
    # This is a simple implementation - you might want to add proper session deletion
    return {"message": "Session deletion endpoint - implement as needed"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    global db_manager
    db_status = "healthy" if db_manager else "not initialized"
    return {
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.datetime.now().isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=config.port, help="port number")
    parser.add_argument("--addr", type=str, default=config.host, help="serve address")
    parser.add_argument("--docker", action="store_true", help="Docker serving, use DNS")
    args = parser.parse_args()

    # Update configuration with command-line arguments
    update_config(port=args.port, host=args.addr, is_docker=args.docker)

    # If docker mode is enabled, update addresses
    if args.docker:
        update_config(
            audio_address="*",
            asr_address="asr",
            sid_address="sid",
            kws_address="kws",
            trans_address="*",
            agent_address="agent",
        )

    # Logging will be set up in startup_event
    uvicorn.run(app, host=args.addr, port=args.port)
