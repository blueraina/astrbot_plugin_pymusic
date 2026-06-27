from __future__ import annotations

import ast
import asyncio
import json
import math
import random
import re
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import musicpy as mp
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
DEFAULT_VARIATION_STRENGTH = 1
MAX_RANDOM_SEED = 2**32 - 1
VOICE_SEND_TIMEOUT_SECONDS = 8
FILE_SEND_TIMEOUT_SECONDS = 20

TECHNIQUE_GUIDES: tuple[dict[str, Any], ...] = (
    {
        "id": "arrangement_motifs",
        "category": "structure",
        "keywords": ["melody", "motif", "theme", "variation", "旋律", "动机", "主题", "变奏"],
        "summary": "general arrangement grammar",
        "basis": "common composition practice",
        "guide": (
            "Create 2-3 original motifs, an A/B or call-response shape, phrase-level variation every 4 or 8 bars, "
            "and avoid looping one short note list unchanged for the whole piece."
        ),
    },
    {
        "id": "micro_arrangement",
        "category": "structure",
        "keywords": ["30", "short", "mini", "drop", "过门", "短", "推进", "爆发"],
        "summary": "mini electronic arrangement for short durations",
        "basis": "electronic arrangement practice",
        "guide": (
            "For 15-35 second renders, still create a tiny form: identity hook, short build, mini drop/release, "
            "and a clean ending or loop seam."
        ),
    },
    {
        "id": "call_response_theme",
        "category": "composition",
        "keywords": ["call", "response", "answer", "hook", "问答", "回答", "主题", "副旋律"],
        "summary": "melodic call-response and related B phrase",
        "basis": "common melodic development",
        "guide": (
            "Write a call phrase and an answer phrase that share interval DNA but differ in contour, rhythm, or register; "
            "make the B section a developed relative, not a totally new loop."
        ),
    },
    {
        "id": "chord_tone_targeting",
        "category": "composition",
        "keywords": ["harmony", "chord", "strong beat", "和弦", "强拍", "和声"],
        "summary": "strong beats land on chord tones",
        "basis": "tonal melodic writing",
        "guide": (
            "Place strong-beat melody notes on root/third/fifth/seventh chord tones and use passing/neighbor tones "
            "on weak subdivisions for more intentional phrasing."
        ),
    },
    {
        "id": "bass_kick_lock",
        "category": "composition",
        "keywords": ["bass", "kick", "groove", "贝斯", "底鼓", "律动"],
        "summary": "bassline interacts with kick and harmony",
        "basis": "electronic dance production practice",
        "guide": (
            "Coordinate bass notes with kick spaces: leave room for the kick transient, answer it with offbeat notes, "
            "use roots/fifths/octaves plus occasional approach tones and accents."
        ),
    },
    {
        "id": "sidechain_ducking",
        "category": "effect",
        "keywords": ["sidechain", "duck", "pump", "house", "techno", "侧链", "抽吸", "泵感"],
        "summary": "kick-triggered gain ducking",
        "basis": "electronic mixing practice",
        "guide": (
            "Duck pads, bass sustain, and effects after kicks to create movement and make drums sit clearly; "
            "keep the amount style-dependent."
        ),
    },
    {
        "id": "riser_downlifter",
        "category": "effect",
        "keywords": ["riser", "drop", "transition", "build", "上升", "下坠", "过门", "爆发"],
        "summary": "transition FX for build and release",
        "basis": "electronic production practice",
        "guide": (
            "Use filtered noise risers, downlifters, reverse-like sweeps, short fills, and delay throws before section changes."
        ),
    },
    {
        "id": "ambient_pad",
        "category": "style",
        "keywords": ["ambient", "pad", "drone", "space", "cold", "winter", "星空", "宇宙", "寒冬", "冰", "空灵", "氛围"],
        "summary": "slow ambient pad, drone, and space",
        "basis": "ambient synthesis practice",
        "guide": (
            "Use long pads or drones, sparse bell/pluck notes, slow filter or amplitude motion, airy noise texture, "
            "wide reverb/delay, and gradual harmonic color changes."
        ),
    },
    {
        "id": "ambient_techno",
        "category": "style",
        "keywords": ["ambient techno", "dub techno", "氛围科技", "极简", "深空"],
        "summary": "atmospheric techno pulse with evolving pads",
        "basis": "ambient techno production practice",
        "guide": (
            "Combine soft four-on-floor or broken pulses with evolving pads, filtered chord stabs, dubby delay, and restrained bass movement."
        ),
    },
    {
        "id": "8bit_chiptune",
        "category": "style",
        "keywords": ["8bit", "chiptune", "chip", "pixel", "game", "arcade", "像素", "游戏", "复古"],
        "summary": "chip lead, pulse bass, counter melody, and noise drums",
        "basis": "chiptune synthesis practice",
        "guide": (
            "Use square/pulse/triangle oscillators, pingpong arps, a related counter melody, noise kick/snare/hat, "
            "short envelopes, and playful register changes without harsh constant high notes."
        ),
    },
    {
        "id": "lofi_hiphop",
        "category": "style",
        "keywords": ["lofi", "chill", "rain", "cafe", "study", "雨", "咖啡", "学习", "放松", "夜晚"],
        "summary": "warm lofi keys and swung drums",
        "basis": "lofi beatmaking practice",
        "guide": (
            "Use warm keys or soft plucks, 7th/9th-flavored harmony, lazy swung drums, mellow bass, vinyl/tape noise, "
            "lowpass color, and small timing/level imperfections."
        ),
    },
    {
        "id": "melodic_techno",
        "category": "style",
        "keywords": ["melodic techno", "techno", "progressive", "night", "dark", "旋律科技", "夜店", "黑暗", "地下"],
        "summary": "minor hook, rolling bass, hypnotic drums, and automation",
        "basis": "melodic techno production practice",
        "guide": (
            "Use a memorable minor hook, rolling offbeat bass, syncopated hats, filter automation, a short build, and a clear drop/release."
        ),
    },
    {
        "id": "synthwave",
        "category": "style",
        "keywords": ["synthwave", "retro", "neon", "80s", "霓虹", "复古未来", "赛博"],
        "summary": "retro electronic drums, warm pads, and heroic lead",
        "basis": "synthwave production practice",
        "guide": (
            "Use warm analog-like pads, octave bass, gated or roomy drums, chorus-like detune, and a singable neon lead motif."
        ),
    },
    {
        "id": "trance",
        "category": "style",
        "keywords": ["trance", "uplift", "rave", "anthem", "迷幻", "锐舞", "高能"],
        "summary": "arpeggiated harmony, supersaw-like lead, and build/drop",
        "basis": "trance production practice",
        "guide": (
            "Use arpeggiated chord tones, a bright but controlled lead, tension risers, snare/hat density ramps, and euphoric release."
        ),
    },
    {
        "id": "house_groove",
        "category": "style",
        "keywords": ["house", "club", "dance", "deep", "funky", "浩室", "舞曲"],
        "summary": "four-on-floor groove with offbeat bass and chord stabs",
        "basis": "house production practice",
        "guide": (
            "Use four-on-floor kick, offbeat hats, syncopated bass, short chord stabs, call-response hook, and moderate sidechain."
        ),
    },
    {
        "id": "breakbeat",
        "category": "style",
        "keywords": ["breakbeat", "breaks", "drum", "idm", "碎拍", "鼓组", "故障"],
        "summary": "broken drums, ghost notes, and glitchy fills",
        "basis": "breakbeat production practice",
        "guide": (
            "Use broken kick/snare placement, ghost hats, syncopated bass, short stutters before phrase ends, and call-response lead fragments."
        ),
    },
    {
        "id": "acid_bass",
        "category": "style",
        "keywords": ["acid", "cyber", "techno", "rave", "dark", "赛博", "地下", "紧张", "黑暗"],
        "summary": "acid/electro bass movement",
        "basis": "acid/electro synthesis practice",
        "guide": (
            "Use a saw or square bassline with accents, slides or octave jumps, resonant-filter-like brightness motion, "
            "four-on-floor or broken electronic drums, and evolving automation."
        ),
    },
    {
        "id": "euclidean_drums",
        "category": "composition",
        "keywords": ["drum", "rhythm", "groove", "beat", "techno", "hat", "kick", "鼓", "节奏", "律动", "电子"],
        "summary": "Euclidean rhythm for lively drum placement",
        "basis": "Toussaint 2005",
        "guide": (
            "Distribute k hits across n steps with a Euclidean/Bjorklund-like pattern for hats, claps, ghost kicks, "
            "or percussion, then add accents and small rests so the beat is structured but not rigid."
        ),
    },
    {
        "id": "markov_melody",
        "category": "composition",
        "keywords": ["melody", "continuation", "answer", "phrase", "旋律", "接话", "回答句", "延续"],
        "summary": "Markov-style phrase continuation",
        "basis": "Pachet 2003",
        "guide": (
            "Generate a first motif, then choose following notes from small transition rules based on the previous degree or interval "
            "so the answer phrase feels related without copying the same loop."
        ),
    },
    {
        "id": "motif_recombine",
        "category": "composition",
        "keywords": ["motif", "variation", "recombine", "oracle", "变奏", "重组", "发展", "动机"],
        "summary": "motif recombination and controlled reuse",
        "basis": "Variable Markov Oracle practice",
        "guide": (
            "Create short motif cells, then recombine, invert, transpose, stretch, or truncate them for B phrases and fills "
            "so repetition has memory and development."
        ),
    },
    {
        "id": "lsystem_phrase",
        "category": "composition",
        "keywords": ["long", "evolve", "fractal", "growth", "develop", "长", "发展", "推进", "生成"],
        "summary": "rule-expanded phrase growth",
        "basis": "Prusinkiewicz 1986",
        "guide": (
            "Start from a compact symbol phrase and apply 1-3 simple rewrite rules to create longer note or rhythm sequences, "
            "then map symbols to scale degrees, rests, or register shifts."
        ),
    },
    {
        "id": "pingpong_arpeggio",
        "category": "composition",
        "keywords": ["arp", "arpeggio", "8bit", "chiptune", "game", "琶音", "像素", "游戏"],
        "summary": "forward-backward arpeggio motion",
        "basis": "algorithmic pattern practice",
        "guide": (
            "Walk chord tones forward then backward, rotate the start point between bars, and answer the lead or bass rhythm "
            "so arpeggios feel intentional instead of mechanical."
        ),
    },
    {
        "id": "random_walk_melody",
        "category": "composition",
        "keywords": ["random", "walk", "wandering", "ambient", "lofi", "游走", "随机", "氛围"],
        "summary": "bounded random-walk melodic motion",
        "basis": "algorithmic composition practice",
        "guide": (
            "Move through a scale with mostly stepwise intervals, occasional leaps, and reflection at register bounds; anchor important beats on chord tones."
        ),
    },
    {
        "id": "stutter_pattern",
        "category": "composition",
        "keywords": ["stutter", "glitch", "idm", "fill", "break", "卡顿", "故障", "过门"],
        "summary": "short repeats and glitch fills",
        "basis": "electronic pattern practice",
        "guide": (
            "Repeat tiny note or drum fragments near phrase ends, with quick gates or rests, to create fills without making the main groove chaotic."
        ),
    },
    {
        "id": "phase_pattern",
        "category": "composition",
        "keywords": ["phase", "minimal", "techno", "pulse", "minimalism", "相位", "极简", "脉冲"],
        "summary": "slowly shifting repeated pattern",
        "basis": "minimal/process music practice",
        "guide": (
            "Layer two related patterns of slightly different lengths or accents, then let their alignment drift over bars for evolving minimal electronic motion."
        ),
    },
    {
        "id": "fm_synthesis",
        "category": "synthesis",
        "keywords": ["bell", "crystal", "ice", "magic", "star", "铃", "铃声", "冰晶", "魔法", "星"],
        "summary": "FM bell, bass, and metallic tone color",
        "basis": "Chowning 1973",
        "guide": (
            "Use carrier plus modulator oscillators, with an envelope on modulation index, for glassy bells, icy plucks, metallic hits, or expressive FM bass timbre."
        ),
    },
    {
        "id": "noise_texture",
        "category": "synthesis",
        "keywords": ["noise", "wind", "rain", "tape", "vinyl", "风", "雨", "噪声", "磁带"],
        "summary": "controlled noise and texture layer",
        "basis": "computer music noise synthesis",
        "guide": (
            "Use filtered noise for wind, rain, vinyl, tape hiss, risers, or soft percussion, with envelopes and volume automation so texture supports the music."
        ),
    },
    {
        "id": "karplus_pluck",
        "category": "synthesis",
        "keywords": ["pluck", "guitar", "harp", "string", "拨弦", "吉他", "竖琴", "弦"],
        "summary": "pluck or string-like synthesis",
        "basis": "Karplus-Strong 1983",
        "guide": (
            "Use short noise bursts, decaying resonant or comb-like tones, or bright pluck envelopes for guitar/harp-like parts, then vary pitch and decay across phrases."
        ),
    },
    {
        "id": "bandlimited_saw",
        "category": "synthesis",
        "keywords": ["saw", "supersaw", "lead", "acid", "bright", "锯齿", "明亮", "贝斯"],
        "summary": "less harsh saw-like waveform",
        "basis": "Stilson and Smith 1996",
        "guide": (
            "Approximate a band-limited saw by summing only harmonics below Nyquist or by softening a naive saw with lowpass-style shaping."
        ),
    },
    {
        "id": "bandlimited_square",
        "category": "synthesis",
        "keywords": ["square", "pulse", "pwm", "8bit", "chiptune", "方波", "脉冲", "像素"],
        "summary": "less harsh square or pulse waveform",
        "basis": "Stilson and Smith 1996",
        "guide": (
            "Approximate square or pulse waves with odd harmonics below Nyquist, optional PWM motion, and short envelopes so chip leads stay bright without excessive aliasing."
        ),
    },
    {
        "id": "additive_pad",
        "category": "synthesis",
        "keywords": ["pad", "warm", "ambient", "choir", "soft", "铺底", "温暖", "氛围"],
        "summary": "layered additive pad partials",
        "basis": "additive synthesis practice",
        "guide": (
            "Combine several low-amplitude partials, slow detune/LFO movement, and long envelopes for a pad that evolves without needing samples."
        ),
    },
    {
        "id": "subtractive_bass",
        "category": "synthesis",
        "keywords": ["bass", "sub", "acid", "filter", "techno", "贝斯", "低频", "滤波"],
        "summary": "subtractive bass with envelope-shaped brightness",
        "basis": "subtractive synthesis practice",
        "guide": (
            "Start with saw/square/triangle bass, then simulate filter envelope by changing harmonic mix or lowpass-like brightness over each note."
        ),
    },
    {
        "id": "noise_drum_resonator",
        "category": "synthesis",
        "keywords": ["drum", "snare", "hat", "kick", "percussion", "鼓", "军鼓", "镲", "打击"],
        "summary": "noise-based synthetic percussion",
        "basis": "Karplus-Strong/noise percussion practice",
        "guide": (
            "Shape filtered noise and short sine sweeps into kick, snare, hat, click, or tom sounds, with different decay times and accents."
        ),
    },
    {
        "id": "wavetable_lead",
        "category": "synthesis",
        "keywords": ["lead", "synth", "electronic", "bright", "future", "电子", "合成器", "明亮"],
        "summary": "wavetable-like lead and evolving timbre",
        "basis": "wavetable synthesis practice",
        "guide": (
            "Blend sine/saw/square/triangle shapes, crossfade or modulate brightness over time, and write lead phrases that answer the bass or chord rhythm."
        ),
    },
    {
        "id": "schroeder_reverb",
        "category": "effect",
        "keywords": ["reverb", "space", "ambient", "room", "hall", "混响", "空间", "大厅"],
        "summary": "light comb/allpass-style artificial reverb",
        "basis": "Schroeder 1962",
        "guide": (
            "Use a few short feedback delays and allpass-like diffusers or repeated quiet taps to add space while keeping CPU and memory simple."
        ),
    },
    {
        "id": "moorer_reverb",
        "category": "effect",
        "keywords": ["reverb", "early", "reflection", "tail", "lofi", "ambient", "反射", "尾巴"],
        "summary": "early reflections plus smoother reverb tail",
        "basis": "Moorer 1979",
        "guide": (
            "Add a small pattern of early reflections before the reverb tail, then keep wet level controlled so the mix stays clear."
        ),
    },
    {
        "id": "loopable_ab_cycle",
        "category": "structure",
        "keywords": ["loop", "loopable", "cycle", "seamless", "循环", "可循环", "无缝"],
        "summary": "periodic A/B cycle for seamless loops",
        "basis": "loop-based composition practice",
        "guide": (
            "Use a periodic A/B or A/B/A cycle, align phrase lengths to bars, avoid one-shot intros/outros, and keep effect tails either short or wrapped into the next cycle."
        ),
    },
    {
        "id": "tension_release",
        "category": "structure",
        "keywords": ["build", "drop", "tension", "release", "energy", "推进", "爆发", "张力", "释放"],
        "summary": "energy curve and release moments",
        "basis": "electronic arrangement practice",
        "guide": (
            "Shape density, register, filter brightness, and drum activity into small build-and-release arcs instead of keeping the same intensity throughout."
        ),
    },
)

STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "ambient": {
        "bpm": 76,
        "energy": 0.32,
        "brightness": 0.68,
        "density": 0.32,
        "key": "D minor",
        "instruments": ["soft_pad", "sine_bell", "sub_bass", "noise_texture"],
        "effects": ["wide_reverb", "gentle_delay", "slow_filter"],
    },
    "ambient techno": {
        "bpm": 118,
        "energy": 0.52,
        "brightness": 0.48,
        "density": 0.54,
        "key": "F minor",
        "instruments": ["soft_pad", "warm_bass", "dub_stabs", "soft_techno_drums"],
        "effects": ["sidechain", "dub_delay", "wide_reverb"],
    },
    "lofi": {
        "bpm": 84,
        "energy": 0.42,
        "brightness": 0.42,
        "density": 0.52,
        "key": "A minor",
        "instruments": ["warm_keys", "synth_bass", "lofi_drums", "vinyl_noise"],
        "effects": ["soft_clip", "small_delay", "lowpass", "tape_wobble"],
    },
    "8bit": {
        "bpm": 132,
        "energy": 0.72,
        "brightness": 0.82,
        "density": 0.68,
        "key": "C minor",
        "instruments": ["8bit_lead", "pulse_bass", "pingpong_arp", "chip_drums"],
        "effects": ["short_delay", "soft_clip"],
    },
    "melodic techno": {
        "bpm": 124,
        "energy": 0.70,
        "brightness": 0.50,
        "density": 0.68,
        "key": "F minor",
        "instruments": ["saw_lead", "acid_bass", "dark_pad", "techno_drums"],
        "effects": ["sidechain", "filter_sweep", "riser", "small_delay"],
    },
    "acid techno": {
        "bpm": 128,
        "energy": 0.78,
        "brightness": 0.62,
        "density": 0.72,
        "key": "F minor",
        "instruments": ["acid_bass", "saw_lead", "dark_pad", "electro_drums"],
        "effects": ["sidechain", "filter_sweep", "riser", "soft_clip"],
    },
    "synthwave": {
        "bpm": 100,
        "energy": 0.58,
        "brightness": 0.62,
        "density": 0.55,
        "key": "C minor",
        "instruments": ["warm_pad", "octave_bass", "retro_lead", "gated_drums"],
        "effects": ["chorus_detune", "plate_reverb", "tape_saturation"],
    },
    "trance": {
        "bpm": 136,
        "energy": 0.80,
        "brightness": 0.76,
        "density": 0.76,
        "key": "E minor",
        "instruments": ["supersaw_lead", "rolling_bass", "trance_arp", "drums"],
        "effects": ["sidechain", "riser", "delay_throw", "wide_reverb"],
    },
    "house": {
        "bpm": 122,
        "energy": 0.64,
        "brightness": 0.56,
        "density": 0.62,
        "key": "A minor",
        "instruments": ["warm_keys", "offbeat_bass", "house_drums", "short_lead"],
        "effects": ["sidechain", "room_reverb", "small_delay"],
    },
    "breakbeat": {
        "bpm": 128,
        "energy": 0.70,
        "brightness": 0.58,
        "density": 0.76,
        "key": "D minor",
        "instruments": ["pluck_lead", "syncopated_bass", "breakbeat_drums", "noise_texture"],
        "effects": ["stutter", "delay_throw", "soft_clip"],
    },
    "electronic": {
        "bpm": 112,
        "energy": 0.56,
        "brightness": 0.60,
        "density": 0.58,
        "key": "C minor",
        "instruments": ["wavetable_lead", "synth_bass", "soft_pad", "electro_drums"],
        "effects": ["soft_clip", "small_delay", "sidechain"],
    },
}

KEY_ROOTS = {
    "c": 60,
    "c#": 61,
    "db": 61,
    "d": 62,
    "d#": 63,
    "eb": 63,
    "e": 64,
    "f": 65,
    "f#": 66,
    "gb": 66,
    "g": 67,
    "g#": 68,
    "ab": 68,
    "a": 69,
    "a#": 70,
    "bb": 70,
    "b": 71,
}
MUSICPY_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
DORIAN_SCALE = [0, 2, 3, 5, 7, 9, 10]
PENTATONIC_MINOR = [0, 3, 5, 7, 10]


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
    brightness: float = 0.60
    density: float = 0.55
    bpm: int = 110
    key: str = "C minor"
    instruments: list[str] = field(default_factory=lambda: ["8bit_lead", "synth_bass", "soft_pad", "lofi_drums"])
    effects: list[str] = field(default_factory=lambda: ["soft_clip", "small_delay"])
    duration: int = DEFAULT_DURATION
    loopable: bool = False
    send_mode: str = "auto"


@dataclass
class CompositionPlan:
    version: int = 2
    seed: int = 0
    style_profile: str = "melodic techno"
    root_midi: int = 60
    tonality: str = "minor"
    scale: list[int] = field(default_factory=lambda: MINOR_SCALE.copy())
    bpm: int = 110
    bar_count: int = 4
    phrase_bars: int = 4
    variation_seed: int = 0
    variation_strength: int = DEFAULT_VARIATION_STRENGTH
    selected_techniques: list[str] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    chords: dict[str, Any] = field(default_factory=dict)
    motifs: dict[str, Any] = field(default_factory=dict)
    bass: dict[str, Any] = field(default_factory=dict)
    drums: dict[str, Any] = field(default_factory=dict)
    automation: dict[str, Any] = field(default_factory=dict)
    mix: dict[str, Any] = field(default_factory=dict)


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
    composer: dict[str, Any] = field(default_factory=dict)


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
        parsed = int(float(value))
    except Exception:
        parsed = default
    return int(max(low, min(high, parsed)))


def _safe_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return _clamp(parsed, low, high)


def _normalize_send_mode(value: Any, default: str = "auto") -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in SEND_MODES else default


def _normalize_diversity_level(value: Any, default: int = DEFAULT_DIVERSITY_LEVEL) -> int:
    return _safe_int(value, default, 0, 2)


def _stable_seed(text: str, salt: str = "pymusic") -> int:
    data = f"{salt}|{text}".encode("utf-8", "ignore")
    acc = 2166136261
    for b in data:
        acc ^= b
        acc = (acc * 16777619) & 0xFFFFFFFF
    return int(acc or 1)


def _technique_catalog() -> str:
    return "\n".join(
        f"- {guide['id']} [{guide.get('category', 'general')}]: {guide['summary']}. {guide['guide']}"
        for guide in TECHNIQUE_GUIDES
    )


def _format_technique_guides(guides: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {guide['id']} [{guide.get('category', 'general')}, {guide.get('basis', 'practice')}]: {guide['guide']}"
        for guide in guides
    )


