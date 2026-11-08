"""python3 -m ink: the Kindle panel (KINDLE_LISTEN) and the phone's mic WebSocket (KINDLE_MIC_LISTEN), one process.

    python3 -m ink               both, as configured in kindle/.env
    python3 -m ink --port 8791   the panel on another port (a second copy for testing; its mic stays off)
"""

import argparse
import sys
import threading

from ink import config, mic, panel


def main():
    parser = argparse.ArgumentParser(prog="python3 -m ink", description="Claude Code panel for a Kindle")
    parser.add_argument("--port", type=int, help="serve the panel on 127.0.0.1:PORT instead, without the mic")
    args = parser.parse_args()
    if args.port:
        return panel.serve(("127.0.0.1", args.port))
    if config.AUTH == "access" and not (config.ACCESS_TEAM and config.ACCESS_AUD and config.ACCESS_EMAIL):
        sys.stderr.write("ink: Cloudflare Access isn't configured (KINDLE_ACCESS_*): every page will be refused\n")
    sys.stderr.write(f"ink: panel on {config.LISTEN[0]}:{config.LISTEN[1]}, "
                     f"mic on {config.MIC_LISTEN[0]}:{config.MIC_LISTEN[1]}\n")
    threading.Thread(target=mic.serve, args=(config.MIC_LISTEN,), daemon=True).start()
    panel.serve(config.LISTEN)


if __name__ == "__main__":
    main()
