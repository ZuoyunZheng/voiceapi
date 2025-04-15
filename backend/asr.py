import argparse
from typing import Union
import logging
import time
import sherpa_onnx
import os
import asyncio
import zmq
import zmq.asyncio
from utils import ASRResult

logger = logging.getLogger(__file__)
_asr_engines = dict()


class ASRStream:
    def __init__(
        self,
        model: Union[sherpa_onnx.OnlineRecognizer | sherpa_onnx.OfflineRecognizer],
        sample_rate: int,
        push_port: str,
        pull_port: str,
    ) -> None:
        self.model = model
        self.push_port = push_port
        self.pull_port = pull_port
        self.sample_rate = sample_rate
        self.online = isinstance(model, sherpa_onnx.OnlineRecognizer)
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
        raise NotImplementedError("Online ASR not implemented")
        stream = self.model.create_stream()
        last_result = ""
        segment_id = 0
        logger.info("asr: start real-time recognizer")
        while not self.is_closed:
            samples = await self.inbuf.get()
            stream.accept_waveform(self.sample_rate, samples)
            while self.model.is_ready(stream):
                self.model.decode_stream(stream)

            is_endpoint = self.model.is_endpoint(stream)
            result = self.model.get_result(stream)

            if result and (last_result != result):
                last_result = result
                logger.info(f" > {segment_id}:{result}")
                self.outbuf.put_nowait(ASRResult(result, False, segment_id))

            if is_endpoint:
                if result:
                    logger.info(f"{segment_id}: {result}")
                    self.outbuf.put_nowait(ASRResult(result, True, segment_id))
                    segment_id += 1
                self.model.reset(stream)

    async def run_offline(self):
        st = None
        while True:
            segment_id, samples = await self.pull_socket.recv_pyobj()
            asr_stream = self.model.create_stream()
            if not st:
                st = time.time()
            asr_stream.accept_waveform(self.sample_rate, samples)

            self.model.decode_stream(asr_stream)
            result = asr_stream.result.text.strip()
            await self.push_socket.send_pyobj(ASRResult(result, True, segment_id))
            duration = time.time() - st
            logger.info(f"{segment_id}:{result} ({duration:.2f}s)")
            st = None

    async def close(self):
        self.push_socket.close()
        self.pull_socket.close()


def create_zipformer(
    sample_rate: int, model_dir, threads, provider
) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(
        model_dir, "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
    )
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    encoder = os.path.join(d, "encoder-epoch-99-avg-1.onnx")
    decoder = os.path.join(d, "decoder-epoch-99-avg-1.onnx")
    joiner = os.path.join(d, "joiner-epoch-99-avg-1.onnx")
    tokens = os.path.join(d, "tokens.txt")

    model = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        provider=provider,
        num_threads=threads,
        sample_rate=sample_rate,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20,  # it essentially disables this rule
    )
    return model


def create_sensevoice(
    sample_rate: int, model_dir, threads, provider
) -> sherpa_onnx.OfflineRecognizer:
    d = os.path.join(model_dir, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17")

    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=threads,
        sample_rate=sample_rate,
        provider=provider,
        use_itn=True,
        debug=0,
        language="en",  # ASR language: zh, en, ja, ko, yue
    )
    return model


def create_paraformer_trilingual(
    sample_rate: int, model_dir, threads, provider
) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(model_dir, "sherpa-onnx-paraformer-trilingual-zh-cantonese-en")
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    model = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=threads,
        sample_rate=sample_rate,
        debug=0,
        provider=provider,
    )
    return model


def create_paraformer_en(
    sample_rate: int, model_dir, threads, provider
) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(model_dir, "sherpa-onnx-paraformer-en")
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    model = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=threads,
        sample_rate=sample_rate,
        use_itn=True,
        debug=0,
        provider=provider,
    )
    return model


def load_asr_engine(
    sample_rate: int,
    model_dir: str,
    model_type: str,
    provider: str,
    threads: int,
) -> sherpa_onnx.OnlineRecognizer:
    cache_engine = _asr_engines.get(model_type)
    if cache_engine:
        return cache_engine
    st = time.time()
    if model_type == "zipformer-bilingual":
        cache_engine = create_zipformer(sample_rate, model_dir, threads, provider)
    elif model_type == "sensevoice":
        cache_engine = create_sensevoice(sample_rate, model_dir, threads, provider)
    elif model_type == "paraformer-trilingual":
        cache_engine = create_paraformer_trilingual(
            sample_rate, model_dir, threads, provider
        )
    elif model_type == "paraformer-en":
        cache_engine = create_paraformer_en(sample_rate, model_dir, threads, provider)
    else:
        raise ValueError(f"asr: unknown model {model_type}")
    _asr_engines[model_type] = cache_engine
    logger.info(f"ASR: engine loaded in {time.time() - st:.2f}s")
    return cache_engine


async def start_asr_stream(args) -> ASRStream:
    """
    Start a ASR stream
    """
    stream = ASRStream(
        load_asr_engine(
            args.sample_rate,
            args.model_dir,
            args.model_type,
            args.provider,
            args.threads,
        ),
        args.sample_rate,
        args.push_port,
        args.pull_port,
    )
    await stream.start()
    return stream


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample_rate", type=int, default=16000, help="Sample rate")
    parser.add_argument(
        "--push_port", type=str, default="tcp://127.0.0.1:8003", help="ZeroMQ push port"
    )
    parser.add_argument(
        "--pull_port", type=str, default="tcp://127.0.0.1:8002", help="ZeroMQ pull port"
    )
    parser.add_argument(
        "--model_dir", type=str, default="./models", help="Root directory for models"
    )
    parser.add_argument(
        "--model_type",
        type=str,
        default="sensevoice",
        help="ASR model name: zipformer-bilingual, sensevoice, paraformer-trilingual, paraformer-en",
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
    asr_stream = loop.run_until_complete(start_asr_stream(args))
    # TODO: logger not printing here for some reason
    logger.info(f"ASR stream started with ports: {args.pull_port}, {args.push_port}")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(asr_stream.close())
    finally:
        loop.close()
