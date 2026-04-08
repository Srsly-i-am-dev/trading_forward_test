import logging


class _DefaultSignalIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "signal_id"):
            record.signal_id = "-"
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.addFilter(_DefaultSignalIdFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s signal_id=%(signal_id)s %(message)s"
        )
    )
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)


class SignalLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("signal_id", self.extra.get("signal_id", "-"))
        return msg, kwargs
