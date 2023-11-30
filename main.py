""" Leaphy compiler and minifier backend webservice """
import asyncio
import tempfile
import base64
from os import path

import aiofiles
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from python_minifier import minify

from conf import settings
from deps.cache import code_cache, get_code_cache_key, library_cache
from deps.lifespan import lifespan
from deps.logs import logger
from deps.session import Session, sessions
from models import Sketch, Library, PythonProgram

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Limit compiler concurrency to prevent overloading the vm
semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)


async def _install_libraries(libraries: list[Library]) -> None:
    # Install required libraries
    for library in libraries:
        if library_cache.get(library):
            continue

        logger.info("Installing libraries: %s", library)
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
            logger.error(
                "Failed to install library: %s", stderr.decode() + stdout.decode()
            )
            raise HTTPException(
                500, f"Failed to install library: {stderr.decode() + stdout.decode()}"
            )
        library_cache[library] = 1


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
            logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
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


@app.post("/minify/python")
async def minify_python(program: PythonProgram, session_id: Session) -> PythonProgram:
    """Minify a python program"""
    # Make sure there's no more than X minify requests per user
    sessions[session_id] += 1
    try:
        # Check if this code was minified before
        code = base64.b64decode(program.source_code).decode()
        cache_key = get_code_cache_key(code)
        if minified_code := code_cache.get(cache_key):
            # It was -> return cached result
            return minified_code

        # Nope -> minify and store in cache
        async with semaphore:
            program.source_code = base64.b64encode(
                minify(
                    code, filename=program.filename, remove_annotations=False
                ).encode()
            )
            code_cache[cache_key] = program.source_code
            return program
    finally:
        sessions[session_id] -= 1
