"""Sketch and library management"""

from contextlib import asynccontextmanager
import asyncio
import io
import json
import os
import re
import zipfile
from os import path

import semver
import aiofiles
import httpx
from fastapi import FastAPI, HTTPException

from deps.utils import repeat_every, check_for_internet
from deps.logs import logger
from models import Library, Sketch
from conf import settings

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

library_index_json = {}
library_indexed_json = {}


def _get_latest_version(versions: list[str]) -> str:
    """Return the latest version from a list of semantically versioned strings"""
    max_version = versions[0]
    for version in versions:
        if semver.compare(version, max_version) == 1:
            max_version = version
    return max_version


def _parse_library_properties(properties: str) -> dict[str, str]:
    """Parse a library.properties file and return the key-value pairs"""
    return dict(line.split("=") for line in properties.split("\n") if "=" in line)


async def _install_library_zip(
    library_zip: zipfile.ZipFile, repo: dict[str, str], library_dir: str
):
    arches = []
    deps = []
    deps_arches = {}
    in_deps = {}
    for file in library_zip.namelist():
        if file.split(".")[-1] in ["cpp", "c", "h", "hpp"]:
            # Check where we want to store the file
            # If it is in a src/ directory everything after that should be preserved
            # If it is in the root we want tom perserve everything after the library name
            new_file = file
            root_folder = repo["archiveFileName"].removesuffix(".zip")
            if new_file.startswith(f"{root_folder}/src/"):
                new_file = new_file.replace(f"{root_folder}/src/", "")
            elif new_file == f"{root_folder}/" + new_file.split("/")[-1]:
                new_file = new_file.replace(f"{root_folder}/", "")
            else:
                continue
            # Recursively create the directories for the file
            os.makedirs(path.dirname(f"{library_dir}/src/{new_file}"), exist_ok=True)
            async with aiofiles.open(
                f"{library_dir}/src/{new_file}", "w+"
            ) as source_file:
                await source_file.write(library_zip.read(file).decode())
        elif file.endswith("library.properties"):
            # Read only the dependencies and arches from the library.properties file
            # Recursively call _install_libraries
            library_props = _parse_library_properties(library_zip.read(file).decode())
            arches = library_props.get("architectures", "*").split(",")
            deps = library_props.get("depends", "").split(",")
            if deps[0] == "":
                deps = []
            if deps:
                for i, dep in enumerate(deps):
                    deps[i] = dep.strip()
                in_deps = await _install_libraries(deps)
                for dep in deps:
                    async with aiofiles.open(
                        f"./arduino-libs/{dep}@{in_deps[dep]}/compiled_sources.json",
                        "r",
                    ) as compiled_sources:
                        deps_arches[dep] = json.loads(await compiled_sources.read())[
                            "arches"
                        ]
    return arches, deps, deps_arches, in_deps


