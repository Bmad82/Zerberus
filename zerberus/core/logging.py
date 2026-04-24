"""
Zentrales Logging für Zerberus.
"""
import logging
import sys
from pathlib import Path

def setup_logging(level: str = "INFO"):
    """Konfiguriert das Logging einheitlich."""
    Path("logs").mkdir(exist_ok=True)
    
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.stream.reconfigure(encoding="utf-8", errors="replace")
    handlers = [
        stream_handler,
        logging.FileHandler("logs/zerberus.log", encoding="utf-8")
    ]
    
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        handlers=handlers
    )

    for _noisy in ("httpx", "apscheduler", "apscheduler.scheduler",
                   "apscheduler.executors", "sentence_transformers"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("zerberus")
    return logger
