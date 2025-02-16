import asyncio
import io
import json
import os
import tempfile
import zipfile
from os import path
import aiofiles
import requests

from fastapi import HTTPException

from deps.logs import logger
from deps.spi import SPI_H
from deps.utils import check_for_internet

from models import Library

fqbn_to_board = { # Mapping from fqbn to PlatformIO board
    "arduino:avr:uno": "uno",
    "arduino:avr:nano": "nanoatmega328",
    "arduino:avr:mega": "ATmega2560",
    "arduino:esp32:nano_nora": "arduino_nano_esp32",
}

fqbn_to_platform = { # Mapping from fqbn to PlatformIO platform
    "arduino:avr:uno": "atmelavr",
    "arduino:avr:nano": "atmelavr",
    "arduino:avr:mega": "atmelavr",
    "arduino:esp32:nano_nora": "espressif32",
}

library_platformio_ini = """
[env]
framework = arduino
build_type = release 
lib_deps = ./lib/lib 
           SPI

[env:build]
platform = atmelavr
board = megaatmega2560
""" # Default platformio.ini

for _fqbn, _board in fqbn_to_board.items():
    library_platformio_ini += f"""
[env:{_board}]
platform = {fqbn_to_platform[_fqbn]}
board = {_board}
"""

CWD = path.dirname(path.realpath(__file__))

library_index_json = requests.get("https://downloads.arduino.cc/libraries/library_index.json").json()
library_indexed_json = {}
for index_library in library_index_json["libraries"]:
    if index_library["name"] not in library_indexed_json:
        library_indexed_json[index_library["name"]] = [index_library]
    else:
        library_indexed_json[index_library["name"]].append(index_library)

def _get_latest_version(versions: list[str]) -> str:
    """Return the latest version from a list of semantically versioned strings"""
    return max(versions, key=lambda x: tuple(map(int, x.split("."))))

async def _install_libraries(libraries: list[Library]) -> dict[Library, str]:
    # Install required libraries
    if not await check_for_internet():
        logger.warning("No internet connection, skipping library install")
        return {}

    installed_library_versions = {}
    for library in libraries:
        library = library.strip()
        potential_repos = library_indexed_json.get(library)
        if library.find("@") != -1:
            version_required = library.split("@")[1]
        else:
            versions = [repo["version"] for repo in potential_repos]
            if not versions:
                print(versions, library, potential_repos)
            version_required = _get_latest_version(versions)

        # Check if the library is already installed
        if path.exists(f"./arduino-libs/{library}@{version_required}"):
            installed_library_versions[library] = version_required
            continue

        if not potential_repos:
            raise HTTPException(404, f"Library {library} not found")

        logger.info("Installing libraries: %s", library)

        for potential_repo in potential_repos:
            if potential_repo["version"] == version_required:
                repo = potential_repo
                break
        else:
            raise HTTPException(404, f"Library {library} not found, with version {version_required}")


        zip_response = requests.get(repo["url"])
        library_zip = zipfile.ZipFile(io.BytesIO(zip_response.content))
        # All the content is in ZIP/ZIP_NAME/ but we want it in ZIP/ so we extract it to the root
        # Only export any CPP files to lib/ dir
        os.mkdir(f"./arduino-libs/{repo['name']}@{repo["version"]}")
        os.mkdir(f"./arduino-libs/{repo['name']}@{repo["version"]}/src")
        os.mkdir(f"./arduino-libs/{repo['name']}@{repo["version"]}/lib")
        os.mkdir(f"./arduino-libs/{repo['name']}@{repo["version"]}/lib/lib")

        async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/src/main.cpp", "w+") as _f:
            await _f.write("#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}")

        dependencies = []
        installed_dependency = {}
        includes = ""

        for file in library_zip.namelist():
            if file.endswith(".cpp") or file.endswith(".h") or file.endswith(".c"):
                async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/lib/lib/{file.split('/')[-1]}", "wb+") as _f:
                    await _f.write( library_zip.read(file))
            elif file.endswith("library.properties"):
                # Read only the dependencies from the library.properties file and recursively call _install_libraries
                library_properties = library_zip.read(file).decode()
                dependencies = [line.split("=")[1] for line in library_properties.split("\n") if line.startswith("depends=")]
                if dependencies:
                    dependencies = dependencies[0].split(",")
                    for i, dependency in enumerate(dependencies):
                        dependencies[i] = dependency.strip()
                    installed_dependency = await _install_libraries(dependencies)

        async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/package.json", "w+") as _f:
            await _f.write(json.dumps({"name": repo, "version": repo["version"]}))

        # Compile the library using platformio and store the compiled sources so we can use them later
        if dependencies:
            base_dependency_platformio_ini = "[env]\nlib_deps = ./lib/lib\n\t\t   SPI\nframework = arduino\nbuild_type = release\n"
            for dependency in dependencies:
                async with aiofiles.open(f"./arduino-libs/{dependency}@{installed_dependency[dependency]}/compiled_sources.json", "r") as _f:
                    includes += json.loads(await _f.read())["include"]
                includes += f"-I'{CWD}/arduino-libs/{dependency}@{installed_dependency[dependency]}/lib/lib/' "

            for fqbn, board in fqbn_to_board.items():
                base_dependency_platformio_ini += f"[env:{board}]\nplatform = {fqbn_to_platform[fqbn]}\nboard = {board}\nbuild_flags = {includes} "
                for dependency in dependencies:
                    base_dependency_platformio_ini += f"-L'{CWD}/arduino-libs/{dependency}@{installed_dependency[dependency]}/' -l{dependency.replace(" ", "-")}-{board}  "
                base_dependency_platformio_ini += "\n"

            async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/platformio.ini", "w+") as _f:
                await _f.write(base_dependency_platformio_ini)
        else:
            async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/platformio.ini", "w+") as _f:
                await _f.write(library_platformio_ini)

        compiler = await asyncio.create_subprocess_exec(
            "platformio",
            "run",
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=f"./arduino-libs/{repo['name']}@{repo['version']}",
        )

        stdout, stderr = await compiler.communicate()
        if compiler.returncode != 0:
            logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
            raise HTTPException(500, stderr.decode() + stdout.decode())

        # Search where liblib.a is stored for each build type and copy it to the root directory of the lib as libLIBRARY_NAME-BUILD_TYPE.a
        for board in fqbn_to_board.values():
            for root, _, files in os.walk(f"./arduino-libs/{repo['name']}@{repo["version"]}/.pio/build/{board}"):
                for file in files:
                    if file.endswith("liblib.a"):
                        os.rename(path.join(root, file), f"./arduino-libs/{repo['name']}@{repo['version']}/lib{repo['name'].replace(" ", "-")}-{board}.a")

        # Store the compiled sources in the library cache
        async with aiofiles.open(f"./arduino-libs/{repo['name']}@{repo["version"]}/compiled_sources.json", "w+") as _f:
            await _f.write(json.dumps({"include": includes}))

        installed_library_versions[library] = version_required
    return installed_library_versions