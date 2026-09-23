"""The parser sandbox: where files from outside the company are opened (guardrail 9).

`run_sandboxed` is the only way a parser should ever be called. Everything else here is what
it puts in place before the parser sees a byte.
"""

from firebid.sandbox.limits import DEFAULT_LIMITS, Limits, NetworkBlocked
from firebid.sandbox.runner import SandboxFailure, run_sandboxed, scratch_dir
from firebid.sandbox.safety import UnsafeContent

__all__ = [
    "DEFAULT_LIMITS",
    "Limits",
    "NetworkBlocked",
    "SandboxFailure",
    "UnsafeContent",
    "run_sandboxed",
    "scratch_dir",
]
