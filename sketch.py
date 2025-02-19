import asyncio
import io
import json
import os
import re
import zipfile
from os import path

import aiofiles
import requests
from fastapi import HTTPException

from deps.logs import logger
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

CWD = os.getcwd()

library_platformio_ini = """
[env]
lib_ldf_mode = deep+
lib_compile_flags = -Wl,--whole-archive
build_flags = -fno-eliminate-unused-debug-types -w
framework = arduino
build_type = release 
lib_deps = ./lib/lib 
           SPI
           Wire

""" # Default platformio.ini

for _fqbn, _board in fqbn_to_board.items():
    library_platformio_ini += f"""
[env:{_board}]
platform = {fqbn_to_platform[_fqbn]}
board = {_board}
"""

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
            versions = [ re.sub(r'[^.0-9]', '', repo["version"]) for repo in potential_repos]
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


        library_dir = f"./arduino-libs/{repo['name']}@{repo['version']}"
        zip_response = requests.get(repo["url"])
        library_zip = zipfile.ZipFile(io.BytesIO(zip_response.content))
        # All the content is in ZIP/ZIP_NAME/ but we want it in ZIP/ so we extract it to the root
        # Only export any CPP files to lib/ dir
        os.mkdir(library_dir)
        os.mkdir(f"{library_dir}/src")
        os.mkdir(f"{library_dir}/lib")
        os.mkdir(f"{library_dir}/lib/lib")



        dependencies = []
        installed_dependency = {}
        supported_arches = []
        includes = {}
        dir_dependencies = {}
        header_files = []

        for includes_board in fqbn_to_board.values():
            includes[includes_board] = ""
            dir_dependencies[includes_board] = ""

        for file in library_zip.namelist():
            if file.endswith(".cpp") or file.endswith(".h") or file.endswith(".c") or file.endswith(".hpp"):
                # Check where we want to store the file if it is in a src/ directory everything after that should be preserved, if it is in the root we want tom perserve everything after the library name
                actual_file = file
                if file.startswith(f"{repo['archiveFileName'].removesuffix(".zip")}/src/"):
                    file = file.replace(f"{repo['archiveFileName'].removesuffix('.zip')}/src/", "")
                elif file == f"{repo['archiveFileName'].removesuffix('.zip')}/" + file.split("/")[-1]:
                    file = file.replace(f"{repo['archiveFileName'].removesuffix('.zip')}/", "")
                else:
                    continue
                # Recursively create the directories for the file
                if file.endswith(".hpp") or file.endswith(".h"):
                    header_files.append(file)
                os.makedirs(path.dirname(f"{library_dir}/lib/lib/{file}"), exist_ok=True)
                async with aiofiles.open(f"{library_dir}/lib/lib/{file}", "w+") as _f:
                    await _f.write(library_zip.read(actual_file).decode())
            elif file.endswith("library.properties"):
                # Read only the dependencies from the library.properties file and recursively call _install_libraries
                library_properties = library_zip.read(file).decode()
                dependencies = [line.split("=")[1] for line in library_properties.split("\n") if line.startswith("depends=")]
                if dependencies:
                    dependencies = dependencies[0].split(",")
                    for i, dependency in enumerate(dependencies):
                        dependencies[i] = dependency.strip()
                    installed_dependency = await _install_libraries(dependencies)
                supported_arches = [line.split("=")[1] for line in library_properties.split("\n") if line.startswith("architectures=")]
                supported_arches =  supported_arches[0].split(",")

        async with aiofiles.open(f"{library_dir}/src/main.cpp", "w+") as _f:
            await _f.write("#include <SPI.h>\n#include <Wire.h>\n#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}")


        async with aiofiles.open(f"{library_dir}/package.json", "w+") as _f:
            await _f.write(json.dumps({"name": repo, "version": repo["version"]}))

        # Compile the library using platformio and store the compiled sources so we can use them later
        if dependencies or (not "*" in supported_arches):
            directory_dependencies = []
            base_dependency_platformio_ini = f"[env]\nlib_ldf_mode = deep+\nlib_compile_flags = -Wl,--whole-archive\nframework = arduino\nbuild_type = release\n"
            for dependency in dependencies:
                directory_dependencies.append(f"../{dependency}@{installed_dependency[dependency]}/")

            for fqbn, board in fqbn_to_board.items():
                if fqbn.split(":")[1] in supported_arches or "*" in supported_arches:
                    base_dependency_platformio_ini += f"[env:{board}]\nlib_deps = ./lib/lib\n"
                    for dir_dependency in directory_dependencies:
                        if os.path.exists(f"{CWD}/arduino-libs/{dir_dependency.split("/")[-2]}/lib{dir_dependency.split("/")[-2].split("@")[0].removeprefix("/").replace(" ", "-")}-{board}.a"):
                            base_dependency_platformio_ini += f"\t\t\t{dir_dependency}lib/lib\n"
                            dir_dependencies[board] += f"\t\t\t{dir_dependency}lib/lib\n"
                            async with aiofiles.open(
                                    f"./arduino-libs/{dir_dependency.split('/')[-2]}/compiled_sources.json",
                                    "r") as _f:
                                dep_incl = json.loads(await _f.read())["dirs"][board]
                                dir_dependencies[board] += dep_incl
                    base_dependency_platformio_ini += f"\nplatform = {fqbn_to_platform[fqbn]}\nboard = {board}\nbuild_flags = -w "
                    for dependency in dependencies:
                        if os.path.exists(f"{CWD}/arduino-libs/{dependency}@{installed_dependency[dependency]}/lib{dependency.replace(" ", "-")}-{board}.a"):
                            base_dependency_platformio_ini += f"-L'../{dependency}@{installed_dependency[dependency]}/' -l{dependency.replace(" ", "-")}-{board}  "
                            async with aiofiles.open(
                                    f"./arduino-libs/{dependency}@{installed_dependency[dependency]}/compiled_sources.json",
                                    "r") as _f:
                                dep_incl = json.loads(await _f.read())["include"][board]
                                base_dependency_platformio_ini += dep_incl
                                includes[board] += dep_incl
                            base_dependency_platformio_ini += f"-I'../{dependency}@{installed_dependency[dependency]}/lib/lib/' "
                            includes[board] += f"-I'../{dependency}@{installed_dependency[dependency]}/lib/lib/' "
                    base_dependency_platformio_ini += "\n"

            async with aiofiles.open(f"{library_dir}/platformio.ini", "w+") as _f:
                await _f.write(base_dependency_platformio_ini)
        else:
            async with aiofiles.open(f"{library_dir}/platformio.ini", "w+") as _f:
                await _f.write(library_platformio_ini)

        compiler = await asyncio.create_subprocess_exec(
            "platformio",
            "run",
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=f"{library_dir}",
        )

        stdout, stderr = await compiler.communicate()
        if compiler.returncode != 0:
            logger.warning("Compilation failed: %s", stderr.decode() + stdout.decode())
            raise HTTPException(500, stderr.decode() + stdout.decode())

        # Search where liblib.a is stored for each build type and copy it to the root directory of the lib as libLIBRARY_NAME-BUILD_TYPE.a
        for board in fqbn_to_board.values():
            for root, _, files in os.walk(f"{library_dir}/.pio/build/{board}"):
                for file in files:
                    if file.endswith("liblib.a"): os.rename(path.join(root, file), f"{library_dir}/lib{repo['name'].replace(" ", "-")}-{board}.a")

        # Store the compiled sources in the library cache
        async with aiofiles.open(f"{library_dir}/compiled_sources.json", "w+") as _f:
            await _f.write(json.dumps({"include": includes, "dirs": dir_dependencies}))

        installed_library_versions[library] = version_required
    return installed_library_versions