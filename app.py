from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
import asyncio
import logging
import uvicorn
from voiceapi.asr import ASRResult
import argparse
import os
import zmq
import zmq.asyncio

context = zmq.asyncio.Context()
app = FastAPI()
vad_address, vad_port = "0.0.0.0", "7001"
asr_address, asr_port = "0.0.0.0", "7003"
logger = logging.getLogger(__file__)


@app.websocket("/asr")
async def websocket_asr(
    websocket: WebSocket,
    sample_rate: int = Query(
        16000, title="Sample Rate", description="The sample rate of the audio."
    ),
):
    await websocket.accept()

    # Set up ZeroMQ sockets
    byte_push_port = f"tcp://{vad_address}:{vad_port}"
    asr_pull_port = f"tcp://{asr_address}:{asr_port}"
    byte_push_socket = context.socket(zmq.PUSH)
    byte_push_socket.connect(byte_push_port)
    asr_pull_socket = context.socket(zmq.PULL)
    asr_pull_socket.connect(asr_pull_port)

    # Message passing pipeline
    # Push raw bytes -> VAD
    async def task_recv_pcm():
        while True:
            pcm_bytes = await websocket.receive_bytes()
            if not pcm_bytes:
                return
            await byte_push_socket.send_pyobj(pcm_bytes)
            logger.info("Sent websocket bytes")

    # Receive results from ASR
    async def task_recv_asr():
        while True:
            asr_result: ASRResult = await asr_pull_socket.recv_pyobj()
            if not asr_result:
                return
            await websocket.send_json(asr_result.to_dict())

    try:
        await asyncio.gather(task_recv_pcm(), task_recv_asr())
    except WebSocketDisconnect:
        logger.info("asr: disconnected")
    finally:
        byte_push_socket.close()
        asr_pull_socket.close()


if __name__ == "__main__":
    models_root = "./models"

    for d in [".", "..", "../.."]:
        if os.path.isdir(f"{d}/models"):
            models_root = f"{d}/models"
            break

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000, help="port number")
    parser.add_argument("--addr", type=str, default="127.0.0.1", help="serve address")
    parser.add_argument("--docker", action="store_true", help="Docker serving, use DNS")
    args = parser.parse_args()
    if args.docker:
        args.addr = "0.0.0.0"

    app.mount("/", app=StaticFiles(directory="./assets", html=True), name="assets")

    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )
    uvicorn.run(app, host=args.addr, port=args.port)
