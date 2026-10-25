import asyncio
import logging

from . import config
from .agent import Agent, wait_for_display


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    wait_for_display()
    asyncio.run(Agent(cfg).run())


if __name__ == "__main__":
    main()
