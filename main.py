from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import File, Plain, Record
from astrbot.core.star.star_tools import StarTools

SUPPORTED_PLATFORMS = {"aiocqhttp", "qq_official", "qq_official_webhook"}
SEND_MODES = {"auto", "voice", "file"}
VOICE_MAX_SECONDS = 60
HARD_MAX_SECONDS = 180
DEFAULT_DURATION = 20
DEFAULT_SAMPLE_RATE = 44100


@dataclass
class MusicSpec:
    mood: str = "playful"
    energy: float = 0.55
    brightness: float = 0.6
    density: float = 0.55
    bpm: int = 110
    key: str = "C minor"
    instruments: list[str] = field(default_factory=lambda: ["8bit_lead", "synth_bass", "soft_pad", "lofi_drums"])
    effects: list[str] = field(default_factory=lambda: ["soft_clip", "small_delay"])
    duration: int = DEFAULT_DURATION
    loopable: bool = False
    send_mode: str = "auto"


@dataclass
class RenderPlan:
    tracks: list[dict[str, Any]] = field(default_factory=list)
    drums: dict[str, Any] = field(default_factory=dict)
    bass: dict[str, Any] = field(default_factory=dict)
    chords: dict[str, Any] = field(default_factory=dict)
    melody: dict[str, Any] = field(default_factory=dict)
    texture: dict[str, Any] = field(default_factory=dict)
    effects: dict[str, Any] = field(default_factory=dict)
    master: dict[str, Any] = field(default_factory=dict)


class RateLimiter:
    def __init__(self, cooldown_sec: int = 30) -> None:
        self.cooldown_sec = cooldown_sec
        self._last_by_key: dict[str, float] = {}

    def check(self, key: str) -> int:
        now = time.monotonic()
        last = self._last_by_key.get(key, 0.0)
        wait = int(math.ceil(self.cooldown_sec - (now - last)))
        if wait > 0:
            return wait
        self._last_by_key[key] = now
        return 0


def _get_data_dir() -> Path:
    try:
        path = Path(StarTools.get_data_dir())
    except Exception:
        path = Path(__file__).resolve().parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cfg_get(config: AstrBotConfig | None, key: str, default: Any) -> Any:
    if config is None:
        return default
    try:
        return config.get(key, default)
    except Exception:
        try:
            return config[key]
        except Exception:
            return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return int(max(low, min(high, parsed)))


def _normalize_send_mode(value: Any, default: str = "auto") -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in SEND_MODES else default


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _prompt_overrides(prompt: str) -> dict[str, Any]:
    lower = prompt.lower()
    duration = None
    match = re.search(r"(\d{1,3})\s*(?:秒|s|sec|second|seconds)", lower)
    if match:
        duration = int(match.group(1))
    send_mode = None
    if "文件" in prompt or "file" in lower:
        send_mode = "file"
    elif "语音" in prompt or "voice" in lower:
        send_mode = "voice"
    loopable = None
    if "可循环" in prompt or "循环" in prompt or "loop" in lower:
        loopable = True
    if "不要循环" in prompt or "不循环" in prompt:
        loopable = False
    return {"duration": duration, "send_mode": send_mode, "loopable": loopable}


