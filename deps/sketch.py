"""Sketch and library management"""

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import aiofiles
from fastapi import FastAPI, HTTPException

from conf import settings
from deps.cache import library_cache
from deps.logs import logger
from models import Library, Sketch

fqbn_to_board = {  # Mapping from fqbn to PlatformIO board
    "arduino:avr:uno": "uno",
    "arduino:avr:nano": "nanoatmega328",
    "arduino:avr:mega": "megaatmega2560",
    "arduino:esp32:nano_nora": "arduino_nano_esp32",
    "arduino:mbed_nano:nanorp2040connect": "nanorp2040connect",
}

fqbn_to_platform = {  # Mapping from fqbn to PlatformIO platform
    "arduino:avr:uno": "atmelavr",
    "arduino:avr:nano": "atmelavr",
    "arduino:avr:mega": "atmelavr",
    "arduino:esp32:nano_nora": "espressif32",
    "arduino:mbed_nano:nanorp2040connect": "raspberrypi",
}


async def install_libraries(libraries: list[Library], fqbn: str):
    """Install libraries for the given sketch"""
    if not fqbn in fqbn_to_board:
        raise HTTPException(422, "Unsupported fqbn")
    pio_environment = fqbn_to_board[fqbn]
    for library in libraries:
        if library_cache.get(library + pio_environment):
            continue
        installer = await asyncio.create_subprocess_exec(
            "platformio",
            "pkg",
            "install",
            "--library",
            library,
            "--environment",
            pio_environment,
            "--no-save",
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=settings.platformio_data_dir,
        )
        stdout, stderr = await installer.communicate()
        if installer.returncode != 0:
            logger.warning(
                "Library install failed. Error: %s",
                stderr.decode() + stdout.decode(),
            )
            continue
        library_cache[library + pio_environment] = 1


async def compile_sketch(sketch: Sketch, task_num: int) -> dict[str, str]:
    """Compile the sketch and return the result in HEX format or as a binary blob"""
    sketch_path = f"{settings.platformio_data_dir}/src{task_num}/main.cpp"

    # Write the sketch to a temp .ino file
    async with aiofiles.open(sketch_path, "w+") as platform_ini:
        await platform_ini.write("#include <Arduino.h>\n" + sketch.source_code)

    compiler = await asyncio.create_subprocess_exec(
        "platformio",
        "run",
        "-c",
        f"platformio{task_num}.ini",
        "-e",
        f"{fqbn_to_board[sketch.board]}",
        "-j",
        str(settings.threads_per_platformio_compile),
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=settings.platformio_data_dir,
    )
    stdout, stderr = await compiler.communicate()
    if compiler.returncode != 0:
        logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
        raise HTTPException(500, stderr.decode() + stdout.decode())

    output_file = f"{settings.platformio_data_dir}/build{task_num}/{fqbn_to_board[sketch.board]}/firmware."

    result = {}
    if os.path.exists(output_file + "hex"):
        async with aiofiles.open(
            output_file + "hex", "r", encoding="utf-8"
        ) as hex_file:
            result["hex"] = str(await hex_file.read())
    if os.path.exists(output_file + "bin"):
        async with aiofiles.open(output_file + "bin", "rb") as bin_file:
            result["sketch"] = base64.b64encode(await bin_file.read()).decode("utf-8")
    if os.path.exists(output_file + "uf2"):
        async with aiofiles.open(output_file, "rb") as elf_file:
            result["sketch"] = base64.b64encode(await elf_file.read()).decode("utf-8")
    return result


async def setup_platformio() -> None:
    """Setup PlatformIO compile directory and config files"""
    platformio_ini_text = "[env]\nlib_compat_mode = strict\n"
    for fqbn, board in fqbn_to_board.items():
        platformio_ini_text += (
            f"\n[env:{board}]\nframework = arduino\n"
            f"platform = {fqbn_to_platform[fqbn]}\nboard = {board}\n"
        )

    # Make sure compile dir exists
    for task_num in range(settings.max_concurrent_tasks):
        os.makedirs(f"{settings.platformio_data_dir}/src{task_num}", exist_ok=True)
        # Generate the platformio{i}.ini file
        async with aiofiles.open(
            f"{settings.platformio_data_dir}/platformio{task_num}.ini", "w+"
        ) as platform_ini:
            await platform_ini.write(
                platformio_ini_text
                + f"\n[platformio]\nsrc_dir = src{task_num}\nbuild_dir = build{task_num}"
            )
    # Make platformio.ini
    async with aiofiles.open(
        f"{settings.platformio_data_dir}/platformio.ini", "w+"
    ) as default_platform_ini:
        await default_platform_ini.write(platformio_ini_text)


@asynccontextmanager
async def startup(_app: FastAPI) -> AsyncGenerator[None, Any]:
    """Startup context manager"""
    await setup_platformio()
    yield
