"""Sketch and library management"""

import asyncio
import base64
import os
import threading
from contextlib import asynccontextmanager
from math import floor

import aiofiles
from fastapi import FastAPI, HTTPException

from conf import settings
from deps.logs import logger
from models import Library, Sketch

CWD = os.getcwd()

fqbn_to_board = {  # Mapping from fqbn to PlatformIO board
    "arduino:avr:uno": "uno",
    "arduino:avr:nano": "nanoatmega328",
    "arduino:avr:mega": "megaADK",
    "arduino:esp32:nano_nora": "arduino_nano_esp32",
}

fqbn_to_platform = {  # Mapping from fqbn to PlatformIO platform
    "arduino:avr:uno": "atmelavr",
    "arduino:avr:nano": "atmelavr",
    "arduino:avr:mega": "atmelavr",
    "arduino:esp32:nano_nora": "espressif32",
}


async def _install_libraries(  # pylint: disable=too-many-locals, too-many-branches
    libraries: list[Library], fqbn: str
) -> dict[Library, str]:
    for library in libraries:
        installer = await asyncio.create_subprocess_exec(
            "platformio",
            "pkg",
            "install",
            "--library",
            f"{library}",
            "--no-save",
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd="./compiles",
        )
        _, _ = await installer.communicate()
        if installer.returncode != 0:
            # Retry but only for our current platform
            logger.warning(
                "Library install failed for all platforms, retrying for current platform"
            )
            installer = await asyncio.create_subprocess_exec(
                "platformio",
                "pkg",
                "install",
                "--library",
                f"{library}",
                "--no-save",
                "--platform",
                fqbn_to_platform[fqbn],
                stderr=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd="./compiles",
            )
            _, _ = await installer.communicate()
            if installer.returncode != 0:
                logger.warning("Library install failed for all platforms, skipping")
                continue


async def _compile_sketch(  # pylint: disable=too-many-locals
    sketch: Sketch, i: int
) -> dict[str, str]:
    sketch_path = f"compiles/src{i}/main.cpp"

    # Write the sketch to a temp .ino file
    async with aiofiles.open(sketch_path, "w+") as platform_ini:
        await platform_ini.write("#include <Arduino.h>\n" + sketch.source_code)

    compiler = await asyncio.create_subprocess_exec(
        "platformio",
        "run",
        "-c",
        f"{CWD}/compiles/platformio{i}.ini",
        "-e",
        f"{fqbn_to_board[sketch.board]}",
        "-j",
        str(floor(settings.max_concurrent_tasks / threading.active_count())),
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd="compiles/",
    )
    stdout, stderr = await compiler.communicate()
    if compiler.returncode != 0:
        logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
        raise HTTPException(500, stderr.decode() + stdout.decode())

    output_file = f"compiles/build{i}/{fqbn_to_board[sketch.board]}/firmware."
    if os.path.exists(output_file + "hex"):
        async with aiofiles.open(
            output_file + ".hex", "r", encoding="utf-8"
        ) as hex_file:
            return {"hex": str(await hex_file.read())}
    elif os.path.exists(output_file + "bin"):
        async with aiofiles.open(output_file + "bin", "rb") as bin_file:
            return {"sketch": base64.b64encode(await bin_file.read()).decode("utf-8")}
    elif os.path.exists(output_file + "uf2"):
        async with aiofiles.open(output_file, "rb") as elf_file:
            return {"sketch": base64.b64encode(await elf_file.read()).decode("utf-8")}
    return {"hex": ""}


@asynccontextmanager
async def startup(_app: FastAPI) -> None:
    """Startup context manager"""
    platformio_ini_text = "[env]\nlib_compat_mode = strict\nlib_deps =\n"
    for fqbn, board in fqbn_to_board.items():
        platformio_ini_text += f"\n[env:{board}]\nframework = arduino\nplatform = {fqbn_to_platform[fqbn]}\nboard = {board}\n" # pylint: disable=line-too-long

    # Make sure compile dir exists
    for i in range(settings.max_concurrent_tasks):
        os.makedirs(f"compiles/src{i}", exist_ok=True)
        # Generate the platformio{i}.ini file
        async with aiofiles.open(f"compiles/platformio{i}.ini", "w+") as platform_ini:
            await platform_ini.write(
                platformio_ini_text
                + f"\n[platformio]\nsrc_dir = src{i}\nbuild_dir = build{i}"
            )
    # Make compiles/platformio.ini
    async with aiofiles.open("compiles/platformio.ini", "w+") as default_platform_ini:
        await default_platform_ini.write(platformio_ini_text)
    yield
