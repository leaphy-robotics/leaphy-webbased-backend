""" Leaphy compiler and minifier backend webservice """

import asyncio
import base64
import json
import os


import aiofiles
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from python_minifier import minify

from conf import settings
from deps.cache import code_cache, get_code_cache_key, library_cache
from deps.logs import logger
from deps.session import Session, compile_sessions, llm_tokens
from deps.tasks import startup
from models import Sketch, Library, PythonProgram, Messages
from sketch import _install_libraries, fqbn_to_platform, fqbn_to_board

app = FastAPI(lifespan=startup)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
client = Groq(api_key=settings.groq_api_key)

# Limit compiler concurrency to prevent overloading the vm
semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)

CWD = os.getcwd()

async def _compile_sketch(sketch: Sketch, installed_libs:  dict[Library, str]) -> dict[str, str]:
    dir_name = "/tmp/build"
    sketch_path = f"{dir_name}/src/main.cpp"
    platformio_config_path = f"{dir_name}/platformio.ini"

    os.mkdir(f"{dir_name}/src")

    # Write the sketch to a temp .ino file
    async with aiofiles.open(sketch_path, "w+") as _f:
        await _f.write("#include <SPI.h>\n#include <Wire.h>\n#include <Arduino.h>\n" + sketch.source_code)

    async with aiofiles.open(platformio_config_path, "w+") as _f:
        libs = "SPI\n\t\t\tWire"
        includes = ""
        for lib in installed_libs:
            libs += f"\n\t\t\t{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/lib/lib "
            includes += f"-I'{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/lib/lib' "
            async with aiofiles.open(f"{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/compiled_sources.json", "r") as _f2:
                data = json.loads(await _f2.read())
                includes += data["include"][fqbn_to_board[sketch.board]].replace("../", f"{CWD}/arduino-libs/")
                libs += "\n" + data["dirs"][fqbn_to_board[sketch.board]].replace("../", f"{CWD}/arduino-libs/")
        await _f.write(f"[env:build]\nplatform = {fqbn_to_platform[sketch.board]}\nbuild_flags = -w {includes}\nboard = {fqbn_to_board[sketch.board]}\nframework = arduino\nlib_deps = {libs}")

    compiler = await asyncio.create_subprocess_exec(
        "platformio",
        "run",
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=dir_name,
    )
    stdout, stderr = await compiler.communicate()
    if compiler.returncode != 0:
        logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
        raise HTTPException(500, stderr.decode() + stdout.decode())

    output_file = f"{dir_name}/.pio/build/build/firmware.hex"
    async with aiofiles.open(output_file, "r", encoding="utf-8") as _f:
        return {"hex": str(await _f.read())}


@app.post("/compile/cpp")
async def compile_cpp(sketch: Sketch, session_id: Session) -> dict[str, str]:
    """Compile code and return the result in HEX format"""
    # Make sure there's no more than X compile requests per user
    compile_sessions[session_id] += 1

    try:
        # Check if this code was compiled before
        cache_key = get_code_cache_key(sketch.model_dump_json())
        if compiled_code := code_cache.get(cache_key):
            # It was -> return cached result
            return compiled_code

        # Nope -> compile and store in cache
        async with semaphore:
            installed_libs = await _install_libraries(sketch.libraries)
            result = await _compile_sketch(sketch, installed_libs)
            code_cache[cache_key] = result
            return result
    finally:
        compile_sessions[session_id] -= 1


@app.post("/minify/python")
async def minify_python(program: PythonProgram, session_id: Session) -> PythonProgram:
    """Minify a python program"""
    # Make sure there's no more than X minify requests per user
    compile_sessions[session_id] += 1
    try:
        # Check if this code was minified before
        try:
            code = base64.b64decode(program.source_code).decode()
        except Exception as ex:
            raise HTTPException(
                422, f"Unable to base64 decode program: {str(ex)}"
            ) from ex

        cache_key = get_code_cache_key(code)
        if minified_code := code_cache.get(cache_key):
            # It was -> return cached result
            return minified_code

        # Nope -> minify and store in cache
        async with semaphore:
            try:
                code = minify(code, filename=program.filename, remove_annotations=False)
            except Exception as ex:
                raise HTTPException(
                    422, f"Unable to minify python program: {str(ex)}"
                ) from ex
            program.source_code = base64.b64encode(code.encode())
            code_cache[cache_key] = program
            return program
    finally:
        compile_sessions[session_id] -= 1


@app.post("/ai/generate")
async def generate(messages: Messages, session_id: Session):
    """Generate message"""
    if llm_tokens[session_id] >= settings.max_llm_tokens:
        raise HTTPException(429, {"detail": "Try again later"})

    response = client.chat.completions.create(
        messages=list(map(lambda e: e.dict(), messages.messages)),
        model="llama3-70b-8192",
    )
    llm_tokens[session_id] += response.usage.total_tokens

    return response.choices[0].message.content
