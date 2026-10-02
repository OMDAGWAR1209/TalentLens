"""Voice-based screening (Milestone 4).

The brief's reference wires SpeechRecognition and pyttsx3 together in one function that
needs a working microphone to do anything, so it cannot be tested and cannot run on a
server. This module splits the problem in three:

    VoiceScreen.prompts()      what the AI interviewer says, built from the M3 interview kit
    assess_transcript()        pure function: transcript text -> scored screening result
    listen() / speak()         thin hardware adapters, imported lazily, optional

Everything that matters for a hiring decision lives in `assess_transcript`, which takes
plain text. That means the scoring is identical whether the words came from a microphone,
the browser's Web Speech API, a phone-call transcript, or a typed answer — and it means
the test suite can exercise it without audio hardware.

Speech-to-text accuracy is not measured here. A transcription error becomes a scoring
error, so a production deployment should log the raw transcript next to the score and let
a reviewer listen back. That limitation is stated, not hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .interview import generate_interview_kit, score_answer

# speech markers that show up in spoken answers and almost never in typed ones
SPOKEN_FILLER = re.compile(r"\b(um+|uh+|erm|hmm+|like|you know|kind of|sort of|basically|actually)\b", re.I)
HEDGES = re.compile(r"\b(i think|i guess|maybe|probably|not sure|i don'?t know)\b", re.I)
WORDS_PER_MINUTE_IDEAL = (120, 170)

INTRO_PROMPT = ("Hello, I'm the screening assistant. Please introduce yourself and describe "
                "the experience most relevant to this role.")
CLOSING_PROMPT = "Thank you. Your answers have been recorded for the hiring team."


@dataclass
class VoiceScreen:
    """A short spoken screen: an introduction plus the top questions from the M3 kit."""

    candidate: Dict[str, Any]
    job: Dict[str, Any]
    max_questions: int = 3
    responses: List[Dict[str, Any]] = dc_field(default_factory=list)

    def prompts(self) -> List[Dict[str, Any]]:
        kit = generate_interview_kit(self.candidate, self.job, total=max(4, self.max_questions + 1))
        # a voice screen favours behavioural and technical questions over long scenarios
        chosen = [q for q in kit["questions"] if q["category"] != "scenario"][: self.max_questions]
        intro = {"question_id": "intro", "question_text": INTRO_PROMPT, "category": "behavioural",
                 "difficulty_level": "easy", "skill": None,
                 "rationale": "Opening question — tests clarity and self-presentation.",
                 "expected_points": ["clear summary of background", "relevance to the role",
                                     "a concrete example"],
                 "estimated_duration": "1–2 min"}
        return [intro] + chosen

    def record(self, prompt: Dict[str, Any], transcript: str,
               duration_seconds: Optional[float] = None) -> Dict[str, Any]:
        result = assess_transcript(transcript, prompt, duration_seconds)
        self.responses.append(result)
        return result

    def summary(self) -> Dict[str, Any]:
        return summarise_screen(self.candidate.get("name"), self.job.get("title"), self.responses)


def assess_transcript(transcript: str, prompt: Dict[str, Any],
                      duration_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Score one spoken answer. Reuses the M3 rubric, then adds delivery signals.

    Content carries 80% of the result and delivery 20%. Delivery is deliberately the minor
    part: penalising accent, pace or nerves heavily would make a voice screen a test of
    presentation rather than ability, which is both unfair and a known source of bias.
    """
    text = (transcript or "").strip()
    content = score_answer(text, prompt)
    words = len(text.split()) if text else 0

    fillers = len(SPOKEN_FILLER.findall(text))
    hedges = len(HEDGES.findall(text))
    filler_rate = fillers / words if words else 0.0

    wpm = None
    pace_note = "duration not captured"
    pace_score = 1.0
    if duration_seconds and duration_seconds > 0 and words:
        wpm = round(words / (duration_seconds / 60), 1)
        lo, hi = WORDS_PER_MINUTE_IDEAL
        if wpm < lo:
            pace_score = max(0.6, wpm / lo)
            pace_note = f"{wpm} wpm — slower than typical"
        elif wpm > hi:
            pace_score = max(0.6, hi / wpm)
            pace_note = f"{wpm} wpm — faster than typical"
        else:
            pace_note = f"{wpm} wpm — comfortable pace"

    fluency_score = max(0.0, 1.0 - filler_rate * 6)
    confidence_score = max(0.0, 1.0 - hedges * 0.15)
    delivery = (fluency_score * 0.5 + confidence_score * 0.3 + pace_score * 0.2) * 100

    if not words:
        overall = 0.0
    else:
        overall = round(content["score"] * 0.8 + delivery * 0.2, 1)

    notes = [content["feedback"]] if content.get("feedback") else []
    if filler_rate > 0.08:
        notes.append("frequent filler words — may be unprepared or nervous; do not over-weight")
    if hedges >= 3:
        notes.append("heavy hedging — probe whether they actually did the work")

    return {
        "question_id": prompt.get("question_id"),
        "question_text": prompt.get("question_text"),
        "category": prompt.get("category"),
        "transcript": text,
        "words": words,
        "content_score": content["score"],
        "delivery_score": round(delivery, 1),
        "score": overall,
        "verdict": ("strong" if overall >= 72 else "adequate" if overall >= 55
                    else "weak" if overall >= 35 else "poor"),
        "signals": {"filler_words": fillers, "filler_rate": round(filler_rate, 3),
                    "hedges": hedges, "words_per_minute": wpm, "pace": pace_note},
        "feedback": "; ".join(notes) or "clear and specific",
    }


