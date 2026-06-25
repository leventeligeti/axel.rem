"""axel.rem belépési pont — API server + prompt-chill + dream scheduler + task worker."""
import logging
import sys
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from axel_rem.prompt_chill import PromptChill
from axel_rem.dream import DreamScheduler
from axel_rem.agent import RemAgent
from axel_rem.api import start_api_server


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    # API server mindig indul (signal + recall végpontok)
    if mode in ("all", "chill", "worker", "dream"):
        start_api_server()

    if mode in ("all", "chill"):
        t_chill = threading.Thread(
            target=PromptChill().run, daemon=True, name="prompt-chill"
        )
        t_chill.start()

    if mode in ("all", "worker"):
        t_worker = threading.Thread(
            target=RemAgent().run_worker, kwargs={"poll_interval": 15.0},
            daemon=True, name="rem-worker"
        )
        t_worker.start()

    if mode in ("all", "dream"):
        DreamScheduler().run()  # blokkoló — scheduler loop
    elif mode in ("chill", "worker"):
        import time
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