def _fallback_spec(prompt: str, default_duration: int, max_duration: int, default_send_mode: str, loopable_default: bool) -> MusicSpec:
    lower = prompt.lower()
    mood = "playful"
    energy = 0.55
    brightness = 0.6
    density = 0.55
    bpm = 110
    key = "C minor"
    instruments = ["8bit_lead", "synth_bass", "soft_pad", "lofi_drums"]
    effects = ["soft_clip", "small_delay"]

    if any(word in lower or word in prompt for word in ["ambient", "氛围", "星空", "宇宙", "空灵"]):
        mood, energy, brightness, density, bpm = "ambient", 0.32, 0.72, 0.35, 76
        instruments = ["soft_pad", "sine_bell", "sub_bass", "noise_texture"]
        effects = ["wide_reverb", "gentle_delay"]
        key = "D minor"
    elif any(word in lower or word in prompt for word in ["lofi", "雨", "咖啡", "chill", "放松"]):
        mood, energy, brightness, density, bpm = "lofi", 0.42, 0.42, 0.52, 84
        instruments = ["warm_keys", "synth_bass", "lofi_drums", "vinyl_noise"]
        effects = ["soft_clip", "small_delay", "lowpass"]
        key = "A minor"
    elif any(word in lower or word in prompt for word in ["8bit", "chiptune", "像素", "游戏", "电子"]):
        mood, energy, brightness, density, bpm = "8bit", 0.72, 0.86, 0.66, 132
        instruments = ["8bit_lead", "pulse_bass", "arp", "chip_drums"]
        effects = ["soft_clip"]
        key = "C minor"
    elif any(word in lower or word in prompt for word in ["dark", "黑暗", "紧张", "赛博", "cyber"]):
        mood, energy, brightness, density, bpm = "dark electronic", 0.68, 0.35, 0.64, 124
        instruments = ["saw_lead", "synth_bass", "dark_pad", "electro_drums"]
        effects = ["soft_clip", "small_delay"]
        key = "F minor"

    overrides = _prompt_overrides(prompt)
    duration = _safe_int(overrides["duration"], default_duration, 5, max_duration) if overrides["duration"] else default_duration
    send_mode = _normalize_send_mode(overrides["send_mode"], default_send_mode)
    loopable = loopable_default if overrides["loopable"] is None else bool(overrides["loopable"])

    return MusicSpec(
        mood=mood,
        energy=energy,
        brightness=brightness,
        density=density,
        bpm=bpm,
        key=key,
        instruments=instruments,
        effects=effects,
        duration=duration,
        loopable=loopable,
        send_mode=send_mode,
    )


def _spec_from_dict(data: dict[str, Any], fallback: MusicSpec, max_duration: int) -> MusicSpec:
    duration = _safe_int(data.get("duration", fallback.duration), fallback.duration, 5, max_duration)
    bpm = _safe_int(data.get("bpm", fallback.bpm), fallback.bpm, 55, 180)
    instruments = data.get("instruments", fallback.instruments)
    effects = data.get("effects", fallback.effects)
    if not isinstance(instruments, list):
        instruments = fallback.instruments
    if not isinstance(effects, list):
        effects = fallback.effects
    return MusicSpec(
        mood=str(data.get("mood", fallback.mood))[:80],
        energy=_clamp(float(data.get("energy", fallback.energy)), 0.0, 1.0),
        brightness=_clamp(float(data.get("brightness", fallback.brightness)), 0.0, 1.0),
        density=_clamp(float(data.get("density", fallback.density)), 0.0, 1.0),
        bpm=bpm,
        key=str(data.get("key", fallback.key))[:40],
        instruments=[str(item)[:40] for item in instruments[:8]],
        effects=[str(item)[:40] for item in effects[:8]],
        duration=duration,
        loopable=bool(data.get("loopable", fallback.loopable)),
        send_mode=_normalize_send_mode(data.get("send_mode", fallback.send_mode), fallback.send_mode),
    )


def _default_plan(spec: MusicSpec) -> RenderPlan:
    return RenderPlan(
        tracks=[{"name": name, "gain": 1.0} for name in spec.instruments],
        drums={"pattern": "four_on_floor" if spec.energy > 0.6 else "half_time", "density": spec.density},
        bass={"pattern": "root_octave" if spec.energy > 0.55 else "warm_roots"},
        chords={"progression": ["i", "VI", "III", "VII"], "voicing": "soft" if spec.brightness < 0.55 else "open"},
        melody={"shape": "periodic", "activity": spec.density, "register": "high" if spec.brightness > 0.65 else "mid"},
        texture={"noise": "vinyl" if "lofi" in spec.mood.lower() else "air"},
        effects={"delay": "small_delay" in spec.effects or "gentle_delay" in spec.effects, "reverb": "wide_reverb" in spec.effects},
        master={"target_peak": 0.92, "soft_clip": True},
    )


