"""Leaphy compiler and minifier backend webservice"""

import asyncio
import base64
from tempfile import TemporaryDirectory

import aiofiles
import tensorflow as tf
import tensorflowjs as tfjs
from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from python_minifier import minify

from conf import settings
from deps.cache import code_cache, get_code_cache_key
from deps.session import Session, compile_sessions, llm_tokens
from deps.sketch import install_libraries, compile_sketch, startup
from deps.utils import bin2header
from models import Sketch, PythonProgram, Messages

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
available_task_ids = list(range(settings.max_concurrent_tasks))


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
            try:
                task_id = available_task_ids.pop(0)
                await install_libraries(sketch.libraries, sketch.board)
                result = await compile_sketch(sketch, task_id)
                code_cache[cache_key] = result
            finally:
                available_task_ids.insert(0, task_id)
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


@app.post("/ml/convert")
async def convert(
    _session_id: Session,
    model_json: UploadFile = File(..., alias="model.json"),
    model_weights: UploadFile = File(..., alias="model.weights.bin"),
):
    """Converts a TFJS model to TFLite"""
    with TemporaryDirectory() as directory:
        async with aiofiles.open(f"{directory}/model.json", "wb") as f:
            await f.write(await model_json.read())

        async with aiofiles.open(f"{directory}/model.weights.bin", "wb") as f:
            await f.write(await model_weights.read())

        model = tfjs.converters.load_keras_model(f"{directory}/model.json")

        converter = tf.lite.TFLiteConverter.from_keras_model(model)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        converter.target_spec.supported_types = [tf.float32]

        tflite_model = converter.convert()
        return bin2header(tflite_model, "model_data")
