"""Sound design, synthesized in pure Python at startup.

There are no audio assets in this repo and no third-party dependency: every
sound is described as tiny layers of oscillators/noise with ADSR envelopes,
rendered to 16-bit PCM and cached as WAV files under ``.cache/audio``.
Playback is handed to whichever player the machine already has
(``afplay`` / ``aplay`` / ``paplay`` / ``play``) from a dispatcher thread, so
the game loop never waits on audio.

The ambient score is generated in 16-second chunks on a second thread and is
driven by an ``intensity`` value the game loop updates - the arp brightens and
climbs an octave as you chain food and level up, then falls away when you
pause.  Same trick an arcade cabinet uses, minus the cabinet.
"""

from __future__ import annotations

import itertools
import math
import os
import queue
import random
import shutil
import struct
import subprocess
import sys
import threading
import time
from array import array
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .theme import MusicSpec

SAMPLE_RATE = 22050
VERSION = "v4"  # bump when the synth patches change so caches regenerate

A4 = 440.0

# Sine is the hottest call in the synth (every pad partial), so it is a table
# lookup rather than math.sin.  8192 points is far beyond what an ambient pad
# needs and it roughly doubles generation speed.
_SINE_POINTS = 8192
_SINE_TABLE = [math.sin(2.0 * math.pi * i / _SINE_POINTS) for i in range(_SINE_POINTS)]


def _sin_phase(phase: float, mult: float = 1.0) -> float:
    return _SINE_TABLE[int(phase * _SINE_POINTS * mult) & (_SINE_POINTS - 1)]


def note(semitones_from_a4: float) -> float:
    return A4 * (2.0 ** (semitones_from_a4 / 12.0))


def midi(midi_note: int) -> float:
    """MIDI note number -> Hz (A4 = 69)."""
    return note(midi_note - 69)


# ------------------------------------------------------------------ layers


@dataclass
class Layer:
    """One voice: a waveform over [start, start+dur) with an ADSR envelope."""

    start: float
    dur: float
    freq: float
    wave: str = "square"
    gain: float = 0.3
    attack: float = 0.005
    decay: float = 0.05
    sustain: float = 0.6
    release: float = 0.05
    slide_to: Optional[float] = None  # glide to this frequency over the note
    vib_hz: float = 0.0
    vib_depth: float = 0.0
    filter: float = 0.0  # one-pole lowpass cutoff as 0..1 (0 = off), for noise
    harmonics: Tuple[Tuple[float, float], ...] = ()  # additive partials (mult, amp)


@dataclass
class Track:
    """A rendered sound: PCM samples ready to be written as a WAV file."""

    rate: int
    samples: array

    def wav_bytes(self) -> bytes:
        return wav_header(len(self.samples), self.rate) + self.samples.tobytes()


def wav_header(num_samples: int, rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = num_samples * block_align
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, channels, channels, rate, byte_rate, block_align, bits),
            b"data",
            struct.pack("<I", data_size),
        ]
    )


def _wave_sample(wave: str, phase: float, rng: random.Random, harmonics: Sequence[Tuple[float, float]]) -> float:
    """phase is 0..1 within the cycle."""
    if wave == "sine":
        v = _sin_phase(phase)
    elif wave == "square":
        v = 1.0 if phase < 0.5 else -1.0
    elif wave == "triangle":
        v = 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
    elif wave == "saw":
        v = 2.0 * phase - 1.0
    elif wave == "noise":
        v = rng.uniform(-1.0, 1.0)
    else:
        v = _sin_phase(phase)
    for mult, amp in harmonics:
        v += amp * _sin_phase(phase, mult)
    return v


