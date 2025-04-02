class ASRResult:
    def __init__(self, text: str, finished: bool, idx: int):
        self.text = text
        self.finished = finished
        self.idx = idx

    def to_dict(self):
        return {"text": self.text, "finished": self.finished, "idx": self.idx}

