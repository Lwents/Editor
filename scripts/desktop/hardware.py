"""Probe the installed machine rather than carrying the build machine's settings."""
import json
import os
import subprocess
import sys
from pathlib import Path


def probe():
    logical = os.cpu_count() or 1
    try:
        import psutil
        physical = psutil.cpu_count(logical=False) or logical
        memory_gb = round(psutil.virtual_memory().total / 1024**3, 1)
    except ImportError:
        physical, memory_gb = logical, None
        if os.name == "nt":
            try:
                command = "@{cores=(Get-CimInstance Win32_Processor|Measure-Object NumberOfCores -Sum).Sum;ram=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory}|ConvertTo-Json -Compress"
                output = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", command],
                                                 timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                info = json.loads(output)
                physical = int(info["cores"]) or logical
                memory_gb = round(int(info["ram"]) / 1024**3, 1)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    result = {"logicalCores": logical, "cpuThreads": physical, "memoryGB": memory_gb,
              "cuda": False, "gpu": None, "vramGB": 0, "encoder": "libx264"}
    try:
        import torch
        if torch.cuda.is_available():
            device = torch.cuda.get_device_properties(0)
            # Allocation verifies the driver, not just the advertised device name.
            torch.zeros(1, device="cuda")
            result.update(cuda=True, gpu=device.name, vramGB=round(device.total_memory / 1024**3, 1))
    except Exception:
        pass
    ffmpeg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if ffmpeg and ffmpeg.exists():
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            try:
                completed = subprocess.run(
                    [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=size=256x256:rate=1",
                     "-frames:v", "1", "-c:v", encoder, "-pix_fmt", "yuv420p", "-f", "null", "-"],
                    capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode == 0:
                    result["encoder"] = encoder
                    break
            except (OSError, subprocess.TimeoutExpired):
                continue
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False))
