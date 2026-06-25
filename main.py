"""axel.rem belépési pont — prompt-chill + dream scheduler."""
import logging
import sys
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from axel_rem.prompt_chill import PromptChill
from axel_rem.dream import DreamScheduler


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("all", "chill"):
        t = threading.Thread(target=PromptChill().run, daemon=True, name="prompt-chill")
        t.start()

    if mode in ("all", "dream"):
        DreamScheduler().run()  # blokkoló — scheduler loop
    elif mode == "chill":
        # Csak chill — várunk hogy a daemon thread éljen
        t.join()


if __name__ == "__main__":
    main()