async def _install_libraries(  # pylint: disable=too-many-locals, too-many-branches
    libraries: list[Library],
) -> dict[Library, str]:
    # Install required libraries
    if not await check_for_internet():
        logger.warning("No internet connection, skipping library install")
        return {}

    installed_library_versions = {}
    for library in libraries:
        library = library.strip()
        potential_repos = library_indexed_json.get(library)
        if not potential_repos:
            raise HTTPException(404, f"Library {library} not found")

        version_required = ""
        if "@" in library:
            version_required = library.split("@")[1]
        else:
            versions = [
                re.sub(r"[^.0-9]", "", repo["version"]) for repo in potential_repos
            ]
            version_required = _get_latest_version(versions)

        # Check if the library is already installed
        if path.exists(f"./arduino-libs/{library}@{version_required}"):
            installed_library_versions[library] = version_required
            continue

        logger.info("Installing libraries: %s@%s", library, version_required)

        repo = list(
            filter(
                lambda x: x["version"]
                == version_required,  # pylint: disable=cell-var-from-loop
                potential_repos,
            )
        )[0]
        if not repo:
            raise HTTPException(
                404, f"Library {library} not found, with version {version_required}"
            )

        library_dir = f"./arduino-libs/{repo['name']}@{repo['version']}"
        async with httpx.AsyncClient() as client:
            library_zip = zipfile.ZipFile(
                io.BytesIO((await client.get(repo["url"])).content)
            )
        # All the content is in ZIP/ZIP_NAME/ but we want it in ZIP/ so we extract it to the root
        # Only export any CPP files to lib/ dir
        os.makedirs(library_dir, exist_ok=True)
        os.mkdir(f"{library_dir}/src")

        includes = {}
        dir_deps = {}
        arches, deps, deps_arches, in_deps = await _install_library_zip(
            library_zip, repo, library_dir
        )

        for includes_board in fqbn_to_board.values():
            includes[includes_board] = ""
            dir_deps[includes_board] = ""

        # Compile the library using platformio and store the compiled sources so we can use them later
        if deps or (not "*" in arches):
            for fqbn, board in fqbn_to_board.items():
                if fqbn.split(":")[1] not in arches and "*" not in arches:
                    continue
                for dep in deps:
                    if board not in deps_arches[dep] and "*" not in deps_arches[dep]:
                        continue
                    dir_path = f"../{dep}@{in_deps[dep]}/"
                    dir_deps[board] += f"\t\t\t{dir_path}src\n"
                    includes[board] += f"-I'../{dep}@{in_deps[dep]}/src/' "
                    async with aiofiles.open(
                        f"./arduino-libs/{dep}@{in_deps[dep]}/compiled_sources.json",
                        "r",
                    ) as _f:
                        data = json.loads(await _f.read())
                        dir_deps[board] += data["dirs"][board]
                        includes[board] += data["include"][board]

        # Store the compiled sources in the library cache
        async with aiofiles.open(f"{library_dir}/compiled_sources.json", "w+") as _f:
            await _f.write(
                json.dumps(
                    {
                        "include": includes,
                        "dirs": dir_deps,
                        "arches": arches,
                    }
                )
            )

        installed_library_versions[library] = version_required
    return installed_library_versions


async def _compile_sketch(  # pylint: disable=too-many-locals
    sketch: Sketch, installed_libs: dict[Library, str]
) -> dict[str, str]:
    async with aiofiles.tempfile.TemporaryDirectory() as dir_name:
        sketch_path = f"{dir_name}/src/main.cpp"
        platformio_config_path = f"{dir_name}/platformio.ini"

        os.mkdir(f"{dir_name}/src")

        # Write the sketch to a temp .ino file
        async with aiofiles.open(sketch_path, "w+") as platform_ini:
            await platform_ini.write("#include <Arduino.h>\n" + sketch.source_code)

        async with aiofiles.open(platformio_config_path, "w+") as platformio_ini:
            libs = ""
            includes = ""
            for lib in installed_libs:
                libs += f"\n\t\t\t{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/src "
                includes += f"-I'{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/src' "
                async with aiofiles.open(
                    f"{CWD}/arduino-libs/{lib}@{installed_libs[lib]}/compiled_sources.json",
                    "r",
                ) as compiled_sources:
                    data = json.loads(await compiled_sources.read())
                    includes += data["include"][fqbn_to_board[sketch.board]].replace(
                        "../", f"{CWD}/arduino-libs/"
                    )
                    libs += "\n" + data["dirs"][fqbn_to_board[sketch.board]].replace(
                        "../", f"{CWD}/arduino-libs/"
                    )
            await platformio_ini.write(
                f"[env:build]\nplatform = {fqbn_to_platform[sketch.board]}\nbuild_flags = -w {includes}\nboard = {fqbn_to_board[sketch.board]}\nframework = arduino\nlib_deps = {libs}"  # pylint: disable=line-too-long
            )

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
        async with aiofiles.open(output_file, "r", encoding="utf-8") as hex_file:
            return {"hex": str(await hex_file.read())}


@asynccontextmanager
async def startup(_app: FastAPI) -> None:
    """Startup context manager"""
    if settings.library_index_refresh_interval > 0:
        await refresh_library_index()
    yield


@repeat_every(seconds=settings.library_index_refresh_interval, logger=logger)
async def refresh_library_index():
    """Update the Arduino library index"""
    if not await check_for_internet():
        return
    logger.info("Updating library index...")
    global library_index_json, library_indexed_json  # pylint: disable=global-statement
    async with httpx.AsyncClient() as client:
        library_index_json = (
            await client.get(
                "https://downloads.arduino.cc/libraries/library_index.json"
            )
        ).json()
    library_indexed_json = {}
    for index_library in library_index_json["libraries"]:
        if index_library["name"] not in library_indexed_json:
            library_indexed_json[index_library["name"]] = [index_library]
        else:
            library_indexed_json[index_library["name"]].append(index_library)
