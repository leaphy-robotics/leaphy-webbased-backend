"""Sketch and library management"""

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from shutil import rmtree
from typing import Any, AsyncGenerator

import aiofiles
from fastapi import FastAPI, HTTPException

from conf import settings
from deps.logs import logger
from models import Sketch

fqbn_to_board = {  # Mapping from fqbn to PlatformIO board
    "arduino:avr:uno": "uno",
    "arduino:avr:nano": "nanoatmega328",
    "arduino:avr:mega": "megaatmega2560",
    "arduino:esp32:nano_nora": "arduino_nano_esp32",
    "arduino:mbed_nano:nanorp2040connect": "nanorp2040connect",
}

fqbn_to_platform = {  # Mapping from fqbn to PlatformIO platform
    "arduino:avr:uno": "atmelavr@5.1.0",
    "arduino:avr:nano": "atmelavr@5.1.0",
    "arduino:avr:mega": "atmelavr@5.1.0",
    "arduino:esp32:nano_nora": "espressif32@6.10.0",
    "arduino:mbed_nano:nanorp2040connect": "raspberrypi@1.16.0",
}


async def install_libraries(sketch: Sketch, task_num: int):
    """Install libraries for the given sketch"""
    if not sketch.board in fqbn_to_board:
        raise HTTPException(
            422,
            "Unsupported fqbn, valid values are: " + ", ".join(fqbn_to_board.keys()),
        )
    pio_environment = fqbn_to_board[sketch.board]
    for library in sketch.libraries:
        logger.info("Using library %s in environment %s", library, pio_environment)
        # We cannot use --global here because we need to support different library versions per project/env
        installer = await asyncio.create_subprocess_exec(
            "platformio",
            "pkg",
            "install",
            "--library",
            library,
            "--environment",
            pio_environment,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=f"{settings.platformio_data_dir}/{task_num}",
        )
        stdout, stderr = await installer.communicate()
        if installer.returncode != 0:
            logger.warning(
                "Library install failed. Error: %s",
                stderr.decode() + stdout.decode(),
            )


async def compile_sketch(sketch: Sketch, task_num: int) -> dict[str, str]:
    """Compile the sketch and return the result in HEX format or as a binary blob"""
    sketch_path = f"{settings.platformio_data_dir}/{task_num}/src/main.ino"

    # Write the sketch to a temp .ino file
    async with aiofiles.open(sketch_path, "w+") as source_code:
        await source_code.write(sketch.source_code)

    compiler = await asyncio.create_subprocess_exec(
        "platformio",
        "run",
        "-v",
        "-e",
        f"{fqbn_to_board[sketch.board]}",
        "-j",
        str(settings.threads_per_platformio_compile),
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=f"{settings.platformio_data_dir}/{task_num}",
    )
    stdout, stderr = await compiler.communicate()
    if compiler.returncode != 0:
        logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
        raise HTTPException(500, stderr.decode() + stdout.decode())

    output_file = f"{settings.platformio_data_dir}/{task_num}/build/{fqbn_to_board[sketch.board]}/firmware."

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
        async with aiofiles.open(output_file + "uf2", "rb") as elf_file:
            result["sketch"] = base64.b64encode(await elf_file.read()).decode("utf-8")
    return result


async def setup_task_platformio_ini(task_num: int):
    """Setup workdirs and platformio.ini for a single task"""
    platformio_ini_text = "[env]\nlib_compat_mode = strict\n"
    for fqbn, board in fqbn_to_board.items():
        platformio_ini_text += (
            f"\n[env:{board}]\nframework = arduino\n"
            f"platform = {fqbn_to_platform[fqbn]}\nboard = {board}\n"
        )
    # Make sure compile and build dirs exist
    os.makedirs(f"{settings.platformio_data_dir}/{task_num}/src", exist_ok=True)
    os.makedirs(f"{settings.platformio_data_dir}/{task_num}/build", exist_ok=True)
    # Generate the platformio.ini file
    async with aiofiles.open(
        f"{settings.platformio_data_dir}/{task_num}/platformio.ini", "w+"
    ) as platform_ini:
        await platform_ini.write(
            platformio_ini_text + "\n[platformio]\nsrc_dir = src\nbuild_dir = build\n"
        )


async def setup_platformio() -> None:
    """Setup PlatformIO compile directory and config files"""
    rmtree(settings.platformio_data_dir)

    for task_num in range(settings.max_concurrent_tasks):
        await setup_task_platformio_ini(task_num)

    # Pre-install all supported platforms
    logger.info("Pre-installing all platforms, this will take a while...")
    install = await asyncio.create_subprocess_exec(
        "platformio",
        "-c",
        f"{settings.platformio_data_dir}/0/platformio.ini",
        "pkg",
        "install",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=f"{settings.platformio_data_dir}/0",
    )
    stdout, stderr = await install.communicate()
    logger.info(
        "Pre-installed all platforms:\n %s, %s", stdout.decode(), stderr.decode()
    )


@asynccontextmanager
async def startup(_app: FastAPI) -> AsyncGenerator[None, Any]:
    """Startup context manager"""
    await setup_platformio()
    yield
