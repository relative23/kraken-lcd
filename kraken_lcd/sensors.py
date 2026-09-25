"""Local sensor sources (CPU/RAM/NVMe via psutil, GPU via nvidia-smi or amdgpu).

Liquid temperature and pump/fan speeds are read from the Kraken itself and
passed into ``SensorReader.snapshot`` by the caller. Every source degrades
to ``None`` on failure — a dead sensor must never take the carousel down.
"""

import glob
import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace

import psutil

log = logging.getLogger(__name__)

NVIDIA_SMI_CMD = (
    "nvidia-smi",
    "--query-gpu=temperature.gpu,utilization.gpu",
    "--format=csv,noheader,nounits",
)
NVIDIA_SMI_DETAIL_CMD = (
    "nvidia-smi",
    "--query-gpu=memory.used,power.draw",
    "--format=csv,noheader,nounits",
)
GIB = 1024 ** 3


@dataclass(frozen=True)
class SensorSnapshot:
    cpu_load: float | None = None
    cpu_temp: float | None = None
    gpu_load: float | None = None
    gpu_temp: float | None = None
    ram_percent: float | None = None
    nvme_temp: float | None = None
    liquid_temp: float | None = None
    pump_rpm: int | None = None
    fan_rpm: int | None = None
    # detailed values, read only for screens that show them
    cpu_core_loads: tuple[float, ...] | None = None
    cpu_freq_ghz: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    gpu_mem_used_gb: float | None = None
    gpu_power_w: float | None = None
    # recent values per series, oldest first (see history.py)
    history: Mapping[str, tuple[float, ...]] | None = None


