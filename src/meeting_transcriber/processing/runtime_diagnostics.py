from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from meeting_transcriber.processing.diarization_engine import DiarizationModelManager


@dataclass(frozen=True, slots=True)
class DiarizationRuntimeStatus:
    runtime_installed: bool
    model_cached: bool
    torch_installed: bool
    cuda_available: bool
    model_directory: Path

    @property
    def summary(self) -> str:
        runtime = "installed" if self.runtime_installed else "not installed"
        model = "cached" if self.model_cached else "not cached"
        if not self.torch_installed:
            compute = "not installed"
        else:
            compute = (
                "installed (CUDA available)" if self.cuda_available else "installed (CPU only)"
            )
        return (
            f"pyannote.audio: {runtime}\n"
            f"Community-1 model: {model}\n"
            f"PyTorch: {compute}\n"
            f"Model folder: {self.model_directory}"
        )


def inspect_diarization_runtime(model_root: Path) -> DiarizationRuntimeStatus:
    manager = DiarizationModelManager(model_root)
    torch_installed, cuda_available = _torch_status()
    return DiarizationRuntimeStatus(
        runtime_installed=_module_available("pyannote") and _module_available("pyannote.audio"),
        model_cached=manager.is_available,
        torch_installed=torch_installed,
        cuda_available=cuda_available,
        model_directory=manager.model_directory,
    )


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


class _CudaModule(Protocol):
    def is_available(self) -> bool: ...


class _TorchModule(Protocol):
    cuda: _CudaModule


def _torch_status() -> tuple[bool, bool]:
    if not _module_available("torch"):
        return False, False
    try:
        module = cast(_TorchModule, importlib.import_module("torch"))
        return True, module.cuda.is_available()
    except (ImportError, AttributeError, RuntimeError):
        return False, False
