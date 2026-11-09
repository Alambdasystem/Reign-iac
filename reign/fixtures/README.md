# Voice acceptance fixture

`voice-status.wav` is synthesized test speech, not a recording of a person.
It was generated locally with FFmpeg's CMU Flite `slt` voice at 24 kHz,
mono, signed 16-bit PCM. The exact original phrase is:

> Please use workspace status and tell me whether the core temporal and computer services are ready.

The release probe sends this file to the configured Realtime provider and
requires an actual delegated LangGraph tool result and non-silent returned
audio. Synthetic input does not verify a physical phone microphone or speaker.
