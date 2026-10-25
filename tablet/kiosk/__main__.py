import logging
import signal

from . import config
from .agent import Agent, wait_for_display


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    wait_for_display()
    agent = Agent(cfg)
    # systemd stops the service with SIGTERM: finish the capture so its last segment is playable.
    signal.signal(signal.SIGTERM, lambda *_: agent.stop_event.set())
    agent.run()


if __name__ == "__main__":
    main()