def _plan_from_dict(data: dict[str, Any], fallback: RenderPlan) -> RenderPlan:
    return RenderPlan(
        tracks=data.get("tracks") if isinstance(data.get("tracks"), list) else fallback.tracks,
        drums=data.get("drums") if isinstance(data.get("drums"), dict) else fallback.drums,
        bass=data.get("bass") if isinstance(data.get("bass"), dict) else fallback.bass,
        chords=data.get("chords") if isinstance(data.get("chords"), dict) else fallback.chords,
        melody=data.get("melody") if isinstance(data.get("melody"), dict) else fallback.melody,
        texture=data.get("texture") if isinstance(data.get("texture"), dict) else fallback.texture,
        effects=data.get("effects") if isinstance(data.get("effects"), dict) else fallback.effects,
        master=data.get("master") if isinstance(data.get("master"), dict) else fallback.master,
    )


class PythonMusicRenderer:
    NOTE_OFFSETS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    MINOR = [0, 2, 3, 5, 7, 8, 10]
    MAJOR = [0, 2, 4, 5, 7, 9, 11]
    PROGRESSION_MINOR = [0, 5, 2, 6]

    def render(self, spec: MusicSpec, plan: RenderPlan, out_path: Path, sample_rate: int) -> None:
        sample_rate = _safe_int(sample_rate, DEFAULT_SAMPLE_RATE, 16000, 48000)
        duration = max(5, min(HARD_MAX_SECONDS, int(spec.duration)))
        bpm = max(55, min(180, int(spec.bpm)))
        beat = 60.0 / bpm
        bar = beat * 4
        if spec.loopable:
            bars = max(2, round(duration / bar))
            duration = int(round(bars * bar))

        total_samples = int(duration * sample_rate)
        audio = np.zeros(total_samples, dtype=np.float32)
        root, scale = self._scale(spec.key)
        rng = random.Random(self._seed(spec))

        audio += self._render_drums(total_samples, sample_rate, beat, spec, plan, rng)
        audio += self._render_bass(total_samples, sample_rate, beat, root, scale, spec, rng)
        audio += self._render_chords(total_samples, sample_rate, bar, root, scale, spec, rng)
        audio += self._render_melody(total_samples, sample_rate, beat, root, scale, spec, rng)
        audio += self._render_texture(total_samples, sample_rate, spec, rng)

        audio = self._apply_effects(audio, sample_rate, spec, plan)
        if spec.loopable:
            audio = self._make_loopable(audio, sample_rate)
        audio = self._master(audio, plan)
        self._write_wav(out_path, audio, sample_rate)

    def _seed(self, spec: MusicSpec) -> int:
        basis = f"{spec.mood}|{spec.key}|{spec.bpm}|{spec.instruments}|{spec.effects}"
        return sum(ord(ch) * (idx + 1) for idx, ch in enumerate(basis)) % (2**32)

    def _scale(self, key: str) -> tuple[int, list[int]]:
        parts = key.replace("minor", " minor").replace("major", " major").split()
        note = parts[0] if parts else "C"
        root = 60 + self.NOTE_OFFSETS.get(note, 0)
        scale = self.MINOR if "minor" in key.lower() or "小" in key else self.MAJOR
        return root, scale

    def _freq(self, midi: int) -> float:
        return 440.0 * (2.0 ** ((midi - 69) / 12.0))

    def _env(self, n: int, attack: float, release: float, sample_rate: int) -> np.ndarray:
        env = np.ones(n, dtype=np.float32)
        attack_n = min(n, max(1, int(attack * sample_rate)))
        release_n = min(n, max(1, int(release * sample_rate)))
        env[:attack_n] *= np.linspace(0.0, 1.0, attack_n, dtype=np.float32)
        env[-release_n:] *= np.linspace(1.0, 0.0, release_n, dtype=np.float32)
        return env

    def _osc(self, freq: float, n: int, sample_rate: int, wave_shape: str, brightness: float) -> np.ndarray:
        t = np.arange(n, dtype=np.float32) / sample_rate
        phase = 2.0 * np.pi * freq * t
        if wave_shape == "square":
            return np.sign(np.sin(phase)).astype(np.float32)
        if wave_shape == "pulse":
            duty = 0.18 + 0.22 * brightness
            return ((phase / (2.0 * np.pi)) % 1.0 < duty).astype(np.float32) * 2.0 - 1.0
        if wave_shape == "saw":
            return (2.0 * ((freq * t) % 1.0) - 1.0).astype(np.float32)
        if wave_shape == "triangle":
            return (2.0 * np.abs(2.0 * ((freq * t) % 1.0) - 1.0) - 1.0).astype(np.float32)
        return np.sin(phase).astype(np.float32)

    def _add_note(self, audio: np.ndarray, sample_rate: int, start: float, dur: float, midi: int, gain: float, shape: str, brightness: float) -> None:
        start_i = int(start * sample_rate)
        n = int(dur * sample_rate)
        if start_i >= len(audio) or n <= 0:
            return
        n = min(n, len(audio) - start_i)
        freq = self._freq(midi)
        tone = self._osc(freq, n, sample_rate, shape, brightness)
        if shape in {"saw", "square", "pulse"}:
            tone += 0.35 * self._osc(freq * 2.0, n, sample_rate, "sine", brightness)
        env = self._env(n, 0.006 if shape in {"square", "pulse"} else 0.02, min(0.18, dur * 0.45), sample_rate)
        audio[start_i : start_i + n] += tone * env * gain

    def _render_drums(self, total_samples: int, sample_rate: int, beat: float, spec: MusicSpec, plan: RenderPlan, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        steps = int(math.ceil(total_samples / sample_rate / (beat / 2.0)))
        for step in range(steps):
            t = step * beat / 2.0
            pos = step % 8
            if pos in {0, 4}:
                self._kick(audio, sample_rate, t, 0.52 + 0.22 * spec.energy)
            if pos in {2, 6} and spec.energy > 0.25:
                self._snare(audio, sample_rate, t, 0.28 + 0.25 * spec.density, rng)
            if spec.density > 0.42 or pos % 2 == 0:
                self._hat(audio, sample_rate, t, 0.08 + 0.14 * spec.brightness, rng)
            if spec.energy > 0.65 and pos in {3, 7}:
                self._hat(audio, sample_rate, t + beat * 0.22, 0.08, rng)
        return audio

    def _kick(self, audio: np.ndarray, sample_rate: int, start: float, gain: float) -> None:
        n = int(0.18 * sample_rate)
        start_i = int(start * sample_rate)
        if start_i >= len(audio):
            return
        n = min(n, len(audio) - start_i)
        t = np.arange(n, dtype=np.float32) / sample_rate
        freq = 42.0 + 58.0 * np.exp(-t * 32.0)
        phase = 2.0 * np.pi * np.cumsum(freq) / sample_rate
        env = np.exp(-t * 18.0).astype(np.float32)
        audio[start_i : start_i + n] += np.sin(phase).astype(np.float32) * env * gain

    def _snare(self, audio: np.ndarray, sample_rate: int, start: float, gain: float, rng: random.Random) -> None:
        n = int(0.14 * sample_rate)
        start_i = int(start * sample_rate)
        if start_i >= len(audio):
            return
        n = min(n, len(audio) - start_i)
        noise = np.array([rng.uniform(-1.0, 1.0) for _ in range(n)], dtype=np.float32)
        env = np.exp(-np.arange(n, dtype=np.float32) / sample_rate * 22.0)
        body = self._osc(180.0, n, sample_rate, "triangle", 0.4) * 0.25
        audio[start_i : start_i + n] += (noise * 0.75 + body) * env * gain

    def _hat(self, audio: np.ndarray, sample_rate: int, start: float, gain: float, rng: random.Random) -> None:
        n = int(0.055 * sample_rate)
        start_i = int(start * sample_rate)
        if start_i >= len(audio):
            return
        n = min(n, len(audio) - start_i)
        noise = np.array([rng.uniform(-1.0, 1.0) for _ in range(n)], dtype=np.float32)
        env = np.exp(-np.arange(n, dtype=np.float32) / sample_rate * 75.0)
        audio[start_i : start_i + n] += noise * env * gain

    def _render_bass(self, total_samples: int, sample_rate: int, beat: float, root: int, scale: list[int], spec: MusicSpec, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        shape = "pulse" if any("8bit" in inst or "pulse" in inst for inst in spec.instruments) else "triangle"
        degrees = [0, 0, 5, 4, 0, 2, 5, 4]
        step_dur = beat if spec.energy < 0.6 else beat / 2.0
        steps = int(math.ceil(total_samples / sample_rate / step_dur))
        for step in range(steps):
            degree = degrees[step % len(degrees)] % len(scale)
            midi = root - 24 + scale[degree]
            dur = step_dur * (0.82 if spec.energy > 0.6 else 0.95)
            self._add_note(audio, sample_rate, step * step_dur, dur, midi, 0.16 + 0.14 * spec.energy, shape, spec.brightness)
        return audio

    def _render_chords(self, total_samples: int, sample_rate: int, bar: float, root: int, scale: list[int], spec: MusicSpec, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        shape = "sine" if spec.mood in {"ambient", "lofi"} else "triangle"
        bars = int(math.ceil(total_samples / sample_rate / bar))
        for idx in range(bars):
            degree = self.PROGRESSION_MINOR[idx % len(self.PROGRESSION_MINOR)] % len(scale)
            notes = [
                root - 12 + scale[degree],
                root - 12 + scale[(degree + 2) % len(scale)] + (12 if degree + 2 >= len(scale) else 0),
                root - 12 + scale[(degree + 4) % len(scale)] + (12 if degree + 4 >= len(scale) else 0),
            ]
            for offset, midi in enumerate(notes):
                self._add_note(audio, sample_rate, idx * bar + offset * 0.015, bar * 0.92, midi, 0.055 + 0.055 * (1.0 - spec.energy), shape, spec.brightness)
        return audio

    def _render_melody(self, total_samples: int, sample_rate: int, beat: float, root: int, scale: list[int], spec: MusicSpec, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        shape = "square" if spec.brightness > 0.72 else "sine"
        phrase = [0, 2, 4, 5, 4, 2, 1, 2, 4, 6, 5, 4, 2, 1, 0, 2]
        step_dur = beat / 2.0
        steps = int(math.ceil(total_samples / sample_rate / step_dur))
        play_every = 1 if spec.density > 0.62 else 2
        for step in range(steps):
            if step % play_every != 0:
                continue
            if spec.density < 0.45 and step % 8 not in {0, 2, 5}:
                continue
            degree = phrase[step % len(phrase)] % len(scale)
            octave = 12 if spec.brightness > 0.55 else 0
            midi = root + octave + scale[degree]
            if spec.mood == "ambient":
                midi += 12
            self._add_note(audio, sample_rate, step * step_dur, step_dur * 0.78, midi, 0.08 + 0.1 * spec.energy, shape, spec.brightness)
        return audio

    def _render_texture(self, total_samples: int, sample_rate: int, spec: MusicSpec, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        if "vinyl_noise" in spec.instruments or "lofi" in spec.mood.lower():
            noise = np.array([rng.uniform(-1.0, 1.0) for _ in range(total_samples)], dtype=np.float32)
            audio += self._one_pole_lowpass(noise, 0.08) * 0.025
        if "ambient" in spec.mood.lower() or "noise_texture" in spec.instruments:
            t = np.arange(total_samples, dtype=np.float32) / sample_rate
            audio += (np.sin(2.0 * np.pi * 0.07 * t) * 0.5 + 0.5).astype(np.float32) * 0.015
        return audio

    def _apply_effects(self, audio: np.ndarray, sample_rate: int, spec: MusicSpec, plan: RenderPlan) -> np.ndarray:
        if "lowpass" in spec.effects or spec.brightness < 0.5:
            audio = self._one_pole_lowpass(audio, 0.08 + spec.brightness * 0.18)
        if plan.effects.get("delay") or "small_delay" in spec.effects or "gentle_delay" in spec.effects:
            delay = int((0.18 if spec.energy > 0.55 else 0.28) * sample_rate)
            feedback = 0.26 if spec.loopable else 0.18
            delayed = np.zeros_like(audio)
            if delay < len(audio):
                delayed[delay:] = audio[:-delay] * feedback
                if spec.loopable:
                    delayed[:delay] += audio[-delay:] * feedback * 0.65
            audio = audio + delayed
        if plan.effects.get("reverb") or "wide_reverb" in spec.effects:
            taps = [int(sample_rate * x) for x in (0.031, 0.047, 0.071, 0.113)]
            wet = np.zeros_like(audio)
            for idx, tap in enumerate(taps):
                gain = 0.1 / (idx + 1)
                if tap < len(audio):
                    wet[tap:] += audio[:-tap] * gain
                    if spec.loopable:
                        wet[:tap] += audio[-tap:] * gain * 0.75
            audio = audio + wet
        return audio

    def _one_pole_lowpass(self, audio: np.ndarray, amount: float) -> np.ndarray:
        amount = _clamp(amount, 0.01, 0.95)
        out = np.empty_like(audio)
        last = 0.0
        for idx, sample in enumerate(audio):
            last += amount * (float(sample) - last)
            out[idx] = last
        return out

    def _make_loopable(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        n = len(audio)
        fade = min(int(0.35 * sample_rate), n // 8)
        if fade <= 4:
            return audio
        head = audio[:fade].copy()
        tail = audio[-fade:].copy()
        curve = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        blend = tail * (1.0 - curve) + head * curve
        audio[:fade] = blend
        audio[-fade:] = blend
        micro = min(int(0.025 * sample_rate), n // 16)
        if micro > 4:
            audio[:micro] *= np.linspace(0.0, 1.0, micro, dtype=np.float32)
            audio[-micro:] *= np.linspace(1.0, 0.0, micro, dtype=np.float32)
        return audio

    def _master(self, audio: np.ndarray, plan: RenderPlan) -> np.ndarray:
        audio = np.tanh(audio * 1.15).astype(np.float32)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        target = float(plan.master.get("target_peak", 0.92)) if isinstance(plan.master, dict) else 0.92
        target = _clamp(target, 0.3, 0.98)
        if peak > 0.0001:
            audio = audio / peak * target
        return audio.astype(np.float32)

    def _write_wav(self, path: Path, audio: np.ndarray, sample_rate: int) -> None:
        pcm = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16.tobytes())


@register(
    "astrbot_plugin_pymusic",
    "Lenovo",
    "Generate pure Python WAV music from prompts and send it to QQ chats.",
    "0.1.1",
)
class PyMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config
        self.data_dir = _get_data_dir()
        self.renderer = PythonMusicRenderer()
        self.render_sem = asyncio.Semaphore(1)
        self.rate_limiter = RateLimiter(cooldown_sec=30)

    @filter.command("pymusic")
    async def pymusic(self, event: AstrMessageEvent) -> Any:
        async for result in self._handle_command(event):
            yield result

    @filter.llm_tool(name="generate_python_music")
    async def generate_python_music(self, event: AstrMessageEvent, prompt: str, duration: int = 20, loopable: bool = False, send_mode: str = "auto") -> str:
        """Generate and send a pure Python WAV music clip. Use this tool only when the user explicitly asks to generate music, send music, make a music clip, or express something with music.

        Args:
            prompt(string): Music prompt, such as electronic night city, 8bit battle, ambient stars, or lofi rainy cafe.
            duration(number): Requested duration in seconds.
            loopable(boolean): Whether the result should be loopable.
            send_mode(string): voice, file, or auto.
        """
        if not self._is_supported_platform(event):
            return "pymusic only supports QQ personal-account adapters and QQ official adapters."

        max_duration = self._max_duration()
        duration = _safe_int(duration, self._default_duration(), 5, max_duration)
        send_mode = _normalize_send_mode(send_mode, self._default_send_mode())
        prompt = f"{prompt}\nRequested duration: {duration}s. Loopable: {loopable}. Send mode: {send_mode}."

        async with self.render_sem:
            try:
                spec, plan, wav_path = await self._generate(prompt, event, duration, loopable, send_mode)
            except Exception as exc:
                logger.exception("[pymusic] generation failed")
                return f"pymusic failed to generate music: {exc}"

        sent_mode = await self._send_music(event, wav_path, spec)
        self._cleanup_history()
        return f"Generated a {spec.duration}s {spec.mood} WAV music clip and sent it as {sent_mode}."

    async def _handle_command(self, event: AstrMessageEvent) -> Any:
        if not self._is_supported_platform(event):
            yield event.chain_result([Plain("pymusic 目前只支持 QQ 个人号适配器和 QQ 官方机器人。")])
            return

        prompt = self._message_text(event).strip()
        if not prompt:
            yield event.chain_result([Plain("用法：/pymusic 20秒 8bit 夜晚城市 可循环")])
            return

        cooldown_key = self._cooldown_key(event)
        wait = self.rate_limiter.check(cooldown_key)
        if wait > 0:
            yield event.chain_result([Plain(f"pymusic 冷却中，请 {wait} 秒后再试。")])
            return

        yield event.chain_result([Plain("正在用纯 Python 合成音乐，请稍等。")])

        overrides = _prompt_overrides(prompt)
        duration = _safe_int(overrides["duration"], self._default_duration(), 5, self._max_duration()) if overrides["duration"] else self._default_duration()
        loopable = self._waveform_loopable() if overrides["loopable"] is None else bool(overrides["loopable"])
        send_mode = _normalize_send_mode(overrides["send_mode"], self._default_send_mode())

        async with self.render_sem:
            try:
                spec, plan, wav_path = await self._generate(prompt, event, duration, loopable, send_mode)
            except Exception as exc:
                logger.exception("[pymusic] generation failed")
                yield event.chain_result([Plain(f"生成失败：{exc}")])
                return

        try:
            sent_mode = await self._send_music(event, wav_path, spec)
        except Exception as exc:
            logger.exception("[pymusic] send failed")
            yield event.chain_result([Plain(f"音乐已生成，但发送失败：{wav_path}\n{exc}")])
            return

        yield event.chain_result([Plain(f"pymusic 已生成：{spec.mood} / {spec.duration}s / {sent_mode}")])
        self._cleanup_history()

    async def _generate(self, prompt: str, event: AstrMessageEvent, duration: int, loopable: bool, send_mode: str) -> tuple[MusicSpec, RenderPlan, Path]:
        spec = await self._build_spec(prompt, event, duration, loopable, send_mode)
        if spec.duration > VOICE_MAX_SECONDS and spec.send_mode in {"voice", "auto"}:
            spec.send_mode = "file"
        plan = await self._build_plan(prompt, event, spec)
        wav_path = self.data_dir / f"pymusic_{int(time.time())}_{random.randint(1000, 9999)}.wav"
        sample_rate = self._sample_rate()
        await asyncio.to_thread(self.renderer.render, spec, plan, wav_path, sample_rate)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError("WAV 文件没有成功生成")
        return spec, plan, wav_path

    async def _build_spec(self, prompt: str, event: AstrMessageEvent, duration: int, loopable: bool, send_mode: str) -> MusicSpec:
        fallback = _fallback_spec(prompt, duration, self._max_duration(), send_mode, loopable)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You convert a user music prompt into one strict JSON object named MusicSpec. "
            "Do not include markdown. Do not write Python. "
            "Allowed styles are electronic, 8bit, ambient, and lofi. "
            "Fields: mood string, energy number 0..1, brightness number 0..1, density number 0..1, "
            "bpm integer 55..180, key string, instruments array of strings, effects array of strings, "
            "duration integer seconds, loopable boolean, send_mode string voice/file/auto."
        )
        user_prompt = (
            f"Prompt: {prompt}\n"
            f"Defaults: duration={duration}, loopable={loopable}, send_mode={send_mode}. "
            f"Max duration={self._max_duration()}."
        )
        try:
            response = await provider.text_chat(prompt=user_prompt, system_prompt=system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _spec_from_dict(data, fallback, self._max_duration())
        except Exception as exc:
            logger.warning(f"[pymusic] MusicSpec LLM planning failed, using fallback: {exc}")
        return fallback

    async def _build_plan(self, prompt: str, event: AstrMessageEvent, spec: MusicSpec) -> RenderPlan:
        fallback = _default_plan(spec)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You convert a MusicSpec into one strict JSON object named RenderPlan for a fixed Python renderer. "
            "Do not include markdown. Do not write Python. "
            "Fields: tracks array, drums object, bass object, chords object, melody object, texture object, effects object, master object. "
            "Keep values simple and renderer-friendly."
        )
        try:
            response = await provider.text_chat(prompt=json.dumps(spec.__dict__, ensure_ascii=False), system_prompt=system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _plan_from_dict(data, fallback)
        except Exception as exc:
            logger.warning(f"[pymusic] RenderPlan LLM planning failed, using fallback: {exc}")
        return fallback

    async def _send_music(self, event: AstrMessageEvent, wav_path: Path, spec: MusicSpec) -> str:
        mode = _normalize_send_mode(spec.send_mode, self._default_send_mode())
        if spec.duration > VOICE_MAX_SECONDS:
            mode = "file"

        if mode in {"voice", "auto"}:
            try:
                await event.send(MessageChain([Record.fromFileSystem(str(wav_path))]))
                return "voice"
            except Exception as exc:
                logger.warning(f"[pymusic] voice send failed: {exc}")
                if mode == "voice":
                    raise

        await event.send(MessageChain([File(name=wav_path.name, file=str(wav_path))]))
        return "file"

    def _is_supported_platform(self, event: AstrMessageEvent) -> bool:
        try:
            return event.get_platform_name() in SUPPORTED_PLATFORMS
        except Exception:
            return False

    def _message_text(self, event: AstrMessageEvent) -> str:
        for attr in ("message_str", "text"):
            value = getattr(event, attr, None)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                return re.sub(r"^/pymusic\s*", "", text, flags=re.I).strip()
        try:
            return re.sub(r"^/pymusic\s*", "", event.get_message_str(), flags=re.I).strip()
        except Exception:
            return ""

    def _cooldown_key(self, event: AstrMessageEvent) -> str:
        return getattr(event, "unified_msg_origin", "") or "global"

    def _max_duration(self) -> int:
        return _safe_int(_cfg_get(self.config, "max_duration_sec", HARD_MAX_SECONDS), HARD_MAX_SECONDS, 5, HARD_MAX_SECONDS)

    def _default_duration(self) -> int:
        return _safe_int(_cfg_get(self.config, "default_duration_sec", DEFAULT_DURATION), DEFAULT_DURATION, 5, self._max_duration())

    def _waveform_loopable(self) -> bool:
        return bool(_cfg_get(self.config, "waveform_loopable", True))

    def _default_send_mode(self) -> str:
        return _normalize_send_mode(_cfg_get(self.config, "default_send_mode", "auto"), "auto")

    def _music_provider_id(self) -> str:
        return str(_cfg_get(self.config, "music_provider_id", "") or "").strip()

    def _get_music_provider(self, event: AstrMessageEvent) -> Any:
        provider_id = self._music_provider_id()
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if provider is not None and hasattr(provider, "text_chat"):
                    return provider
                logger.warning(f"[pymusic] configured provider is unavailable or not chat-capable: {provider_id}")
            except Exception as exc:
                logger.warning(f"[pymusic] failed to load configured provider {provider_id}: {exc}")
        return self.context.get_using_provider(event.unified_msg_origin)

    def _sample_rate(self) -> int:
        return _safe_int(_cfg_get(self.config, "sample_rate", DEFAULT_SAMPLE_RATE), DEFAULT_SAMPLE_RATE, 16000, 48000)

    def _keep_history_wav(self) -> bool:
        return bool(_cfg_get(self.config, "keep_history_wav", False))

    def _cleanup_history(self) -> None:
        if self._keep_history_wav():
            return
        try:
            wavs = sorted(self.data_dir.glob("pymusic_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in wavs[5:]:
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"[pymusic] cleanup failed: {exc}")
