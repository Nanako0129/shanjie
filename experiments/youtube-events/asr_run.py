"""Contract docs/contracts/youtube-events.md section 3 (C): transcribe 10-minute WAV segments with each ASR model. Runs on a Colab GPU VM.

Usage:  python3 asr_run.py WAV_DIR OUT_DIR [--models tea,breeze,whisper]
  WAV_DIR/<video id>.wav   16 kHz mono, one 10-minute segment per video
  OUT_DIR/<model>/<video id>.json   {"text", "segments": [{"start","end","text"}] (may be empty), "wall_s", "audio_s"}
Existing outputs are skipped (resumable). Prints counts and timings only. Model load time is not in wall_s, and neither is the first-call warm-up: after loading, each model transcribes 1 s of silence once before the timed files.

Requirements (pip lines for the VM; nothing is installed by this repo):
  pip install -U transformers accelerate torch soundfile
  pip install -U qwen-asr          # tea only
  apt-get install -y ffmpeg        # transformers pipeline reads the WAV path through ffmpeg (usually present on Colab)

Each model is one function that loads the model and returns transcribe(path) -> (text, segments).
The TEA call follows the qwen-asr API as documented (Qwen3ASRModel.transcribe, language="Chinese"); if the model card
differs, fix tea() only.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import wave


TEA_CHUNK_S = 30   # seconds per TEA call (the model card is for short clips; see run() below)


def tea():
    import torch
    from qwen_asr import Qwen3ASRModel
    m = Qwen3ASRModel.from_pretrained("JacobLinCool/TEA-ASR-1.1", dtype=torch.bfloat16, device_map="cuda:0")

    def run(path):
        # One call on a 10-minute file returned only the first few hundred characters (2026-10-25 pilot: 6,460 Han for 30,082
        # in the references), so the audio goes in TEA_CHUNK_S pieces, each transcribed on its own, with times per piece.
        import os, tempfile
        import soundfile as sf
        audio, rate = sf.read(path)
        step, texts, segs = TEA_CHUNK_S * rate, [], []
        with tempfile.TemporaryDirectory() as d:
            for i in range(0, len(audio), step):
                piece = os.path.join(d, "piece.wav")
                sf.write(piece, audio[i:i + step], rate)
                t = m.transcribe(audio=piece, language="Chinese")[0].text
                texts.append(t)
                segs.append({"start": i / rate, "end": min(i + step, len(audio)) / rate, "text": t})
        return "".join(texts), segs
    return run


def _whisper_like(repo):
    import torch
    from transformers import pipeline
    p = pipeline("automatic-speech-recognition", model=repo, torch_dtype=torch.float16, device="cuda:0", chunk_length_s=30)

    def run(path):
        r = p(path, batch_size=8, return_timestamps=True, generate_kwargs={"language": "zh", "task": "transcribe"})
        segs = [{"start": c["timestamp"][0], "end": c["timestamp"][1], "text": c["text"]} for c in r.get("chunks", [])]
        return r["text"], segs
    return run


def breeze():
    return _whisper_like("MediaTek-Research/Breeze-ASR-25")


def whisper():
    return _whisper_like("openai/whisper-large-v3")


MODELS = {"tea": tea, "breeze": breeze, "whisper": whisper}


def audio_seconds(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--models", default=",".join(MODELS))
    a = ap.parse_args()
    names = [m for m in a.models.split(",") if m]
    for m in names:
        if m not in MODELS:
            sys.exit(f"unknown model {m}; known: {','.join(MODELS)}")
    ids = sorted(f[:-4] for f in os.listdir(a.wav_dir) if f.endswith(".wav"))
    for m in names:
        os.makedirs(os.path.join(a.out_dir, m), exist_ok=True)
        todo = [i for i in ids if not os.path.exists(os.path.join(a.out_dir, m, i + ".json"))]
        print(f"{m}: {len(ids) - len(todo)} done, {len(todo)} to do", flush=True)
        if not todo:
            continue
        transcribe = MODELS[m]()
        with tempfile.TemporaryDirectory() as tmp:     # warm-up: the first call pays CUDA/kernel setup, which must not land in the first file's wall_s
            silence = os.path.join(tmp, "silence.wav")
            with wave.open(silence, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(32000))
            transcribe(silence)
        for i in todo:
            path = os.path.join(a.wav_dir, i + ".wav")
            t0 = time.time()
            text, segs = transcribe(path)
            wall = time.time() - t0
            dur = audio_seconds(path)
            out = os.path.join(a.out_dir, m, i + ".json")
            with open(out + ".tmp", "w", encoding="utf-8") as f:   # write then rename: a killed run never leaves a half file that counts as done
                json.dump({"text": text, "segments": segs, "wall_s": wall, "audio_s": dur}, f, ensure_ascii=False)
            os.replace(out + ".tmp", out)
            print(f"{m} {i}: {wall:.1f}s for {dur:.0f}s audio (rtf {wall / dur:.3f})", flush=True)


if __name__ == "__main__":
    main()