def _select_technique_guides(
    brief: PromptBrief,
    spec: MusicSpec | None = None,
    diversity_level: int = DEFAULT_DIVERSITY_LEVEL,
) -> list[dict[str, Any]]:
    diversity_level = _normalize_diversity_level(diversity_level)
    text_parts = [
        brief.original_prompt,
        brief.enriched_prompt,
        brief.style,
        brief.scene,
        brief.musical_intent,
        " ".join(brief.references),
    ]
    if spec is not None:
        text_parts.extend([
            spec.mood,
            spec.key,
            " ".join(spec.instruments),
            " ".join(spec.effects),
        ])
    text = " ".join(str(part) for part in text_parts if part).lower()
    ref_text = " ".join(str(item) for item in brief.references).lower()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, guide in enumerate(TECHNIQUE_GUIDES):
        guide_id = str(guide["id"])
        category = str(guide.get("category", "general"))
        score = 0
        if guide_id == "arrangement_motifs":
            score += 10
        elif guide_id in {"call_response_theme", "chord_tone_targeting", "bass_kick_lock", "sidechain_ducking"}:
            score += 6
        elif category == "structure":
            score += 3
        elif category in {"composition", "synthesis", "effect"}:
            score += 2
        if guide_id in text or guide_id.replace("_", " ") in text:
            score += 12
        if guide_id in ref_text or guide_id.replace("_", " ") in ref_text:
            score += 12
        for keyword in guide.get("keywords", []):
            keyword_text = str(keyword).lower()
            if keyword_text and keyword_text in text:
                score += 3
        if spec is not None:
            style_text = f"{spec.mood} {' '.join(spec.instruments)} {' '.join(spec.effects)}".lower()
            if spec.loopable and guide_id == "loopable_ab_cycle":
                score += 10
            if spec.duration <= 35 and guide_id == "micro_arrangement":
                score += 8
            if guide_id == "ambient_pad" and ("ambient" in style_text or "pad" in style_text or "drone" in style_text):
                score += 6
            elif guide_id == "ambient_techno" and "ambient" in style_text and "techno" in style_text:
                score += 7
            elif guide_id == "8bit_chiptune" and ("8bit" in style_text or "chip" in style_text):
                score += 7
            elif guide_id == "lofi_hiphop" and "lofi" in style_text:
                score += 7
            elif guide_id == "melodic_techno" and "techno" in style_text:
                score += 7
            elif guide_id == "synthwave" and ("synthwave" in style_text or "retro" in style_text):
                score += 7
            elif guide_id == "trance" and "trance" in style_text:
                score += 7
            elif guide_id == "house_groove" and "house" in style_text:
                score += 7
            elif guide_id == "breakbeat" and ("break" in style_text or "idm" in style_text):
                score += 7
            elif guide_id == "acid_bass" and ("acid" in style_text or "cyber" in style_text or spec.energy > 0.72):
                score += 6
            elif guide_id == "euclidean_drums" and (spec.density > 0.45 or "drum" in style_text or "beat" in style_text):
                score += 6
            elif guide_id == "markov_melody":
                score += 4
            elif guide_id == "motif_recombine" and diversity_level >= 1:
                score += 4
            elif guide_id == "lsystem_phrase" and spec.duration >= 45:
                score += 5
            elif guide_id == "pingpong_arpeggio" and ("8bit" in style_text or "chip" in style_text or "trance" in style_text or spec.brightness > 0.72):
                score += 5
            elif guide_id == "random_walk_melody" and ("ambient" in style_text or "lofi" in style_text or spec.energy < 0.48):
                score += 5
            elif guide_id == "stutter_pattern" and (diversity_level >= 2 or "break" in style_text or spec.energy > 0.70):
                score += 4
            elif guide_id == "phase_pattern" and ("techno" in style_text or "minimal" in style_text or "acid" in style_text):
                score += 5
            elif guide_id == "fm_synthesis" and ("bell" in style_text or "ice" in text or "crystal" in text or spec.brightness > 0.70):
                score += 5
            elif guide_id == "noise_texture" and ("noise" in style_text or "reverb" in style_text or "rain" in text or "wind" in text):
                score += 4
            elif guide_id == "karplus_pluck" and "pluck" in style_text:
                score += 4
            elif guide_id == "bandlimited_saw" and ("saw" in style_text or "acid" in style_text or spec.energy > 0.68):
                score += 4
            elif guide_id == "bandlimited_square" and ("8bit" in style_text or "chip" in style_text or "square" in style_text):
                score += 5
            elif guide_id == "additive_pad" and ("ambient" in style_text or "pad" in style_text):
                score += 5
            elif guide_id == "subtractive_bass" and ("bass" in style_text or "acid" in style_text or spec.energy > 0.60):
                score += 4
            elif guide_id == "noise_drum_resonator" and (spec.density > 0.50 or "drum" in style_text or "8bit" in style_text):
                score += 4
            elif guide_id == "wavetable_lead" and ("lead" in style_text or "synth" in style_text or "electronic" in style_text):
                score += 4
            elif guide_id == "schroeder_reverb" and ("ambient" in style_text or "reverb" in style_text or spec.energy < 0.50):
                score += 4
            elif guide_id == "moorer_reverb" and ("lofi" in style_text or "reverb" in style_text):
                score += 4
            elif guide_id == "riser_downlifter" and not spec.loopable and (spec.duration >= 18 or spec.energy > 0.55):
                score += 5
            elif guide_id == "tension_release" and not spec.loopable:
                score += 5
            if diversity_level >= 2 and guide_id in {
                "acid_bass",
                "karplus_pluck",
                "wavetable_lead",
                "stutter_pattern",
                "phase_pattern",
                "lsystem_phrase",
                "breakbeat",
                "trance",
            }:
                score += 2
        scored.append((score, index, guide))

    scored.sort(key=lambda item: (-item[0], item[1]))
    limit = 10 if diversity_level >= 2 else 8
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_guide(guide: dict[str, Any]) -> None:
        guide_id = str(guide["id"])
        if guide_id not in seen:
            selected.append(guide)
            seen.add(guide_id)

    def add_guide_by_id(guide_id: str) -> None:
        for candidate in TECHNIQUE_GUIDES:
            if candidate["id"] == guide_id:
                add_guide(candidate)
                return

    def add_top_category(category: str, fallback_id: str) -> None:
        if any(str(guide.get("category")) == category for guide in selected):
            return
        for score, _, guide in scored:
            if score <= 0:
                continue
            if str(guide.get("category")) == category:
                add_guide(guide)
                return
        add_guide_by_id(fallback_id)

    for required in ("arrangement_motifs", "call_response_theme", "chord_tone_targeting", "bass_kick_lock"):
        add_guide_by_id(required)
    if spec is not None and spec.duration <= 35:
        add_guide_by_id("micro_arrangement")
    add_top_category("style", "melodic_techno")
    add_top_category("composition", "markov_melody")
    add_top_category("synthesis", "wavetable_lead")
    add_top_category("effect", "sidechain_ducking")
    if spec is not None and spec.loopable:
        add_guide_by_id("loopable_ab_cycle")
    else:
        add_guide_by_id("tension_release")
        add_guide_by_id("riser_downlifter")
    for score, _, guide in scored:
        if score <= 0:
            continue
        add_guide(guide)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
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
    text = str(text or "").strip().lstrip("\ufeff")
    if not text:
        return ""
    data = _extract_json(text)
    if data:
        for key in ("code", "python", "source"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    fenced = re.search(r"```(?:python|py)?\s*(.*?)\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    else:
        lines = text.splitlines()
        start = 0
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("import ", "from ", "def ", "class ", "#")):
                start = index
                break
        text = "\n".join(lines[start:]).strip()
    if "\\n" in text and "\n" not in text and ("def " in text or "import " in text):
        try:
            decoded = bytes(text, "utf-8").decode("unicode_escape")
            if "def " in decoded:
                text = decoded
        except Exception:
            pass
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"^```(?:python|py)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    return text


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def _validate_generated_python(source: str) -> None:
    if len(source) > 32000:
        raise ValueError("generated Python code is too long")
    tree = ast.parse(source)
    allowed_imports = {"numpy", "math", "random", "musicpy"}
    blocked_names = {
        "open",
        "exec",
        "eval",
        "compile",
        "input",
        "__import__",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "os",
        "sys",
        "subprocess",
        "pathlib",
        "shutil",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "wave",
        "builtins",
    }
    blocked_attrs = {
        "save",
        "write",
        "read",
        "export",
        "play",
        "savez",
        "savez_compressed",
        "load",
        "fromfile",
        "tofile",
        "memmap",
        "genfromtxt",
        "loadtxt",
        "savetxt",
        "ctypeslib",
        "DataSource",
        "lib",
        "testing",
        "distutils",
        "f2py",
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
    match = re.search(r"(\d{1,4})\s*(?:秒|s|sec|second|seconds)", lower)
    if match:
        duration = int(match.group(1))
    send_mode = None
    if "文件" in prompt or "file" in lower:
        send_mode = "file"
    elif "语音" in prompt or "voice" in lower:
        send_mode = "voice"
    loopable = None
    if "可循环" in prompt or "循环" in prompt or "loop" in lower or "seamless" in lower:
        loopable = True
    if "不要循环" in prompt or "不循环" in prompt or "not loop" in lower:
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


def _profile_name_from_text(text: str) -> str:
    lower = text.lower()
    # Explicit genre words should win over scene/mood words such as “寒冬” or “雨”.
    if any(word in lower or word in text for word in ["ambient techno", "dub techno", "氛围科技", "深空科技"]):
        return "ambient techno"
    if any(word in lower or word in text for word in ["synthwave", "retro", "neon", "80s", "霓虹", "复古未来"]):
        return "synthwave"
    if any(word in lower or word in text for word in ["trance", "uplift", "rave", "anthem", "迷幻", "锐舞", "高能"]):
        return "trance"
    if any(word in lower or word in text for word in ["house", "club", "deep house", "浩室", "舞曲"]):
        return "house"
    if any(word in lower or word in text for word in ["breakbeat", "breaks", "idm", "碎拍", "故障"]):
        return "breakbeat"
    if any(word in lower or word in text for word in ["8bit", "chiptune", "pixel", "arcade", "game", "像素", "游戏"]):
        return "8bit"
    if any(word in lower or word in text for word in ["acid", "303", "acid techno", "cyber", "赛博", "酸性"]):
        return "acid techno"
    if any(word in lower or word in text for word in ["techno", "melodic techno", "dark", "underground", "黑暗", "紧张", "地下"]):
        return "melodic techno"
    if any(word in lower or word in text for word in ["lofi", "rain", "cafe", "study", "chill", "雨", "咖啡", "学习", "放松"]):
        return "lofi"
    if any(word in lower or word in text for word in ["ambient", "winter", "ice", "cold", "lonely", "wind", "氛围", "星空", "宇宙", "空灵", "寒冬", "冰", "冷", "冬", "孤独", "风"]):
        return "ambient"
    return "electronic"


def _fallback_spec(prompt: str, default_duration: int, max_duration: int, default_send_mode: str, loopable_default: bool) -> MusicSpec:
    profile_name = _profile_name_from_text(prompt)
    profile = STYLE_PROFILES[profile_name]
    overrides = _prompt_overrides(prompt)
    duration = _safe_int(overrides["duration"], default_duration, 5, max_duration) if overrides["duration"] else default_duration
    send_mode = _normalize_send_mode(overrides["send_mode"], default_send_mode)
    loopable = loopable_default if overrides["loopable"] is None else bool(overrides["loopable"])
    return MusicSpec(
        mood=profile_name,
        energy=float(profile["energy"]),
        brightness=float(profile["brightness"]),
        density=float(profile["density"]),
        bpm=int(profile["bpm"]),
        key=str(profile["key"]),
        instruments=list(profile["instruments"]),
        effects=list(profile["effects"]),
        duration=duration,
        loopable=loopable,
        send_mode=send_mode,
    )


def _fallback_brief(prompt: str) -> PromptBrief:
    fallback = _fallback_spec(prompt, DEFAULT_DURATION, HARD_MAX_SECONDS, "auto", False)
    style = fallback.mood
    instruments = ", ".join(fallback.instruments[:4])
    effects = ", ".join(fallback.effects[:4])
    style_text = f"{style} {' '.join(fallback.instruments)} {' '.join(fallback.effects)}".lower()
    technique_refs = ["arrangement_motifs", "call_response_theme", "chord_tone_targeting", "bass_kick_lock", "sidechain_ducking"]
    if fallback.duration <= 35:
        technique_refs.append("micro_arrangement")
    if style == "ambient":
        technique_refs.extend(["ambient_pad", "random_walk_melody", "fm_synthesis", "additive_pad", "schroeder_reverb", "noise_texture"])
    elif style == "ambient techno":
        technique_refs.extend(["ambient_techno", "euclidean_drums", "phase_pattern", "additive_pad", "subtractive_bass", "moorer_reverb"])
    elif "lofi" in style_text:
        technique_refs.extend(["lofi_hiphop", "euclidean_drums", "markov_melody", "karplus_pluck", "moorer_reverb", "noise_texture"])
    elif "8bit" in style_text or "chip" in style_text:
        technique_refs.extend(["8bit_chiptune", "pingpong_arpeggio", "euclidean_drums", "bandlimited_square", "noise_drum_resonator", "stutter_pattern"])
    elif "synthwave" in style_text:
        technique_refs.extend(["synthwave", "motif_recombine", "bandlimited_saw", "additive_pad", "moorer_reverb"])
    elif "trance" in style_text:
        technique_refs.extend(["trance", "pingpong_arpeggio", "bandlimited_saw", "riser_downlifter", "tension_release"])
    elif "house" in style_text:
        technique_refs.extend(["house_groove", "euclidean_drums", "subtractive_bass", "moorer_reverb"])
    elif "break" in style_text:
        technique_refs.extend(["breakbeat", "stutter_pattern", "noise_drum_resonator", "wavetable_lead"])
    elif "acid" in style_text or "techno" in style_text or "dark" in style_text:
        technique_refs.extend(["melodic_techno", "acid_bass", "euclidean_drums", "subtractive_bass", "bandlimited_saw", "phase_pattern", "riser_downlifter", "tension_release"])
    else:
        technique_refs.extend(["markov_melody", "motif_recombine", "wavetable_lead", "fm_synthesis", "tension_release"])
    if fallback.loopable:
        technique_refs.append("loopable_ab_cycle")
    # Keep order and uniqueness.
    technique_refs = list(dict.fromkeys(technique_refs))
    enriched = (
        f"A polished {style} instrumental for the scene '{prompt}'. "
        f"Use {instruments}, {fallback.bpm} BPM, {fallback.key}, energy {fallback.energy:.2f}, "
        f"brightness {fallback.brightness:.2f}, density {fallback.density:.2f}, with {effects}. "
        "Write a memorable theme and answer phrase, target chord tones on strong beats, coordinate bass with kick/chords, "
        "add groove accents and fills, and shape a small build/drop or evolving arc with sidechain, delay/reverb, riser/downlifter, and soft saturation where appropriate."
    )
    return PromptBrief(
        original_prompt=prompt,
        enriched_prompt=enriched,
        style=style,
        scene=prompt,
        musical_intent="generate a structured pure-Python electronic music clip with theme, variation, groove, and production movement",
        references=technique_refs,
        avoid=["vocals", "lyrics", "external samples", "copyrighted artist imitation", "one-bar test loop"],
    )


def _brief_from_dict(data: dict[str, Any], original_prompt: str, fallback: PromptBrief) -> PromptBrief:
    refs = data.get("references", fallback.references)
    avoid = data.get("avoid", fallback.avoid)
    return PromptBrief(
        original_prompt=original_prompt,
        enriched_prompt=str(data.get("enriched_prompt") or fallback.enriched_prompt)[:2000],
        style=str(data.get("style") or fallback.style)[:80],
        scene=str(data.get("scene") or fallback.scene)[:240],
        musical_intent=str(data.get("musical_intent") or fallback.musical_intent)[:400],
        references=[str(item)[:80] for item in refs if str(item).strip()][:12] if isinstance(refs, list) else fallback.references,
        avoid=[str(item)[:120] for item in avoid if str(item).strip()][:12] if isinstance(avoid, list) else fallback.avoid,
    )


def _spec_from_dict(data: dict[str, Any], fallback: MusicSpec, max_duration: int) -> MusicSpec:
    instruments = data.get("instruments", fallback.instruments)
    effects = data.get("effects", fallback.effects)
    return MusicSpec(
        mood=str(data.get("mood") or fallback.mood)[:80],
        energy=_safe_float(data.get("energy"), fallback.energy, 0.0, 1.0),
        brightness=_safe_float(data.get("brightness"), fallback.brightness, 0.0, 1.0),
        density=_safe_float(data.get("density"), fallback.density, 0.0, 1.0),
        bpm=_safe_int(data.get("bpm"), fallback.bpm, 55, 180),
        key=str(data.get("key") or fallback.key)[:40],
        instruments=[str(item)[:80] for item in instruments if str(item).strip()][:10]
        if isinstance(instruments, list)
        else fallback.instruments,
        effects=[str(item)[:80] for item in effects if str(item).strip()][:10]
        if isinstance(effects, list)
        else fallback.effects,
        duration=_safe_int(data.get("duration"), fallback.duration, 5, max_duration),
        loopable=bool(data.get("loopable", fallback.loopable)),
        send_mode=_normalize_send_mode(data.get("send_mode", fallback.send_mode), fallback.send_mode),
    )


def _root_midi_and_scale(key: str) -> tuple[int, list[int], str]:
    lower = str(key or "C minor").strip().lower().replace("♯", "#").replace("♭", "b")
    match = re.match(r"^([a-g](?:#|b)?).*", lower)
    root_name = match.group(1) if match else "c"
    root = KEY_ROOTS.get(root_name, 60)
    if "major" in lower or "ionian" in lower or "大调" in lower:
        return root, MAJOR_SCALE, "major"
    if "dorian" in lower or "多利亚" in lower:
        return root, DORIAN_SCALE, "dorian"
    return root, MINOR_SCALE, "minor"


def _chord_progression_for(spec: MusicSpec, profile: str) -> tuple[str, list[list[int]]]:
    text = f"{spec.key} {profile} {spec.mood} {' '.join(spec.instruments)}".lower()
    if "major" in text and "lofi" not in text:
        if profile in {"house", "synthwave"}:
            return "I-V-vi-IV", [[0, 2, 4], [4, 6, 1], [5, 0, 2], [3, 5, 0]]
        return "I-vi-IV-V", [[0, 2, 4], [5, 0, 2], [3, 5, 0], [4, 6, 1]]
    if profile in {"lofi", "synthwave"}:
        return "i-VI-iv-V", [[0, 2, 4, 6], [5, 0, 2, 4], [3, 5, 0, 2], [4, 6, 1, 3]]
    if profile in {"ambient", "ambient techno"}:
        return "i-VI-iv-v", [[0, 2, 4], [5, 0, 2], [3, 5, 0], [4, 6, 1]]
    if profile in {"trance", "melodic techno", "acid techno"}:
        return "i-VI-III-VII", [[0, 2, 4], [5, 0, 2], [2, 4, 6], [6, 1, 3]]
    return "i-VI-III-VII", [[0, 2, 4], [5, 0, 2], [2, 4, 6], [6, 1, 3]]


def _allocate_section_bars(total_bars: int, section_defs: list[tuple[str, float, str, float]], loopable: bool) -> list[dict[str, Any]]:
    if total_bars <= 0:
        total_bars = 1
    min_bars = 1 if total_bars < 8 else 2
    bars = [max(min_bars, int(round(frac * total_bars))) for _, frac, _, _ in section_defs]
    while sum(bars) > total_bars and max(bars) > min_bars:
        idx = max(range(len(bars)), key=lambda i: bars[i])
        bars[idx] -= 1
    while sum(bars) < total_bars:
        idx = max(range(len(bars)), key=lambda i: section_defs[i][1])
        bars[idx] += 1
    start = 0
    sections: list[dict[str, Any]] = []
    for (name, _, role, energy), length in zip(section_defs, bars):
        sections.append({"id": name, "start_bar": start, "bars": int(length), "role": role, "energy": float(energy)})
        start += length
    if loopable and sections:
        sections[0]["loop_anchor"] = True
        sections[-1]["loop_anchor"] = True
    return sections


def _section_defs(duration: int, loopable: bool, profile: str) -> list[tuple[str, float, str, float]]:
    if loopable:
        return [
            ("A_hook", 0.38, "hook", 0.86),
            ("B_answer", 0.36, "variation", 0.94),
            ("A2_return", 0.26, "return", 0.90),
        ]
    if duration < 35:
        return [
            ("identity_hook", 0.30, "hook", 0.74),
            ("micro_build", 0.24, "build", 0.88),
            ("mini_drop", 0.32, "drop", 1.00),
            ("tail", 0.14, "outro", 0.58),
        ]
    if duration < 60:
        return [
            ("intro", 0.16, "intro", 0.50),
            ("A_theme", 0.30, "hook", 0.78),
            ("build", 0.18, "build", 0.92),
            ("B_drop", 0.26, "drop", 1.00),
            ("outro", 0.10, "outro", 0.55),
        ]
    if profile == "ambient":
        return [
            ("fade_in", 0.16, "intro", 0.42),
            ("A_theme", 0.28, "hook", 0.64),
            ("B_evolve", 0.28, "variation", 0.76),
            ("break_space", 0.14, "break", 0.52),
            ("return", 0.14, "return", 0.68),
        ]
    return [
        ("intro", 0.12, "intro", 0.48),
        ("A_theme", 0.24, "hook", 0.74),
        ("build", 0.16, "build", 0.95),
        ("B_drop", 0.24, "drop", 1.00),
        ("break", 0.10, "break", 0.46),
        ("final_drop", 0.14, "return", 0.92),
    ]


def _musicpy_note_name_from_midi(midi: int) -> str:
    midi = int(midi)
    return f"{MUSICPY_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _musicpy_scale_context(root_midi: int, tonality: str) -> Any:
    mode = str(tonality or "minor").lower()
    if mode not in {"major", "minor", "dorian"}:
        mode = "minor"
    try:
        return mp.scale(_musicpy_note_name_from_midi(root_midi), mode)
    except Exception:
        # Strong dependency remains: import must exist. This fallback only protects
        # unusual key/mode spellings so the renderer can still complete.
        return mp.scale(_musicpy_note_name_from_midi(root_midi), "minor")


def _musicpy_midi_from_degree(scale_ctx: Any, degree: int, octave: int = 0) -> int:
    degree = int(degree)
    octave = int(octave)
    note_obj = scale_ctx.get_note_from_degree(degree + 1)
    return int(note_obj.degree + octave * 12)


def _reflect_scale_degree(value: int, scale_len: int) -> int:
    if scale_len <= 1:
        return 0
    high = scale_len - 1
    value = int(value)
    while value < 0 or value > high:
        if value < 0:
            value = -value
        if value > high:
            value = high - (value - high)
    return value


def _musicpy_melody_score(
    motif: list[dict[str, Any]],
    scale_ctx: Any,
    chord_degrees: list[int],
    profile: str,
) -> float:
    if not motif:
        return -999.0
    score = 0.0
    midis: list[int] = []
    degrees: list[int] = []
    strong = {int(degree) for degree in chord_degrees}
    for event in motif:
        degree = int(event.get("degree", 0))
        octave = int(event.get("octave", 0))
        midi = _musicpy_midi_from_degree(scale_ctx, degree, octave)
        midis.append(midi)
        degrees.append(degree + octave * 7)
        step = int(event.get("step", 0))
        if step % 8 in {0, 4}:
            score += 3.0 if degree in strong else -2.5
        if float(event.get("length_steps", 2)) >= 3:
            score += 0.4
    intervals = [degrees[i + 1] - degrees[i] for i in range(len(degrees) - 1)]
    for idx, interval in enumerate(intervals):
        distance = abs(interval)
        if distance == 0:
            score -= 0.35
        elif distance <= 2:
            score += 1.1
        elif distance <= 4:
            score += 0.25
        else:
            score -= 2.6
            if idx + 1 < len(intervals) and intervals[idx + 1] * interval < 0 and abs(intervals[idx + 1]) <= 2:
                score += 2.2
    final_degree = int(motif[-1].get("degree", 0))
    score += 4.0 if final_degree in strong else -1.5
    if final_degree == 0:
        score += 1.4
    pitch_range = max(midis) - min(midis) if midis else 0
    if profile in {"ambient", "lofi"}:
        score += 2.0 if 4 <= pitch_range <= 12 else -1.0
    else:
        score += 1.8 if 7 <= pitch_range <= 18 else -0.8
    repeated_run = 1
    for i in range(1, len(degrees)):
        if degrees[i] == degrees[i - 1]:
            repeated_run += 1
            if repeated_run >= 3:
                score -= 1.0
        else:
            repeated_run = 1
    step_set = {int(event.get("step", 0)) for event in motif}
    if len(step_set) != len(motif):
        score -= 3.0
    return score


def _musicpy_generate_motif(
    rng: random.Random,
    root_midi: int,
    scale: list[int],
    tonality: str,
    chord_progression: list[dict[str, Any]],
    diversity: int,
    profile: str,
) -> tuple[list[dict[str, Any]], float]:
    scale_ctx = _musicpy_scale_context(root_midi, tonality)
    scale_len = max(1, len(scale))
    if profile in {"8bit", "trance"}:
        steps = [0, 2, 4, 6, 8, 10, 12, 14]
        length_bank = [1, 2, 2, 1, 2, 2, 1, 2]
    elif profile in {"lofi", "ambient"}:
        steps = [0, 3, 6, 8, 11, 14]
        length_bank = [3, 2, 2, 3, 2, 2]
    else:
        steps = [0, 2, 5, 7, 8, 10, 13, 14]
        length_bank = [2, 2, 1, 1, 2, 2, 1, 2]
    chord_degrees = [int(x) % scale_len for x in (chord_progression[0].get("scale_degrees", [0, 2, 4]) if chord_progression else [0, 2, 4])]
    candidate_count = 12 + diversity * 8
    best: list[dict[str, Any]] = []
    best_score = -999.0
    for _ in range(candidate_count):
        current = rng.choice(chord_degrees)
        previous_jump = 0
        motif: list[dict[str, Any]] = []
        direction_bias = rng.choice([-1, 1])
        for idx, step in enumerate(steps):
            strong = step % 8 in {0, 4}
            if strong:
                degree = rng.choice(chord_degrees)
            elif previous_jump:
                degree = current - int(math.copysign(rng.choice([1, 2]), previous_jump))
            else:
                if rng.random() < (0.78 if profile in {"lofi", "ambient"} else 0.65):
                    degree = current + rng.choice([-1, 0, 1, direction_bias])
                else:
                    degree = current + rng.choice([-3, -2, 2, 3])
            degree = _reflect_scale_degree(degree, scale_len)
            jump = degree - current
            current = degree
            previous_jump = jump if abs(jump) >= 3 else 0
            octave = 0
            if profile not in {"ambient", "lofi"} and idx >= len(steps) // 2 and rng.random() < 0.55:
                octave = 1
            if profile == "ambient" and rng.random() < 0.30:
                octave = rng.choice([-1, 0])
            length = int(length_bank[idx % len(length_bank)])
            if diversity >= 2 and not strong and rng.random() < 0.22:
                length = max(1, min(4, length + rng.choice([-1, 1])))
            motif.append(
                {
                    "step": int(step),
                    "degree": int(degree),
                    "octave": int(octave),
                    "length_steps": int(length),
                    "velocity": round(0.70 + 0.22 * (1 if strong else rng.random()), 3),
                    "strong_chord_tone": bool(strong),
                    "musicpy_midi": _musicpy_midi_from_degree(scale_ctx, degree, octave),
                }
            )
        score = _musicpy_melody_score(motif, scale_ctx, chord_degrees, profile)
        if score > best_score:
            best = motif
            best_score = score
    return best, round(float(best_score), 3)


def _make_motif(rng: random.Random, scale_len: int, diversity: int, profile: str) -> list[dict[str, Any]]:
    if profile in {"8bit", "trance"}:
        steps = [0, 2, 4, 6, 8, 10, 12, 14]
        lengths = [2, 2, 2, 2, 2, 2, 2, 2]
    elif profile in {"lofi", "ambient"}:
        steps = [0, 3, 6, 8, 11, 14]
        lengths = [3, 2, 2, 3, 2, 2]
    else:
        steps = [0, 2, 5, 7, 8, 10, 13, 14]
        lengths = [2, 2, 1, 1, 2, 2, 1, 2]
    contour = [0, 2, 4, 3, 2, 5, 4, 1]
    if diversity >= 1:
        contour = [degree + rng.choice([-1, 0, 0, 1]) for degree in contour]
    motif: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        strong = step % 8 in {0, 4}
        degree = contour[i % len(contour)] % scale_len
        if strong:
            degree = [0, 2, 4, 6][i % 4] % scale_len
        octave = 1 if i >= len(steps) // 2 and profile not in {"ambient", "lofi"} else 0
        motif.append(
            {
                "step": int(step),
                "degree": int(degree),
                "octave": int(octave),
                "length_steps": int(lengths[i % len(lengths)]),
                "velocity": round(0.72 + 0.18 * (1 if strong else rng.random()), 3),
                "strong_chord_tone": bool(strong),
            }
        )
    return motif


def _variant_motif(motif: list[dict[str, Any]], scale_len: int, mode: str, rng: random.Random) -> list[dict[str, Any]]:
    variant: list[dict[str, Any]] = []
    if mode == "response":
        for event in motif:
            degree = int(event["degree"])
            if not event.get("strong_chord_tone"):
                degree = (degree + rng.choice([-1, 1, 2])) % scale_len
            variant.append(
                {
                    **event,
                    "step": int((event["step"] + 1 + (2 if event["step"] >= 8 else 0)) % 16),
                    "degree": int(degree),
                    "octave": int(event.get("octave", 0)) + (1 if event["step"] >= 8 else 0),
                    "velocity": round(float(event.get("velocity", 0.75)) * 0.92, 3),
                }
            )
    elif mode == "b":
        cells = motif[:3] + motif[-3:]
        for idx, event in enumerate(cells):
            variant.append(
                {
                    **event,
                    "step": int((idx * 2 + (1 if idx % 2 else 0)) % 16),
                    "degree": int((scale_len - 1 - int(event["degree"]) + (2 if idx >= 3 else 0)) % scale_len),
                    "octave": int(event.get("octave", 0)) + (1 if idx >= 3 else 0),
                    "length_steps": int(max(1, min(4, int(event.get("length_steps", 2)) + (1 if idx % 2 else 0)))),
                    "velocity": round(min(1.0, float(event.get("velocity", 0.75)) * 1.05), 3),
                }
            )
    else:
        variant = [dict(item) for item in motif]
    return sorted(variant, key=lambda item: item["step"])


def _euclidean_hits(pulses: int, steps: int, offset: int = 0) -> list[int]:
    if pulses <= 0:
        return []
    hits: list[int] = []
    bucket = 0
    for step in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            hits.append((step + offset) % steps)
    return sorted(set(hits))


def _composition_plan_from_dict(data: dict[str, Any]) -> CompositionPlan:
    if not isinstance(data, dict):
        data = {}
    scale = data.get("scale", MINOR_SCALE)
    if not isinstance(scale, list) or not scale:
        scale = MINOR_SCALE
    selected = data.get("selected_techniques", [])
    if not isinstance(selected, list):
        selected = []
    sections = data.get("sections", [])
    if not isinstance(sections, list):
        sections = []
    return CompositionPlan(
        version=_safe_int(data.get("version", 2), 2, 1, 99),
        seed=_safe_int(data.get("seed", 0), 0, 0, MAX_RANDOM_SEED),
        variation_seed=_safe_int(data.get("variation_seed", 0), 0, 0, MAX_RANDOM_SEED),
        variation_strength=_safe_int(
            data.get("variation_strength", DEFAULT_VARIATION_STRENGTH),
            DEFAULT_VARIATION_STRENGTH,
            0,
            3,
        ),
        style_profile=str(data.get("style_profile", "melodic techno"))[:80],
        root_midi=_safe_int(data.get("root_midi", 60), 60, 0, 127),
        tonality=str(data.get("tonality", "minor"))[:40],
        scale=[int(item) for item in scale[:12]],
        bpm=_safe_int(data.get("bpm", 110), 110, 55, 180),
        bar_count=_safe_int(data.get("bar_count", 4), 4, 1, 4096),
        phrase_bars=_safe_int(data.get("phrase_bars", 4), 4, 1, 64),
        selected_techniques=[str(item)[:80] for item in selected[:16]],
        sections=[item for item in sections[:128] if isinstance(item, dict)],
        chords=data.get("chords") if isinstance(data.get("chords"), dict) else {},
        motifs=data.get("motifs") if isinstance(data.get("motifs"), dict) else {},
        bass=data.get("bass") if isinstance(data.get("bass"), dict) else {},
        drums=data.get("drums") if isinstance(data.get("drums"), dict) else {},
        automation=data.get("automation") if isinstance(data.get("automation"), dict) else {},
        mix=data.get("mix") if isinstance(data.get("mix"), dict) else {},
    )


def _composer_to_dict(composer: CompositionPlan | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(composer, CompositionPlan):
        return asdict(composer)
    if isinstance(composer, dict):
        return composer
    return {}


def _compose_arrangement(
    brief: PromptBrief,
    spec: MusicSpec,
    technique_guides: list[dict[str, Any]],
    diversity_level: int,
    variation_seed: int = 0,
    variation_strength: int = DEFAULT_VARIATION_STRENGTH,
) -> CompositionPlan:
    diversity_level = _normalize_diversity_level(diversity_level)
    variation_strength = _safe_int(variation_strength, DEFAULT_VARIATION_STRENGTH, 0, 3)
    effective_variation_seed = _safe_int(variation_seed, 0, 0, MAX_RANDOM_SEED) if variation_strength > 0 else 0
    profile = _profile_name_from_text(
        f"{brief.original_prompt} {brief.enriched_prompt} {brief.style} {spec.mood} {' '.join(spec.instruments)} {' '.join(spec.effects)}"
    )
    seed = _stable_seed(
        f"{brief.original_prompt}|{brief.enriched_prompt}|{spec.mood}|{spec.key}|{spec.bpm}|{spec.duration}|{spec.loopable}|{diversity_level}|{effective_variation_seed}|{variation_strength}",
        "composer-plan-v4",
    )
    rng = random.Random(seed)
    root_midi, scale, tonality = _root_midi_and_scale(spec.key)
    beat_sec = 60.0 / max(55, min(180, int(spec.bpm)))
    bar_sec = beat_sec * 4.0
    total_bars = max(4, int(math.ceil(max(5, spec.duration) / bar_sec)))
    if spec.loopable:
        phrase = 4 if total_bars < 12 else 8
        total_bars = max(phrase, int(math.ceil(total_bars / phrase)) * phrase)
    progression_name, progression_degrees = _chord_progression_for(spec, profile)
    chord_progression = []
    for i, degrees in enumerate(progression_degrees):
        chord_progression.append(
            {
                "bar_mod": i,
                "name": progression_name.split("-")[i] if "-" in progression_name and i < len(progression_name.split("-")) else f"chord_{i + 1}",
                "scale_degrees": [int(degree % len(scale)) for degree in degrees],
                "bass_degree": int(degrees[0] % len(scale)),
                "color": "seventh" if len(degrees) >= 4 else "triad",
            }
        )
    section_defs = _section_defs(spec.duration, spec.loopable, profile)
    sections = _allocate_section_bars(total_bars, section_defs, spec.loopable)
    motif_call, melody_score = _musicpy_generate_motif(
        rng,
        root_midi,
        scale,
        tonality,
        chord_progression,
        diversity_level,
        profile,
    )
    if not motif_call:
        motif_call = _make_motif(rng, len(scale), diversity_level, profile)
        melody_score = 0.0
    motif_response = _variant_motif(motif_call, len(scale), "response", rng)
    motif_b = _variant_motif(motif_call, len(scale), "b", rng)
    bass_steps = [0, 6, 8, 11, 14]
    if profile in {"house", "melodic techno", "acid techno", "trance"}:
        bass_steps = [2, 4, 6, 10, 12, 14]
    elif profile == "lofi":
        bass_steps = [0, 5, 8, 13]
    elif profile == "ambient":
        bass_steps = [0]
    elif profile == "breakbeat":
        bass_steps = [0, 3, 7, 10, 14]
    bass_pattern = []
    for idx, step in enumerate(bass_steps):
        degree = 0 if idx % 3 != 2 else 4
        if idx == len(bass_steps) - 1 and diversity_level >= 1:
            degree = rng.choice([1, 4, 6])
        bass_pattern.append(
            {
                "step": int(step),
                "degree": int(degree % len(scale)),
                "octave": -2 if profile not in {"8bit", "lofi"} else -1,
                "length_steps": 2 if profile != "ambient" else 12,
                "accent": round(0.78 + 0.18 * (idx % 2 == 0), 3),
            }
        )
    if variation_strength >= 2 and profile not in {"ambient"} and bass_pattern:
        shift = rng.choice([-1, 1, 2])
        for item in bass_pattern[1::2]:
            item["step"] = int((int(item["step"]) + shift) % 16)
        bass_pattern = sorted(bass_pattern, key=lambda item: int(item["step"]))
    if profile == "breakbeat":
        kick_steps = [0, 3, 7, 10, 14]
        snare_steps = [4, 12]
    elif profile in {"ambient"}:
        kick_steps = []
        snare_steps = []
    elif profile in {"lofi"}:
        kick_steps = [0, 6, 10]
        snare_steps = [4, 12]
    else:
        kick_steps = [0, 4, 8, 12]
        if diversity_level >= 1:
            kick_steps += [14]
        snare_steps = [4, 12]
    hat_density = 5 if profile in {"ambient", "lofi"} else 7 + diversity_level
    if variation_strength >= 3 and profile not in {"ambient"}:
        hat_density = min(12, hat_density + rng.choice([1, 2]))
    hat_steps = _euclidean_hits(hat_density, 16, offset=1 if profile == "lofi" else 0)
    if profile in {"house", "melodic techno", "acid techno", "trance"}:
        open_hat_steps = [2, 6, 10, 14]
    elif profile == "breakbeat":
        open_hat_steps = [3, 7, 11, 15]
    else:
        open_hat_steps = [6, 14]
    guide_ids = [guide["id"] for guide in technique_guides]
    plan = {
        "version": 2,
        "seed": seed,
        "variation_seed": int(effective_variation_seed),
        "variation_strength": int(variation_strength),
        "style_profile": profile,
        "root_midi": root_midi,
        "tonality": tonality,
        "scale": scale,
        "bpm": int(spec.bpm),
        "bar_count": int(total_bars),
        "phrase_bars": 4 if total_bars < 12 else 8,
        "selected_techniques": guide_ids,
        "sections": sections,
        "chords": {
            "progression_name": progression_name,
            "progression": chord_progression,
            "strong_beat_policy": "melody steps 0/4/8/12 prefer chord tones; weak steps may use passing tones",
            "voicing": "open" if profile in {"ambient", "synthwave"} else "stabs" if profile in {"house", "ambient techno"} else "soft",
        },
        "motifs": {
            "call": motif_call,
            "response": motif_response,
            "b_variation": motif_b,
            "planner": "musicpy_candidate_scorer",
            "musicpy_scale": f"{_musicpy_note_name_from_midi(root_midi)} {tonality}",
            "melody_score": melody_score,
            "development_notes": [
                "call motif is selected from multiple musicpy-assisted melody candidates",
                "call and response share interval material but differ in rhythm/register",
                "B variation recombines motif cells and raises energy",
                "phrase endings allow delay throw or stutter fill",
            ],
        },
        "bass": {
            "pattern": bass_pattern,
            "relationship": "leave kick transient space, answer kicks with offbeat root/fifth/octave movement",
            "tone": "acid" if profile in {"acid techno", "melodic techno"} else "warm" if profile in {"lofi", "house"} else "sub",
        },
        "drums": {
            "kick_steps": sorted(set(kick_steps)),
            "snare_steps": snare_steps,
            "hat_steps": hat_steps,
            "open_hat_steps": open_hat_steps,
            "fill_policy": "last bar of every phrase adds denser hats, ghost snare, or stutter; section changes get riser/downlifter",
            "swing": 0.58 if profile == "lofi" else 0.54 if profile == "breakbeat" else 0.50,
        },
        "automation": {
            "sidechain_amount": round(0.12 + 0.25 * float(spec.energy), 3) if profile != "ambient" else 0.05,
            "filter_start": round(max(0.25, float(spec.brightness) - 0.30), 3),
            "filter_end": round(min(1.0, float(spec.brightness) + 0.22), 3),
            "riser_before_drop_bars": 1 if not spec.loopable and spec.duration >= 18 else 0,
            "delay_throw_on_phrase_end": True,
            "downlifter_on_drop": not spec.loopable and profile != "ambient",
        },
        "mix": {
            "target_peak": 0.92,
            "drum_gain": 0.72 if profile != "ambient" else 0.18,
            "bass_gain": 0.55 if profile != "ambient" else 0.30,
            "chord_gain": 0.42 if profile != "8bit" else 0.24,
            "lead_gain": 0.38 if profile != "ambient" else 0.26,
            "texture_gain": 0.18 if profile in {"lofi", "ambient", "ambient techno"} else 0.08,
            "reverb_wet": 0.22 if profile not in {"ambient"} else 0.42,
            "delay_wet": 0.16 if profile not in {"8bit"} else 0.08,
            "soft_saturation_drive": 1.15 + 0.60 * float(spec.energy),
        },
    }
    return _composition_plan_from_dict(plan)


def _default_plan(spec: MusicSpec, diversity_level: int, composer: CompositionPlan | dict[str, Any] | None = None) -> RenderPlan:
    profile = _profile_name_from_text(f"{spec.mood} {' '.join(spec.instruments)} {' '.join(spec.effects)}")
    if composer is None:
        pseudo_brief = PromptBrief(
            original_prompt=spec.mood,
            enriched_prompt=f"fallback {spec.mood} renderer plan",
            style=profile,
            references=[],
        )
        guides = _select_technique_guides(pseudo_brief, spec, diversity_level)
        composer = _compose_arrangement(pseudo_brief, spec, guides, diversity_level)
    composer_data = _composer_to_dict(composer)
    if profile == "ambient":
        drums_pattern = "ambient_no_drums"
        bass_pattern = "sub_drone"
        melody_shape = "random_walk"
        voicing = "drone"
        noise = "air"
    elif profile == "8bit":
        drums_pattern = "8bit_arpeggio_beat"
        bass_pattern = "root_octave"
        melody_shape = "arpeggio"
        voicing = "stabs"
        noise = "none"
    elif profile == "lofi":
        drums_pattern = "lofi_swing"
        bass_pattern = "warm_roots"
        melody_shape = "call_response"
        voicing = "open"
        noise = "vinyl"
    elif profile in {"melodic techno", "acid techno", "ambient techno"}:
        drums_pattern = "minimal_techno"
        bass_pattern = "acid_bass" if profile != "ambient techno" else "syncopated_pulse"
        melody_shape = "motif_variation"
        voicing = "stabs"
        noise = "space"
    elif profile == "breakbeat":
        drums_pattern = "breakbeat"
        bass_pattern = "syncopated_pulse"
        melody_shape = "call_response"
        voicing = "stabs"
        noise = "tape"
    else:
        drums_pattern = "minimal_techno" if spec.energy > 0.60 else "lofi_swing"
        bass_pattern = "syncopated_pulse"
        melody_shape = "motif_variation"
        voicing = "soft"
        noise = "tape"
    return RenderPlan(
        tracks=[
            {"role": "drums", "name": drums_pattern, "gain": 1.0},
            {"role": "bass", "name": bass_pattern, "gain": 1.0},
            {"role": "chords", "name": "pad" if voicing == "drone" else "warm_keys", "gain": 0.9},
            {"role": "melody", "name": "chip_lead" if profile == "8bit" else "bell" if profile == "ambient" else "wavetable_lead", "gain": 1.0},
            {"role": "texture", "name": "noise_texture", "gain": 0.6},
        ],
        drums={"pattern": drums_pattern, "swing": composer_data.get("drums", {}).get("swing", 0.5)},
        bass={"pattern": bass_pattern, "tone": composer_data.get("bass", {}).get("tone", "warm")},
        chords={"progression": composer_data.get("chords", {}).get("progression_name", "i-VI-III-VII"), "voicing": voicing},
        melody={"shape": melody_shape, "register": "high" if profile in {"8bit", "trance"} else "mid"},
        texture={"noise": noise},
        effects={
            "delay": True,
            "reverb": True,
            "lowpass": profile in {"lofi", "ambient"},
            "sidechain": profile != "ambient",
            "riser": not spec.loopable and profile != "ambient",
        },
        master={"target_peak": composer_data.get("mix", {}).get("target_peak", 0.92)},
        composer=composer_data,
    )


def _plan_from_dict(data: dict[str, Any], fallback: RenderPlan, composer: CompositionPlan | dict[str, Any] | None = None) -> RenderPlan:
    allowed_drums = {"lofi_swing", "8bit_arpeggio_beat", "ambient_no_drums", "breakbeat", "minimal_techno"}
    allowed_bass = {"root_octave", "warm_roots", "acid_bass", "sub_drone", "syncopated_pulse"}
    allowed_prog = {"i-VI-III-VII", "i-iv-V-i", "I-V-vi-IV", "ii-V-I", "modal_drone"}
    allowed_voicing = {"soft", "open", "stabs", "drone"}
    allowed_shape = {"arpeggio", "call_response", "pentatonic", "stepwise", "random_walk", "motif_variation"}
    allowed_register = {"low", "mid", "high"}
    allowed_noise = {"vinyl", "tape", "air", "space", "none"}

    def pick(obj: dict[str, Any], key: str, fallback_value: str, allowed: set[str]) -> str:
        value = str(obj.get(key, fallback_value))
        return value if value in allowed else fallback_value

    tracks = data.get("tracks", fallback.tracks)
    if not isinstance(tracks, list):
        tracks = fallback.tracks
    clean_tracks: list[dict[str, Any]] = []
    for item in tracks[:8]:
        if not isinstance(item, dict):
            continue
        clean_tracks.append(
            {
                "role": str(item.get("role", "melody"))[:40],
                "name": str(item.get("name", "wavetable_lead"))[:60],
                "gain": _safe_float(item.get("gain"), 1.0, 0.1, 1.5),
            }
        )
    drums = data.get("drums", {}) if isinstance(data.get("drums"), dict) else {}
    bass = data.get("bass", {}) if isinstance(data.get("bass"), dict) else {}
    chords = data.get("chords", {}) if isinstance(data.get("chords"), dict) else {}
    melody = data.get("melody", {}) if isinstance(data.get("melody"), dict) else {}
    texture = data.get("texture", {}) if isinstance(data.get("texture"), dict) else {}
    effects = data.get("effects", {}) if isinstance(data.get("effects"), dict) else {}
    master = data.get("master", {}) if isinstance(data.get("master"), dict) else {}
    return RenderPlan(
        tracks=clean_tracks or fallback.tracks,
        drums={"pattern": pick(drums, "pattern", fallback.drums.get("pattern", "minimal_techno"), allowed_drums), "swing": _safe_float(drums.get("swing"), fallback.drums.get("swing", 0.5), 0.45, 0.65)},
        bass={"pattern": pick(bass, "pattern", fallback.bass.get("pattern", "syncopated_pulse"), allowed_bass), "tone": str(bass.get("tone", fallback.bass.get("tone", "warm")))[:40]},
        chords={"progression": pick(chords, "progression", fallback.chords.get("progression", "i-VI-III-VII"), allowed_prog), "voicing": pick(chords, "voicing", fallback.chords.get("voicing", "soft"), allowed_voicing)},
        melody={"shape": pick(melody, "shape", fallback.melody.get("shape", "motif_variation"), allowed_shape), "register": pick(melody, "register", fallback.melody.get("register", "mid"), allowed_register)},
        texture={"noise": pick(texture, "noise", fallback.texture.get("noise", "tape"), allowed_noise)},
        effects={
            "delay": bool(effects.get("delay", fallback.effects.get("delay", True))),
            "reverb": bool(effects.get("reverb", fallback.effects.get("reverb", True))),
            "lowpass": bool(effects.get("lowpass", fallback.effects.get("lowpass", False))),
            "sidechain": bool(effects.get("sidechain", fallback.effects.get("sidechain", True))),
            "riser": bool(effects.get("riser", fallback.effects.get("riser", True))),
        },
        master={"target_peak": _safe_float(master.get("target_peak"), fallback.master.get("target_peak", 0.92), 0.3, 0.98)},
        composer=_composer_to_dict(composer) or fallback.composer,
    )


def _midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _degree_to_midi(root: int, scale: list[int], degree: int, octave: int = 0) -> int:
    if not scale:
        scale = MINOR_SCALE
    scale_len = len(scale)
    return int(root + scale[degree % scale_len] + 12 * (octave + degree // scale_len))


def _adsr(length: int, sr: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    if length <= 0:
        return np.zeros(0, dtype=np.float32)
    attack_n = max(1, min(length, int(attack * sr)))
    decay_n = max(1, min(length - attack_n, int(decay * sr))) if length > attack_n else 0
    release_n = max(1, min(length - attack_n - decay_n, int(release * sr))) if length > attack_n + decay_n else 0
    sustain_n = max(0, length - attack_n - decay_n - release_n)
    parts = [np.linspace(0.0, 1.0, attack_n, endpoint=False, dtype=np.float32)]
    if decay_n:
        parts.append(np.linspace(1.0, sustain, decay_n, endpoint=False, dtype=np.float32))
    if sustain_n:
        parts.append(np.full(sustain_n, sustain, dtype=np.float32))
    if release_n:
        start = sustain if decay_n or sustain_n else 1.0
        parts.append(np.linspace(start, 0.0, release_n, endpoint=True, dtype=np.float32))
    env = np.concatenate(parts) if parts else np.zeros(length, dtype=np.float32)
    if len(env) < length:
        env = np.pad(env, (0, length - len(env)))
    return env[:length]


def _bandlimited_square(freq: float, t: np.ndarray, sr: int, harmonics: int = 16) -> np.ndarray:
    max_h = min(harmonics, max(1, int((sr * 0.45) // max(freq, 1.0))))
    wave_out = np.zeros_like(t, dtype=np.float32)
    for h in range(1, max_h + 1, 2):
        wave_out += (1.0 / h) * np.sin(2.0 * np.pi * freq * h * t).astype(np.float32)
    peak = np.max(np.abs(wave_out)) if len(wave_out) else 1.0
    return wave_out / max(float(peak), 1e-6)


def _bandlimited_saw(freq: float, t: np.ndarray, sr: int, brightness: float = 0.7, harmonics: int = 18) -> np.ndarray:
    max_h = min(harmonics, max(1, int((sr * 0.45) // max(freq, 1.0))))
    wave_out = np.zeros_like(t, dtype=np.float32)
    damping = 0.35 + 0.85 * _clamp(brightness, 0.0, 1.0)
    for h in range(1, max_h + 1):
        wave_out += ((-1.0) ** (h + 1)) * (damping ** (h - 1)) / h * np.sin(2.0 * np.pi * freq * h * t).astype(np.float32)
    peak = np.max(np.abs(wave_out)) if len(wave_out) else 1.0
    return wave_out / max(float(peak), 1e-6)


def _triangle(freq: float, t: np.ndarray, sr: int) -> np.ndarray:
    max_h = min(15, max(1, int((sr * 0.45) // max(freq, 1.0))))
    wave_out = np.zeros_like(t, dtype=np.float32)
    sign = 1.0
    for h in range(1, max_h + 1, 2):
        wave_out += sign * (1.0 / (h * h)) * np.sin(2.0 * np.pi * freq * h * t).astype(np.float32)
        sign *= -1.0
    peak = np.max(np.abs(wave_out)) if len(wave_out) else 1.0
    return wave_out / max(float(peak), 1e-6)


def _clip_add(target: np.ndarray, start: int, source: np.ndarray, gain: float = 1.0) -> None:
    if start >= len(target) or len(source) == 0:
        return
    if start < 0:
        source = source[-start:]
        start = 0
    end = min(len(target), start + len(source))
    if end <= start:
        return
    target[start:end] += (source[: end - start] * gain).astype(np.float32)


def _add_note(
    target: np.ndarray,
    start_sec: float,
    dur_sec: float,
    midi: float,
    velocity: float,
    sr: int,
    synth: str,
    brightness: float,
    rng: np.random.Generator,
) -> None:
    start = int(max(0.0, start_sec) * sr)
    length = int(max(0.03, dur_sec) * sr)
    if start >= len(target) or length <= 0:
        return
    length = min(length, len(target) - start)
    t = np.arange(length, dtype=np.float32) / float(sr)
    freq = _midi_to_hz(midi)
    synth = synth.lower()
    if "chip" in synth or "8bit" in synth or "square" in synth:
        wave_data = _bandlimited_square(freq, t, sr, harmonics=13)
        env = _adsr(length, sr, 0.004, 0.04, 0.45, 0.035)
    elif "acid" in synth or "bass" in synth and "warm" not in synth:
        sweep = np.exp(-t * (5.0 / max(dur_sec, 0.05))).astype(np.float32)
        wave_data = 0.60 * np.sin(2 * np.pi * freq * t).astype(np.float32) + 0.40 * _bandlimited_saw(freq, t, sr, 0.35 + 0.55 * sweep.mean())
        wave_data = np.tanh(wave_data * (1.25 + 1.2 * sweep))
        env = _adsr(length, sr, 0.006, 0.05, 0.62, 0.060)
    elif "pad" in synth or "drone" in synth:
        detune = 0.003 + 0.003 * brightness
        wave_data = (
            0.48 * np.sin(2 * np.pi * freq * t)
            + 0.28 * np.sin(2 * np.pi * freq * (1.0 + detune) * t + 0.4)
            + 0.18 * np.sin(2 * np.pi * freq * 2.0 * t + 1.1)
            + 0.10 * np.sin(2 * np.pi * freq * 3.0 * t + 2.0)
        ).astype(np.float32)
        env = _adsr(length, sr, min(0.8, dur_sec * 0.35), 0.2, 0.82, min(1.0, dur_sec * 0.35))
    elif "bell" in synth or "fm" in synth:
        mod_env = np.exp(-t * 5.0).astype(np.float32)
        mod = np.sin(2 * np.pi * freq * 2.01 * t).astype(np.float32) * (3.0 + 5.0 * brightness) * mod_env
        wave_data = np.sin(2 * np.pi * freq * t + mod).astype(np.float32) + 0.25 * np.sin(2 * np.pi * freq * 3.0 * t).astype(np.float32)
        env = _adsr(length, sr, 0.006, 0.18, 0.18, min(0.65, dur_sec * 0.5))
    elif "pluck" in synth or "key" in synth or "warm" in synth:
        wave_data = (0.70 * np.sin(2 * np.pi * freq * t) + 0.20 * _triangle(freq * 2.0, t, sr) + 0.10 * _bandlimited_square(freq, t, sr, 7)).astype(np.float32)
        env = _adsr(length, sr, 0.008, 0.12, 0.35, 0.18)
    else:
        wave_data = (0.55 * _bandlimited_saw(freq, t, sr, brightness) + 0.35 * np.sin(2 * np.pi * freq * t).astype(np.float32) + 0.10 * _triangle(freq, t, sr)).astype(np.float32)
        env = _adsr(length, sr, 0.010, 0.08, 0.55, 0.10)
    # Tiny deterministic imperfection for lofi/warm tones, never enough to detune out of key.
    if "warm" in synth or "lofi" in synth:
        wobble = 1.0 + 0.006 * np.sin(2 * np.pi * 3.1 * t + float(rng.random()))
        wave_data *= wobble.astype(np.float32)
    _clip_add(target, start, wave_data * env, velocity)


def _add_kick(target: np.ndarray, start_sec: float, sr: int, amp: float = 1.0) -> None:
    length = int(0.52 * sr)
    t = np.arange(length, dtype=np.float32) / float(sr)
    freq = 48.0 + 82.0 * np.exp(-t * 13.0)
    phase = 2 * np.pi * np.cumsum(freq) / float(sr)
    env = np.exp(-t * 8.0).astype(np.float32)
    click = np.exp(-t * 90.0).astype(np.float32) * np.sin(2 * np.pi * 850.0 * t).astype(np.float32)
    body = np.sin(phase).astype(np.float32)
    _clip_add(target, int(start_sec * sr), np.tanh((body * env + 0.18 * click) * 1.8), 0.90 * amp)


def _add_snare(target: np.ndarray, start_sec: float, sr: int, rng: np.random.Generator, amp: float = 1.0) -> None:
    length = int(0.34 * sr)
    t = np.arange(length, dtype=np.float32) / float(sr)
    noise = rng.standard_normal(length).astype(np.float32)
    noise = noise - _moving_average(noise, 12)
    env = np.exp(-t * 13.0).astype(np.float32)
    tone = np.sin(2 * np.pi * 185.0 * t).astype(np.float32) * np.exp(-t * 9.0).astype(np.float32)
    _clip_add(target, int(start_sec * sr), np.tanh((0.65 * noise + 0.25 * tone) * env * 1.4), 0.48 * amp)


def _add_hat(target: np.ndarray, start_sec: float, sr: int, rng: np.random.Generator, amp: float = 1.0, open_hat: bool = False) -> None:
    length = int((0.18 if open_hat else 0.065) * sr)
    t = np.arange(length, dtype=np.float32) / float(sr)
    noise = rng.standard_normal(length).astype(np.float32)
    noise = noise - _moving_average(noise, 6)
    env = np.exp(-t * (11.0 if open_hat else 42.0)).astype(np.float32)
    _clip_add(target, int(start_sec * sr), noise * env, (0.16 if open_hat else 0.10) * amp)


def _add_clap(target: np.ndarray, start_sec: float, sr: int, rng: np.random.Generator, amp: float = 1.0) -> None:
    for offset in (0.0, 0.012, 0.026):
        length = int(0.12 * sr)
        t = np.arange(length, dtype=np.float32) / float(sr)
        noise = rng.standard_normal(length).astype(np.float32)
        env = np.exp(-t * 20.0).astype(np.float32)
        _clip_add(target, int((start_sec + offset) * sr), noise * env, 0.11 * amp)


def _moving_average(audio: np.ndarray, window: int) -> np.ndarray:
    window = int(max(1, window))
    if window <= 1 or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    padded = np.pad(audio.astype(np.float32, copy=False), (window - 1, 0), mode="edge")
    cumsum = np.cumsum(padded, dtype=np.float64)
    cumsum = np.pad(cumsum, (1, 0), mode="constant")
    out = (cumsum[window:] - cumsum[:-window]) / float(window)
    return out[: len(audio)].astype(np.float32)


def _delay(audio: np.ndarray, sr: int, bpm: int, wet: float = 0.16, feedback: float = 0.34, dotted: bool = False) -> np.ndarray:
    if wet <= 0.0 or len(audio) == 0:
        return audio
    beat = 60.0 / max(55, min(180, bpm))
    delay_sec = beat * (0.75 if dotted else 0.5)
    delay_n = max(1, int(delay_sec * sr))
    out = audio.astype(np.float32, copy=True)
    tap = audio.astype(np.float32, copy=False)
    for repeat in range(1, 4):
        d = delay_n * repeat
        if d >= len(out):
            break
        out[d:] += tap[:-d] * (wet * (feedback ** (repeat - 1)))
    return out.astype(np.float32)


def _reverb(audio: np.ndarray, sr: int, wet: float = 0.22) -> np.ndarray:
    if wet <= 0.0 or len(audio) == 0:
        return audio
    out = audio.astype(np.float32, copy=True)
    taps = [(0.019, 0.28), (0.031, 0.22), (0.047, 0.18), (0.073, 0.14), (0.109, 0.10), (0.151, 0.07)]
    for delay_sec, gain in taps:
        d = int(delay_sec * sr)
        if d <= 0 or d >= len(out):
            continue
        out[d:] += audio[:-d] * (wet * gain)
    return out.astype(np.float32)


def _soft_saturate(audio: np.ndarray, drive: float) -> np.ndarray:
    drive = max(0.2, float(drive))
    return (np.tanh(audio * drive) / math.tanh(drive)).astype(np.float32)


def _sidechain_envelope(length: int, sr: int, kick_times: list[float], amount: float, release_sec: float) -> np.ndarray:
    env = np.ones(length, dtype=np.float32)
    if amount <= 0.0:
        return env
    release_n = max(1, int(release_sec * sr))
    curve = 1.0 - amount * np.exp(-np.linspace(0.0, 5.0, release_n, dtype=np.float32))
    curve = np.clip(curve, 0.15, 1.0)
    for kick_time in kick_times:
        start = int(kick_time * sr)
        if start >= length:
            continue
        end = min(length, start + release_n)
        env[start:end] = np.minimum(env[start:end], curve[: end - start])
    return env


def _section_for_bar(composer: dict[str, Any], bar: int) -> dict[str, Any]:
    sections = composer.get("sections") or []
    for section in sections:
        start = int(section.get("start_bar", 0))
        end = start + int(section.get("bars", 1))
        if start <= bar < end:
            return section
    return sections[-1] if sections else {"id": "A", "role": "hook", "energy": 0.8, "start_bar": 0, "bars": 4}


def _chord_for_bar(composer: dict[str, Any], bar: int) -> dict[str, Any]:
    progression = composer.get("chords", {}).get("progression") or []
    if not progression:
        return {"scale_degrees": [0, 2, 4], "bass_degree": 0, "name": "i"}
    return progression[bar % len(progression)]


def _step_time(bar: int, step: int, step_sec: float, swing: float = 0.5) -> float:
    sixteenth = bar * 16 + step
    swing_offset = 0.0
    if sixteenth % 2 == 1:
        swing_offset = (swing - 0.5) * step_sec
    return (bar * 16 + step) * step_sec + swing_offset


def _make_loopable(audio: np.ndarray, sr: int) -> np.ndarray:
    if len(audio) < sr // 2:
        return audio
    fade = min(int(0.18 * sr), len(audio) // 12)
    if fade <= 8:
        return audio
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    head = audio[:fade].copy()
    tail = audio[-fade:].copy()
    blend = tail * (1.0 - ramp) + head * ramp
    audio[:fade] = blend
    audio[-fade:] = blend
    return audio


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int, target_peak: float = 0.92) -> None:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    if len(audio) == 0:
        audio = np.zeros(sample_rate, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 1e-6:
        audio = audio / peak * float(target_peak)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


class PythonMusicRenderer:
    """Fixed fallback renderer with a local structured composition layer.

    This renderer is intentionally dependency-light: it only uses numpy and the standard library.  It
    consumes a RenderPlan/CompositionPlan rather than inventing a short note list at audio time, so model
    failures still produce a theme, answer phrase, bass/kick relationship, section movement, and simple
    production processing.
    """

    def render(
        self,
        spec: MusicSpec,
        plan: RenderPlan,
        wav_path: Path,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        diversity_level: int = DEFAULT_DIVERSITY_LEVEL,
    ) -> None:
        sample_rate = _safe_int(sample_rate, DEFAULT_SAMPLE_RATE, 16000, 48000)
        duration = _safe_int(spec.duration, DEFAULT_DURATION, 5, HARD_MAX_SECONDS)
        n = int(duration * sample_rate)
        music = np.zeros(n, dtype=np.float32)
        drums = np.zeros(n, dtype=np.float32)
        texture = np.zeros(n, dtype=np.float32)
        composer = plan.composer or _default_plan(spec, diversity_level).composer
        root = int(composer.get("root_midi", 60))
        scale = [int(x) for x in composer.get("scale", MINOR_SCALE)] or MINOR_SCALE
        profile = str(composer.get("style_profile", _profile_name_from_text(spec.mood)))
        bpm = _safe_int(composer.get("bpm", spec.bpm), spec.bpm, 55, 180)
        beat_sec = 60.0 / float(bpm)
        bar_sec = beat_sec * 4.0
        step_sec = bar_sec / 16.0
        total_bars = max(1, int(math.ceil(duration / bar_sec)))
        seed = int(composer.get("seed", _stable_seed(spec.mood)))
        rng = np.random.default_rng(seed)
        mix = composer.get("mix", {}) if isinstance(composer.get("mix"), dict) else {}
        automation = composer.get("automation", {}) if isinstance(composer.get("automation"), dict) else {}
        drum_gain = _safe_float(mix.get("drum_gain"), 0.72, 0.0, 1.5)
        bass_gain = _safe_float(mix.get("bass_gain"), 0.55, 0.0, 1.5)
        chord_gain = _safe_float(mix.get("chord_gain"), 0.42, 0.0, 1.5)
        lead_gain = _safe_float(mix.get("lead_gain"), 0.38, 0.0, 1.5)
        texture_gain = _safe_float(mix.get("texture_gain"), 0.12, 0.0, 1.0)
        brightness = _safe_float(spec.brightness, 0.6, 0.0, 1.0)
        energy = _safe_float(spec.energy, 0.55, 0.0, 1.0)
        density = _safe_float(spec.density, 0.55, 0.0, 1.0)
        swing = _safe_float(composer.get("drums", {}).get("swing", plan.drums.get("swing", 0.5)), 0.5, 0.45, 0.65)
        motif_call = composer.get("motifs", {}).get("call", [])
        motif_response = composer.get("motifs", {}).get("response", [])
        motif_b = composer.get("motifs", {}).get("b_variation", [])
        kick_times: list[float] = []

        self._render_harmony(music, spec, plan, composer, root, scale, sample_rate, total_bars, step_sec, chord_gain, brightness, rng)
        self._render_bass(music, spec, composer, root, scale, sample_rate, total_bars, step_sec, bass_gain, brightness, profile, rng)
        self._render_melody(
            music,
            spec,
            plan,
            composer,
            root,
            scale,
            sample_rate,
            total_bars,
            step_sec,
            lead_gain,
            brightness,
            profile,
            motif_call,
            motif_response,
            motif_b,
            rng,
        )
        self._render_drums(drums, spec, composer, sample_rate, total_bars, step_sec, drum_gain, density, profile, swing, rng, kick_times)
        self._render_texture(texture, spec, composer, sample_rate, duration, texture_gain, profile, rng)
        if plan.effects.get("riser", True):
            self._render_transitions(texture, composer, sample_rate, duration, step_sec, texture_gain, profile, rng)

        if plan.effects.get("sidechain", True):
            amount = _safe_float(automation.get("sidechain_amount"), 0.22, 0.0, 0.65)
            duck = _sidechain_envelope(n, sample_rate, kick_times, amount, release_sec=0.30 * beat_sec)
            music *= duck
            texture *= np.maximum(duck, 0.82)

        if plan.effects.get("delay", True):
            music = _delay(music, sample_rate, bpm, wet=_safe_float(mix.get("delay_wet"), 0.14, 0.0, 0.45), feedback=0.33, dotted=profile in {"trance", "ambient", "synthwave"})
        if plan.effects.get("reverb", True):
            music = _reverb(music, sample_rate, wet=_safe_float(mix.get("reverb_wet"), 0.22, 0.0, 0.65))
            texture = _reverb(texture, sample_rate, wet=min(0.50, _safe_float(mix.get("reverb_wet"), 0.22, 0.0, 0.65) + 0.12))
        combined = music + drums + texture
        if plan.effects.get("lowpass", False) or profile in {"lofi", "ambient"}:
            window = 3 if profile != "lofi" else 5
            combined = 0.74 * combined + 0.26 * _moving_average(combined, window)
        combined = _soft_saturate(combined, _safe_float(mix.get("soft_saturation_drive"), 1.45, 0.5, 3.0))
        # Avoid hard digital start/end clicks even when the music is not loopable.
        fade = min(int(0.018 * sample_rate), n // 20)
        if fade > 8:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            combined[:fade] *= ramp
            combined[-fade:] *= ramp[::-1]
        if spec.loopable:
            combined = _make_loopable(combined, sample_rate)
        _write_wav(wav_path, combined, sample_rate, _safe_float(plan.master.get("target_peak"), 0.92, 0.3, 0.98))

    def _render_harmony(
        self,
        target: np.ndarray,
        spec: MusicSpec,
        plan: RenderPlan,
        composer: dict[str, Any],
        root: int,
        scale: list[int],
        sr: int,
        total_bars: int,
        step_sec: float,
        gain: float,
        brightness: float,
        rng: np.random.Generator,
    ) -> None:
        profile = str(composer.get("style_profile", "electronic"))
        voicing = str(plan.chords.get("voicing", composer.get("chords", {}).get("voicing", "soft")))
        synth = "pad" if voicing in {"drone", "open"} or profile in {"ambient", "synthwave"} else "warm_keys"
        for bar in range(total_bars):
            section = _section_for_bar(composer, bar)
            role = str(section.get("role", "hook"))
            energy_mul = _safe_float(section.get("energy"), 0.8, 0.1, 1.2)
            chord = _chord_for_bar(composer, bar)
            degrees = [int(x) for x in chord.get("scale_degrees", [0, 2, 4])]
            if role == "break" and profile not in {"ambient", "lofi"}:
                degrees = degrees[:2]
            if profile == "ambient":
                start = bar * 16 * step_sec
                dur = 15.8 * step_sec
                octave = -1 if bar % 2 == 0 else 0
                for idx, degree in enumerate(degrees[:4]):
                    midi = _degree_to_midi(root, scale, degree, octave + (1 if idx >= 2 else 0))
                    _add_note(target, start + idx * 0.015, dur, midi, gain * 0.26 * energy_mul, sr, "pad", brightness, rng)
            elif voicing == "stabs":
                for step in (0, 6, 8, 14):
                    if role == "intro" and step in {6, 14}:
                        continue
                    start = _step_time(bar, step, step_sec, swing=0.5)
                    for idx, degree in enumerate(degrees[:3]):
                        midi = _degree_to_midi(root, scale, degree, 0 + (1 if idx == 2 else 0))
                        _add_note(target, start, 1.3 * step_sec, midi, gain * 0.18 * energy_mul, sr, "warm_keys", brightness, rng)
            else:
                for step in (0, 8):
                    if role == "intro" and step == 8 and spec.duration > 20:
                        continue
                    start = _step_time(bar, step, step_sec, swing=0.5)
                    dur = 6.0 * step_sec if voicing == "open" else 3.5 * step_sec
                    for idx, degree in enumerate(degrees[:4]):
                        midi = _degree_to_midi(root, scale, degree, -1 + (idx // 2))
                        _add_note(target, start + idx * 0.01, dur, midi, gain * 0.17 * energy_mul, sr, synth, brightness, rng)

    def _render_bass(
        self,
        target: np.ndarray,
        spec: MusicSpec,
        composer: dict[str, Any],
        root: int,
        scale: list[int],
        sr: int,
        total_bars: int,
        step_sec: float,
        gain: float,
        brightness: float,
        profile: str,
        rng: np.random.Generator,
    ) -> None:
        pattern = composer.get("bass", {}).get("pattern", [])
        tone = str(composer.get("bass", {}).get("tone", "warm"))
        synth = "acid_bass" if tone == "acid" else "warm_bass" if tone == "warm" else "sub_bass"
        for bar in range(total_bars):
            section = _section_for_bar(composer, bar)
            role = str(section.get("role", "hook"))
            if role == "intro" and profile not in {"ambient", "lofi"} and bar % 2 == 1:
                continue
            if role == "break" and profile not in {"ambient", "lofi"}:
                continue
            chord = _chord_for_bar(composer, bar)
            bass_degree = int(chord.get("bass_degree", 0))
            energy_mul = _safe_float(section.get("energy"), 0.8, 0.1, 1.2)
            for event in pattern:
                step = int(event.get("step", 0))
                degree = bass_degree
                if int(event.get("degree", 0)) in {4, 5}:
                    degree = int(chord.get("scale_degrees", [bass_degree, 2, 4])[min(2, len(chord.get("scale_degrees", [0, 2, 4])) - 1)])
                elif int(event.get("degree", 0)) not in {0}:
                    degree = (bass_degree + int(event.get("degree", 0))) % len(scale)
                octave = int(event.get("octave", -2))
                if role in {"drop", "return"} and profile in {"acid techno", "melodic techno", "trance"} and step in {10, 14}:
                    octave += 1
                start = _step_time(bar, step, step_sec, swing=0.5)
                dur = max(0.08, float(event.get("length_steps", 2)) * step_sec * 0.92)
                midi = _degree_to_midi(root, scale, degree, octave)
                amp = gain * float(event.get("accent", 0.82)) * energy_mul
                _add_note(target, start + 0.012, dur, midi, amp, sr, synth, brightness, rng)

    def _render_melody(
        self,
        target: np.ndarray,
        spec: MusicSpec,
        plan: RenderPlan,
        composer: dict[str, Any],
        root: int,
        scale: list[int],
        sr: int,
        total_bars: int,
        step_sec: float,
        gain: float,
        brightness: float,
        profile: str,
        motif_call: list[dict[str, Any]],
        motif_response: list[dict[str, Any]],
        motif_b: list[dict[str, Any]],
        rng: np.random.Generator,
    ) -> None:
        register = str(plan.melody.get("register", "mid"))
        base_oct = 1 if register == "mid" else 2 if register == "high" else 0
        synth = "chip_lead" if profile == "8bit" else "bell" if profile == "ambient" else "pluck" if profile in {"lofi", "breakbeat"} else "saw_lead"
        for bar in range(total_bars):
            section = _section_for_bar(composer, bar)
            role = str(section.get("role", "hook"))
            energy_mul = _safe_float(section.get("energy"), 0.8, 0.1, 1.2)
            if role == "intro" and bar % 2 == 1 and profile not in {"8bit", "trance"}:
                continue
            if role == "break":
                motif = motif_response[::2] if motif_response else motif_call[::2]
            elif role in {"variation", "drop", "return"}:
                motif = motif_b if motif_b else motif_call
            elif bar % 2:
                motif = motif_response if motif_response else motif_call
            else:
                motif = motif_call
            chord = _chord_for_bar(composer, bar)
            chord_degrees = [int(x) for x in chord.get("scale_degrees", [0, 2, 4])]
            for event in motif:
                step = int(event.get("step", 0))
                degree = int(event.get("degree", 0))
                if event.get("strong_chord_tone") or step % 8 in {0, 4}:
                    degree = chord_degrees[(step // 4) % len(chord_degrees)]
                octave = base_oct + int(event.get("octave", 0))
                if role in {"drop", "return"} and profile not in {"ambient", "lofi"}:
                    octave += 1 if step >= 8 else 0
                start = _step_time(bar, step, step_sec, swing=0.5 if profile != "lofi" else 0.57)
                length_steps = float(event.get("length_steps", 2))
                dur = max(0.05, step_sec * length_steps * (0.86 if profile == "8bit" else 1.08))
                midi = _degree_to_midi(root, scale, degree, octave)
                amp = gain * float(event.get("velocity", 0.75)) * energy_mul
                if role == "outro":
                    amp *= 0.55
                _add_note(target, start, dur, midi, amp, sr, synth, brightness, rng)
            # Counter or pingpong arpeggio gives electronic identity without relying on the main hook only.
            if profile in {"8bit", "trance", "synthwave"} or (profile in {"melodic techno", "acid techno"} and role in {"drop", "build", "return"}):
                chord_degrees = [int(x) for x in chord.get("scale_degrees", [0, 2, 4])]
                arp_steps = list(range(0, 16, 2)) if profile != "8bit" else list(range(0, 16, 1))
                seq = chord_degrees + list(reversed(chord_degrees[1:-1] or chord_degrees))
                for idx, step in enumerate(arp_steps):
                    if role == "intro" and idx % 2:
                        continue
                    degree = seq[(idx + bar) % len(seq)]
                    midi = _degree_to_midi(root, scale, degree, base_oct - 1 + (1 if idx % 4 == 3 else 0))
                    _add_note(target, _step_time(bar, step, step_sec, swing=0.5), step_sec * 0.72, midi, gain * 0.22 * energy_mul, sr, "chip_lead" if profile == "8bit" else "pluck", brightness, rng)

    def _render_drums(
        self,
        target: np.ndarray,
        spec: MusicSpec,
        composer: dict[str, Any],
        sr: int,
        total_bars: int,
        step_sec: float,
        gain: float,
        density: float,
        profile: str,
        swing: float,
        rng: np.random.Generator,
        kick_times: list[float],
    ) -> None:
        drums = composer.get("drums", {}) if isinstance(composer.get("drums"), dict) else {}
        if plan_is_ambient_no_drums := (profile == "ambient" and spec.energy < 0.45):
            # Ambient still gets very quiet pulse/noise ticks so it has motion without becoming a beat.
            for bar in range(total_bars):
                if bar % 2 == 0:
                    t = _step_time(bar, 0, step_sec, swing=0.5)
                    kick_times.append(t)
                    _add_kick(target, t, sr, amp=gain * 0.13)
            return
        kick_steps = [int(x) for x in drums.get("kick_steps", [0, 4, 8, 12])]
        snare_steps = [int(x) for x in drums.get("snare_steps", [4, 12])]
        hat_steps = [int(x) for x in drums.get("hat_steps", [0, 3, 6, 9, 12, 15])]
        open_hat_steps = [int(x) for x in drums.get("open_hat_steps", [6, 14])]
        for bar in range(total_bars):
            section = _section_for_bar(composer, bar)
            role = str(section.get("role", "hook"))
            energy_mul = _safe_float(section.get("energy"), 0.8, 0.1, 1.2)
            phrase_end = (bar + 1) % int(composer.get("phrase_bars", 4) or 4) == 0
            local_kicks = list(kick_steps)
            if role == "intro" and profile not in {"lofi", "breakbeat"}:
                local_kicks = [0, 8]
            if role == "break":
                local_kicks = [0] if profile not in {"breakbeat"} else [0, 7]
            if phrase_end and role in {"build", "drop", "return"}:
                local_kicks = sorted(set(local_kicks + [14, 15] if profile == "breakbeat" else local_kicks + [14]))
            for step in local_kicks:
                t = _step_time(bar, step, step_sec, swing=0.5)
                kick_times.append(t)
                _add_kick(target, t, sr, amp=gain * energy_mul)
            for step in snare_steps:
                if role == "intro" and step == snare_steps[-1] and spec.duration > 25:
                    continue
                t = _step_time(bar, step, step_sec, swing=swing)
                if profile in {"house", "melodic techno", "acid techno", "trance"}:
                    _add_clap(target, t, sr, rng, amp=gain * 0.72 * energy_mul)
                else:
                    _add_snare(target, t, sr, rng, amp=gain * energy_mul)
            for step in hat_steps:
                if role == "intro" and step % 4 not in {0, 2} and density < 0.65:
                    continue
                if role == "break" and step % 4 != 0:
                    continue
                t = _step_time(bar, step, step_sec, swing=swing)
                accent = 1.25 if step in open_hat_steps else 0.86
                _add_hat(target, t, sr, rng, amp=gain * accent * (0.75 + 0.35 * density) * energy_mul, open_hat=False)
            for step in open_hat_steps:
                if role in {"intro", "break"} and profile not in {"breakbeat", "lofi"}:
                    continue
                t = _step_time(bar, step, step_sec, swing=swing)
                _add_hat(target, t, sr, rng, amp=gain * 0.85 * energy_mul, open_hat=True)
            if phrase_end:
                fill_steps = [13, 14, 15] if profile in {"breakbeat", "8bit"} else [14, 15]
                for idx, step in enumerate(fill_steps):
                    t = _step_time(bar, step, step_sec, swing=swing)
                    _add_snare(target, t, sr, rng, amp=gain * (0.28 + idx * 0.08) * energy_mul)
                    _add_hat(target, t + 0.018, sr, rng, amp=gain * 0.60 * energy_mul, open_hat=False)

    def _render_texture(
        self,
        target: np.ndarray,
        spec: MusicSpec,
        composer: dict[str, Any],
        sr: int,
        duration: int,
        gain: float,
        profile: str,
        rng: np.random.Generator,
    ) -> None:
        n = len(target)
        if gain <= 0 or n == 0:
            return
        noise = rng.standard_normal(n).astype(np.float32)
        if profile in {"lofi", "synthwave"}:
            noise = _moving_average(noise, 72)
            wobble = 0.70 + 0.30 * np.sin(2 * np.pi * np.arange(n, dtype=np.float32) / float(sr) * 0.23 + 1.4)
            target += noise * wobble.astype(np.float32) * gain * 0.16
            # Vinyl-like sparse crackles.
            crackle_count = min(160, max(4, duration * 3))
            positions = rng.integers(0, n, size=crackle_count)
            for pos in positions:
                length = min(n - int(pos), int(0.006 * sr))
                if length > 0:
                    target[int(pos) : int(pos) + length] += rng.standard_normal(length).astype(np.float32) * np.exp(-np.linspace(0, 8, length)).astype(np.float32) * gain * 0.08
        elif profile in {"ambient", "ambient techno"}:
            slow = _moving_average(noise, max(32, int(0.018 * sr)))
            movement = 0.45 + 0.55 * np.sin(2 * np.pi * np.arange(n, dtype=np.float32) / float(sr) * 0.035 + 0.8)
            target += slow * movement.astype(np.float32) * gain * 0.32
        else:
            target += _moving_average(noise, 24) * gain * 0.06

    def _render_transitions(
        self,
        target: np.ndarray,
        composer: dict[str, Any],
        sr: int,
        duration: int,
        step_sec: float,
        gain: float,
        profile: str,
        rng: np.random.Generator,
    ) -> None:
        if profile == "ambient":
            return
        sections = composer.get("sections") or []
        for section in sections:
            role = str(section.get("role", ""))
            if role not in {"drop", "return"}:
                continue
            start_bar = int(section.get("start_bar", 0))
            start_sec = start_bar * 16 * step_sec
            riser_len = min(2.0, max(0.6, 12 * step_sec))
            riser_start = max(0.0, start_sec - riser_len)
            length = int(riser_len * sr)
            if length <= 0:
                continue
            t = np.linspace(0.0, 1.0, length, dtype=np.float32)
            noise = rng.standard_normal(length).astype(np.float32)
            sweep = _moving_average(noise, max(3, int((1.0 - t.mean() * 0.6) * 16)))
            sweep *= (t ** 1.8).astype(np.float32)
            _clip_add(target, int(riser_start * sr), sweep, gain * 0.35)
            down_len = int(min(1.2, max(0.35, 8 * step_sec)) * sr)
            if down_len > 0:
                tt = np.linspace(0.0, 1.0, down_len, dtype=np.float32)
                down = rng.standard_normal(down_len).astype(np.float32)
                down = _moving_average(down, 28) * np.exp(-tt * 5.0).astype(np.float32)
                _clip_add(target, int(start_sec * sr), down, gain * 0.28)


def _event_plain_result(event: AstrMessageEvent, text: str) -> Any:
    try:
        return event.chain_result([Plain(text)])
    except Exception:
        return event.plain_result(text)


@register(
    "astrbot_plugin_pymusic",
    "Lenovo",
    "Generate structured pure-Python WAV electronic music from prompts and send it to QQ chats.",
    "v0.4.6",
    repo="https://github.com/blueraina/astrbot_plugin_pymusic",
)
class PyMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config
        self.context = context
        self.data_dir = _get_data_dir()
        self.renderer = PythonMusicRenderer()
        self.rate_limiter = RateLimiter(cooldown_sec=30)
        self.render_sem = asyncio.Semaphore(1)

    @filter.llm_tool(name="generate_python_music")
    async def generate_python_music(
        self,
        event: AstrMessageEvent,
        prompt: str,
        duration: float = DEFAULT_DURATION,
        loopable: bool = False,
        send_mode: str = "auto",
    ) -> str:
        """Generate and send a pure Python WAV music clip.

        Use this tool only when the user explicitly asks to generate music, send music, make a music clip, or express something with music.

        Args:
            prompt(string): Music prompt, such as melodic techno winter city, 8bit battle, ambient stars, synthwave neon road, or lofi rainy cafe.
            duration(number): Requested duration in seconds.
            loopable(boolean): Whether the result should be loopable.
            send_mode(string): voice, file, or auto.
        """
        if not self._is_supported_platform(event):
            return "pymusic only supports QQ personal-account adapters and QQ official adapters."
        max_duration = self._max_duration()
        duration_int = _safe_int(duration, self._default_duration(), 5, max_duration)
        send_mode = _normalize_send_mode(send_mode, self._default_send_mode())
        prompt = f"{prompt}\nRequested duration: {duration_int}s. Loopable: {loopable}. Send mode: {send_mode}."
        async with self.render_sem:
            try:
                brief, spec, plan, wav_path = await self._generate(prompt, event, duration_int, loopable, send_mode)
            except Exception as exc:
                logger.exception("[pymusic] generation failed")
                return f"pymusic failed to generate music: {exc}"
        try:
            sent_mode = await self._send_music(event, wav_path, spec)
        except Exception as exc:
            logger.exception("[pymusic] send failed")
            return f"Generated a {spec.duration}s {spec.mood} WAV music clip, but sending failed: {exc}"
        self._cleanup_history()
        return (
            f"Generated a {spec.duration}s {spec.mood} WAV music clip and sent it as {sent_mode}. "
            f"Enriched prompt: {brief.enriched_prompt[:220]}"
        )

    @filter.command("pymusic")
    async def pymusic(self, event: AstrMessageEvent) -> Any:
        if not self._is_supported_platform(event):
            yield _event_plain_result(event, "pymusic 目前只支持 QQ 个人号适配器和 QQ 官方机器人。")
            return
        payload = self._message_text(event).strip()
        requested_duration, prompt = _parse_command_payload(payload)
        if requested_duration is None or not prompt:
            yield _event_plain_result(event, "用法：/pymusic 时间(秒) 提示词\n例如：/pymusic 30 寒冬 melodic techno 可循环")
            return
        cooldown_key = self._cooldown_key(event)
        wait = self.rate_limiter.check(cooldown_key)
        if wait > 0:
            yield _event_plain_result(event, f"pymusic 冷却中，请 {wait} 秒后再试。")
            return
        yield _event_plain_result(event, "正在用纯 Python 合成音乐：先规划主题/段落/鼓贝斯配合，再渲染 WAV。")
        overrides = _prompt_overrides(prompt)
        duration = _safe_int(requested_duration, self._default_duration(), 5, self._max_duration())
        loopable = self._waveform_loopable() if overrides["loopable"] is None else bool(overrides["loopable"])
        send_mode = _normalize_send_mode(overrides["send_mode"], self._default_send_mode())
        async with self.render_sem:
            try:
                brief, spec, plan, wav_path = await self._generate(prompt, event, duration, loopable, send_mode)
            except Exception as exc:
                logger.exception("[pymusic] generation failed")
                yield _event_plain_result(event, f"生成失败：{exc}")
                return
        try:
            sent_mode = await self._send_music(event, wav_path, spec)
        except Exception as exc:
            logger.exception("[pymusic] send failed")
            yield _event_plain_result(event, f"音乐已生成，但发送失败：{wav_path}\n{exc}")
            return
        section_names = ", ".join(str(s.get("id")) for s in plan.composer.get("sections", [])[:5])
        yield _event_plain_result(
            event,
            f"pymusic 已生成：{spec.mood} / {spec.duration}s / {sent_mode}\n"
            f"段落：{section_names}\n"
            f"理解为：{brief.enriched_prompt[:180]}",
        )
        self._cleanup_history()

    async def _generate(
        self,
        prompt: str,
        event: AstrMessageEvent,
        duration: int,
        loopable: bool,
        send_mode: str,
    ) -> tuple[PromptBrief, MusicSpec, RenderPlan, Path]:
        brief = await self._build_prompt_brief(prompt, event, duration, loopable, send_mode)
        spec = await self._build_spec(brief, event, duration, loopable, send_mode)
        if spec.duration > VOICE_MAX_SECONDS and spec.send_mode in {"voice", "auto"}:
            spec.send_mode = "file"
        diversity_level = self._diversity_level()
        variation_strength = self._variation_strength()
        variation_seed = self._generation_variation_seed(prompt, event)
        technique_guides = _select_technique_guides(brief, spec, diversity_level)
        composer = _compose_arrangement(brief, spec, technique_guides, diversity_level, variation_seed, variation_strength)
        plan = _default_plan(spec, diversity_level, composer)
        wav_path = self.data_dir / f"pymusic_{int(time.time())}_{random.randint(1000, 9999)}.wav"
        sample_rate = self._sample_rate()
        ai_code = await self._build_python_renderer_code(
            brief,
            event,
            spec,
            technique_guides,
            composer,
            variation_seed,
            variation_strength,
        )
        if ai_code:
            try:
                await asyncio.to_thread(self._run_ai_python_renderer, ai_code, wav_path, spec.duration, sample_rate, spec.loopable)
            except Exception as exc:
                render_error = _format_exception(exc)
                logger.warning(f"[pymusic] AI Python renderer failed, trying one repair: {render_error}")
                wav_path.unlink(missing_ok=True)
                repaired_code = await self._repair_python_renderer_code(
                    brief,
                    event,
                    spec,
                    technique_guides,
                    composer,
                    ai_code,
                    render_error,
                )
                if repaired_code:
                    try:
                        await asyncio.to_thread(
                            self._run_ai_python_renderer,
                            repaired_code,
                            wav_path,
                            spec.duration,
                            sample_rate,
                            spec.loopable,
                        )
                    except Exception as retry_exc:
                        logger.warning(
                            f"[pymusic] repaired AI Python renderer failed, using fixed renderer fallback: {_format_exception(retry_exc)}"
                        )
                        wav_path.unlink(missing_ok=True)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            plan = await self._build_plan(brief, event, spec, composer)
            await asyncio.to_thread(self.renderer.render, spec, plan, wav_path, sample_rate, diversity_level)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            raise RuntimeError("WAV 文件没有成功生成")
        return brief, spec, plan, wav_path

    async def _build_prompt_brief(
        self,
        prompt: str,
        event: AstrMessageEvent,
        duration: int,
        loopable: bool,
        send_mode: str,
    ) -> PromptBrief:
        fallback = _fallback_brief(prompt)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You are a music prompt producer for a deterministic pure-Python synthesizer. "
            "Rewrite short or vague user input into one professional, concrete music brief that a code-writing synthesizer can implement. "
            "Return one strict JSON object only. Do not include markdown. Do not write Python. "
            "Fields: enriched_prompt string, style string, scene string, musical_intent string, references array of short strings, avoid array of short strings. "
            "The enriched_prompt must describe mood, genre, tempo feel, instruments, drum groove, bassline role, harmony, melody identity, call-response, texture, effects, mix, and arrangement arc. "
            "Infer concrete musical intent for sparse prompts instead of repeating the user's words. "
            "Use references for 5-9 recommended technique ids from the provided catalog, mixing structure, composition, synthesis, effect, and style categories. "
            "Prefer mature electronic arrangement concepts: theme/answer phrase, chord-tone targeting, kick-bass lock, fills, sidechain, riser/downlifter, filter sweep, delay throw, soft saturation. "
            "Use only electronic, chiptune/8bit, ambient, lofi, techno, synthwave, trance, house, breakbeat, or nearby pure-synth styles. "
            "Avoid vocals, lyrics, external samples, and copyrighted artist imitation."
        )
        user_prompt = (
            f"Original user prompt: {prompt}\n"
            f"Defaults: duration={duration}, loopable={loopable}, send_mode={send_mode}. "
            f"Max duration={self._max_duration()}. Diversity level={self._diversity_level()} where 0=stable, 1=balanced, 2=bold.\n"
            "Available technique catalog:\n"
            f"{_technique_catalog()}\n"
            "Make sparse input sound intentional and musical. Prefer technique ids that match the prompt; do not invent artist names or unsupported technique ids."
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                brief = _brief_from_dict(data, prompt, fallback)
                # Always keep core local composition IDs even when the model returns a terse plan.
                core = ["arrangement_motifs", "call_response_theme", "chord_tone_targeting", "bass_kick_lock"]
                brief.references = list(dict.fromkeys(core + brief.references))[:12]
                return brief
        except Exception as exc:
            logger.warning(f"[pymusic] prompt enrichment failed, using fallback: {_format_exception(exc)}")
        return fallback

    async def _build_spec(
        self,
        brief: PromptBrief,
        event: AstrMessageEvent,
        duration: int,
        loopable: bool,
        send_mode: str,
    ) -> MusicSpec:
        fallback = _fallback_spec(f"{brief.original_prompt}\n{brief.enriched_prompt}", duration, self._max_duration(), send_mode, loopable)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        system_prompt = (
            "You convert an enriched music brief into one strict JSON object named MusicSpec. "
            "Do not include markdown. Do not write Python. "
            "Allowed moods/styles include electronic, 8bit, ambient, lofi, melodic techno, synthwave, trance, house, breakbeat, ambient techno, and acid techno. "
            "Fields: mood string, energy number 0..1, brightness number 0..1, density number 0..1, bpm integer 55..180, key string, "
            "instruments array of strings, effects array of strings, duration integer seconds, loopable boolean, send_mode string voice/file/auto. "
            "Choose values that make arrangement and production specific: drums, bass, chords/pad, lead/melody, texture, sidechain, filter, delay/reverb, transition FX. "
            "Use technique ids as stylistic hints; do not output fixed code or a fixed melody."
        )
        user_prompt = (
            f"Original prompt: {brief.original_prompt}\n"
            f"Enriched prompt: {brief.enriched_prompt}\n"
            f"Style: {brief.style}\nScene: {brief.scene}\nMusical intent: {brief.musical_intent}\n"
            f"Technique references: {brief.references}\nAvoid: {brief.avoid}\n"
            f"Defaults: duration={duration}, loopable={loopable}, send_mode={send_mode}. Max duration={self._max_duration()}."
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _spec_from_dict(data, fallback, self._max_duration())
        except Exception as exc:
            logger.warning(f"[pymusic] MusicSpec LLM planning failed, using fallback: {_format_exception(exc)}")
        return fallback

    async def _build_python_renderer_code(
        self,
        brief: PromptBrief,
        event: AstrMessageEvent,
        spec: MusicSpec,
        technique_guides: list[dict[str, Any]],
        composer: CompositionPlan | dict[str, Any],
        variation_seed: int = 0,
        variation_strength: int = DEFAULT_VARIATION_STRENGTH,
    ) -> str | None:
        provider = self._get_music_provider(event)
        if provider is None:
            return None
        composer_data = _composer_to_dict(composer)
        system_prompt = (
            "You write pure Python DSP code for a sandboxed music renderer. Return Python code only, no markdown, no explanation. "
            "The code must define render(duration, sample_rate, loopable); small helper functions are allowed. "
            "render must return a one-dimensional numpy array of float audio samples in -1..1. "
            "Allowed imports: numpy as np, math, random, musicpy as mp. Use musicpy for composition helpers only; do not use musicpy file export, playback, or DAW features. Do not read or write files. "
            "Never import, reference, or use the identifier name wave; the host plugin writes the returned audio array to a WAV file after render() finishes. "
            "Do not call wave.open or any file-writing API. Use variable names like audio, signal, osc, layer, or buffer instead of wave. "
            "Do not use os, sys, subprocess, pathlib, sockets, network, eval, exec, open, __import__, external samples, or any network music-generation API. "
            "The sandbox provides safe_add(target,start,source,gain=1.0), safe_assign(target,start,source), safe_min_assign(target,start,source), and safe_multiply(target,start,source). "
            "Use these host-provided helpers for every partial array write: notes, drum hits, delay taps, reverb tails, sidechain envelopes, filter/automation segments, risers, downlifters, fills, and texture layers. "
            "Never do target[start:end] += source, target[start:end] = source, np.minimum(target[start:end], source), or target[start:end] *= source directly unless start/end are known to cover the full array. "
            "Do not use backslash line continuations; wrap long expressions in parentheses. "
            "Use the provided composition_blueprint / structured_composer_plan as the composition source of truth. Do not invent a single short melody array inside render and loop it unchanged. "
            "Implement the plan's section timeline, chord progression, call motif, response motif, B variation, bass pattern, drum steps, fills, and automation. "
            "Use variation_seed and variation_strength to create a distinct realization when the same short prompt is requested repeatedly. "
            "If variation_strength is 0, keep the core melody stable; if it is 1..3, increasingly vary motif contour, response phrase, bass accents, drum fills, section automation, and effects timing while preserving the brief. "
            "Hard musical requirements: derive beat/bar timing from bpm; create drums or rhythmic texture, bass, chords/pad, and melody/lead/texture layers when stylistically appropriate; "
            "strong melody beats should use chord tones and weak beats may use passing notes; bass should leave room for kick and answer it; drums need accents and fills; "
            "A/B phrases must be related but not identical; include a mini build/drop for short clips and clearer sections for longer clips; "
            "use envelopes, sidechain-like ducking, filter or harmonic brightness motion, riser/downlifter or fill, delay/reverb, and soft saturation where appropriate. "
            "For ambient, drums may be sparse or replaced by pulse/noise motion, but include evolving texture and melodic identity. "
            "For 8bit/chiptune, use pingpong arpeggio, counter melody, and noise drums without harsh constant high notes. "
            "For lofi, use swing, warm keys, vinyl/tape noise, and lowpass color. "
            "For techno/acid/cyber, use Euclidean/percussive groove, acid/subtractive bass, filter automation, and sidechain. "
            "For loopable=True, make phrase lengths periodic, avoid one-shot intros/outros, and keep delay/reverb tails compatible with looping. "
            "Selected technique guides:\n"
            f"{_format_technique_guides(technique_guides)}"
        )
        user_prompt = json.dumps(
            {
                "original_prompt": brief.original_prompt,
                "enriched_prompt": brief.enriched_prompt,
                "style": brief.style,
                "scene": brief.scene,
                "musical_intent": brief.musical_intent,
                "technique_references": brief.references,
                "selected_technique_guides": [
                    {
                        "id": guide["id"],
                        "category": guide.get("category"),
                        "basis": guide.get("basis"),
                        "summary": guide["summary"],
                        "guide": guide["guide"],
                    }
                    for guide in technique_guides
                ],
                "music_spec": spec.__dict__,
                "variation_seed": int(variation_seed),
                "variation_strength": int(variation_strength),
                "composition_blueprint": composer_data,
                "structured_composer_plan": composer_data,
                "implementation_notes": [
                    "Do not import or reference wave. render() must only return a numpy audio array; the host plugin handles WAV encoding and file writing.",
                    "Avoid using wave as a variable name because it is reserved by the sandbox validator; use audio, signal, osc, layer, or buffer.",
                    "Use host-provided safe_add for note/drum/delay/reverb/texture/riser additions.",
                    "Use host-provided safe_min_assign for sidechain ducking envelopes or any np.minimum-style gain writes.",
                    "Use host-provided safe_assign or safe_multiply for partial automation/filter/gain segments.",
                    "Do not write target[start:end] += source or target[start:end] = np.minimum(...) directly for partial segments.",
                    "Do not use backslash line continuations; use parentheses for multi-line expressions.",
                    "Use composition_blueprint.motifs.call / response / b_variation instead of ad hoc one-array melody loops.",
                    "Use variation_seed to initialize any local random generator so repeated identical prompts can sound different when variation_strength > 0.",
                    "Use variation_strength as a musical control, not just a random noise amount: higher values should alter phrase contour, fills, accents, automation and transitions more clearly.",
                    "Use composition_blueprint.chords.progression to target chord tones on steps 0, 4, 8, 12.",
                    "Use composition_blueprint.bass.pattern and drums.kick_steps together so kick and bass breathe.",
                    "Use composition_blueprint.sections to change density, register, drum fill, timbre, and effects.",
                    "Use numpy vector operations where easy; simple loops over musical events are OK.",
                ],
            },
            ensure_ascii=False,
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            code = _extract_python_code(getattr(response, "completion_text", "") or str(response))
            _validate_generated_python(code)
            return code
        except Exception as exc:
            logger.warning(f"[pymusic] AI Python code generation failed, using fixed renderer fallback: {_format_exception(exc)}")
            return None

    async def _repair_python_renderer_code(
        self,
        brief: PromptBrief,
        event: AstrMessageEvent,
        spec: MusicSpec,
        technique_guides: list[dict[str, Any]],
        composer: CompositionPlan | dict[str, Any],
        broken_code: str,
        error_detail: str,
    ) -> str | None:
        provider = self._get_music_provider(event)
        if provider is None:
            return None
        composer_data = _composer_to_dict(composer)
        system_prompt = (
            "You repair sandboxed pure Python DSP renderer code. Return the complete corrected Python code only, no markdown, no explanation. "
            "The corrected code must define render(duration, sample_rate, loopable) and return a one-dimensional numpy float audio array in -1..1. "
            "Allowed imports: numpy as np, math, random, musicpy as mp. Use musicpy for composition helpers only; do not use musicpy file export, playback, or DAW features. Do not read or write files. "
            "Never import, reference, or use the identifier name wave; the host plugin writes the returned array to WAV. "
            "Do not use os, sys, subprocess, pathlib, sockets, network, eval, exec, open, __import__, external samples, or any network music-generation API. "
            "Fix the reported runtime error while preserving the musical intent and composition_blueprint. "
            "The sandbox provides safe_add(target,start,source,gain=1.0), safe_assign(target,start,source), safe_min_assign(target,start,source), and safe_multiply(target,start,source). "
            "Use these helpers for every partial write: notes, drum hits, delay taps, reverb tails, sidechain envelopes, automation/filter/gain segments, risers, downlifters, fills, and texture layers. "
            "Replace direct empty-slice-prone code such as target[start:end] += source, target[start:end] = source, target[start:end] = np.minimum(...), or target[start:end] *= source with the helpers. "
            "For sidechain or ducking gain envelopes specifically, use safe_min_assign(sc_gain, trigger_idx, sc_envelope). "
            "Avoid numpy broadcasting errors from empty target slices, negative indices, or events scheduled past the end of the clip. "
            "Do not use backslash line continuations; wrap long expressions in parentheses."
        )
        user_prompt = json.dumps(
            {
                "runtime_error": error_detail[-4000:],
                "original_prompt": brief.original_prompt,
                "enriched_prompt": brief.enriched_prompt,
                "music_spec": spec.__dict__,
                "composition_blueprint": composer_data,
                "selected_technique_ids": [guide["id"] for guide in technique_guides],
                "broken_code": broken_code[-32000:],
                "repair_checklist": [
                    "Return full corrected code, not a diff.",
                    "Keep render(duration, sample_rate, loopable).",
                    "Do not import or reference wave.",
                    "Use safe_add for note, drum, delay, reverb, riser, downlifter, fill and texture additions.",
                    "Use safe_min_assign for sidechain ducking envelopes and np.minimum-style partial writes.",
                    "Use safe_assign or safe_multiply for automation, filter and gain segments.",
                    "Remove direct partial writes like target[start:end] += source or sc_gain[start:end] = np.minimum(...).",
                    "Clamp/crop all delayed echoes, drum hits, fills, tails and section-transition FX near the end of the clip via the safe helpers.",
                    "Do not use backslash line continuations.",
                ],
            },
            ensure_ascii=False,
        )
        try:
            response = await self._provider_text_chat(provider, user_prompt, system_prompt)
            code = _extract_python_code(getattr(response, "completion_text", "") or str(response))
            _validate_generated_python(code)
            return code
        except Exception as exc:
            logger.warning(f"[pymusic] AI Python repair failed, using fixed renderer fallback: {_format_exception(exc)}")
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

import musicpy as mp
import numpy as np

code_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
duration = int(sys.argv[3])
sample_rate = int(sys.argv[4])
loopable = sys.argv[5] == "1"
source = code_path.read_text(encoding="utf-8")
allowed_modules = {"numpy": np, "math": math, "random": random, "musicpy": mp}
blocked_module_parts = {"ctypeslib", "lib", "testing", "distutils", "f2py"}


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    parts = set(name.split("."))
    if level != 0 or root not in allowed_modules or parts & blocked_module_parts:
        raise ImportError(f"import not allowed: {name}")
    if root == "numpy":
        return __import__(name, globals, locals, fromlist, level)
    if root == "math":
        return math
    if root == "random":
        return random
    if root == "musicpy":
        return mp
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
    "sorted": sorted,
    "reversed": reversed,
    "all": all,
    "any": any,
}


def _safe_runtime_view():
    return {
        "np": np,
        "math": math,
        "random": random,
        "safe_add": safe_add,
        "safe_assign": safe_assign,
        "safe_min_assign": safe_min_assign,
        "safe_multiply": safe_multiply,
    }


def safe_globals():
    return _safe_runtime_view()


def safe_locals():
    return _safe_runtime_view()


def safe_vars(obj=None):
    if obj is None:
        return _safe_runtime_view()
    if obj in (np, math, random):
        return {name: value for name, value in vars(obj).items() if not name.startswith("__")}
    return {}


def safe_dir(obj=None):
    if obj is None:
        return sorted(_safe_runtime_view().keys())
    if obj in (np, math, random):
        return sorted(name for name in dir(obj) if not name.startswith("__"))
    return []


def safe_help(*args, **kwargs):
    return None


def _safe_slice(target, start, source):
    target = np.asarray(target)
    source = np.asarray(source, dtype=np.float32).reshape(-1)
    if len(target) == 0 or len(source) == 0:
        return None
    start = int(start)
    if start >= len(target):
        return None
    if start < 0:
        source = source[-start:]
        start = 0
        if len(source) == 0:
            return None
    end = min(len(target), start + len(source))
    if end <= start:
        return None
    return start, end, source[: end - start]


def safe_add(target, start, source, gain=1.0):
    clipped = _safe_slice(target, start, source)
    if clipped is None:
        return target
    start, end, source = clipped
    target[start:end] += source * float(gain)
    return target


def safe_assign(target, start, source):
    clipped = _safe_slice(target, start, source)
    if clipped is None:
        return target
    start, end, source = clipped
    target[start:end] = source
    return target


def safe_min_assign(target, start, source):
    clipped = _safe_slice(target, start, source)
    if clipped is None:
        return target
    start, end, source = clipped
    target[start:end] = np.minimum(target[start:end], source)
    return target


def safe_multiply(target, start, source):
    clipped = _safe_slice(target, start, source)
    if clipped is None:
        return target
    start, end, source = clipped
    target[start:end] *= source
    return target


env = {
    "__builtins__": safe_builtins,
    "np": np,
    "math": math,
    "random": random,
    "safe_add": safe_add,
    "safe_assign": safe_assign,
    "safe_min_assign": safe_min_assign,
    "safe_multiply": safe_multiply,
}
safe_builtins.update(
    {
        "globals": safe_globals,
        "locals": safe_locals,
        "vars": safe_vars,
        "dir": safe_dir,
        "help": safe_help,
    }
)
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
                [
                    sys.executable,
                    "-c",
                    runner,
                    str(code_path),
                    str(wav_path),
                    str(duration),
                    str(sample_rate),
                    "1" if loopable else "0",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown AI renderer error").strip()[-1000:]
                raise RuntimeError(detail)
        finally:
            code_path.unlink(missing_ok=True)

    async def _build_plan(
        self,
        brief: PromptBrief,
        event: AstrMessageEvent,
        spec: MusicSpec,
        composer: CompositionPlan | dict[str, Any],
    ) -> RenderPlan:
        diversity_level = self._diversity_level()
        fallback = _default_plan(spec, diversity_level, composer)
        provider = self._get_music_provider(event)
        if provider is None:
            return fallback
        composer_data = _composer_to_dict(composer)
        system_prompt = (
            "You convert a MusicSpec and composition_blueprint into one strict JSON object named RenderPlan for a fixed Python renderer. "
            "Do not include markdown. Do not write Python. "
            "Fields: tracks array, drums object, bass object, chords object, melody object, texture object, effects object, master object. "
            "Stay renderer-friendly and preserve the CompositionPlan's theme/answer/B variation, sections, chord-tone targeting, bass-kick relation, and automation. "
            "Respect CompositionPlan variation_seed and variation_strength: repeated identical prompts should not collapse into the same phrase unless variation_strength is 0. "
            "tracks: role melody/chords/bass/drums/texture, name chip_lead/warm_keys/pluck/pad/bell/acid_bass/sub_drone/warm_bass/lofi_drums/noise_texture, optional gain 0.1..1.5. "
            "drums.pattern: lofi_swing, 8bit_arpeggio_beat, ambient_no_drums, breakbeat, minimal_techno. "
            "bass.pattern: root_octave, warm_roots, acid_bass, sub_drone, syncopated_pulse. "
            "chords.progression: i-VI-III-VII, i-iv-V-i, I-V-vi-IV, ii-V-I, modal_drone; chords.voicing: soft/open/stabs/drone. "
            "melody.shape: arpeggio, call_response, pentatonic, stepwise, random_walk, motif_variation; melody.register: low/mid/high. "
            "texture.noise: vinyl/tape/air/space/none. effects: delay/reverb/lowpass/sidechain/riser booleans. master.target_peak 0.3..0.98."
        )
        try:
            technique_guides = _select_technique_guides(brief, spec, diversity_level)
            plan_input = {
                "original_prompt": brief.original_prompt,
                "enriched_prompt": brief.enriched_prompt,
                "technique_references": brief.references,
                "selected_technique_guides": [
                    {"id": guide["id"], "category": guide.get("category"), "basis": guide.get("basis"), "summary": guide["summary"]}
                    for guide in technique_guides
                ],
                "music_spec": spec.__dict__,
                "composition_blueprint": composer_data,
                "composer_plan": composer_data,
                "diversity_level": diversity_level,
                "variation_seed": composer_data.get("variation_seed", 0),
                "variation_strength": composer_data.get("variation_strength", DEFAULT_VARIATION_STRENGTH),
            }
            response = await self._provider_text_chat(provider, json.dumps(plan_input, ensure_ascii=False), system_prompt)
            data = _extract_json(getattr(response, "completion_text", "") or str(response))
            if data:
                return _plan_from_dict(data, fallback, composer_data)
        except Exception as exc:
            logger.warning(f"[pymusic] RenderPlan LLM planning failed, using fallback: {_format_exception(exc)}")
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

    def _deterministic_mode(self) -> bool:
        return bool(_cfg_get(self.config, "deterministic_mode", False))

    def _variation_strength(self) -> int:
        return _safe_int(
            _cfg_get(self.config, "variation_strength", DEFAULT_VARIATION_STRENGTH),
            DEFAULT_VARIATION_STRENGTH,
            0,
            3,
        )

    def _generation_variation_seed(self, prompt: str, event: AstrMessageEvent) -> int:
        if self._deterministic_mode() or self._variation_strength() <= 0:
            return 0
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        prompt_seed = _stable_seed(f"{origin}|{prompt}", "variation-context") & MAX_RANDOM_SEED
        entropy_seed = random.SystemRandom().randrange(1, MAX_RANDOM_SEED)
        return int((entropy_seed ^ (time.time_ns() & MAX_RANDOM_SEED) ^ prompt_seed) or 1)

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
        try:
            return self.context.get_using_provider(event.unified_msg_origin)
        except Exception:
            return None

    async def _provider_text_chat(self, provider: Any, prompt: str, system_prompt: str) -> Any:
        timeout = self._model_call_timeout()
        try:
            return await asyncio.wait_for(
                provider.text_chat(prompt=prompt, system_prompt=system_prompt),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"model text_chat timed out after {timeout}s") from exc

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
