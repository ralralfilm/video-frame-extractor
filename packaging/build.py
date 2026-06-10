from __future__ import annotations

import compileall
import os
import shutil
import subprocess
import sys
import tomllib
import venv
import zipfile
from pathlib import Path


CONFIG = {
    "audio-stripper": {
        "windowed": False,
        "collect_all": ["imageio_ffmpeg"],
        "icon_bg": "#164e63",
        "icon_fg": "#f8fafc",
        "icon_text": "AS",
    },
    "forced-alignment": {
        "windowed": False,
        "collect_all": [],
        "icon_bg": "#4c1d95",
        "icon_fg": "#f5f3ff",
        "icon_text": "FA",
    },
    "social-video-downloader": {
        "windowed": True,
        "collect_all": ["imageio_ffmpeg", "yt_dlp"],
        "icon_bg": "#0f766e",
        "icon_fg": "#f0fdfa",
        "icon_text": "SD",
    },
    "video-editor": {
        "windowed": True,
        "collect_all": ["imageio_ffmpeg"],
        "icon_bg": "#7f1d1d",
        "icon_fg": "#fef2f2",
        "icon_text": "VE",
    },
    "video-frame-extractor": {
        "windowed": True,
        "collect_all": [],
        "icon_bg": "#1d4ed8",
        "icon_fg": "#eff6ff",
        "icon_text": "VF",
    },
}

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.name
SETTINGS = CONFIG[PROJECT]
ENTRYPOINT = ROOT / "main.py"
VENV_DIR = ROOT / "packaging" / ".venv"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
ASSETS_DIR = ROOT / "assets"

IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "output",
    "outputs",
    "downloads",
    "input",
    "inputs",
    "exports",
}


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> Path:
    python = venv_python()
    if not python.exists():
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    return python


def read_pyproject_dependencies() -> list[str]:
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return []
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data.get("project", {}).get("dependencies", []))


def install_dependencies(python: Path) -> None:
    run([str(python), "-m", "pip", "install", "--upgrade", "pyinstaller", "pillow"])
    requirements = ROOT / "requirements.txt"
    if requirements.exists():
        run([str(python), "-m", "pip", "install", "-r", str(requirements)])
        return
    dependencies = read_pyproject_dependencies()
    if dependencies:
        run([str(python), "-m", "pip", "install", *dependencies])


def source_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        rel_parts = path.relative_to(ROOT).parts
        if any(part in IGNORED_PARTS for part in rel_parts):
            continue
        if rel_parts[:2] == ("packaging", ".venv"):
            continue
        files.append(path)
    return files


def compile_sources() -> None:
    ok = True
    for path in source_files():
        ok = compileall.compile_file(str(path), quiet=1, force=True) and ok
    if not ok:
        raise SystemExit("Python compile check failed.")


def generate_icon(python: Path) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    png_path = ASSETS_DIR / "icon.png"
    ico_path = ASSETS_DIR / "icon.ico"
    script = """
import sys
from PIL import Image, ImageDraw, ImageFont

bg, fg, text, png_path, ico_path = sys.argv[1:]
image = Image.new("RGBA", (256, 256), bg)
draw = ImageDraw.Draw(image)
try:
    font = ImageFont.truetype("arialbd.ttf", 96)
except OSError:
    font = ImageFont.load_default()
box = draw.textbbox((0, 0), text, font=font)
x = (256 - (box[2] - box[0])) / 2
y = (256 - (box[3] - box[1])) / 2 - 6
draw.rounded_rectangle((18, 18, 238, 238), radius=44, outline=fg, width=8)
draw.text((x, y), text, fill=fg, font=font)
image.save(png_path)
image.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
"""
    subprocess.run(
        [
            str(python),
            "-c",
            script,
            SETTINGS["icon_bg"],
            SETTINGS["icon_fg"],
            SETTINGS["icon_text"],
            str(png_path),
            str(ico_path),
        ],
        cwd=ROOT,
        check=True,
    )


def build_exe(python: Path) -> None:
    if not ENTRYPOINT.exists():
        raise SystemExit(f"Missing entry point: {ENTRYPOINT}")
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        PROJECT,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        "--icon",
        str(ASSETS_DIR / "icon.ico"),
    ]
    if SETTINGS["windowed"]:
        command.append("--windowed")
    for package in SETTINGS["collect_all"]:
        command.extend(["--collect-all", package])
    command.append(str(ENTRYPOINT))
    run(command)


def package_zip() -> Path:
    app_dir = DIST_DIR / PROJECT
    exe = app_dir / f"{PROJECT}.exe"
    if not exe.exists():
        raise SystemExit(f"Expected executable was not created: {exe}")
    zip_path = DIST_DIR / f"{PROJECT}-windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in app_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(DIST_DIR))
    return zip_path


def main() -> None:
    print(f"Building {PROJECT} for Windows")
    compile_sources()
    python = ensure_venv()
    install_dependencies(python)
    generate_icon(python)
    build_exe(python)
    zip_path = package_zip()
    print(f"Built {DIST_DIR / PROJECT / (PROJECT + '.exe')}")
    print(f"Packaged {zip_path}")


if __name__ == "__main__":
    main()
