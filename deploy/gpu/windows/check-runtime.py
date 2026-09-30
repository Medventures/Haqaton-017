"""Inspect the native ASR runtime without loading models or displaying credentials."""

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def load_environment(path):
    if not path.is_file():
        print(f"Environment file: MISSING ({path})")
        return False
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Invalid environment entry on line {number}; expected KEY=value")
        name, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[name] = value
    print("Environment file: loaded (values are not displayed)")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    parser.add_argument("--cuda-test", action="store_true", help="Run a small CUDA matrix multiplication and inspect CTranslate2 CUDA support")
    args = parser.parse_args()
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    try:
        prepared = load_environment(args.env_file)
    except (OSError, ValueError) as error:
        print(f"Environment file: ERROR ({error})")
        return 1

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    torch_lib = Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib"
    dll_handle = None
    if torch_lib.is_dir():
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        if os.name == "nt":
            dll_handle = os.add_dll_directory(str(torch_lib))
        print("PyTorch DLL directory: present")
    else:
        prepared = False
        print("PyTorch DLL directory: MISSING")

    modules = {}
    for module_name, package_name in (
        ("torch", "torch"),
        ("ctranslate2", "ctranslate2"),
        ("faster_whisper", "faster-whisper"),
        ("pyannote.audio", "pyannote.audio"),
    ):
        try:
            version = importlib.metadata.version(package_name)
            modules[module_name] = importlib.import_module(module_name)
            print(f"{package_name}: {version}, import OK")
        except Exception as error:
            prepared = False
            print(f"{package_name}: IMPORT FAILED ({type(error).__name__}: {error})")

    if "torch" in modules:
        print(f"PyTorch compiled CUDA: {modules['torch'].version.cuda or 'CPU-only build'}")
    if args.cuda_test:
        try:
            torch = modules["torch"]
            if not torch.cuda.is_available():
                raise RuntimeError("PyTorch cannot access a CUDA GPU")
            with torch.inference_mode():
                matrix = torch.ones((32, 32), device="cuda")
                result = matrix @ matrix
                torch.cuda.synchronize()
                if not torch.all(result == 32).item():
                    raise RuntimeError("Unexpected CUDA matrix multiplication result")
                del result, matrix
            torch.cuda.empty_cache()
            print(f"CUDA math: OK ({torch.cuda.get_device_name(0)})")
            ct2 = modules["ctranslate2"]
            count = ct2.get_cuda_device_count()
            if count < 1:
                raise RuntimeError("CTranslate2 cannot access a CUDA GPU")
            supported = ct2.get_supported_compute_types("cuda", 0)
            selected = os.environ.get("ASR_COMPUTE_TYPE", "int8_float16")
            print(f"CTranslate2 CUDA: {count} GPU(s); compute types: {', '.join(sorted(supported))}")
            if selected not in supported:
                raise RuntimeError(f"Configured compute type {selected} is not supported")
        except Exception as error:
            prepared = False
            print(f"CUDA check: FAILED ({type(error).__name__}: {error})")
    else:
        print("CUDA math: NOT TESTED (add --cuda-test to execute a small GPU operation)")

    model_files_present = True
    for name, required_files in (
        ("ASR_MODEL", ("model.bin", "config.json", "tokenizer.json")),
        ("DIARIZATION_MODEL", ("config.yaml",)),
    ):
        configured_path = os.environ.get(name, "").strip()
        model_path = Path(configured_path) if configured_path else None
        missing = [filename for filename in required_files if model_path is None or not (model_path / filename).is_file()]
        if missing:
            model_files_present = False
            print(f"{name}: MISSING required files ({', '.join(missing)}); directory: {configured_path or 'not configured'}")
        else:
            print(f"{name}: expected files present ({model_path}); model loading remains unverified")
    if not model_files_present:
        prepared = False

    token_ok = len(os.environ.get("ASR_SERVICE_TOKEN", "")) >= 32
    print(f"ASR credential: {'configured (value hidden)' if token_ok else 'MISSING or shorter than 32 characters'}")
    prepared = prepared and token_ok

    bind_ip = os.environ.get("BIND_IP", "127.0.0.1")
    if bind_ip in ("0.0.0.0", "::"):
        bind_ip = "127.0.0.1"
    host = f"[{bind_ip}]" if ":" in bind_ip else bind_ip
    try:
        with urlopen(f"http://{host}:8090/health", timeout=3) as response:
            health = json.load(response)
            process_healthy = response.status == 200 and health.get("status") == "ok"
        print(f"ASR process health: {'OK' if process_healthy else 'UNEXPECTED RESPONSE'}")
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
        print(f"ASR process health: NOT REACHABLE ({type(error).__name__}); it may not have been started")

    print("Model readiness: UNVERIFIED. /health does not load weights; validate both models with a synthetic /transcribe request.")
    print(f"Preparation: {'basic checks passed' if prepared else 'INCOMPLETE; see missing prerequisites above'}")
    # Keep the Windows DLL search directory registered until all checks finish.
    if dll_handle is not None:
        dll_handle.close()
    return 0 if prepared else 1


if __name__ == "__main__":
    raise SystemExit(main())
