from typing import Tuple
import logging
import time
import sherpa_onnx
import os
import numpy as np
import asyncio
import zmq
import zmq.asyncio
import argparse

logger = logging.getLogger(__file__)
_asr_engines = dict()


class SIDStream:
    def __init__(
        self,
        model: Tuple[
            sherpa_onnx.SpeakerEmbeddingExtractor, sherpa_onnx.SpeakerEmbeddingManager
        ],
        sample_rate: int,
        push_port: str,
        pull_port: str,
    ) -> None:
        self.model = model
        self.push_port = push_port
        self.pull_port = pull_port
        self.sample_rate = sample_rate
        self.num_speakers = 1  # 0 reserved for assistant agent
        self.online = False
        # ZeroMQ context
        self.context = zmq.asyncio.Context()
        self.push_socket = self.context.socket(zmq.PUSH)
        self.push_socket.bind(push_port)
        self.pull_socket = self.context.socket(zmq.SUB)
        self.pull_socket.connect(pull_port)
        self.pull_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    async def start(self):
        if self.online:
            asyncio.create_task(self.run_online())
        else:
            asyncio.create_task(self.run_offline())

    async def run_online(self):
        raise NotImplementedError("Online SID not implemented")

    async def run_offline(self):
        sid, sid_manager = self.model
        st = None
        while True:
            segment_id, samples = await self.pull_socket.recv_pyobj()
            sid_stream = sid.create_stream()
            logger.info(f"{segment_id}: SID start")
            if not st:
                st = time.time()
            sid_stream.accept_waveform(self.sample_rate, samples)

            embedding = sid.compute(sid_stream)
            embedding = np.array(embedding)
            name = sid_manager.search(embedding, threshold=0.4)
            if not name:
                # register it
                name = f"speaker_{self.num_speakers}"
                status = sid_manager.add(name, embedding)
                if not status:
                    raise RuntimeError(f"Failed to register speaker {name}")
                logger.info(f"{segment_id}: Detected new speaker. Registered as {name}")
                self.num_speakers += 1
            else:
                logger.info(f"{segment_id}: Detected existing speaker: {name}")
            await self.push_socket.send_pyobj(
                {"idx": segment_id, "name": name, "finished": True}
            )
            duration = time.time() - st
            logger.info(f"{segment_id}: {name} ({duration:.2f}s)")
            st = None

    async def close(self):
        self.push_socket.close()
        self.pull_socket.close()


def load_sid_engine(
    sample_rate: int,
    model_dir: str,
    provider: str,
    threads: int,
) -> Tuple[sherpa_onnx.SpeakerEmbeddingExtractor, sherpa_onnx.SpeakerEmbeddingManager]:
    d = os.path.join(
        model_dir, "3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx"
    )
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=d,
        provider=provider,
        num_threads=threads,
    )

    if not config.validate():
        raise ValueError(f"Invalid config. {config}")
    sid = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return (sid, sherpa_onnx.SpeakerEmbeddingManager(sid.dim))


async def start_sid_stream(args) -> SIDStream:
    """
    Start a SID stream
    """
    stream = SIDStream(
        load_sid_engine(args.sample_rate, args.model_dir, args.provider, args.threads),
        args.sample_rate,
        args.push_port,
        args.pull_port,
    )
    await stream.start()
    return stream


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the SID stream.")
    parser.add_argument(
        "--sample_rate", type=int, default=16000, help="Sample rate of the audio"
    )
    parser.add_argument(
        "--push_port", type=str, default="tcp://127.0.0.1:8004", help="ZeroMQ push port"
    )
    parser.add_argument(
        "--pull_port", type=str, default="tcp://127.0.0.1:8002", help="ZeroMQ pull port"
    )
    parser.add_argument(
        "--model_dir", type=str, default="./models", help="Root directory for models"
    )
    parser.add_argument(
        "--provider", type=str, default="cpu", help="provider, cpu or cuda"
    )
    parser.add_argument("--threads", type=int, default=2, help="Number of threads")
    parser.add_argument("--docker", action="store_true", help="Docker serving, use DNS")
    args = parser.parse_args()
    if args.docker:
        args.push_port = os.environ.get("PUSH_PORT", args.push_port)
        args.pull_port = os.environ.get("PULL_PORT", args.pull_port)

    logging.basicConfig(
        format="%(levelname)s: %(asctime)s %(name)s:%(lineno)s %(message)s",
        level=logging.INFO,
    )

    loop = asyncio.get_event_loop()
    sid_stream = loop.run_until_complete(start_sid_stream(args))
    # TODO: logger not printing here for some reason
    logger.info(f"SID stream started with ports: {args.pull_port}, {args.push_port}")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(sid_stream.close())
    finally:
        loop.close()
