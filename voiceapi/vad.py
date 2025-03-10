from typing import Tuple, List
import logging
import time
import sherpa_onnx
import os
import asyncio
import numpy as np

logger = logging.getLogger(__file__)
_vad_engines = dict()


class VADStream:
    def __init__(
        self,
        model: sherpa_onnx.VoiceActivityDetector,
        sample_rate: int,
    ) -> None:
        self.model = model
        self.inbuf = asyncio.Queue()
        self.outbuf = asyncio.Queue()
        self.sample_rate = sample_rate
        self.is_closed = False
        self.online = False

    async def start(self):
        if self.online:
            asyncio.create_task(self.run_online())
        else:
            asyncio.create_task(self.run_offline())

    async def run_online(self):
        raise NotImplementedError("Online VAD not implemented")

    async def run_offline(self):
        vad = self.model
        segment_id = 0
        st = None
        while not self.is_closed:
            samples = await self.inbuf.get()
            vad.accept_waveform(samples)
            while not vad.empty():
                if not st:
                    st = time.time()

                await self.outbuf.put((segment_id, vad.front.samples))
                duration = time.time() - st
                logger.info(f"{segment_id}: VAD ({duration:.2f}s)")
                vad.pop()
                segment_id += 1
            st = None

    async def close(self):
        self.is_closed = True
        self.outbuf.put_nowait(None)

    async def write(self, pcm_bytes: bytes):
        pcm_data = np.frombuffer(pcm_bytes, dtype=np.int16)
        samples = pcm_data.astype(np.float32) / 32768.0
        self.inbuf.put_nowait(samples)

    async def read(self) -> Tuple[int, List]:
        return await self.outbuf.get()


def load_vad_engine(
    samplerate: int,
    args,
    min_silence_duration: float = 0.25,
    buffer_size_in_seconds: int = 100,
) -> sherpa_onnx.VoiceActivityDetector:
    if "vad" not in _vad_engines:
        config = sherpa_onnx.VadModelConfig()
        d = os.path.join(args.models_root, "silero_vad")
        if not os.path.exists(d):
            raise ValueError(f"vad: model not found {d}")

        config.silero_vad.model = os.path.join(d, "silero_vad.onnx")
        config.silero_vad.min_silence_duration = min_silence_duration
        config.sample_rate = samplerate
        config.provider = args.asr_provider
        config.num_threads = args.threads

        vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=buffer_size_in_seconds
        )
        _vad_engines["vad"] = vad
    return _vad_engines["vad"]


async def start_vad_stream(samplerate: int, args) -> VADStream:
    """
    Start a VAD stream
    """
    stream = VADStream(load_vad_engine(samplerate, args), samplerate)
    await stream.start()
    return stream
