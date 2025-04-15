from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
import asyncio
import logging
import uvicorn
from utils import ASRResult
import argparse
import os
import zmq
import zmq.asyncio
from collections import defaultdict

context = zmq.asyncio.Context()
app = FastAPI()
# mic -ws:.../8000/asr-> app -8001-> vad -8002-> asr -8003-> app -ws->
#                                        \8002-> sid -8004->
vad_address, vad_port = "0.0.0.0", "8001"
asr_address, asr_port = "0.0.0.0", "8003"
sid_address, sid_port = "0.0.0.0", "8004"
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
    sid_pull_port = f"tcp://{sid_address}:{sid_port}"
    byte_push_socket = context.socket(zmq.PUSH)
    byte_push_socket.bind(byte_push_port)
    asr_pull_socket = context.socket(zmq.PULL)
    asr_pull_socket.connect(asr_pull_port)
    sid_pull_socket = context.socket(zmq.PULL)
    sid_pull_socket.connect(sid_pull_port)
    speaker_ids = {0: "Assistant", -1: "Unknown speaker"}
    # segment_id: {"id": str, "content": str, "finished": bool}
    intermediate_result = defaultdict(
        lambda: {
            "id": speaker_ids[-1],
            "content": "",
            "sid_finished": False,
            "asr_finished": False,
        }
    )
    result_queue = asyncio.Queue()
    logger.info(f"App ports: {byte_push_port}, {asr_pull_port}, {sid_pull_port}")

    # Message passing pipeline
    # Send raw bytes -> VAD
    async def task_send_pcm():
        while True:
            pcm_bytes = await websocket.receive_bytes()
            if not pcm_bytes:
                logging.info("Received zero bytes, returning")
                return
            await byte_push_socket.send_pyobj(pcm_bytes)

    # Receive results from ASR
    async def task_recv_asr():
        while True:
            asr_result: ASRResult = await asr_pull_socket.recv_pyobj()
            logger.info(f"Received ASR results for segment {asr_result.idx}")
            if not asr_result:
                return
            ir = intermediate_result[asr_result.idx]
            ir["content"] += asr_result.text
            ir["asr_finished"] = asr_result.finished
            if ir["asr_finished"] and ir["sid_finished"]:
                del ir["asr_finished"]
                del ir["sid_finished"]
                result_queue.put_nowait(intermediate_result[asr_result.idx])
                logger.info(f"Enqueued results for segment {asr_result.idx}: {ir['id']}, {ir['content']}")
                del ir
            # await websocket.send_json(asr_result.to_dict())

    # Receive results from SID
    async def task_recv_sid():
        while True:
            sid_result: dict = await sid_pull_socket.recv_pyobj()
            logger.info(f"Received SID results for segment {sid_result['idx']}")
            if not sid_result:
                return
            ir = intermediate_result[sid_result["idx"]]
            ir["id"] = sid_result["name"]
            ir["sid_finished"] = sid_result["finished"]
            if ir["asr_finished"] and ir["sid_finished"]:
                del ir["asr_finished"]
                del ir["sid_finished"]
                result_queue.put_nowait(intermediate_result[sid_result["idx"]])
                logger.info(f"Enqueued results for segment {sid_result['idx']}: {ir['id']}, {ir['content']}")
                del ir
            # await websocket.send_json(sid_result)

    # Send result
    async def task_send_result():
        while True:
            result = await result_queue.get()
            await websocket.send_json(result)

    try:
        await asyncio.gather(
            task_send_pcm(), task_recv_asr(), task_recv_sid(), task_send_result()
        )
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
    # TODO: make arg parsing better designed
    if args.docker:
        args.addr, vad_address, asr_address, sid_address = "0.0.0.0", "*", "asr", "sid"

    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )
    uvicorn.run(app, host=args.addr, port=args.port)
