"""End-to-end validation of the real main.py: stubs astrbot, imports the module,
then exercises the fixed renderer across styles and checks loudness / clipping / no-NaN.
Run: python _test_e2e.py   (cleanup: deletes its own temp wavs)
"""
import sys
import types
import wave
from pathlib import Path

import numpy as np

# --- stub the astrbot packages so main.py imports cleanly ---
def _mk(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

astrbot = _mk("astrbot")
api = _mk("astrbot.api")
api.AstrBotConfig = dict
api.logger = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                   error=lambda *a, **k: None, debug=lambda *a, **k: None)
ev = _mk("astrbot.api.event")
ev.AstrMessageEvent = object
ev.MessageChain = object
class _filter:
    @staticmethod
    def command(*a, **k):
        def deco(f): return f
        return deco
    @staticmethod
    def llm_tool(*a, **k):
        def deco(f): return f
        return deco
ev.filter = _filter
star = _mk("astrbot.api.star")
star.Context = object
class _Star:
    def __init__(self, *a, **k): pass
star.Star = _Star
star.register = lambda *a, **k: (lambda cls: cls)
core = _mk("astrbot.core")
msg = _mk("astrbot.core.message")
comp = _mk("astrbot.core.message.components")
comp.File = comp.Plain = comp.Record = object
st = _mk("astrbot.core.star")
stt = _mk("astrbot.core.star.star_tools")
stt.StarTools = types.SimpleNamespace(get_data_dir=lambda *a, **k: Path("."))

import importlib.util
spec = importlib.util.spec_from_file_location("pymusic_main", str(Path(__file__).parent / "main.py"))
main = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = main
spec.loader.exec_module(main)
print("imported main.py OK; musicpy present:", main.mp is not None)

sr = 44100
fails = []

def analyze(path):
    with wave.open(str(path), "rb") as wf:
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    peak = float(np.max(np.abs(a))) if len(a) else 0.0
    rms = float(np.sqrt(np.mean(a**2))) if len(a) else 0.0
    clip_frac = float(np.mean(np.abs(a) > 0.999)) if len(a) else 0.0
    nan = bool(np.isnan(a).any())
    return peak, rms, clip_frac, nan, len(a)

profiles_moods = {
    "lofi": "lofi 雨天咖啡馆 放松",
    "ambient": "ambient 星空 寒冬 空灵",
    "8bit": "8bit 夜晚城市 chiptune",
    "melodic techno": "melodic techno 紧张 黑暗",
    "house": "house groove 律动",
    "trance": "trance 霓虹 上升",
}

made = []
for prof, mood in profiles_moods.items():
    spec_obj = main._fallback_spec(mood, 12, 600, "auto", True)
    plan = main._default_plan(spec_obj, 1)
    out = Path(__file__).parent / f"_test_{prof.replace(' ','_')}.wav"
    main.PythonMusicRenderer().render(spec_obj, plan, out, sr, 1)
    made.append(out)
    peak, rms, clip, nan, ln = analyze(out)
    detected = plan.composer.get("style_profile", "?") if isinstance(plan.composer, dict) else "?"
    print(f"[{prof:>15}] profile={detected:>15} peak={peak:.3f} rms={rms:.4f} clip%={clip:.3%} nan={nan} samples={ln}")
    if detected != prof: fails.append(f"{prof}: style classifier resolved {detected}")
    if nan: fails.append(f"{prof}: NaN in output")
    if peak > 1.0001: fails.append(f"{prof}: peak>1.0 ({peak})")
    if clip > 0.01: fails.append(f"{prof}: clipping {clip:.2%} of samples (harsh)")
    if rms < 0.02: fails.append(f"{prof}: too quiet rms={rms:.4f}")
    if ln < sr * 11: fails.append(f"{prof}: too short {ln}")

# musicpy-absent path: force mp=None and re-render lofi
print("\n-- musicpy-absent fallback --")
saved = main.mp
main.mp = None
try:
    spec_obj = main._fallback_spec("lofi 咖啡馆", 10, 600, "auto", False)
    plan = main._default_plan(spec_obj, 1)
    out = Path(__file__).parent / "_test_nomusicpy.wav"
    main.PythonMusicRenderer().render(spec_obj, plan, out, sr, 0)
    made.append(out)
    peak, rms, clip, nan, ln = analyze(out)
    print(f"[no-musicpy lofi] peak={peak:.3f} rms={rms:.4f} clip%={clip:.3%} nan={nan} samples={ln}")
    if nan or peak > 1.0001 or rms < 0.02: fails.append("no-musicpy render unhealthy")
except Exception as e:
    fails.append(f"no-musicpy render raised: {e!r}")
finally:
    main.mp = saved

# determinism: same seed/spec twice -> identical bytes
print("\n-- determinism (deterministic spec) --")
s1 = main._fallback_spec("lofi 咖啡馆", 8, 600, "auto", False)
p1 = main._default_plan(s1, 0)
o1 = Path(__file__).parent / "_test_det1.wav"
o2 = Path(__file__).parent / "_test_det2.wav"
main.PythonMusicRenderer().render(s1, p1, o1, sr, 0)
main.PythonMusicRenderer().render(s1, p1, o2, sr, 0)
made += [o1, o2]
same = o1.read_bytes() == o2.read_bytes()
print("identical bytes:", same)
# determinism is expected because composer seed is stable for a fixed mood

# limiter stress: impulses and short bursts must never rely on hard clipping
print("\n-- limiter transient stress --")
ceiling = 0.95
stress_cases = []
for pos in [0, 1, 64, 220, 221, 500, sr - 1]:
    a = np.zeros(sr, dtype=np.float32)
    a[pos] = 10.0
    stress_cases.append((f"impulse@{pos}", a))
burst = np.zeros(sr, dtype=np.float32)
burst[1000:1010] = 10.0
stress_cases.append(("burst10", burst))
for name, audio in stress_cases:
    limited = main._limiter(audio, sr, ceiling=ceiling, lookahead_ms=5.0, release_ms=80.0)
    peak = float(np.max(np.abs(limited))) if len(limited) else 0.0
    print(f"{name}: peak={peak:.6f}")
    if peak > ceiling + 1e-5:
        fails.append(f"{name}: limiter leaked peak {peak:.6f} above ceiling {ceiling:.6f}")

for p in made:
    try: p.unlink()
    except Exception: pass

print("\nRESULT:", "ALL PASS" if not fails else "FAILURES:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
