"""Query endpoint request/response schemas."""

from pydantic import BaseModel

from app.schemas.vision import BeaconStep


class QueryResponse(BaseModel):
    """Response returned from POST /query.

    Attributes:
        transcript: Groq Whisper transcript used for this query.
        steps: Ordered list of guided instruction steps for the overlay panel.
               Each may carry a ``target`` box (normalised 0–1 to the
               screenshot) for the overlay beacon — see ``StepTarget``.
        summary: Warm spoken summary of what the AI understood and will guide
                 the user through. Played via TTS on the client.
        speech_b64: Base64-encoded WAV of the spoken summary.
                    Source depends on language: Sahara TTS for African languages
                    (en-pidgin, yo, ha, ig), Groq Orpheus for en/fr.
                    Empty string if TTS is disabled or all providers failed.
        conversation_id: UUID of the user's conversation. Carry this in the
                         desktop UI to display or reference chat history later.
    """

    transcript: str
    steps: list[BeaconStep]
    summary: str
    speech_b64: str
    conversation_id: str
