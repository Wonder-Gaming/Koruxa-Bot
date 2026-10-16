"""Belt and braces: redact anything token-shaped from log output."""

import logging
import re

_REDACTIONS = [
    (re.compile(r"kxu_[A-Za-z0-9]+"), "kxu_[redacted]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)(token=)[^&\s]+"), r"\1[redacted]"),
    (re.compile(r"\b[0-9a-f]{64}\b"), "[redacted]"),  # clan tokens are 64 hex characters
]


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, replacement in _REDACTIONS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def install() -> None:
    """Attach the filter to every root handler, so it covers all loggers."""
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactSecrets())
