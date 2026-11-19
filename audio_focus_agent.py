import argparse
import logging
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log-file", required=True)
    args, _ = parser.parse_known_args()

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    logging.info("Audio Focus agent starting with Python %s", sys.executable)
    try:
        from audio_focus import main as run_audio_focus

        run_audio_focus()
    except BaseException:
        logging.error("Audio Focus agent terminated with an error:\n%s",
                      traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
