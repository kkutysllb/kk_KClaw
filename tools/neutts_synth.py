#!/usr/bin/env python3
"""独立的 NeuTTS 合成辅助工具。

由 tts_tool.py 通过子进程调用，以将 TTS 模型（约 500MB）
保持在单独的进程中，合成后退出——不会占用内存。

用法：
    python -m tools.neutts_synth --text "Hello" --out output.wav \
        --ref-audio samples/jo.wav --ref-text samples/jo.txt

依赖：python -m pip install -U neutts[all]
系统：  apt install espeak-ng  （或 brew install espeak-ng）
"""

import argparse
import struct
import sys
from pathlib import Path


def _write_wav(path: str, samples, sample_rate: int = 24000) -> None:
    """从 float32 样本写入 WAV 文件（不依赖 soundfile）。"""
    import numpy as np

    if not isinstance(samples, np.ndarray):
        samples = np.array(samples, dtype=np.float32)
    samples = samples.flatten()

    # 限制范围并转换为 int16
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype(np.int16)

    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    data_size = len(pcm) * (bits_per_sample // 8)

    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, num_channels, sample_rate,
                            byte_rate, block_align, bits_per_sample))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(pcm.tobytes())


def main():
    parser = argparse.ArgumentParser(description="NeuTTS synthesis helper")
    parser.add_argument("--text", required=True, help="Text to synthesize")
    parser.add_argument("--out", required=True, help="Output WAV path")
    parser.add_argument("--ref-audio", required=True, help="Reference voice audio path")
    parser.add_argument("--ref-text", required=True, help="Reference voice transcript path")
    parser.add_argument("--model", default="neuphonic/neutts-air-q4-gguf",
                        help="HuggingFace backbone model repo")
    parser.add_argument("--device", default="cpu", help="Device (cpu/cuda/mps)")
    args = parser.parse_args()

    # 验证输入
    ref_audio = Path(args.ref_audio).expanduser()
    ref_text_path = Path(args.ref_text).expanduser()
    if not ref_audio.exists():
        print(f"Error: reference audio not found: {ref_audio}", file=sys.stderr)
        sys.exit(1)
    if not ref_text_path.exists():
        print(f"Error: reference text not found: {ref_text_path}", file=sys.stderr)
        sys.exit(1)

    ref_text = ref_text_path.read_text(encoding="utf-8").strip()

    # 导入并运行 NeuTTS
    try:
        from neutts import NeuTTS
    except ImportError:
        print("Error: neutts not installed. Run: python -m pip install -U neutts[all]", file=sys.stderr)
        sys.exit(1)

    tts = NeuTTS(
        backbone_repo=args.model,
        backbone_device=args.device,
        codec_repo="neuphonic/neucodec",
        codec_device=args.device,
    )
    ref_codes = tts.encode_reference(str(ref_audio))
    wav = tts.infer(args.text, ref_codes, ref_text)

    # 写入输出
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import soundfile as sf
        sf.write(str(out_path), wav, 24000)
    except ImportError:
        _write_wav(str(out_path), wav, 24000)

    print(f"OK: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
