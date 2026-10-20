"""
transcribe_audio.py — turn call recordings into transcript chunks (JSONL).

Runs Whisper on each audio file and writes one JSONL row per chunk, in the same
format chunk_text.py produces, so ingest_pgvector.py can load it unchanged:

    python transcribe_audio.py calls_dir/ --out data/out/audio_chunks.jsonl
    python ingest_pgvector.py data/out/audio_chunks.jsonl

Rows get modality = "audio". For audio, the `bbox` field holds the position in
the recording instead of a page rectangle: {"start": seconds, "end": seconds}.
That is what the citation step uses to point at the right moment of a call.

Setup:
    pip install faster-whisper
"""

import argparse
import json
from pathlib import Path

MAX_CHARS = 1200  # a chunk holds consecutive spoken segments up to this size
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}


def collect_audio_files(paths):
    """Expand a mix of audio files and folders into a list of audio files."""
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(f for f in path.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS)
        elif path.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(path)
    return list(dict.fromkeys(files))  # remove duplicates, keep order


def transcribe(model, audio_path):
    """Run Whisper; return a list of {"start", "end", "text"} spoken segments."""
    # model.transcribe is lazy: the actual work happens as we loop over `segments`
    segments, _ = model.transcribe(str(audio_path), language="en", beam_size=5)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments if s.text.strip()]


def group_segments(segments):
    """Group consecutive segments until a group would exceed MAX_CHARS."""
    groups, current, size = [], [], 0
    for segment in segments:
        if current and size + len(segment["text"]) > MAX_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(segment)
        size += len(segment["text"]) + 1
    if current:
        groups.append(current)
    return groups


def build_records(audio_path, segments):
    records = []
    for n, group in enumerate(group_segments(segments)):
        records.append({
            "chunk_id": f"{audio_path.stem}_c{n}",
            "modality": "audio",
            "content_type": "transcript",
            "source": audio_path.name,
            "page": None,  # audio has no pages
            "bbox": {"start": round(group[0]["start"], 1), "end": round(group[-1]["end"], 1)},
            "text": " ".join(s["text"] for s in group),
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="audio files and/or folders of audio")
    parser.add_argument("--out", type=Path, default=Path("data/out/audio_chunks.jsonl"))
    parser.add_argument("--model", default="small.en",
                        help="Whisper size: base.en is faster, medium.en is more accurate")
    args = parser.parse_args()

    audio_files = collect_audio_files(args.inputs)
    if not audio_files:
        parser.error("No audio files found in the given inputs")

    # imported here so the helper functions above can be used without it installed
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device="cpu", compute_type="auto")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(args.out, "w") as f:
        for audio_path in audio_files:
            records = build_records(audio_path, transcribe(model, audio_path))
            for record in records:
                f.write(json.dumps(record) + "\n")
            total += len(records)
            print(f"{audio_path.name}: {len(records)} chunks")
    print(f"Total: {total} chunks -> {args.out}")


if __name__ == "__main__":
    main()
