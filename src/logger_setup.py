import logging
import sys
from pathlib import Path


def setup_logging(log_dir: str = "logs"):
    Path(log_dir).mkdir(exist_ok=True)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger("bot")
    root.setLevel(logging.DEBUG)

    # Console — INFO
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(ch)

    # File — DEBUG (all details)
    fh = logging.FileHandler(f"{log_dir}/bot.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)

    # Trades file — only trades
    th = logging.FileHandler(f"{log_dir}/trades.log", encoding="utf-8")
    th.setLevel(logging.INFO)
    th.setFormatter(logging.Formatter(fmt, datefmt))
    trade_log = logging.getLogger("bot.risk")
    trade_log.addHandler(th)

    return root
