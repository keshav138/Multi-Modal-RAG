"""
Generate synthetic support-call audio from transcripts.json using Piper TTS.

Usage:
    python scripts/audio-creation/audio-conversion.py

Outputs one .wav per call into data/audio_data/ (repo-root relative), so the
folder layout stays consistent no matter which directory the script is run
from.
"""

import json
import subprocess
from pathlib import Path

TRANSCRIPTS_FILE = Path(__file__).with_name("transcripts.json")
AUDIO_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "audio_data"
PIPER_BINARY = r"C:\piper\piper.exe"  # Adjust to your local piper path
PIPER_MODEL = r"C:\piper\en_US-amy-medium.onnx"


def generate_audio():
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(TRANSCRIPTS_FILE, "r") as f:
        calls = json.load(f)

    for item in calls:
        call_id = item["call_id"]
        text_content = item["transcript"]
        output_wav = AUDIO_OUTPUT_DIR / f"{call_id}.wav"

        print(f"Synthesizing {call_id}.wav...")

        # Pipe the text directly into the Piper CLI process
        process = subprocess.Popen(
            [
                PIPER_BINARY,
                "--model", PIPER_MODEL,
                "--output_file", str(output_wav)
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(input=text_content)

        if process.returncode != 0:
            print(f"Error generating {call_id}: {stderr}")
        else:
            print(f"Saved {output_wav}")


if __name__ == "__main__":
    generate_audio()
