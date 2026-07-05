"""Local sensor sources (CPU/RAM/NVMe via psutil, GPU via nvidia-smi or amdgpu).

Liquid temperature and pump/fan speeds are read from the Kraken itself and
passed into ``SensorReader.snapshot`` by the caller. Every source degrades
to ``None`` on failure — a dead sensor must never take the carousel down.
"""

import glob
import logging
import subprocess
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

NVIDIA_SMI_CMD = (
    "nvidia-smi",
    "--query-gpu=temperature.gpu,utilization.gpu",
    "--format=csv,noheader,nounits",
)


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
                 fan_rpm: int | None = None) -> SensorSnapshot:
        temperatures = self._read_temperatures()
        gpu_temp, gpu_load = self._read_gpu(temperatures)
        return SensorSnapshot(
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
