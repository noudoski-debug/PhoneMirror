"""H.264 Annex B decoder built on PyAV (imported lazily so the rest stays testable without it)."""


class H264Decoder:
    def __init__(self):
        import av  # noqa: PLC0415
        self._ctx = av.CodecContext.create("h264", "r")
        self._ctx.options = {"flags": "low_delay", "flags2": "fast"}
        self._ctx.thread_type = "SLICE"

    def decode(self, data):
        """Feed one access unit (Annex B); return a list of RGB uint8 arrays (H x W x 3)."""
        frames = []
        for packet in self._ctx.parse(data):
            for frame in self._ctx.decode(packet):
                frames.append(frame.to_ndarray(format="rgb24"))
        return frames
