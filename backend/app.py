import argparse
import asyncio
import datetime
import logging
import time
from collections import defaultdict

import uvicorn
import zmq
import zmq.asyncio
from db import Session, SessionLocal, Speaker, Transcript, TranscriptType
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from utils import ASRResult

context = zmq.asyncio.Context()
app = FastAPI()
# frontend:mic -ws:asr-> app -8001-\
#     /----------------------------/
#     \-> vad -8002-> asr -8003-> app -ws:asr-> frontend
#             \8002-> sid -8004/      \
#             \8002-> kws -8005/       --8007-> agent -8008-> app -ws:agent-> frontend
#             \8002-> dia -8006/      /
# frontend:txt -ws:agent-------------
audio_address, audio_port = "0.0.0.0", "8001"
asr_address, asr_port = "0.0.0.0", "8003"
sid_address, sid_port = "0.0.0.0", "8004"
kws_address, kws_port = "0.0.0.0", "8005"
dia_address, dia_port = "0.0.0.0", "8006"
trans_address, trans_port = "0.0.0.0", "8007"
agent_address, agent_port = "0.0.0.0", "8008"
logger = logging.getLogger(__file__)

# New session everytime websocket is re-connected
# TODO: Persistant counters from DB
session_id_counter = 0
# speaker_id_counter = 0


@app.websocket("/asr")
async def websocket_asr(
    websocket: WebSocket,
    sample_rate: int = Query(
        16000, title="Sample Rate", description="The sample rate of the audio."
    ),
):
    await websocket.accept()

    # Set up ZeroMQ sockets
    audio_push_port = f"tcp://{audio_address}:{audio_port}"
    asr_pull_port = f"tcp://{asr_address}:{asr_port}"
    sid_pull_port = f"tcp://{sid_address}:{sid_port}"
    kws_pull_port = f"tcp://{kws_address}:{kws_port}"
    trans_push_port = f"tcp://{trans_address}:{trans_port}"
    agent_pull_port = f"tcp://{agent_address}:{agent_port}"
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
        # TODO: async?
        global session_id_counter, speaker_id_counter
        db = SessionLocal()
        # Initialize session if it's the first result
        if session_id_counter == 0:
            # Wipe all data
            db.query(Transcript).delete()
            db.query(Speaker).delete()
            db.query(Session).delete()
            db.commit()
        session = Session(
            session_id=session_id_counter,
            session_name=f"meeting_{session_id_counter}",
            session_date=datetime.date.today(),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id_counter += 1

        while True:
            # Reply result to frontend via websocket
            segment_id, result = await result_queue.get()
            await websocket.send_json(result)

            # Send result to agent module
            if result["type"] == "instruction":
                await trans_push_socket.send_pyobj(result)

            # Send result to diarization module for second pass

            # Write to DB
            try:
                speaker_id = result["speaker_id"]
                speaker_name = result["speaker_name"]
                segment_content = result["segment_content"]
                segment_type = result["segment_type"]

                speaker = Speaker(speaker_id=speaker_id, speaker_name=speaker_name)
                db.add(speaker)
                db.commit()
                db.refresh(speaker)

                # Create transcript segment
                start_time = datetime.datetime.now()
                # Simulate duration
                duration = datetime.timedelta(seconds=5)
                transcript = Transcript(
                    segment_id=segment_id,
                    session_id=session_id_counter,
                    speaker_id=speaker.speaker_id,
                    segment_type=TranscriptType[transcript_type],
                    segment_content=segment_content,
                    start_time=start_time,
                    duration=duration,
                )
                db.add(transcript)
                db.commit()
            except Exception as e:
                print(f"Error storing transcript: {e}")
                db.rollback()
                db.close()

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


from db import init_db

if __name__ == "__main__":
    init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000, help="port number")
    parser.add_argument("--addr", type=str, default="127.0.0.1", help="serve address")
    parser.add_argument("--docker", action="store_true", help="Docker serving, use DNS")
    args = parser.parse_args()
    # TODO: make arg parsing better designed
    if args.docker:
        (
            args.addr,
            audio_address,
            asr_address,
            sid_address,
            kws_address,
            trans_address,
            agent_address,
        ) = (
            "0.0.0.0",
            "*",
            "asr",
            "sid",
            "kws",
            "*",
            "agent",
        )

    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )
    uvicorn.run(app, host=args.addr, port=args.port)