def _envelope(i: int, n: int, layer: Layer) -> float:
    t = i / max(1, n - 1)
    total = layer.attack + layer.decay + layer.release
    if total <= 0:
        return 1.0
    sustain_len = max(0.0, 1.0 - total)
    a = layer.attack
    d = layer.decay
    r = layer.release
    if t < a:
        return t / a if a > 0 else 1.0
    if t < a + d:
        k = (t - a) / d if d > 0 else 1.0
        return 1.0 + (layer.sustain - 1.0) * k
    if t < a + d + sustain_len:
        return layer.sustain
    k = (t - (a + d + sustain_len)) / r if r > 0 else 1.0
    return layer.sustain * (1.0 - min(1.0, k))


def render(duration: float, layers: Sequence[Layer], rate: int = SAMPLE_RATE, seed: int = 1) -> Track:
    """Mix layers into 16-bit PCM. Soft-clipped, so nothing cracks."""
    n = int(duration * rate)
    out = [0.0] * n
    rng = random.Random(seed)
    for layer in layers:
        start = int(layer.start * rate)
        count = int(layer.dur * rate)
        if count <= 0:
            continue
        state = 0.0  # one-pole filter memory (for noise voices)
        phase = 0.0
        for i in range(count):
            t = i / rate
            freq = layer.freq
            if layer.slide_to is not None:
                k = i / max(1, count - 1)
                freq = layer.freq + (layer.slide_to - layer.freq) * k
            if layer.vib_hz:
                freq *= 1.0 + layer.vib_depth * math.sin(2.0 * math.pi * layer.vib_hz * t)
            phase = (phase + freq / rate) % 1.0
            v = _wave_sample(layer.wave, phase, rng, layer.harmonics)
            if layer.filter > 0:
                alpha = layer.filter
                state += alpha * (v - state)
                v = state
            v *= layer.gain * _envelope(i, count, layer)
            idx = start + i
            if 0 <= idx < n:
                out[idx] += v
    peak = 0.0
    for v in out:
        av = -v if v < 0 else v
        if av > peak:
            peak = av
    if peak > 0.999:  # normalize down instead of clipping hard
        factor = 0.999 / peak
        out = [v * factor for v in out]
    ints = [int(v * 32767) for v in out]
    buf = array("h", ints)
    if sys.byteorder != "little":
        buf.byteswap()
    return Track(rate=rate, samples=buf)


# ------------------------------------------------------------------- patches
# Each patch is a small function returning layers - the "sound bank".  Pitch
# centres sit on an A-minor scale so effects stay consonant with the score.


def _blip(freq: float, dur: float = 0.06, wave: str = "square", gain: float = 0.35, slide: Optional[float] = None) -> List[Layer]:
    return [Layer(0.0, dur, freq, wave=wave, gain=gain, attack=0.003, decay=0.02, sustain=0.5, release=dur * 0.6, slide_to=slide)]


def _arp(freqs: Sequence[float], step: float = 0.055, dur: float = 0.13, wave: str = "square", gain: float = 0.3) -> List[Layer]:
    return [
        Layer(i * step, dur, f, wave=wave, gain=gain, attack=0.004, decay=0.05, sustain=0.35, release=dur * 0.7)
        for i, f in enumerate(freqs)
    ]


def _bell(freq: float, start: float = 0.0, dur: float = 0.5, gain: float = 0.3) -> List[Layer]:
    return [
        Layer(start, dur, freq, wave="sine", gain=gain, attack=0.002, decay=0.16, sustain=0.15, release=dur * 0.8,
              harmonics=((2.0, 0.35), (3.01, 0.15), (4.7, 0.06)))
    ]


def patch_move() -> List[Layer]:
    return _blip(note(3), 0.02, gain=0.10)


def patch_select() -> List[Layer]:
    return _blip(note(12), 0.07, gain=0.24, slide=note(19))


def patch_back() -> List[Layer]:
    return _blip(note(12), 0.07, gain=0.20, slide=note(5))


def patch_start() -> List[Layer]:
    seq = [note(0), note(3), note(7), note(12), note(15), note(19)]
    return _arp(seq, step=0.05, dur=0.16, gain=0.26) + [Layer(0.0, 0.7, midi(45), wave="triangle", gain=0.12, attack=0.02, decay=0.3, sustain=0.4, release=0.35)]


