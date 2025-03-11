from typing import Union, Tuple, List
import logging
import time
import sherpa_onnx
import os
import asyncio

logger = logging.getLogger(__file__)
_asr_engines = dict()


class ASRResult:
    def __init__(self, text: str, finished: bool, idx: int):
        self.text = text
        self.finished = finished
        self.idx = idx

    def to_dict(self):
        return {"text": self.text, "finished": self.finished, "idx": self.idx}


class ASRStream:
    def __init__(
        self,
        recognizer: Union[sherpa_onnx.OnlineRecognizer | sherpa_onnx.OfflineRecognizer],
        sample_rate: int,
    ) -> None:
        self.recognizer = recognizer
        self.inbuf = asyncio.Queue()
        self.outbuf = asyncio.Queue()
        self.sample_rate = sample_rate
        self.is_closed = False
        self.online = isinstance(recognizer, sherpa_onnx.OnlineRecognizer)

    async def start(self):
        if self.online:
            asyncio.create_task(self.run_online())
        else:
            asyncio.create_task(self.run_offline())

    async def run_online(self):
        raise NotImplementedError("Online ASR not implemented")
        stream = self.recognizer.create_stream()
        last_result = ""
        segment_id = 0
        logger.info("asr: start real-time recognizer")
        while not self.is_closed:
            samples = await self.inbuf.get()
            stream.accept_waveform(self.sample_rate, samples)
            while self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)

            is_endpoint = self.recognizer.is_endpoint(stream)
            result = self.recognizer.get_result(stream)

            if result and (last_result != result):
                last_result = result
                logger.info(f" > {segment_id}:{result}")
                self.outbuf.put_nowait(ASRResult(result, False, segment_id))

            if is_endpoint:
                if result:
                    logger.info(f"{segment_id}: {result}")
                    self.outbuf.put_nowait(ASRResult(result, True, segment_id))
                    segment_id += 1
                self.recognizer.reset(stream)

    async def run_offline(self):
        st = None
        while not self.is_closed:
            segment_id, samples = await self.inbuf.get()
            if not st:
                st = time.time()
            asr_stream = self.recognizer.create_stream()
            asr_stream.accept_waveform(self.sample_rate, samples)

            self.recognizer.decode_stream(asr_stream)
            result = asr_stream.result.text.strip()
            if result:
                duration = time.time() - st
                logger.info(f"{segment_id}:{result} ({duration:.2f}s)")
                self.outbuf.put_nowait(ASRResult(result, True, segment_id))

            st = None

    async def close(self):
        self.is_closed = True
        self.outbuf.put_nowait(None)

    async def write(self, vad_samples: Tuple[int, List]):
        self.inbuf.put_nowait(vad_samples)

    async def read(self) -> ASRResult:
        return await self.outbuf.get()


def create_zipformer(samplerate: int, args) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(
        args.models_root, "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
    )
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    encoder = os.path.join(d, "encoder-epoch-99-avg-1.onnx")
    decoder = os.path.join(d, "decoder-epoch-99-avg-1.onnx")
    joiner = os.path.join(d, "joiner-epoch-99-avg-1.onnx")
    tokens = os.path.join(d, "tokens.txt")

    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        provider=args.asr_provider,
        num_threads=args.threads,
        sample_rate=samplerate,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20,  # it essentially disables this rule
    )
    return recognizer


def create_sensevoice(samplerate: int, args) -> sherpa_onnx.OfflineRecognizer:
    d = os.path.join(
        args.models_root, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    )

    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=args.threads,
        sample_rate=samplerate,
        use_itn=True,
        debug=0,
        language=args.asr_lang,
    )
    return recognizer


def create_paraformer_trilingual(samplerate: int, args) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(
        args.models_root, "sherpa-onnx-paraformer-trilingual-zh-cantonese-en"
    )
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=args.threads,
        sample_rate=samplerate,
        debug=0,
        provider=args.asr_provider,
    )
    return recognizer


def create_paraformer_en(samplerate: int, args) -> sherpa_onnx.OnlineRecognizer:
    d = os.path.join(args.models_root, "sherpa-onnx-paraformer-en")
    if not os.path.exists(d):
        raise ValueError(f"asr: model not found {d}")

    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=os.path.join(d, "model.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=args.threads,
        sample_rate=samplerate,
        use_itn=True,
        debug=0,
        provider=args.asr_provider,
    )
    return recognizer


def load_asr_engine(samplerate: int, args) -> sherpa_onnx.OnlineRecognizer:
    cache_engine = _asr_engines.get(args.asr_model)
    if cache_engine:
        return cache_engine
    st = time.time()
    if args.asr_model == "zipformer-bilingual":
        cache_engine = create_zipformer(samplerate, args)
    elif args.asr_model == "sensevoice":
        cache_engine = create_sensevoice(samplerate, args)
    elif args.asr_model == "paraformer-trilingual":
        cache_engine = create_paraformer_trilingual(samplerate, args)
    elif args.asr_model == "paraformer-en":
        cache_engine = create_paraformer_en(samplerate, args)
    else:
        raise ValueError(f"asr: unknown model {args.asr_model}")
    _asr_engines[args.asr_model] = cache_engine
    logger.info(f"asr: engine loaded in {time.time() - st:.2f}s")
    return cache_engine


def load_sid_engine(
    samplerate: int,
    args,
) -> Tuple[sherpa_onnx.SpeakerEmbeddingExtractor, sherpa_onnx.SpeakerEmbeddingManager]:
    d = os.path.join(
        args.models_root, "3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx"
    )
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=d,
        num_threads=args.threads,
        provider=args.asr_provider,
    )

    if not config.validate():
        raise ValueError(f"Invalid config. {config}")
    sid = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return (sid, sherpa_onnx.SpeakerEmbeddingManager(sid.dim))


async def start_asr_stream(samplerate: int, args) -> ASRStream:
    """
    Start a ASR stream
    """
    stream = ASRStream(load_asr_engine(samplerate, args), samplerate)
    await stream.start()
    return stream
