from __future__ import annotations

import logging
import os

from app.services.host_bootstrap import apply_host_overrides, build_host_overrides

logging.basicConfig(level=logging.INFO, format='[host-bootstrap] %(message)s')
logger = logging.getLogger(__name__)


def main() -> int:
    overrides = build_host_overrides(os.environ)
    if not overrides:
        logger.info('no unresolved public hosts detected')
        return 0

    applied = apply_host_overrides(overrides)
    for ip, host in overrides:
        logger.info('resolved %s -> %s', host, ip)
    logger.info('applied %d host override(s)', applied)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
