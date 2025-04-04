import logging
import time
import sherpa_onnx
import os
import asyncio
import numpy as np
import zmq
import zmq.asyncio
import argparse


logger = logging.getLogger(__file__)
_vad_engines = dict()


class VADStream:
    def __init__(
        self,
        model: sherpa_onnx.VoiceActivityDetector,
        sample_rate: int,
        push_port: str,
        pull_port: str,
    ) -> None:
        self.model = model
        self.push_port = push_port
        self.pull_port = pull_port
        self.sample_rate = sample_rate
        self.online = False
        # ZeroMQ context
        self.context = zmq.asyncio.Context()
        self.push_socket = self.context.socket(zmq.PUB)
        self.push_socket.bind(push_port)
        self.pull_socket = self.context.socket(zmq.PULL)
        self.pull_socket.connect(pull_port)

    async def start(self):
        await self.push_socket.send_pyobj("")
        if self.online:
            asyncio.create_task(self.run_online())
        else:
            asyncio.create_task(self.run_offline())

    async def run_online(self):
        raise NotImplementedError("Online VAD not implemented")

    async def run_offline(self):
        vad, segment_id, st = self.model, 0, None
        while True:
            pcm_bytes = await self.pull_socket.recv_pyobj()
            pcm_data = np.frombuffer(pcm_bytes, dtype=np.int16)
            samples = pcm_data.astype(np.float32) / 32768.0
            vad.accept_waveform(samples)
            while not vad.empty():
                if not st:
                    st = time.time()
                await self.push_socket.send_pyobj((segment_id, vad.front.samples))
                vad.pop()
                segment_id += 1
                duration = time.time() - st
                logger.info(f"{segment_id}: VAD ({duration:.2f}s)")
            st = None

    async def close(self):
        self.push_socket.close()
        self.pull_socket.close()


def load_vad_engine(
    sample_rate: int,
    model_dir: str,
    provider: str,
    threads: int,
    min_silence_duration: float = 0.25,
    buffer_size_in_seconds: int = 100,
) -> sherpa_onnx.VoiceActivityDetector:
    st = time.time()
    if "vad" not in _vad_engines:
        config = sherpa_onnx.VadModelConfig()
        d = os.path.join(model_dir, "silero_vad")
        if not os.path.exists(d):
            raise ValueError(f"vad: model not found {d}")

        config.silero_vad.model = os.path.join(d, "silero_vad.onnx")
        config.silero_vad.min_silence_duration = min_silence_duration
        config.sample_rate = sample_rate
        config.provider = args.provider
        config.num_threads = args.threads

        vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=buffer_size_in_seconds
        )
        _vad_engines["vad"] = vad
    logger.info(f"VAD engine loaded in {time.time() - st:.2f}s")
    return _vad_engines["vad"]


async def start_vad_stream(args) -> VADStream:
    """
    Start a VAD stream
    """
    stream = VADStream(
        load_vad_engine(args.sample_rate, args.model_dir, args.provider, args.threads),
        args.sample_rate,
        args.push_port,
        args.pull_port,
    )
    await stream.start()
    return stream


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the VAD stream.")
    parser.add_argument(
        "--sample_rate", type=int, default=16000, help="Sample rate of the audio"
    )
    parser.add_argument(
        "--push_port", type=str, default="tcp://127.0.0.1:8002", help="ZeroMQ push port"
    )
    parser.add_argument(
        "--pull_port", type=str, default="tcp://127.0.0.1:8001", help="ZeroMQ pull port"
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
    vad_stream = loop.run_until_complete(start_vad_stream(args))
    # TODO: logger not printing here for some reason
    logger.info(f"VAD stream started with ports: {args.pull_port}, {args.push_port}")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(vad_stream.close())
    finally:
        loop.close()
