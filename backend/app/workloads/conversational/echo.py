"""Mia's own voice, heard back on the line, is not the caller speaking (Mia spec v3 #12, #139).

In the live test the echo of the greeting came back through the caller's microphone, was
transcribed, and answered as if the caller had said it. A transcript is an echo when it arrives
while the agent is speaking, or just after, and is made almost entirely of words the agent has
just said. Such a turn is dropped before the model sees it and is not stored as the caller's.

Kept narrow on purpose: a short answer ("Ja", "Montag passt") is never an echo, so a caller who
talks over the agent or repeats one of its words is still heard.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Echo only exists while the voice plays and for the moment the recogniser needs to close the
# turn after it (Flux's end-of-turn timeout is 2.5 s).
ECHO_TAIL_SECONDS = 4.0
# What the agent said this recently is what an echo can repeat.
AGENT_MEMORY_SECONDS = 30.0
# Fewer words than this is an answer, never an echo.
MIN_ECHO_WORDS = 3
# The share of the transcript's words the agent must just have said.
ECHO_SHARE = 0.8


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", (text or "").casefold())


class AgentSpeech:
    """What the agent has just said, and whether it is still saying it. One per call."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._said: deque[tuple[float, str]] = deque()
        self._speaking = False
        self._stopped_at: float | None = None
        self._decided: dict[int, bool] = {}

    def started(self) -> None:
        self._speaking = True

    def stopped(self) -> None:
        self._speaking = False
        self._stopped_at = self._clock()

    def said(self, text: str) -> None:
        now = self._clock()
        self._said.extend((now, w) for w in _words(text))
        while self._said and now - self._said[0][0] > AGENT_MEMORY_SECONDS:
            self._said.popleft()

    def is_echo(self, text: str) -> bool:
        heard = _words(text)
        if len(heard) < MIN_ECHO_WORDS:
            return False
        now = self._clock()
        if not self._speaking and (
            self._stopped_at is None or now - self._stopped_at > ECHO_TAIL_SECONDS
        ):
            return False
        recent = {w for at, w in self._said if now - at <= AGENT_MEMORY_SECONDS}
        return sum(w in recent for w in heard) / len(heard) >= ECHO_SHARE

    def echo_frame(self, frame: TranscriptionFrame) -> bool:
        """`is_echo`, decided once per frame: the guard and the transcript observer both ask, and
        must not disagree because a moment passed between them."""
        if frame.id not in self._decided:
            self._decided[frame.id] = self.is_echo(frame.text)
        return self._decided[frame.id]


class AgentSpeechTap(FrameProcessor):
    """Sits after the output transport: sees what was actually played, and when."""

    def __init__(self, speech: AgentSpeech) -> None:
        super().__init__()
        self._speech = speech

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speech.started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speech.stopped()
        elif isinstance(frame, TTSTextFrame):
            self._speech.said(frame.text)
        await self.push_frame(frame, direction)


class EchoGuard(FrameProcessor):
    """Sits after the speech recogniser: drops a transcript that is the agent's own voice."""

    def __init__(self, speech: AgentSpeech) -> None:
        super().__init__()
        self._speech = speech

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and self._speech.echo_frame(frame):
            return
        if isinstance(frame, InterimTranscriptionFrame) and self._speech.is_echo(frame.text):
            return
        await self.push_frame(frame, direction)