def patch_eat(combo: int = 1) -> List[Layer]:
    combo = max(1, min(combo, 8))
    base = note(0) * (1.0 + 0.09 * (combo - 1))
    layers = _blip(base, 0.05, wave="square", gain=0.30, slide=base * 1.5)
    layers += _blip(base * 2.0, 0.035, wave="triangle", gain=0.12, slide=base * 3.0)
    if combo >= 3:
        layers += _blip(base * 3.0, 0.05, wave="sine", gain=0.08)
    return layers


def patch_golden() -> List[Layer]:
    seq = [note(24), note(28), note(31), note(36)]
    layers: List[Layer] = []
    for i, f in enumerate(seq):
        layers += _bell(f, start=i * 0.05, dur=0.45, gain=0.26)
    return layers


def patch_levelup() -> List[Layer]:
    seq = [note(0), note(7), note(12), note(15), note(19), note(24)]
    layers = _arp(seq, step=0.06, dur=0.2, wave="square", gain=0.24)
    layers += [Layer(0.0, 0.7, note(0) / 2, wave="triangle", gain=0.16, attack=0.01, decay=0.2, sustain=0.5, release=0.4)]
    return layers


def patch_pause() -> List[Layer]:
    return [
        Layer(0.0, 0.09, note(12), wave="sine", gain=0.26, attack=0.004, decay=0.03, sustain=0.4, release=0.05),
        Layer(0.07, 0.12, note(3), wave="sine", gain=0.26, attack=0.004, decay=0.04, sustain=0.4, release=0.07),
    ]


def patch_resume() -> List[Layer]:
    return [
        Layer(0.0, 0.09, note(3), wave="sine", gain=0.26, attack=0.004, decay=0.03, sustain=0.4, release=0.05),
        Layer(0.07, 0.12, note(15), wave="sine", gain=0.26, attack=0.004, decay=0.04, sustain=0.4, release=0.07),
    ]


def patch_die() -> List[Layer]:
    return [
        Layer(0.0, 0.55, note(-5), wave="saw", gain=0.34, attack=0.004, decay=0.12, sustain=0.5, release=0.42, slide_to=note(-29)),
        Layer(0.0, 0.3, 90.0, wave="square", gain=0.2, attack=0.002, decay=0.08, sustain=0.2, release=0.2, slide_to=42.0),
        Layer(0.0, 0.45, 250.0, wave="noise", gain=0.16, filter=0.12, attack=0.002, decay=0.12, sustain=0.25, release=0.32),
    ]


def patch_win() -> List[Layer]:
    seq = [note(0), note(4), note(7), note(12), note(16), note(19), note(24)]
    layers = _arp(seq, step=0.07, dur=0.3, wave="triangle", gain=0.26)
    for i, f in enumerate(seq):
        layers += _bell(f * 2, start=0.1 + i * 0.06, dur=0.5, gain=0.10)
    return layers


def patch_record() -> List[Layer]:
    layers: List[Layer] = []
    for i, f in enumerate([note(24), note(31), note(36), note(43)]):
        layers += _bell(f, start=i * 0.07, dur=0.7, gain=0.2)
    return layers


def patch_expire() -> List[Layer]:
    return _blip(note(15), 0.16, wave="triangle", gain=0.18, slide=note(0))


def patch_turn() -> List[Layer]:
    return _blip(note(10), 0.018, wave="triangle", gain=0.07)


SFX_NAMES: Tuple[str, ...] = (
    "move",
    "select",
    "back",
    "start",
    "golden",
    "levelup",
    "pause",
    "resume",
    "die",
    "win",
    "record",
    "expire",
    "turn",
)

# name -> callable producing layers; "eat" takes a combo argument at runtime
_PATCHES: Dict[str, Callable[..., List[Layer]]] = {
    "move": patch_move,
    "select": patch_select,
    "back": patch_back,
    "start": patch_start,
    "golden": patch_golden,
    "levelup": patch_levelup,
    "pause": patch_pause,
    "resume": patch_resume,
    "die": patch_die,
    "win": patch_win,
    "record": patch_record,
    "expire": patch_expire,
    "turn": patch_turn,
}


