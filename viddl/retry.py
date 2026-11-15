"""How a temporary failure gets another go, and how the wait is said.

A rate limit, a server having a bad minute, a connection that dropped: one
shape of failure, answered in one place. The download job asks this policy what
to do — wait and how long, or stop — so the number of tries, the doubling wait
and the sentence shown while waiting cannot drift apart, and none of them needs
a subprocess to be tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from .engine import ToolError

#: How many times one download is tried when the failure is the kind a second
#: attempt may fix. Three goes: a rate limit or a dropped connection is usually
#: gone by then, and a fourth would delay the bad news for nothing.
DEFAULT_ATTEMPTS = 3

#: The first wait after a temporary failure; each further wait doubles it, so a
#: site having a bad minute is not hammered by the thing that is failing.
RETRY_BASE_SECONDS = 4.0

#: A wait is slept in slices this long, so a cancel is felt at once instead of
#: whenever the wait happens to end. The user pressed stop; they mean now.
SLICE_SECONDS = 0.1


def seconds_text(seconds: float) -> str:
    """"4s", not "4.0s" — this is read in passing, not studied."""
    if abs(seconds - round(seconds)) < 1e-9:
        return f"{round(seconds)}s"
    return f"{seconds:.1f}s"


@dataclass
class RetryPolicy:
    """The attempts one download gets, and what it is told between them."""

    attempts: int = DEFAULT_ATTEMPTS
    delay: float = RETRY_BASE_SECONDS

    def __post_init__(self) -> None:
        # A caller asking for zero attempts is asking for one, and a negative
        # wait is asking for none. Neither is worth failing a download over.
        self.attempts = max(1, int(self.attempts))
        self.delay = max(0.0, float(self.delay))

    def wait_after(self, failed_attempt: int) -> float:
        """How long to wait after ``failed_attempt`` failed: the base, then doubled."""
        return self.delay * (2 ** (failed_attempt - 1))

    def will_retry(self, failure: ToolError, attempt: int) -> bool:
        """Whether this failure is worth another go, and there is one left.

        Only ``temporary`` failures qualify: a private video stays private, and
        trying again would keep the user waiting for the same answer.
        """
        return bool(failure.temporary) and attempt < self.attempts

    def label(self, attempt: int, wait: float) -> str:
        """The sentence shown while waiting, naming the wait and the try."""
        return (
            f"Retrying in {seconds_text(wait)} "
            f"(attempt {attempt + 1} of {self.attempts})…"
        )

    def exhausted(self, failure: ToolError, attempt: int) -> ToolError:
        """The error to show once trying again is no longer an option.

        Saying how many times Viddl tried matters: "the site is busy" reads
        differently when the user knows it was already asked three times, and
        the next sensible step is to wait rather than press the button again.
        """
        if attempt <= 1:
            # Tried once: there was no wait to explain, and "tried 1 times"
            # would be a strange thing to read.
            return failure
        note = (
            f"Viddl tried {attempt} times, waiting longer between each attempt, and "
            "the site answered the same way every time."
        )
        hint = f"{failure.hint}\n{note}" if failure.hint else note
        return ToolError(
            failure.message, hint, raw=failure.raw, temporary=failure.temporary
        )