class SensorReader:
    # AMD first, Intel as fallback
    CPU_TEMP_CHIPS = ("k10temp", "coretemp")

    def __init__(self) -> None:
        self._gpu_warned = False
        self._cpu_temp_warned = False
        self._nvme_warned = False
        self._no_nvidia = False  # nvidia-smi binary missing: stop retrying

    def snapshot(self, liquid_temp: float | None = None,
                 pump_rpm: int | None = None,
                 fan_rpm: int | None = None,
                 detailed: bool = False) -> SensorSnapshot:
        temperatures = self._read_temperatures()
        gpu_temp, gpu_load = self._read_gpu(temperatures)
        basic = SensorSnapshot(
            cpu_load=self._read_cpu_load(),
            cpu_temp=self._read_cpu_temp(temperatures),
            gpu_load=gpu_load,
            gpu_temp=gpu_temp,
            ram_percent=self._read_ram(),
            nvme_temp=self._read_nvme_temp(temperatures),
            liquid_temp=liquid_temp,
            pump_rpm=pump_rpm,
            fan_rpm=fan_rpm,
        )
        return self._add_details(basic) if detailed else basic

    def _add_details(self, snap: SensorSnapshot) -> SensorSnapshot:
        """Per-core load, clock, memory in GB, VRAM and GPU power."""
        details: dict = {}
        try:
            # load per core since the previous call (the carousel calls
            # about once per tile), no extra blocking wait
            details["cpu_core_loads"] = tuple(
                float(v) for v in psutil.cpu_percent(interval=None, percpu=True))
        except Exception as exc:
            log.debug("per-core load unavailable: %s", exc)
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                details["cpu_freq_ghz"] = float(freq.current) / 1000
        except Exception as exc:
            log.debug("CPU clock unavailable: %s", exc)
        try:
            memory = psutil.virtual_memory()
            details["ram_used_gb"] = float(memory.total - memory.available) / GIB
            details["ram_total_gb"] = float(memory.total) / GIB
        except Exception as exc:
            log.debug("memory size unavailable: %s", exc)
        vram, power = self._read_gpu_details()
        details["gpu_mem_used_gb"], details["gpu_power_w"] = vram, power
        return replace(snap, **details)

    def _read_gpu_details(self) -> tuple[float | None, float | None]:
        """(VRAM in use GB, power W): nvidia-smi, then amdgpu sysfs."""
        if not self._no_nvidia:
            try:
                result = subprocess.run(NVIDIA_SMI_DETAIL_CMD, capture_output=True,
                                        text=True, timeout=5, check=True)
                mem_s, power_s = (p.strip() for p in
                                  result.stdout.strip().splitlines()[0].split(","))
                return _number(mem_s, 1 / 1024), _number(power_s, 1.0)
            except FileNotFoundError:
                self._no_nvidia = True
            except Exception as exc:
                log.debug("nvidia-smi details unavailable: %s", exc)
        vram = power = None
        for path in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_vram_used")):
            vram = _read_number(path, 1 / GIB)
            break
        for path in sorted(glob.glob("/sys/class/drm/card*/device/hwmon/hwmon*/power1_average")):
            power = _read_number(path, 1e-6)  # microwatts
            break
        return vram, power

    @staticmethod
    def _read_temperatures() -> dict:
        try:
            return psutil.sensors_temperatures()
        except Exception as exc:
            log.warning("temperature sensors unavailable: %s", exc)
            return {}

    def _read_cpu_load(self) -> float | None:
        try:
            return float(psutil.cpu_percent(interval=0.3))
        except Exception as exc:
            log.warning("CPU load unavailable: %s", exc)
            return None

    def _read_ram(self) -> float | None:
        try:
            return float(psutil.virtual_memory().percent)
        except Exception as exc:
            log.warning("RAM usage unavailable: %s", exc)
            return None

    def _read_cpu_temp(self, temperatures: dict) -> float | None:
        for chip in self.CPU_TEMP_CHIPS:
            entries = temperatures.get(chip)
            if entries:
                return float(entries[0].current)
        if not self._cpu_temp_warned:
            log.warning("no CPU temperature sensor found (looked for: %s)",
                        ", ".join(self.CPU_TEMP_CHIPS))
            self._cpu_temp_warned = True
        return None

    def _read_nvme_temp(self, temperatures: dict) -> float | None:
        """Hottest NVMe composite temperature across all drives."""
        entries = temperatures.get("nvme") or []
        composites = [float(e.current) for e in entries
                      if "composite" in (getattr(e, "label", "") or "").lower()]
        if composites:
            return max(composites)
        if entries:  # drives without a labelled composite sensor
            return max(float(e.current) for e in entries)
        if not self._nvme_warned:
            log.warning("no NVMe temperature sensor found — nvme tile will be skipped")
            self._nvme_warned = True
        return None

    def _read_gpu(self, temperatures: dict) -> tuple[float | None, float | None]:
        """(temperature °C, load %) — NVIDIA via nvidia-smi first, then AMD
        via the amdgpu hwmon/sysfs interfaces."""
        temp, load = self._read_nvidia_gpu()
        if temp is None and load is None:
            temp, load = self._read_amd_gpu(temperatures)
        if temp is None and load is None and not self._gpu_warned:
            log.warning("no GPU stats available (tried nvidia-smi and amdgpu) "
                        "— GPU tiles will be skipped")
            self._gpu_warned = True
        return temp, load

    def _read_nvidia_gpu(self) -> tuple[float | None, float | None]:
        if self._no_nvidia:
            return None, None
        try:
            result = subprocess.run(
                NVIDIA_SMI_CMD, capture_output=True, text=True,
                timeout=5, check=True,
            )
            first_line = result.stdout.strip().splitlines()[0]
            temp_s, load_s = (part.strip() for part in first_line.split(","))
            return float(temp_s), float(load_s)
        except FileNotFoundError:
            self._no_nvidia = True  # not installed: no point in retrying
            return None, None
        except Exception:
            return None, None

    def _read_amd_gpu(self, temperatures: dict) -> tuple[float | None, float | None]:
        temp = None
        entries = temperatures.get("amdgpu") or []
        edges = [float(e.current) for e in entries
                 if "edge" in (getattr(e, "label", "") or "").lower()]
        if edges:
            temp = max(edges)
        elif entries:
            temp = float(entries[0].current)

        load = None
        for path in sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")):
            try:
                with open(path) as fh:
                    load = float(fh.read().strip())
                break
            except (OSError, ValueError):
                continue
        return temp, load


def _number(text: str, scale: float) -> float | None:
    try:
        return float(text) * scale
    except ValueError:  # "[N/A]" and similar
        return None


def _read_number(path: str, scale: float) -> float | None:
    try:
        with open(path) as fh:
            return _number(fh.read().strip(), scale)
    except OSError:
        return None