def durations() -> Dict[str, float]:
    """Approximate length of every patch - used to size caches/tests."""
    out: Dict[str, float] = {}
    for name, fn in _PATCHES.items():
        layers = fn()
        out[name] = max((l.start + l.dur) for l in layers) if layers else 0.0
    out["eat"] = 0.12  # runtime-composed, pitch varies with combo
    return out


# ---------------------------------------------------------------- the bank


class AudioEngine:
    """Synthesizes, caches and plays every sound the game makes."""

    def __init__(self, cache_dir: str, enabled: bool = True, music: bool = True, sample_rate: int = SAMPLE_RATE) -> None:
        self.cache_dir = cache_dir
        self.sample_rate = sample_rate
        self.enabled = enabled
        self.music_enabled = music
        self.player: Optional[List[str]] = None
        self.player_kind: str = "none"
        self._queue_obj: "Optional[queue.Queue]" = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._music_thread: Optional[threading.Thread] = None
        self._live: List[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._paths: Dict[str, str] = {}
        self._eat_cache: Dict[int, str] = {}
        self._music_spec: Optional[MusicSpec] = None
        self._intensity = 0.0
        self._music_gain = 0.30
        self._sfx_gain = 0.85
        self.last_error = ""
        self._detect_player()

    # --------------------------------------------------------------- setup

    def _detect_player(self) -> None:
        candidates = (
            ("afplay", ["afplay", "-a", "{gain}", "{path}"]),
            ("aplay", ["aplay", "-q", "-V", "none", "{path}"]),
            ("paplay", ["paplay", "--volume={volume}", "{path}"]),
            ("play", ["play", "-q", "-n", "-v", "{gain}", "{path}"]),
        )
        for kind, template in candidates:
            if shutil.which(kind):
                self.player = template
                self.player_kind = kind
                return
        self.last_error = "no audio player found (afplay/aplay/paplay/play); sound is off"

    def path_for(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{name}_{VERSION}.wav")

    def warm(self) -> None:
        """Generate every SFX up front so playback latency stays ~0."""
        os.makedirs(self.cache_dir, exist_ok=True)
        for name in list(_PATCHES) + ["eat"]:
            path = self.path_for(name)
            self._paths[name] = path
            if os.path.exists(path) and os.path.getsize(path) > 44:
                continue
            layers = _PATCHES[name]() if name in _PATCHES else patch_eat(1)
            length = max((l.start + l.dur) for l in layers) + 0.02 if layers else 0.05
            track = render(length, layers, rate=self.sample_rate, seed=hash(name) & 0xFFFF)
            with open(path, "wb") as fh:
                fh.write(track.wav_bytes())

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._dispatch_loop, name="sfx", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._music_thread is not None:
            self._music_thread.join(timeout=1.0)
            self._music_thread = None
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        with self._lock:
            for proc in self._live:
                try:
                    proc.terminate()
                except OSError:
                    pass
            self._live.clear()

    # -------------------------------------------------------------- playback

    def _args_for(self, path: str, gain: float) -> List[str]:
        assert self.player is not None
        out = []
        for piece in self.player:
            out.append(
                piece.replace("{path}", path)
                .replace("{gain}", f"{max(0.0, min(1.0, gain)):.2f}")
                .replace("{volume}", str(int(max(0.0, min(1.0, gain)) * 65535)))
            )
        return out

    def _spawn(self, args: Sequence[str]) -> None:
        try:
            proc = subprocess.Popen(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.last_error = f"{args[0]}: {exc}"
            return
        with self._lock:
            self._live = [p for p in self._live if p.poll() is None]
            if len(self._live) > 8:  # audio pileup guard
                return
            self._live.append(proc)

    def _dispatch_loop(self) -> None:
        q: "queue.Queue[Optional[List[str]]]" = queue.Queue()
        self._queue_obj = q
        while not self._stop.is_set():
            try:
                item = q.get(timeout=0.2)
            except Exception:
                continue
            if item is None:
                break
            self._spawn(item)
        self._queue_obj = None

    def play(self, name: str, gain: Optional[float] = None, combo: int = 1) -> None:
        """Fire a sound. Never blocks; silently no-ops when audio is off."""
        if not self.enabled or self.player is None:
            return
        path = self._paths.get(name)
        if path is None:
            if name.startswith("eat"):
                path = self.path_for("eat")
            else:
                return
        if not os.path.exists(path):
            return
        if name == "eat" and combo > 1:
            # pitch-shift without re-synthesizing: re-render the voice only
            path = self._eat_pitched(combo)
        eff_gain = self._sfx_gain if gain is None else gain
        q = getattr(self, "_queue_obj", None)
        if q is None:
            self._spawn(self._args_for(path, eff_gain))
            return
        try:
            q.put_nowait(self._args_for(path, eff_gain))
        except Exception:
            pass

    def _eat_pitched(self, combo: int) -> str:
        cached = self._eat_cache.get(combo)
        if cached and os.path.exists(cached):
            return cached
        path = os.path.join(self.cache_dir, f"eat_{combo}_{VERSION}.wav")
        if not os.path.exists(path):
            layers = patch_eat(combo)
            length = max((l.start + l.dur) for l in layers) + 0.02
            track = render(length, layers, rate=self.sample_rate, seed=1000 + combo)
            try:
                with open(path, "wb") as fh:
                    fh.write(track.wav_bytes())
            except OSError:
                return self.path_for("eat")
        self._eat_cache[combo] = path
        return path

    def bell(self) -> None:
        """Last-resort feedback when there is no audio player at all."""
        if not self.enabled:
            return
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass

    # ----------------------------------------------------------- the score

    def set_music(self, spec: Optional[MusicSpec], enabled: Optional[bool] = None) -> None:
        if enabled is not None:
            self.music_enabled = enabled
        if spec == self._music_spec:
            return
        self._music_spec = spec
        self.restart_music()

    def set_music_enabled(self, enabled: bool) -> None:
        if enabled == self.music_enabled:
            return
        self.music_enabled = enabled
        self.restart_music()

    def set_intensity(self, value: float) -> None:
        self._intensity = 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)

    def restart_music(self) -> None:
        if self._music_thread is not None:
            self._stop.set()
            self._music_thread.join(timeout=1.0)
            self._music_thread = None
            self._stop.clear()
        if not (self.music_enabled and self.enabled and self.player is not None and self._music_spec is not None):
            return
        self._music_thread = threading.Thread(target=self._music_loop, name="music", daemon=True)
        self._music_thread.start()

    def _music_loop(self) -> None:
        spec = self._music_spec
        assert spec is not None
        chunk_len = 16.0
        slot = 0
        proc: Optional[subprocess.Popen] = None
        while not self._stop.is_set():
            path = os.path.join(self.cache_dir, f"music_{slot}_{VERSION}.wav")
            slot ^= 1
            try:
                track = render_music(chunk_len, spec, intensity=self._intensity, rate=self.sample_rate)
                with open(path, "wb") as fh:
                    fh.write(track.wav_bytes())
            except OSError:
                return
            # the previous chunk is still playing while this one was generated,
            # so we only wait out whatever fraction is left.
            while proc is not None and proc.poll() is None:
                if self._stop.is_set():
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    proc = None
                    break
                time.sleep(0.05)
            if self._stop.is_set():
                return
            args = self._args_for(path, self._music_gain)
            try:
                proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                self.last_error = f"{args[0]}: {exc}"
                return
            with self._lock:
                self._live.append(proc)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


# ---------------------------------------------------------- the ambient score

CHORD_DEGREES: Tuple[Tuple[int, ...], ...] = ((0, 2, 4), (5, 0, 2), (3, 5, 7), (4, 6, 0))


def render_music(
    duration: float,
    spec: MusicSpec,
    intensity: float = 0.0,
    rate: int = SAMPLE_RATE,
    seed: int = 7,
) -> Track:
    """A 16-second generative pad + arp in the theme's mode.

    ``intensity`` (0..1) raises the octave, thickens the arp and opens the
    filter on the lead, so the score tracks how well you are playing.
    """
    step = (60.0 / spec.bpm) / 4.0  # sixteenth notes
    steps_per_bar = spec.steps_per_bar
    total_steps = int(duration / step)
    degrees = spec.mode
    layers: List[Layer] = []

    def scale_freq(degree_index: int, octave: int = 0) -> float:
        n = len(degrees)
        wrapped = degree_index % n
        octaves = degree_index // n + octave
        return spec.root * (2.0 ** octaves) * (2.0 ** (degrees[wrapped] / 12.0))

    # pad: a slow chord under everything
    bar = 0
    t = 0.0
    while t < duration:
        chord = CHORD_DEGREES[bar % len(CHORD_DEGREES)]
        bar_len = step * steps_per_bar
        for deg in chord:
            layers.append(
                Layer(
                    t,
                    min(bar_len * 1.1, duration - t),
                    scale_freq(deg, -1),
                    wave=spec.pad_wave,
                    gain=0.075,
                    attack=bar_len * 0.35,
                    decay=bar_len * 0.2,
                    sustain=0.7,
                    release=bar_len * 0.4,
                    vib_hz=0.25 + 0.05 * bar,
                    vib_depth=0.002,
                )
            )
        if intensity > 0.35:
            layers.append(
                Layer(t, bar_len, scale_freq(chord[0], 2), wave="triangle", gain=0.02 + 0.03 * intensity,
                      attack=bar_len * 0.2, decay=bar_len * 0.3, sustain=0.4, release=bar_len * 0.4)
            )
        bar += 1
        t += bar_len

    # arp: a deterministic walk over the chord tones
    rng = random.Random(seed)
    pattern = (0, 1, 2, 1, 3, 2, 1, 0, 2, 0, 1, 3, 2, 1, 0, 1)
    octave = 1 + (1 if intensity > 0.55 else 0)
    density = 1 + (1 if intensity > 0.3 else 0)
    s = 0
    while s < total_steps:
        bar = s // steps_per_bar
        chord = CHORD_DEGREES[bar % len(CHORD_DEGREES)]
        pos = s % steps_per_bar
        if pos % max(1, (4 // density)) == 0:
            pick = pattern[(s // max(1, (4 // density))) % len(pattern)] % len(chord)
            deg = chord[pick] + (0 if s % 8 else len(degrees))
            amp = spec.arp_gain * (0.09 + 0.05 * intensity)
            layers.append(
                Layer(
                    s * step,
                    step * 2.4,
                    scale_freq(deg, octave),
                    wave=spec.arp_wave,
                    gain=amp,
                    attack=0.004,
                    decay=step * 1.2,
                    sustain=0.12,
                    release=step * 1.6,
                )
            )
        if intensity > 0.6 and pos % 4 == 2:  # off-beat tick adds drive
            layers.append(Layer(s * step, 0.05, 6000.0, wave="noise", gain=0.02, filter=0.5, attack=0.001, decay=0.02, sustain=0.0, release=0.02))
        s += 1

    track = render(duration, layers, rate=rate, seed=seed)
    return _fade_edges(track, rate)


def _fade_edges(track: Track, rate: int, ms: float = 60.0) -> Track:
    """Micro fades so back-to-back chunks never click at the seam."""
    n = len(track.samples)
    edge = min(n, int(rate * ms / 1000.0))
    samples = track.samples
    for i in range(edge):
        k = i / edge
        samples[i] = int(samples[i] * k)
        samples[n - 1 - i] = int(samples[n - 1 - i] * k)
    return track
