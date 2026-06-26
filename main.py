from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import random
import re
import subprocess
import sys
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
HARD_MAX_SECONDS = 600
DEFAULT_DURATION = 20
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_DIVERSITY_LEVEL = 1
DEFAULT_MODEL_CALL_TIMEOUT_SECONDS = 12
VOICE_SEND_TIMEOUT_SECONDS = 8
FILE_SEND_TIMEOUT_SECONDS = 20


@dataclass
class PromptBrief:
    original_prompt: str = ""
    enriched_prompt: str = ""
    style: str = "electronic"
    scene: str = ""
    musical_intent: str = ""
    references: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)


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


def _normalize_diversity_level(value: Any, default: int = DEFAULT_DIVERSITY_LEVEL) -> int:
    return _safe_int(value, default, 0, 2)


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


def _extract_python_code(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:python|py)?\s*(.*?)\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    return text


def _validate_generated_python(source: str) -> None:
    if len(source) > 18000:
        raise ValueError("generated Python code is too long")
    tree = ast.parse(source)
    allowed_imports = {"numpy", "math", "random"}
    blocked_names = {
        "open",
        "exec",
        "eval",
        "compile",
        "input",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "help",
        "breakpoint",
        "os",
        "sys",
        "subprocess",
        "pathlib",
        "shutil",
        "socket",
        "requests",
        "httpx",
        "wave",
        "builtins",
    }
    blocked_attrs = {
        "save",
        "savez",
        "load",
        "fromfile",
        "tofile",
        "memmap",
        "genfromtxt",
        "loadtxt",
        "savetxt",
        "ctypeslib",
    }
    has_render = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                parts = set(alias.name.split("."))
                if root not in allowed_imports or parts & blocked_attrs:
                    raise ValueError(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            parts = set((node.module or "").split("."))
            alias_parts = {alias.name.split(".", 1)[0] for alias in node.names}
            if node.level != 0 or root not in allowed_imports or parts & blocked_attrs or alias_parts & blocked_attrs:
                raise ValueError(f"disallowed import: {node.module}")
        elif isinstance(node, ast.ClassDef):
            raise ValueError("classes are not allowed in generated renderer code")
        elif isinstance(node, ast.FunctionDef):
            if node.name == "render":
                has_render = True
        elif isinstance(node, ast.Name):
            if node.id in blocked_names or node.id.startswith("__"):
                raise ValueError(f"disallowed name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in blocked_attrs:
                raise ValueError(f"disallowed attribute: {node.attr}")
    if not has_render:
        raise ValueError("generated code must define render(duration, sample_rate, loopable)")


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


def _parse_command_payload(payload: str) -> tuple[int | None, str]:
    payload = _strip_command_prefix(payload)
    match = re.match(r"^\s*(\d{1,4})\s*(?:秒|s|sec|second|seconds)?\s+(.+)$", payload.strip(), re.I)
    if not match:
        return None, ""
    return int(match.group(1)), match.group(2).strip()


def _strip_command_prefix(text: str) -> str:
    text = text.strip()
    return re.sub(r"^/?pymusic(?:\s+|$)", "", text, flags=re.I).strip()


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


def _fallback_brief(prompt: str) -> PromptBrief:
    fallback = _fallback_spec(prompt, DEFAULT_DURATION, HARD_MAX_SECONDS, "auto", False)
    style = fallback.mood
    instruments = ", ".join(fallback.instruments[:4])
    effects = ", ".join(fallback.effects[:3])
    enriched = (
        f"A polished {style} instrumental loop for the scene '{prompt}'. "
        f"Use {instruments}, {fallback.bpm} BPM, {fallback.key}, "
        f"energy {fallback.energy:.2f}, brightness {fallback.brightness:.2f}, "
        f"density {fallback.density:.2f}, with {effects}. "
        "Keep it melodic, coherent, and easy to listen to."
    )
    return PromptBrief(
        original_prompt=prompt,
        enriched_prompt=enriched,
        style=style,
        scene=prompt,
        musical_intent="make a pleasant pure-Python generated instrumental clip",
        references=fallback.instruments[:4],
        avoid=["vocals", "external samples", "overly harsh noise"],
    )


def _brief_from_dict(data: dict[str, Any], original_prompt: str, fallback: PromptBrief) -> PromptBrief:
    references = data.get("references", fallback.references)
    avoid = data.get("avoid", fallback.avoid)
    if not isinstance(references, list):
        references = fallback.references
    if not isinstance(avoid, list):
        avoid = fallback.avoid

    enriched = str(data.get("enriched_prompt", fallback.enriched_prompt)).strip()
    if not enriched:
        enriched = fallback.enriched_prompt
    return PromptBrief(
        original_prompt=original_prompt,
        enriched_prompt=enriched[:1200],
        style=str(data.get("style", fallback.style)).strip()[:80] or fallback.style,
        scene=str(data.get("scene", fallback.scene)).strip()[:200] or fallback.scene,
        musical_intent=str(data.get("musical_intent", fallback.musical_intent)).strip()[:300] or fallback.musical_intent,
        references=[str(item).strip()[:80] for item in references[:8] if str(item).strip()],
        avoid=[str(item).strip()[:80] for item in avoid[:8] if str(item).strip()],
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


def _default_plan(spec: MusicSpec, diversity_level: int = DEFAULT_DIVERSITY_LEVEL) -> RenderPlan:
    diversity_level = _normalize_diversity_level(diversity_level)
    style_text = f"{spec.mood} {' '.join(spec.instruments)}".lower()
    if "ambient" in style_text or "氛围" in style_text:
        tracks = [
            {"role": "chords", "name": "pad", "gain": 1.0},
            {"role": "melody", "name": "bell", "gain": 0.8},
            {"role": "bass", "name": "sub_drone", "gain": 0.9},
            {"role": "texture", "name": "noise_texture", "gain": 0.8},
        ]
        drum_pattern = "ambient_no_drums"
        bass_pattern = "sub_drone"
        progression = "modal_drone"
        melody_shape = "stepwise"
        texture_noise = "space"
    elif "lofi" in style_text or "雨" in style_text:
        tracks = [
            {"role": "chords", "name": "warm_keys", "gain": 1.0},
            {"role": "melody", "name": "pluck", "gain": 0.85},
            {"role": "bass", "name": "warm_bass", "gain": 0.95},
            {"role": "drums", "name": "lofi_drums", "gain": 0.9},
            {"role": "texture", "name": "vinyl_noise", "gain": 0.8},
        ]
        drum_pattern = "lofi_swing"
        bass_pattern = "warm_roots"
        progression = "ii-V-I"
        melody_shape = "pentatonic"
        texture_noise = "vinyl"
    elif "8bit" in style_text or "chiptune" in style_text or "像素" in style_text:
        tracks = [
            {"role": "melody", "name": "chip_lead", "gain": 1.0},
            {"role": "bass", "name": "chip_bass", "gain": 0.95},
            {"role": "chords", "name": "pluck", "gain": 0.8},
            {"role": "drums", "name": "chip_drums", "gain": 0.9},
        ]
        drum_pattern = "8bit_arpeggio_beat"
        bass_pattern = "root_octave"
        progression = "i-VI-III-VII"
        melody_shape = "arpeggio"
        texture_noise = "air"
    elif spec.energy > 0.68:
        tracks = [
            {"role": "bass", "name": "acid_bass", "gain": 1.0},
            {"role": "melody", "name": "chip_lead", "gain": 0.8},
            {"role": "chords", "name": "pluck", "gain": 0.75},
            {"role": "drums", "name": "electronic_drums", "gain": 1.0},
        ]
        drum_pattern = "breakbeat" if diversity_level >= 2 and spec.density > 0.55 else "minimal_techno"
        bass_pattern = "acid_bass" if diversity_level >= 1 else "root_octave"
        progression = "i-iv-V-i"
        melody_shape = "random_walk" if diversity_level >= 2 else "call_response"
        texture_noise = "air"
    else:
        tracks = [
            {"role": "melody", "name": "chip_lead", "gain": 0.9},
            {"role": "chords", "name": "warm_keys", "gain": 0.9},
            {"role": "bass", "name": "warm_bass", "gain": 0.9},
            {"role": "drums", "name": "electronic_drums", "gain": 0.9},
        ]
        drum_pattern = "breakbeat" if diversity_level >= 2 or spec.density > 0.62 else "minimal_techno"
        bass_pattern = "syncopated_pulse" if diversity_level >= 1 else "warm_roots"
        progression = "I-V-vi-IV" if spec.brightness > 0.62 else "i-VI-III-VII"
        melody_shape = "random_walk" if diversity_level >= 2 else "motif_variation"
        texture_noise = "air"

    return RenderPlan(
        tracks=tracks,
        drums={"pattern": drum_pattern, "density": spec.density},
        bass={"pattern": bass_pattern},
        chords={"progression": progression, "voicing": "stabs" if diversity_level >= 2 and spec.energy > 0.62 else ("soft" if spec.brightness < 0.55 else "open")},
        melody={"shape": melody_shape, "activity": spec.density, "register": "high" if spec.brightness > 0.65 else "mid"},
        texture={"noise": texture_noise},
        effects={
            "delay": "small_delay" in spec.effects or "gentle_delay" in spec.effects,
            "reverb": "wide_reverb" in spec.effects,
            "lowpass": "lowpass" in spec.effects,
        },
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
    DRUM_PATTERNS = {"lofi_swing", "8bit_arpeggio_beat", "ambient_no_drums", "breakbeat", "minimal_techno", "four_on_floor", "half_time"}
    BASS_PATTERNS = {"root_octave", "warm_roots", "acid_bass", "sub_drone", "syncopated_pulse"}
    CHORD_PROGRESSIONS = {
        "i-VI-III-VII": [0, 5, 2, 6],
        "i-iv-V-i": [0, 3, 4, 0],
        "I-V-vi-IV": [0, 4, 5, 3],
        "ii-V-I": [1, 4, 0],
        "modal_drone": [0],
    }
    MELODY_SHAPES = {"arpeggio", "call_response", "pentatonic", "stepwise", "random_walk", "motif_variation", "periodic"}
    VOICE_ENGINES = {
        "chip_lead": {"shape": "square", "attack": 0.003, "release": 0.055, "gain": 1.0, "brightness": 0.18},
        "warm_keys": {"shape": "triangle", "attack": 0.018, "release": 0.16, "gain": 0.9, "brightness": -0.1},
        "pluck": {"shape": "saw", "attack": 0.004, "release": 0.09, "gain": 0.82, "brightness": 0.1},
        "pad": {"shape": "sine", "attack": 0.08, "release": 0.32, "gain": 0.78, "brightness": -0.05},
        "bell": {"shape": "sine", "attack": 0.006, "release": 0.36, "gain": 0.72, "brightness": 0.2},
        "acid_bass": {"shape": "saw", "attack": 0.003, "release": 0.07, "gain": 1.0, "brightness": 0.25},
        "sub_drone": {"shape": "sine", "attack": 0.05, "release": 0.28, "gain": 0.88, "brightness": -0.2},
        "warm_bass": {"shape": "triangle", "attack": 0.012, "release": 0.12, "gain": 0.9, "brightness": -0.08},
    }

    def render(self, spec: MusicSpec, plan: RenderPlan, out_path: Path, sample_rate: int, diversity_level: int = DEFAULT_DIVERSITY_LEVEL) -> None:
        sample_rate = _safe_int(sample_rate, DEFAULT_SAMPLE_RATE, 16000, 48000)
        diversity_level = _normalize_diversity_level(diversity_level)
        duration = max(5, min(HARD_MAX_SECONDS, int(spec.duration)))
        bpm = max(55, min(180, int(spec.bpm)))
        beat = 60.0 / bpm
        bar = beat * 4
        if spec.loopable:
            max_bars = max(2, int(HARD_MAX_SECONDS / bar))
            bars = min(max_bars, max(2, round(duration / bar)))
            duration = int(round(bars * bar))

        total_samples = int(duration * sample_rate)
        audio = np.zeros(total_samples, dtype=np.float32)
        root, scale = self._scale(spec.key)
        rng = random.Random(self._seed(spec, plan))
        arrangement = self._build_arrangement(total_samples / sample_rate, bar, spec, diversity_level)

        audio += self._render_drums(total_samples, sample_rate, beat, spec, plan, rng, arrangement, diversity_level)
        audio += self._render_bass(total_samples, sample_rate, beat, root, scale, spec, plan, rng, arrangement)
        audio += self._render_chords(total_samples, sample_rate, bar, root, scale, spec, plan, rng, arrangement)
        audio += self._render_melody(total_samples, sample_rate, beat, root, scale, spec, plan, rng, arrangement)
        audio += self._render_texture(total_samples, sample_rate, spec, plan, rng)

        audio = self._apply_effects(audio, sample_rate, spec, plan)
        audio = self._apply_arrangement_envelope(audio, sample_rate, bar, arrangement, spec.loopable)
        if spec.loopable:
            audio = self._make_loopable(audio, sample_rate)
        audio = self._master(audio, plan)
        self._write_wav(out_path, audio, sample_rate)

    def _seed(self, spec: MusicSpec, plan: RenderPlan | None = None) -> int:
        plan_text = ""
        if plan is not None:
            plan_text = json.dumps(plan.__dict__, ensure_ascii=False, sort_keys=True, default=str)[:2000]
        basis = f"{spec.mood}|{spec.key}|{spec.bpm}|{spec.instruments}|{spec.effects}|{plan_text}"
        return sum(ord(ch) * (idx + 1) for idx, ch in enumerate(basis)) % (2**32)

    def _scale(self, key: str) -> tuple[int, list[int]]:
        parts = key.replace("minor", " minor").replace("major", " major").split()
        note = parts[0] if parts else "C"
        root = 60 + self.NOTE_OFFSETS.get(note, 0)
        scale = self.MINOR if "minor" in key.lower() or "小" in key else self.MAJOR
        return root, scale

    def _section(self, plan: RenderPlan, name: str) -> dict[str, Any]:
        value = getattr(plan, name, {})
        return value if isinstance(value, dict) else {}

    def _choice(self, value: Any, allowed: set[str], default: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return default
        for item in allowed:
            if raw == item or raw.lower() == item.lower():
                return item
        return default

    def _float_value(self, value: Any, default: float, low: float, high: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            parsed = default
        return _clamp(parsed, low, high)

    def _noise(self, n: int, rng: random.Random) -> np.ndarray:
        np_rng = np.random.default_rng(rng.randrange(0, 2**32))
        return np_rng.uniform(-1.0, 1.0, n).astype(np.float32)

    def _style_text(self, spec: MusicSpec) -> str:
        return f"{spec.mood} {' '.join(spec.instruments)} {' '.join(spec.effects)}".lower()

    def _default_drum_pattern(self, spec: MusicSpec) -> str:
        text = self._style_text(spec)
        if "ambient" in text or "氛围" in text:
            return "ambient_no_drums"
        if "lofi" in text or "雨" in text:
            return "lofi_swing"
        if "8bit" in text or "chip" in text or "像素" in text:
            return "8bit_arpeggio_beat"
        if spec.energy > 0.68:
            return "minimal_techno"
        return "breakbeat" if spec.density > 0.6 else "half_time"

    def _default_bass_pattern(self, spec: MusicSpec) -> str:
        text = self._style_text(spec)
        if "ambient" in text or "sub" in text:
            return "sub_drone"
        if "acid" in text or spec.energy > 0.72:
            return "acid_bass"
        if "lofi" in text:
            return "warm_roots"
        return "root_octave" if "8bit" in text or "chip" in text else "syncopated_pulse"

    def _default_melody_shape(self, spec: MusicSpec) -> str:
        text = self._style_text(spec)
        if "8bit" in text or "chip" in text:
            return "arpeggio"
        if "lofi" in text:
            return "pentatonic"
        if "ambient" in text:
            return "stepwise"
        return "call_response" if spec.energy > 0.65 else "motif_variation"

    def _progression_name(self, plan: RenderPlan, spec: MusicSpec) -> str:
        value = self._section(plan, "chords").get("progression")
        if isinstance(value, list):
            raw = "-".join(str(item).strip() for item in value if str(item).strip())
        else:
            raw = str(value or "").strip()
        compact = raw.replace(" ", "").replace("_", "-").replace("–", "-").replace("—", "-")
        exact = {
            "i-VI-III-VII": "i-VI-III-VII",
            "i-iv-V-i": "i-iv-V-i",
            "I-V-vi-IV": "I-V-vi-IV",
            "ii-V-I": "ii-V-I",
            "modal-drone": "modal_drone",
            "modal_drone": "modal_drone",
        }
        if compact in exact:
            return exact[compact]
        lowered = compact.lower()
        aliases = {
            "i-vi-iii-vii": "i-VI-III-VII",
            "1-6-3-7": "i-VI-III-VII",
            "i-iv-v-i": "i-iv-V-i",
            "1-4-5-1": "i-iv-V-i",
            "i-v-vi-iv": "I-V-vi-IV",
            "1-5-6-4": "I-V-vi-IV",
            "ii-v-i": "ii-V-I",
            "2-5-1": "ii-V-I",
        }
        if lowered in aliases:
            return aliases[lowered]
        if "modal" in lowered or "drone" in lowered:
            return "modal_drone"
        text = self._style_text(spec)
        if "ambient" in text:
            return "modal_drone"
        if "lofi" in text:
            return "ii-V-I"
        if spec.brightness > 0.64:
            return "I-V-vi-IV"
        return "i-VI-III-VII"

    def _chord_degrees(self, plan: RenderPlan, spec: MusicSpec) -> list[int]:
        return self.CHORD_PROGRESSIONS[self._progression_name(plan, spec)]

    def _scale_degree_midi(self, root: int, scale: list[int], degree: int, octave: int = 0) -> int:
        wrapped = degree // len(scale)
        return root + octave + scale[degree % len(scale)] + wrapped * 12

    def _track_gain(self, plan: RenderPlan, role: str, default: float) -> float:
        tracks = plan.tracks if isinstance(plan.tracks, list) else []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            track_role = str(track.get("role", "")).lower()
            name = str(track.get("name", "")).lower()
            if track_role == role or (role == "melody" and "lead" in name) or (role == "bass" and "bass" in name):
                return self._float_value(track.get("gain", default), default, 0.1, 1.5)
        return default

    def _voice_engine_name(self, role: str, spec: MusicSpec, plan: RenderPlan) -> str:
        candidates: list[str] = []
        section_name = "chords" if role == "chords" else role
        section = self._section(plan, section_name)
        for key in ("voice", "instrument", "engine", "name"):
            if section.get(key):
                candidates.append(str(section[key]))
        tracks = plan.tracks if isinstance(plan.tracks, list) else []
        for track in tracks:
            if isinstance(track, dict):
                role_text = str(track.get("role", "")).lower()
                name = str(track.get("name", ""))
                if role_text == role or not role_text:
                    candidates.append(name)
            else:
                candidates.append(str(track))
        candidates.extend(spec.instruments)
        text = " ".join(candidates).lower()

        if role == "bass":
            if "acid" in text:
                return "acid_bass"
            if "sub" in text or "drone" in text:
                return "sub_drone"
            if "8bit" in text or "chip" in text:
                return "chip_lead"
            return "warm_bass"
        if role == "chords":
            if "pad" in text or "ambient" in text:
                return "pad"
            if "pluck" in text or "8bit" in text or "chip" in text:
                return "pluck"
            if "bell" in text:
                return "bell"
            return "warm_keys"
        if "bell" in text:
            return "bell"
        if "pluck" in text:
            return "pluck"
        if "8bit" in text or "chip" in text or "lead" in text:
            return "chip_lead"
        if "pad" in text or "ambient" in text:
            return "pad"
        return "warm_keys"

    def _voice_engine(self, role: str, spec: MusicSpec, plan: RenderPlan) -> dict[str, Any]:
        return self.VOICE_ENGINES[self._voice_engine_name(role, spec, plan)]

    def _build_arrangement(self, duration: float, bar: float, spec: MusicSpec, diversity_level: int) -> list[dict[str, Any]]:
        bars = max(1, int(math.ceil(duration / bar)))
        if diversity_level <= 0 or bars < 8:
            return [self._segment(0, bars, "main_a")]

        if spec.loopable:
            if bars < 16:
                split = max(4, bars // 2)
                return [
                    self._segment(0, split, "main_a"),
                    self._segment(split, bars, "main_a"),
                ]
            q1 = max(4, bars // 4)
            q3 = max(q1 + 1, bars - q1)
            return [
                self._segment(0, q1, "main_a"),
                self._segment(q1, q3, "main_b" if diversity_level >= 2 else "main_a"),
                self._segment(q3, bars, "main_a"),
            ]

        if bars < 16:
            intro = max(1, bars // 6)
            outro = max(1, bars // 6)
            main_end = max(intro + 1, bars - outro)
            return [
                self._segment(0, intro, "intro"),
                self._segment(intro, main_end, "main_a"),
                self._segment(main_end, bars, "outro"),
            ]

        intro = min(4, max(2, bars // 10))
        outro = min(4, max(2, bars // 10))
        break_len = min(4, max(2, bars // 12)) if diversity_level >= 1 and bars >= 24 else 0
        body = max(4, bars - intro - outro - break_len)
        a_len = max(4, body // 2)
        b_len = max(4, body - a_len)
        start_b = intro + a_len
        start_break = start_b + b_len
        start_final = start_break + break_len
        segments = [
            self._segment(0, intro, "intro"),
            self._segment(intro, start_b, "main_a"),
            self._segment(start_b, start_break, "main_b" if diversity_level >= 2 else "main_a"),
        ]
        if break_len > 0:
            segments.append(self._segment(start_break, start_final, "break"))
        segments.append(self._segment(start_final, bars - outro, "main_b" if diversity_level >= 2 else "main_a"))
        segments.append(self._segment(bars - outro, bars, "outro"))
        return [segment for segment in segments if int(segment["end_bar"]) > int(segment["start_bar"])]

    def _segment(self, start_bar: int, end_bar: int, label: str) -> dict[str, Any]:
        profiles = {
            "intro": {"drums": 0.35, "bass": 0.5, "chords": 0.85, "melody": 0.15, "density": -0.25, "melody_shift": 0},
            "main_a": {"drums": 1.0, "bass": 1.0, "chords": 1.0, "melody": 0.9, "density": 0.0, "melody_shift": 0},
            "main_b": {"drums": 1.08, "bass": 1.04, "chords": 0.95, "melody": 1.05, "density": 0.18, "melody_shift": 1},
            "break": {"drums": 0.2, "bass": 0.25, "chords": 0.65, "melody": 0.45, "density": -0.38, "melody_shift": 2},
            "outro": {"drums": 0.28, "bass": 0.45, "chords": 0.8, "melody": 0.18, "density": -0.25, "melody_shift": 0},
        }
        segment = {"start_bar": max(0, int(start_bar)), "end_bar": max(0, int(end_bar)), "label": label}
        segment.update(profiles.get(label, profiles["main_a"]))
        return segment

    def _segment_for_bar(self, bar_idx: int, arrangement: list[dict[str, Any]]) -> dict[str, Any]:
        for segment in arrangement:
            if int(segment["start_bar"]) <= bar_idx < int(segment["end_bar"]):
                return segment
        return arrangement[-1] if arrangement else self._segment(0, 1, "main_a")

    def _segment_for_time(self, t: float, bar: float, arrangement: list[dict[str, Any]]) -> dict[str, Any]:
        return self._segment_for_bar(max(0, int(t // bar)), arrangement)

    def _role_gain(self, segment: dict[str, Any], role: str) -> float:
        return self._float_value(segment.get(role, 1.0), 1.0, 0.0, 1.5)

    def _density_shift(self, segment: dict[str, Any]) -> float:
        return self._float_value(segment.get("density", 0.0), 0.0, -0.5, 0.4)

    def _is_fill_bar(self, bar_idx: int, arrangement: list[dict[str, Any]], diversity_level: int) -> bool:
        if diversity_level <= 0 or bar_idx <= 0:
            return False
        segment = self._segment_for_bar(bar_idx, arrangement)
        label = str(segment.get("label", "main_a"))
        if label in {"intro", "outro"}:
            return False
        end_bar = int(segment.get("end_bar", 0))
        start_bar = int(segment.get("start_bar", 0))
        if end_bar - start_bar >= 4 and bar_idx + 1 == end_bar:
            return True
        phrase = 8 if diversity_level >= 2 else 12
        return (bar_idx + 1) % phrase == 0

    def _apply_arrangement_envelope(self, audio: np.ndarray, sample_rate: int, bar: float, arrangement: list[dict[str, Any]], loopable: bool) -> np.ndarray:
        if not arrangement or len(audio) <= 8:
            return audio
        if loopable:
            return audio
        first = arrangement[0]
        last = arrangement[-1]
        fade_in = min(int(sample_rate * bar * max(1, int(first["end_bar"]) - int(first["start_bar"]))), len(audio) // 4)
        fade_out = min(int(sample_rate * bar * max(1, int(last["end_bar"]) - int(last["start_bar"]))), len(audio) // 4)
        if fade_in > 8:
            audio[:fade_in] *= np.linspace(0.15, 1.0, fade_in, dtype=np.float32)
        if fade_out > 8:
            audio[-fade_out:] *= np.linspace(1.0, 0.05, fade_out, dtype=np.float32)
        return audio

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

    def _add_note(
        self,
        audio: np.ndarray,
        sample_rate: int,
        start: float,
        dur: float,
        midi: int,
        gain: float,
        shape: str,
        brightness: float,
        attack: float | None = None,
        release: float | None = None,
    ) -> None:
        start_i = int(start * sample_rate)
        n = int(dur * sample_rate)
        if start_i >= len(audio) or n <= 0:
            return
        n = min(n, len(audio) - start_i)
        freq = self._freq(midi)
        tone = self._osc(freq, n, sample_rate, shape, brightness)
        if shape in {"saw", "square", "pulse"}:
            tone += 0.35 * self._osc(freq * 2.0, n, sample_rate, "sine", brightness)
        attack_value = attack if attack is not None else (0.006 if shape in {"square", "pulse"} else 0.02)
        release_value = release if release is not None else min(0.18, dur * 0.45)
        env = self._env(n, attack_value, min(release_value, dur * 0.9), sample_rate)
        audio[start_i : start_i + n] += tone * env * gain

    def _render_drums(
        self,
        total_samples: int,
        sample_rate: int,
        beat: float,
        spec: MusicSpec,
        plan: RenderPlan,
        rng: random.Random,
        arrangement: list[dict[str, Any]],
        diversity_level: int,
    ) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        drums = self._section(plan, "drums")
        pattern = self._choice(drums.get("pattern"), self.DRUM_PATTERNS, self._default_drum_pattern(spec))
        if pattern == "ambient_no_drums":
            return audio

        density = self._float_value(drums.get("density", spec.density), spec.density, 0.0, 1.0)
        base_drum_gain = self._track_gain(plan, "drums", 1.0)
        step_dur = beat / 4.0
        bar = beat * 4.0
        steps = int(math.ceil(total_samples / sample_rate / step_dur))
        for step in range(steps):
            pos = step % 16
            t = step * step_dur
            bar_idx = int(t // bar)
            segment = self._segment_for_bar(bar_idx, arrangement)
            effective_density = _clamp(density + self._density_shift(segment), 0.0, 1.0)
            drum_gain = base_drum_gain * self._role_gain(segment, "drums")
            if self._is_fill_bar(bar_idx, arrangement, diversity_level) and pos >= 12:
                self._drum_fill(audio, sample_rate, t, pos, pattern, drum_gain, spec, rng)
                continue
            if pattern in {"lofi_swing", "breakbeat"} and pos % 4 in {1, 3}:
                t += step_dur * (0.28 if pattern == "lofi_swing" else 0.14)

            if pattern == "lofi_swing":
                if pos in {0, 6, 10}:
                    self._kick(audio, sample_rate, t, drum_gain * (0.44 + 0.18 * spec.energy))
                if pos in {4, 12} and spec.energy > 0.18:
                    self._snare(audio, sample_rate, t, drum_gain * (0.22 + 0.24 * effective_density), rng)
                if pos in {0, 2, 4, 6, 8, 10, 12, 14} or effective_density > 0.72:
                    self._hat(audio, sample_rate, t, drum_gain * (0.045 + 0.11 * spec.brightness), rng)
                if effective_density > 0.62 and pos in {3, 11, 15}:
                    self._hat(audio, sample_rate, t, drum_gain * 0.045, rng)
            elif pattern == "8bit_arpeggio_beat":
                if pos in {0, 8}:
                    self._kick(audio, sample_rate, t, drum_gain * (0.48 + 0.22 * spec.energy))
                if pos in {4, 12} and spec.energy > 0.22:
                    self._snare(audio, sample_rate, t, drum_gain * (0.18 + 0.22 * effective_density), rng)
                if pos % 2 == 0 or effective_density > 0.65:
                    self._hat(audio, sample_rate, t, drum_gain * (0.05 + 0.08 * spec.brightness), rng)
                if pos in {3, 7, 11, 15} and effective_density > 0.36:
                    self._blip(audio, sample_rate, t, 1200.0 + 90.0 * (pos % 4), drum_gain * 0.04)
            elif pattern == "breakbeat":
                if pos in {0, 3, 10} or (spec.energy > 0.72 and pos == 14):
                    self._kick(audio, sample_rate, t, drum_gain * (0.5 + 0.24 * spec.energy))
                if pos in {4, 12}:
                    self._snare(audio, sample_rate, t, drum_gain * (0.3 + 0.24 * effective_density), rng)
                if effective_density > 0.56 and pos in {7, 15}:
                    self._snare(audio, sample_rate, t, drum_gain * 0.12, rng)
                if pos % 2 == 0 or (effective_density > 0.7 and pos in {1, 5, 9, 13}):
                    self._hat(audio, sample_rate, t, drum_gain * (0.06 + 0.12 * spec.brightness), rng)
            elif pattern == "minimal_techno":
                if pos in {0, 4, 8, 12}:
                    self._kick(audio, sample_rate, t, drum_gain * (0.54 + 0.24 * spec.energy))
                if pos == 8 and spec.energy > 0.35:
                    self._snare(audio, sample_rate, t, drum_gain * (0.18 + 0.18 * effective_density), rng)
                if pos in {2, 6, 10, 14} or effective_density > 0.74:
                    self._hat(audio, sample_rate, t, drum_gain * (0.055 + 0.12 * spec.brightness), rng)
            else:
                if pos in {0, 8} or (pattern == "four_on_floor" and pos in {4, 12}):
                    self._kick(audio, sample_rate, t, drum_gain * (0.5 + 0.22 * spec.energy))
                if pos in {4, 12} and spec.energy > 0.25:
                    self._snare(audio, sample_rate, t, drum_gain * (0.24 + 0.22 * effective_density), rng)
                if effective_density > 0.42 or pos % 4 == 0:
                    self._hat(audio, sample_rate, t, drum_gain * (0.07 + 0.12 * spec.brightness), rng)
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
        noise = self._noise(n, rng)
        env = np.exp(-np.arange(n, dtype=np.float32) / sample_rate * 22.0)
        body = self._osc(180.0, n, sample_rate, "triangle", 0.4) * 0.25
        audio[start_i : start_i + n] += (noise * 0.75 + body) * env * gain

    def _hat(self, audio: np.ndarray, sample_rate: int, start: float, gain: float, rng: random.Random) -> None:
        n = int(0.055 * sample_rate)
        start_i = int(start * sample_rate)
        if start_i >= len(audio):
            return
        n = min(n, len(audio) - start_i)
        noise = self._noise(n, rng)
        env = np.exp(-np.arange(n, dtype=np.float32) / sample_rate * 75.0)
        audio[start_i : start_i + n] += noise * env * gain

    def _blip(self, audio: np.ndarray, sample_rate: int, start: float, freq: float, gain: float) -> None:
        n = int(0.045 * sample_rate)
        start_i = int(start * sample_rate)
        if start_i >= len(audio):
            return
        n = min(n, len(audio) - start_i)
        tone = self._osc(freq, n, sample_rate, "square", 0.8)
        env = np.exp(-np.arange(n, dtype=np.float32) / sample_rate * 62.0)
        audio[start_i : start_i + n] += tone * env * gain

    def _drum_fill(self, audio: np.ndarray, sample_rate: int, start: float, pos: int, pattern: str, gain: float, spec: MusicSpec, rng: random.Random) -> None:
        accent = 0.75 + 0.35 * spec.energy
        if pattern == "8bit_arpeggio_beat":
            self._blip(audio, sample_rate, start, 900.0 + 140.0 * (pos - 12), gain * 0.08 * accent)
            if pos in {12, 15}:
                self._hat(audio, sample_rate, start, gain * 0.08, rng)
            return
        if pos in {12, 14, 15}:
            self._snare(audio, sample_rate, start, gain * (0.16 + 0.16 * spec.density) * accent, rng)
        if pos in {13, 15}:
            self._hat(audio, sample_rate, start, gain * (0.08 + 0.08 * spec.brightness), rng)
        if pattern == "minimal_techno" and pos == 12:
            self._kick(audio, sample_rate, start, gain * 0.42 * accent)

    def _render_bass(self, total_samples: int, sample_rate: int, beat: float, root: int, scale: list[int], spec: MusicSpec, plan: RenderPlan, rng: random.Random, arrangement: list[dict[str, Any]]) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        bass = self._section(plan, "bass")
        pattern = self._choice(bass.get("pattern"), self.BASS_PATTERNS, self._default_bass_pattern(spec))
        engine = self._voice_engine("bass", spec, plan)
        shape = str(engine["shape"])
        brightness = _clamp(spec.brightness + float(engine["brightness"]), 0.0, 1.0)
        gain = self._track_gain(plan, "bass", float(engine["gain"])) * (0.13 + 0.14 * spec.energy)
        attack = float(engine["attack"])
        release = float(engine["release"])
        progression = self._chord_degrees(plan, spec)
        duration_sec = total_samples / sample_rate
        bar = beat * 4.0

        if pattern == "sub_drone":
            bars = int(math.ceil(duration_sec / bar))
            for idx in range(bars):
                start = idx * bar
                segment = self._segment_for_time(start, bar, arrangement)
                segment_gain = self._role_gain(segment, "bass")
                degree = progression[idx % len(progression)]
                midi = self._scale_degree_midi(root, scale, degree, -24)
                self._add_note(audio, sample_rate, start, bar * 0.98, midi, gain * 0.95 * segment_gain, "sine", brightness, attack, release)
                if spec.density > 0.44:
                    fifth = self._scale_degree_midi(root, scale, degree + 4, -24)
                    self._add_note(audio, sample_rate, start + bar * 0.5, bar * 0.46, fifth, gain * 0.35 * segment_gain, "sine", brightness, attack, release)
            return audio

        if pattern == "acid_bass":
            phrase = [0, 0, 2, None, 3, 0, 5, None, 0, 2, 0, 6, 5, None, 3, 2]
            step_dur = beat / 4.0
            steps = int(math.ceil(duration_sec / step_dur))
            for step in range(steps):
                item = phrase[step % len(phrase)]
                if item is None:
                    continue
                bar_idx = int((step * step_dur) // bar)
                segment = self._segment_for_bar(bar_idx, arrangement)
                segment_gain = self._role_gain(segment, "bass")
                degree = progression[bar_idx % len(progression)] + int(item)
                octave = -24 + (12 if step % 8 in {3, 6} else 0)
                midi = self._scale_degree_midi(root, scale, degree, octave)
                self._add_note(audio, sample_rate, step * step_dur, step_dur * 0.7, midi, gain * 0.85 * segment_gain, shape, brightness, attack, release)
            return audio

        if pattern == "syncopated_pulse":
            phrase = [0, None, 0, 2, None, 4, 3, None, 0, 0, None, 5, 4, None, 2, None]
            step_dur = beat / 4.0
            steps = int(math.ceil(duration_sec / step_dur))
            for step in range(steps):
                item = phrase[step % len(phrase)]
                if item is None:
                    continue
                bar_idx = int((step * step_dur) // bar)
                segment = self._segment_for_bar(bar_idx, arrangement)
                segment_gain = self._role_gain(segment, "bass")
                degree = progression[bar_idx % len(progression)] + int(item)
                midi = self._scale_degree_midi(root, scale, degree, -24)
                self._add_note(audio, sample_rate, step * step_dur, step_dur * 0.72, midi, gain * segment_gain, shape, brightness, attack, release)
            return audio

        if pattern == "warm_roots":
            step_dur = beat * 2.0
            steps = int(math.ceil(duration_sec / step_dur))
            for step in range(steps):
                bar_idx = int((step * step_dur) // bar)
                segment = self._segment_for_bar(bar_idx, arrangement)
                segment_gain = self._role_gain(segment, "bass")
                degree = progression[bar_idx % len(progression)]
                if step % 2 == 1 and spec.density > 0.5:
                    degree += 4
                midi = self._scale_degree_midi(root, scale, degree, -24)
                self._add_note(audio, sample_rate, step * step_dur, step_dur * 0.9, midi, gain * 0.9 * segment_gain, shape, brightness, attack, release)
            return audio

        phrase = [0, 0, 7, 0, 5, 0, 4, 7]
        step_dur = beat if spec.energy < 0.6 else beat / 2.0
        steps = int(math.ceil(duration_sec / step_dur))
        for step in range(steps):
            bar_idx = int((step * step_dur) // bar)
            segment = self._segment_for_bar(bar_idx, arrangement)
            segment_gain = self._role_gain(segment, "bass")
            base_degree = progression[bar_idx % len(progression)]
            degree = base_degree + phrase[step % len(phrase)]
            midi = self._scale_degree_midi(root, scale, degree, -24)
            self._add_note(audio, sample_rate, step * step_dur, step_dur * 0.82, midi, gain * segment_gain, shape, brightness, attack, release)
        return audio

    def _render_chords(self, total_samples: int, sample_rate: int, bar: float, root: int, scale: list[int], spec: MusicSpec, plan: RenderPlan, rng: random.Random, arrangement: list[dict[str, Any]]) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        chords = self._section(plan, "chords")
        voicing = self._choice(chords.get("voicing"), {"soft", "open", "stabs", "drone"}, "soft" if spec.brightness < 0.55 else "open")
        engine = self._voice_engine("chords", spec, plan)
        shape = str(engine["shape"])
        brightness = _clamp(spec.brightness + float(engine["brightness"]), 0.0, 1.0)
        gain = self._track_gain(plan, "chords", float(engine["gain"])) * (0.04 + 0.065 * (1.0 - spec.energy))
        attack = float(engine["attack"])
        release = float(engine["release"])
        progression = self._chord_degrees(plan, spec)
        progression_name = self._progression_name(plan, spec)
        beat = bar / 4.0
        bars = int(math.ceil(total_samples / sample_rate / bar))

        for idx in range(bars):
            degree = progression[idx % len(progression)]
            start = idx * bar
            segment = self._segment_for_bar(idx, arrangement)
            segment_gain = self._role_gain(segment, "chords")
            if progression_name == "modal_drone" or voicing == "drone":
                notes = [
                    self._scale_degree_midi(root, scale, degree, -12),
                    self._scale_degree_midi(root, scale, degree + 4, -12),
                    self._scale_degree_midi(root, scale, degree, 0),
                ]
                dur = bar * 0.98
                offsets = [0.0, 0.03, 0.06]
            elif voicing == "open":
                notes = [
                    self._scale_degree_midi(root, scale, degree, -12),
                    self._scale_degree_midi(root, scale, degree + 4, -12),
                    self._scale_degree_midi(root, scale, degree + 2, 0),
                    self._scale_degree_midi(root, scale, degree + 6, 0),
                ]
                dur = bar * 0.88
                offsets = [0.0, 0.018, 0.036, 0.054]
            else:
                notes = [
                    self._scale_degree_midi(root, scale, degree, -12),
                    self._scale_degree_midi(root, scale, degree + 2, -12),
                    self._scale_degree_midi(root, scale, degree + 4, -12),
                ]
                dur = beat * 0.72 if voicing == "stabs" else bar * 0.9
                offsets = [0.0, 0.015, 0.03]

            if voicing == "stabs":
                for stab in (0.0, beat * 1.5, beat * 2.5):
                    for offset, midi in zip(offsets, notes):
                        self._add_note(audio, sample_rate, start + stab + offset, dur, midi, gain * 0.9 * segment_gain, shape, brightness, attack, release)
            else:
                for offset, midi in zip(offsets, notes):
                    self._add_note(audio, sample_rate, start + offset, dur, midi, gain * segment_gain, shape, brightness, attack, release)
        return audio

    def _melody_phrase(self, shape_name: str, rng: random.Random) -> list[int | None]:
        if shape_name == "arpeggio":
            return [0, 2, 4, 7, 4, 2, 0, None, 2, 4, 5, 4, 2, None, 0, 2]
        if shape_name == "call_response":
            return [0, 2, 4, None, 2, 0, None, None, 4, 5, 4, 2, None, 1, 0, None]
        if shape_name == "pentatonic":
            return [0, None, 1, 2, None, 4, 2, None, 5, 4, None, 2, 1, None, 0, None]
        if shape_name == "stepwise":
            return [0, None, 1, None, 2, None, 1, None, 0, None, -1, None, 0, None, 1, None]
        if shape_name == "random_walk":
            position = rng.choice([0, 1, 2, 4])
            phrase: list[int | None] = []
            for idx in range(32):
                if idx % 4 == 3 and rng.random() < 0.55:
                    phrase.append(None)
                    continue
                position = max(-1, min(6, position + rng.choice([-1, 0, 1])))
                phrase.append(position)
            return phrase
        return [0, 2, 4, 2, None, 5, 4, 2, 1, None, 2, 4, 6, 5, 4, None]

    def _render_melody(self, total_samples: int, sample_rate: int, beat: float, root: int, scale: list[int], spec: MusicSpec, plan: RenderPlan, rng: random.Random, arrangement: list[dict[str, Any]]) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        melody = self._section(plan, "melody")
        shape_name = self._choice(melody.get("shape"), self.MELODY_SHAPES, self._default_melody_shape(spec))
        if shape_name == "periodic":
            shape_name = "motif_variation"
        activity = self._float_value(melody.get("activity", spec.density), spec.density, 0.0, 1.0)
        register = self._choice(melody.get("register"), {"low", "mid", "high"}, "high" if spec.brightness > 0.65 else "mid")
        register_octave = {"low": -12, "mid": 0, "high": 12}[register]
        if "ambient" in spec.mood.lower():
            register_octave += 12
        engine = self._voice_engine("melody", spec, plan)
        shape = str(engine["shape"])
        brightness = _clamp(spec.brightness + float(engine["brightness"]), 0.0, 1.0)
        gain = self._track_gain(plan, "melody", float(engine["gain"])) * (0.055 + 0.095 * spec.energy)
        attack = float(engine["attack"])
        release = float(engine["release"])
        progression = self._chord_degrees(plan, spec)
        phrase = self._melody_phrase(shape_name, rng)
        step_dur = beat / 4.0 if shape_name == "arpeggio" else beat / 2.0
        duration_sec = total_samples / sample_rate
        bar = beat * 4.0
        steps = int(math.ceil(duration_sec / step_dur))

        for step in range(steps):
            phrase_item = phrase[step % len(phrase)]
            if phrase_item is None:
                continue
            phrase_pos = step % len(phrase)
            bar_idx = int((step * step_dur) // bar)
            segment = self._segment_for_bar(bar_idx, arrangement)
            effective_activity = _clamp(activity + self._density_shift(segment), 0.0, 1.0)
            if effective_activity < 0.42 and phrase_pos not in {0, 4, 8, 12}:
                continue
            if effective_activity < 0.62 and phrase_pos % 2 == 1:
                continue
            chord_degree = progression[bar_idx % len(progression)]
            if shape_name in {"arpeggio", "call_response"}:
                degree = chord_degree + int(phrase_item)
            elif shape_name == "motif_variation" and (bar_idx % 2 == 1):
                degree = int(phrase_item) + 1
            else:
                degree = int(phrase_item)
            degree += int(segment.get("melody_shift", 0))
            midi = self._scale_degree_midi(root, scale, degree, register_octave)
            dur = step_dur * (0.58 if shape in {"square", "pulse", "saw"} else 0.82)
            if shape_name == "stepwise":
                dur = step_dur * 1.35
            self._add_note(audio, sample_rate, step * step_dur, dur, midi, gain * self._role_gain(segment, "melody"), shape, brightness, attack, release)
        return audio

    def _render_texture(self, total_samples: int, sample_rate: int, spec: MusicSpec, plan: RenderPlan, rng: random.Random) -> np.ndarray:
        audio = np.zeros(total_samples, dtype=np.float32)
        texture = self._section(plan, "texture")
        noise_mode = str(texture.get("noise", "") or "").lower()
        style_text = self._style_text(spec)
        texture_gain = self._track_gain(plan, "texture", 1.0)
        if not noise_mode:
            noise_mode = "vinyl" if "lofi" in style_text else ("space" if "ambient" in style_text else "air")
        if noise_mode == "none":
            return audio

        if noise_mode in {"vinyl", "tape"} or "vinyl_noise" in style_text:
            noise = self._noise(total_samples, rng)
            amount = 0.055 if noise_mode == "tape" else 0.08
            audio += self._one_pole_lowpass(noise, amount) * (0.018 if noise_mode == "tape" else 0.024) * texture_gain
            t = np.arange(total_samples, dtype=np.float32) / sample_rate
            wobble = np.sin(2.0 * np.pi * 0.21 * t).astype(np.float32)
            audio += wobble * 0.004 * texture_gain
        elif noise_mode in {"air", "space"} or "noise_texture" in style_text:
            t = np.arange(total_samples, dtype=np.float32) / sample_rate
            slow = (np.sin(2.0 * np.pi * 0.07 * t) * 0.5 + 0.5).astype(np.float32)
            audio += slow * (0.012 if noise_mode == "air" else 0.018) * texture_gain
            if noise_mode == "space":
                shimmer = np.sin(2.0 * np.pi * 880.0 * t + np.sin(2.0 * np.pi * 0.09 * t)).astype(np.float32)
                audio += shimmer * slow * 0.006 * texture_gain
        return audio

    def _apply_effects(self, audio: np.ndarray, sample_rate: int, spec: MusicSpec, plan: RenderPlan) -> np.ndarray:
        effects = self._section(plan, "effects")
        if effects.get("lowpass") or "lowpass" in spec.effects or spec.brightness < 0.5:
            audio = self._one_pole_lowpass(audio, 0.08 + spec.brightness * 0.18)
        if effects.get("delay") or "small_delay" in spec.effects or "gentle_delay" in spec.effects:
            delay = int((0.18 if spec.energy > 0.55 else 0.28) * sample_rate)
            feedback = 0.26 if spec.loopable else 0.18
            delayed = np.zeros_like(audio)
            if delay < len(audio):
                delayed[delay:] = audio[:-delay] * feedback
                if spec.loopable:
                    delayed[:delay] += audio[-delay:] * feedback * 0.65
            audio = audio + delayed
        if effects.get("reverb") or "wide_reverb" in spec.effects:
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
        if n <= 8:
            return audio
        candidates = []
        for fade_sec in (0.25, 0.45, 0.7, 1.0):
            fade = min(int(fade_sec * sample_rate), n // 6)
            if fade <= 4:
                continue
            candidate = self._crossfade_loop(audio.copy(), fade, sample_rate)
            candidates.append((self._loop_boundary_score(candidate, sample_rate), candidate))
        if not candidates:
            return audio
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _crossfade_loop(self, audio: np.ndarray, fade: int, sample_rate: int) -> np.ndarray:
        head = audio[:fade].copy()
        tail = audio[-fade:].copy()
        curve = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        curve = curve * curve * (3.0 - 2.0 * curve)
        blend = tail * (1.0 - curve) + head * curve
        audio[:fade] = blend
        audio[-fade:] = blend
        micro = min(int(0.03 * sample_rate), len(audio) // 16)
        if micro > 4:
            curve_in = np.linspace(0.0, 1.0, micro, dtype=np.float32)
            curve_out = np.linspace(1.0, 0.0, micro, dtype=np.float32)
            audio[:micro] *= curve_in * curve_in
            audio[-micro:] *= curve_out * curve_out
        return audio

    def _loop_boundary_score(self, audio: np.ndarray, sample_rate: int) -> float:
        window = min(int(0.08 * sample_rate), len(audio) // 10)
        if window <= 4:
            return 0.0
        head = audio[:window]
        tail = audio[-window:]
        diff = float(np.sqrt(np.mean((head - tail) ** 2)))
        level = float(np.sqrt(np.mean(head**2) + np.mean(tail**2))) + 1e-6
        endpoint = abs(float(audio[0]) - float(audio[-1]))
        return diff / level + endpoint

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
    "0.2.0",
    repo="https://github.com/blueraina/astrbot_plugin_pymusic",
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
                brief, spec, plan, wav_path = await self._generate(prompt, event, duration, loopable, send_mode)
            except Exception as exc:
                logger.exception("[pymusic] generation failed")
                return f"pymusic failed to generate music: {exc}"

        try:
            sent_mode = await self._send_music(event, wav_path, spec)
        except Exception as exc:
            logger.exception("[pymusic] send failed")
            return f"Generated a {spec.duration}s {spec.mood} WAV music clip, but sending failed: {exc}"
        self._cleanup_history()
        return f"Generated a {spec.duration}s {spec.mood} WAV music clip and sent it as {sent_mode}. Enriched prompt: {brief.enriched_prompt}"

    async def _handle_command(self, event: AstrMessageEvent) -> Any:
        if not self._is_supported_platform(event):
            yield event.chain_result([Plain("pymusic 目前只支持 QQ 个人号适配器和 QQ 官方机器人。")])
            return

        payload = self._message_text(event).strip()
        requested_duration, prompt = _parse_command_payload(payload)
        if requested_duration is None or not prompt:
            yield event.chain_result([Plain("用法：/pymusic 时间(秒) 提示词\n例如：/pymusic 20 8bit 夜晚城市 可循环")])
            return

        cooldown_key = self._cooldown_key(event)
        wait = self.rate_limiter.check(cooldown_key)
        if wait > 0:
            yield event.chain_result([Plain(f"pymusic 冷却中，请 {wait} 秒后再试。")])
            return

        yield event.chain_result([Plain("正在用纯 Python 合成音乐，请稍等。")])

        overrides = _prompt_overrides(prompt)
        duration = _safe_int(requested_duration, self._default_duration(), 5, self._max_duration())
        loopable = self._waveform_loopable() if overrides["loopable"] is None else bool(overrides["loopable"])
        send_mode = _normalize_send_mode(overrides["send_mode"], self._default_send_mode())

        async with self.render_sem:
            try:
                brief, spec, plan, wav_path = await self._generate(prompt, event, duration, loopable, send_mode)
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

        yield event.chain_result([Plain(f"pymusic 已生成：{spec.mood} / {spec.duration}s / {sent_mode}\n理解为：{brief.enriched_prompt[:180]}")])
        self._cleanup_history()

    async def _generate(self, prompt: str, event: AstrMessageEvent, duration: int, loopable: bool, send_mode: str) -> tuple[PromptBrief, MusicSpec, RenderPlan, Path]:
        brief = await self._build_prompt_brief(prompt, event, duration, loopable, send_mode)
        spec = await self._build_spec(brief, event, duration, loopable, send_mode)
        if spec.duration > VOICE_MAX_SECONDS and spec.send_mode in {"voice", "auto"}:
            spec.send_mode = "file"
        plan = _default_plan(spec, self._diversity_level())
        wav_path = self.data_dir / f"pymusic_{int(time.time())}_{random.randint(1000, 9999)}.wav"
        sample_rate = self._sample_rate()
        ai_code = await self._build_python_renderer_code(brief, event, spec)
        if ai_code:
            try:
                await asyncio.to_thread(self._run_ai_python_renderer, ai_code, wav_path, spec.duration, sample_rate, spec.loopable)
            except Exception as exc:
                logger.warning(f"[pymusic] AI Python renderer failed, using fixed renderer fallback: {exc}")
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            plan = await self._build_plan(brief, event, spec)
            await asyncio.to_thread(self.renderer.render, spec, plan, wav_path, sample_rate, self._diversity_level())
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError("WAV 文件没有成功生成")
        return brief, spec, plan, wav_path

    async def _build_prompt_brief(self, prompt: str, event: AstrMessageEvent, duration: int, loopable: bool, send_mode: str) -> PromptBrief:
        fallback = _fallback_brief(prompt)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You are a music prompt producer for a deterministic pure-Python synthesizer. "
            "Rewrite short or vague user input into one professional, concrete music brief. "
            "Do not include markdown. Do not write Python. Return one strict JSON object. "
            "Fields: enriched_prompt string, style string, scene string, musical_intent string, "
            "references array of short strings, avoid array of short strings. "
            "The enriched_prompt should describe mood, genre, tempo feel, instruments, rhythm, harmony, melody, texture, effects, and mix. "
            "Use only electronic, 8bit, ambient, lofi, or nearby pure-synth styles. Avoid vocals and copyrighted artist imitation."
        )
        user_prompt = (
            f"Original user prompt: {prompt}\n"
            f"Defaults: duration={duration}, loopable={loopable}, send_mode={send_mode}. "
            f"Max duration={self._max_duration()}. Diversity level={self._diversity_level()} where 0=stable, 1=balanced, 2=bold.\n"
            "Make sparse input sound intentional and musical."
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _brief_from_dict(data, prompt, fallback)
        except Exception as exc:
            logger.warning(f"[pymusic] prompt enrichment failed, using fallback: {exc}")
        return fallback

    async def _build_spec(self, brief: PromptBrief, event: AstrMessageEvent, duration: int, loopable: bool, send_mode: str) -> MusicSpec:
        fallback = _fallback_spec(f"{brief.original_prompt}\n{brief.enriched_prompt}", duration, self._max_duration(), send_mode, loopable)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You convert an enriched music brief into one strict JSON object named MusicSpec. "
            "Do not include markdown. Do not write Python. "
            "Allowed styles are electronic, 8bit, ambient, and lofi. "
            "Fields: mood string, energy number 0..1, brightness number 0..1, density number 0..1, "
            "bpm integer 55..180, key string, instruments array of strings, effects array of strings, "
            "duration integer seconds, loopable boolean, send_mode string voice/file/auto."
        )
        user_prompt = (
            f"Original prompt: {brief.original_prompt}\n"
            f"Enriched prompt: {brief.enriched_prompt}\n"
            f"Style: {brief.style}\n"
            f"Scene: {brief.scene}\n"
            f"Defaults: duration={duration}, loopable={loopable}, send_mode={send_mode}. "
            f"Max duration={self._max_duration()}."
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _spec_from_dict(data, fallback, self._max_duration())
        except Exception as exc:
            logger.warning(f"[pymusic] MusicSpec LLM planning failed, using fallback: {exc}")
        return fallback

    async def _build_python_renderer_code(self, brief: PromptBrief, event: AstrMessageEvent, spec: MusicSpec) -> str | None:
        provider = self._get_music_provider(event)
        if provider is None:
            return None
        system_prompt = (
            "You write pure Python DSP code for a sandboxed music renderer. "
            "Return Python code only, no markdown, no explanation. "
            "The code must define exactly one callable: render(duration, sample_rate, loopable). "
            "render must return a one-dimensional numpy array of float audio samples in -1..1. "
            "Allowed imports: numpy as np, math, random. Do not read or write files. Do not use os, sys, subprocess, pathlib, sockets, network, eval, exec, open, or __import__. "
            "Use numpy synthesis: oscillators, envelopes, drums, bass, chords, melody, pads, noise, delay/reverb if useful. "
            "The music should be complete and musical, with a distinctive melody derived from the brief, not a fixed stock phrase. "
            "For loopable=True, make phrase lengths periodic and avoid one-shot intros/outros."
        )
        user_prompt = json.dumps(
            {
                "original_prompt": brief.original_prompt,
                "enriched_prompt": brief.enriched_prompt,
                "style": brief.style,
                "scene": brief.scene,
                "musical_intent": brief.musical_intent,
                "music_spec": spec.__dict__,
                "requirements": {
                    "duration": spec.duration,
                    "sample_rate_runtime_argument": True,
                    "loopable": spec.loopable,
                    "mono_float_array": True,
                    "pure_python_numpy": True,
                },
            },
            ensure_ascii=False,
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            code = _extract_python_code(getattr(response, "completion_text", "") or str(response))
            _validate_generated_python(code)
            return code
        except Exception as exc:
            logger.warning(f"[pymusic] AI Python code generation failed, using fixed renderer fallback: {exc}")
            return None

    def _run_ai_python_renderer(self, code: str, wav_path: Path, duration: int, sample_rate: int, loopable: bool) -> None:
        _validate_generated_python(code)
        code_path = self.data_dir / f"pymusic_code_{int(time.time())}_{random.randint(1000, 9999)}.py"
        code_path.write_text(code, encoding="utf-8")
        runner = r'''
import math
import random
import sys
import wave
from pathlib import Path

import numpy as np

code_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
duration = int(sys.argv[3])
sample_rate = int(sys.argv[4])
loopable = sys.argv[5] == "1"
source = code_path.read_text(encoding="utf-8")

allowed_modules = {"numpy": np, "math": math, "random": random}
blocked_numpy_parts = {"ctypeslib", "lib", "testing", "distutils", "f2py"}

def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    parts = set(name.split("."))
    if level != 0 or root not in allowed_modules or parts & blocked_numpy_parts:
        raise ImportError(f"import not allowed: {name}")
    if root == "numpy":
        return __import__(name, globals, locals, fromlist, level)
    if root == "math":
        return math
    if root == "random":
        return random
    raise ImportError(f"import not allowed: {name}")

safe_builtins = {
    "__import__": safe_import,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "range": range,
    "int": int,
    "float": float,
    "bool": bool,
    "round": round,
    "pow": pow,
    "enumerate": enumerate,
    "zip": zip,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
}
env = {"__builtins__": safe_builtins, "np": np, "math": math, "random": random}
exec(compile(source, str(code_path), "exec"), env)
render = env.get("render")
if not callable(render):
    raise RuntimeError("render function missing")
audio = render(duration, sample_rate, loopable)
audio = np.asarray(audio, dtype=np.float32).reshape(-1)
target = max(1, int(duration * sample_rate))
if len(audio) < target:
    audio = np.pad(audio, (0, target - len(audio)))
elif len(audio) > target:
    audio = audio[:target]
audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
if peak > 1.0:
    audio = audio / peak * 0.95
pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
out_path.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(out_path), "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sample_rate)
    wf.writeframes(pcm16.tobytes())
'''
        timeout = max(20, min(180, int(duration / 2) + 20))
        try:
            result = subprocess.run(
                [sys.executable, "-c", runner, str(code_path), str(wav_path), str(duration), str(sample_rate), "1" if loopable else "0"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown AI renderer error").strip()[-800:]
                raise RuntimeError(detail)
        finally:
            code_path.unlink(missing_ok=True)

    async def _build_plan(self, brief: PromptBrief, event: AstrMessageEvent, spec: MusicSpec) -> RenderPlan:
        diversity_level = self._diversity_level()
        fallback = _default_plan(spec, diversity_level)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You convert a MusicSpec into one strict JSON object named RenderPlan for a fixed Python renderer. "
            "Do not include markdown. Do not write Python. "
            "Fields: tracks array, drums object, bass object, chords object, melody object, texture object, effects object, master object. "
            "Use only these renderer choices when possible. "
            "tracks: objects with role melody/chords/bass/drums/texture, name chip_lead/warm_keys/pluck/pad/bell/acid_bass/sub_drone/warm_bass/lofi_drums/noise_texture, optional gain 0.1..1.5. "
            "drums.pattern: lofi_swing, 8bit_arpeggio_beat, ambient_no_drums, breakbeat, minimal_techno. "
            "bass.pattern: root_octave, warm_roots, acid_bass, sub_drone, syncopated_pulse. "
            "chords.progression: i-VI-III-VII, i-iv-V-i, I-V-vi-IV, ii-V-I, modal_drone; chords.voicing: soft/open/stabs/drone. "
            "melody.shape: arpeggio, call_response, pentatonic, stepwise, random_walk, motif_variation; melody.register: low/mid/high. "
            "texture.noise: vinyl/tape/air/space/none. effects: delay/reverb/lowpass booleans. master.target_peak 0.3..0.98. "
            "For diversity_level 0 choose stable conventional patterns; for 1 use balanced variation; for 2 choose bolder patterns, fills, and melody shapes. "
            "Keep values simple and renderer-friendly."
        )
        try:
            plan_input = {
                "original_prompt": brief.original_prompt,
                "enriched_prompt": brief.enriched_prompt,
                "music_spec": spec.__dict__,
                "diversity_level": diversity_level,
            }
            response = await self._provider_text_chat(provider, json.dumps(plan_input, ensure_ascii=False), system_prompt)
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
                await asyncio.wait_for(
                    event.send(MessageChain([Record.fromFileSystem(str(wav_path))])),
                    timeout=VOICE_SEND_TIMEOUT_SECONDS,
                )
                return "voice"
            except Exception as exc:
                logger.warning(f"[pymusic] voice send failed: {exc}")
                if mode == "voice":
                    raise

        await asyncio.wait_for(
            event.send(MessageChain([File(name=wav_path.name, file=str(wav_path))])),
            timeout=FILE_SEND_TIMEOUT_SECONDS,
        )
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
                return _strip_command_prefix(value)
        try:
            return _strip_command_prefix(event.get_message_str())
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

    def _diversity_level(self) -> int:
        return _normalize_diversity_level(_cfg_get(self.config, "diversity_level", DEFAULT_DIVERSITY_LEVEL))

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

    async def _provider_text_chat(self, provider: Any, prompt: str, system_prompt: str) -> Any:
        return await asyncio.wait_for(
            provider.text_chat(prompt=prompt, system_prompt=system_prompt),
            timeout=self._model_call_timeout(),
        )

    def _sample_rate(self) -> int:
        return _safe_int(_cfg_get(self.config, "sample_rate", DEFAULT_SAMPLE_RATE), DEFAULT_SAMPLE_RATE, 16000, 48000)

    def _model_call_timeout(self) -> int:
        try:
            value = int(_cfg_get(self.config, "model_call_timeout_sec", DEFAULT_MODEL_CALL_TIMEOUT_SECONDS))
        except Exception:
            value = DEFAULT_MODEL_CALL_TIMEOUT_SECONDS
        return max(1, value)

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
