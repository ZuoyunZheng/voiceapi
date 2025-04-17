import logging
import time
import sherpa_onnx
import os
import asyncio
import zmq
import zmq.asyncio
import argparse

logger = logging.getLogger(__file__)
_asr_engines = dict()


class KWSStream:
    def __init__(
        self,
        model: sherpa_onnx.KeywordSpotter,
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
        raise NotImplementedError("Online KWS not implemented")

    async def run_offline(self):
        st = None
        while True:
            segment_id, samples = await self.pull_socket.recv_pyobj()
            kws_stream = self.model.create_stream()
            if not st:
                st = time.time()
            kws_stream.accept_waveform(self.sample_rate, samples)
            result = None
            while self.model.is_ready(kws_stream):
                # logger.info(f"{segment_id}: Decoding")
                self.model.decode_stream(kws_stream)
                result = self.model.get_result(kws_stream)
                # TODO: look at if kws breaks loop
                if result:
                    await self.push_socket.send_pyobj(
                        {"idx": segment_id, "type": "instruction", "finished": True}
                    )
                    self.model.reset_stream(kws_stream)
                    duration = time.time() - st
                    logger.info(f"{segment_id}: KW -- {result} ({duration:.2f}s)")
                    break
            if not result:
                duration = time.time() - st
                logger.info(
                    f"{segment_id}: {result if result else '<None>'} ({duration:.2f}s)"
                )
                await self.push_socket.send_pyobj(
                    {"idx": segment_id, "type": "transcript", "finished": True}
                )
            st = None

    async def close(self):
        self.push_socket.close()
        self.pull_socket.close()


def load_kws_engine(
    sample_rate: int,
    model_dir: str,
    provider: str,
    threads: int,
) -> sherpa_onnx.KeywordSpotter:
    st = time.time()
    d = os.path.join(model_dir, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
    if not os.path.exists(d):
        raise ValueError(f"kws: model not found {d}")

    encoder = os.path.join(d, "encoder-epoch-12-avg-2-chunk-16-left-64.onnx")
    decoder = os.path.join(d, "decoder-epoch-12-avg-2-chunk-16-left-64.onnx")
    joiner = os.path.join(d, "joiner-epoch-12-avg-2-chunk-16-left-64.onnx")
    tokens = os.path.join(d, "tokens.txt")
    keywords_file = os.path.join(d, "keywords.txt")

    kws = sherpa_onnx.KeywordSpotter(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=threads,
        max_active_paths=4,
        keywords_file=keywords_file,
        keywords_score=1.0,
        keywords_threshold=0.25,
        num_trailing_blanks=1,
        provider=provider,
    )

    logger.info(f"KWS engine loaded in {time.time() - st:.2f}s")
    return kws


async def start_kws_stream(args) -> KWSStream:
    """
    Start a KWS stream
    """
    stream = KWSStream(
        load_kws_engine(args.sample_rate, args.model_dir, args.provider, args.threads),
        args.sample_rate,
        args.push_port,
        args.pull_port,
    )
    await stream.start()
    return stream


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the KWS stream.")
    parser.add_argument(
        "--sample_rate", type=int, default=16000, help="Sample rate of the audio"
    )
    parser.add_argument(
        "--push_port", type=str, default="tcp://127.0.0.1:8005", help="ZeroMQ push port"
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
    kws_stream = loop.run_until_complete(start_kws_stream(args))
    # TODO: logger not printing here for some reason
    logger.info(f"KWS stream started with ports: {args.pull_port}, {args.push_port}")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(kws_stream.close())
    finally:
        loop.close()
