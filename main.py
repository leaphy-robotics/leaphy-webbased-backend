""" Leaphy compiler backend webservice """
import asyncio
import tempfile
from os import path

import aiofiles
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from conf import settings
from models import Sketch, Library
from deps.session import Session, sessions
from deps.cache import code_cache, get_code_cache_key

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Limit compiler concurrency to prevent overloading the vm
semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)


async def _install_libraries(libraries: list[Library]):
    # Install required libraries
    for library in libraries:
        print(f"Installing libraries: {library}")
        installer = await asyncio.create_subprocess_exec(
            settings.arduino_cli_path,
            "lib",
            "install",
            library,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await installer.communicate()
        if installer.returncode != 0:
            raise HTTPException(
                500, f"Failed to install library: {stderr.decode() + stdout.decode()}"
            )


async def _compile_sketch(sketch: Sketch) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as dir_name:
        file_name = f"{path.basename(dir_name)}.ino"
        sketch_path = f"{dir_name}/{file_name}"

        # Write the sketch to a temp .ino file
        async with aiofiles.open(sketch_path, "w+") as _f:
            await _f.write(sketch.source_code)

        compiler = await asyncio.create_subprocess_exec(
            settings.arduino_cli_path,
            "compile",
            "--fqbn",
            sketch.board,
            sketch_path,
            "--output-dir",
            dir_name,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await compiler.communicate()
        if compiler.returncode != 0:
            raise HTTPException(500, stderr.decode() + stdout.decode())

        async with aiofiles.open(f"{sketch_path}.hex", "r") as _f:
            return {"hex": await _f.read()}


@app.post("/compile/cpp")
async def compile_cpp(sketch: Sketch, session_id: Session) -> dict[str, str]:
    """Compile code and return the result in HEX format"""
    # Make sure there's no more than X compile requests per user
    sessions[session_id] += 1

    try:
        # Check if this code was compiled before
        cache_key = get_code_cache_key(sketch.source_code)
        if compiled_code := code_cache.get(cache_key):
            # It was -> return cached result
            return compiled_code

        # Nope -> compile and store in cache
        async with semaphore:
            await _install_libraries(sketch.libraries)
            result = await _compile_sketch(sketch)
            code_cache[cache_key] = result
            return result
    finally:
        sessions[session_id] -= 1
