"""Allocator Radar."""
import os as _os

# No telemetry from ML libraries (AGENDA §6).
for _k, _v in {"HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
               "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1"}.items():
    _os.environ.setdefault(_k, _v)