def summarise_screen(candidate: Optional[str], job_title: Optional[str],
                     responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not responses:
        return {"candidate": candidate, "job_title": job_title, "answered": 0, "score": None,
                "recommendation": "no responses recorded", "assessment": ""}
    score = round(sum(r["score"] for r in responses) / len(responses), 1)
    content = round(sum(r["content_score"] for r in responses) / len(responses), 1)
    delivery = round(sum(r["delivery_score"] for r in responses) / len(responses), 1)
    rec = ("advance to technical interview" if score >= 65
           else "second screen recommended" if score >= 50
           else "do not advance on this screen")
    strongest = max(responses, key=lambda r: r["score"])
    weakest = min(responses, key=lambda r: r["score"])
    assessment = (
        f"{candidate or 'The candidate'} averaged {score}/100 across {len(responses)} spoken "
        f"answers (content {content}, delivery {delivery}). Strongest on "
        f"\"{(strongest['question_text'] or '')[:60]}\"; weakest on "
        f"\"{(weakest['question_text'] or '')[:60]}\". Recommendation: {rec}.")
    return {"candidate": candidate, "job_title": job_title, "answered": len(responses),
            "score": score, "content_score": content, "delivery_score": delivery,
            "recommendation": rec, "assessment": assessment,
            "screened_at": datetime.now(timezone.utc).isoformat(), "responses": responses}


# --------------------------------------------------------------------------- #
# hardware adapters — optional, imported only when called
# --------------------------------------------------------------------------- #
def speak(text: str) -> None:
    """Read a prompt aloud with pyttsx3. No-op with a message if unavailable."""
    try:
        import pyttsx3  # noqa: PLC0415
    except ImportError:
        print(f"[tts unavailable] {text}")
        return
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


def listen(timeout: float = 8.0, phrase_limit: float = 90.0) -> Dict[str, Any]:
    """Capture one answer from the default microphone and transcribe it.

    Returns {"transcript", "duration_seconds", "error"}. Never raises for a hardware or
    recognition failure — a screen should record that the answer was not captured rather
    than crash the session.
    """
    try:
        import speech_recognition as sr  # noqa: PLC0415
    except ImportError:
        return {"transcript": "", "duration_seconds": None,
                "error": "SpeechRecognition not installed — pip install SpeechRecognition pyaudio"}
    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            started = datetime.now(timezone.utc)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            seconds = (datetime.now(timezone.utc) - started).total_seconds()
        return {"transcript": recognizer.recognize_google(audio), "duration_seconds": seconds,
                "error": None}
    except Exception as exc:  # noqa: BLE001 — any capture failure is recorded, not raised
        return {"transcript": "", "duration_seconds": None, "error": f"{type(exc).__name__}: {exc}"}


def run_voice_screening(candidate: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """Interactive CLI screen — the brief's `voice_screening()`, built on the testable core."""
    screen = VoiceScreen(candidate, job)
    for prompt in screen.prompts():
        speak(prompt["question_text"])
        print(f"\nAI: {prompt['question_text']}\nListening...")
        heard = listen()
        if heard["error"]:
            print(f"  (not captured: {heard['error']})")
        else:
            print(f"Candidate: {heard['transcript']}")
        screen.record(prompt, heard["transcript"], heard["duration_seconds"])
    speak(CLOSING_PROMPT)
    return screen.summary()
