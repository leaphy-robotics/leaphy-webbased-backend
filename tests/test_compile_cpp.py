"""Tests for /api/compile/cpp endpoint and library validation."""

# pylint: disable=redefined-outer-name,unused-argument

import os
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from conf import settings
from deps.cache import code_cache
from deps.session import compile_sessions
from deps.sketch import fqbn_to_board
from main import app


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    """Clean cache, session state, and point platformio_data_dir to a temp directory."""
    code_cache.clear()
    compile_sessions.clear()
    orig_dir = settings.platformio_data_dir
    settings.platformio_data_dir = str(tmp_path / "compiles")
    yield tmp_path
    settings.platformio_data_dir = orig_dir
    code_cache.clear()
    compile_sessions.clear()


@pytest.fixture
def mock_pio_subprocess():
    """Mock asyncio.create_subprocess_exec for platformio commands."""

    async def _mock_exec(*args, **kwargs):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        cmd = list(args)

        if cmd and cmd[0] == "platformio":
            if len(cmd) > 1 and cmd[1] == "pkg" and "install" in cmd:
                # Mock platformio pkg install
                mock_proc.communicate.return_value = (
                    b"Library installed successfully",
                    b"",
                )
            elif len(cmd) > 1 and cmd[1] == "run":
                # Mock platformio run compile: create simulated firmware.hex in task build dir
                cwd = kwargs.get("cwd", "")
                env_name = None
                if "-e" in cmd:
                    env_idx = cmd.index("-e") + 1
                    env_name = cmd[env_idx]

                if cwd and env_name:
                    build_dir = os.path.join(cwd, "build", env_name)
                    os.makedirs(build_dir, exist_ok=True)
                    hex_path = os.path.join(build_dir, "firmware.hex")
                    with open(hex_path, "w", encoding="utf-8") as f:
                        f.write(":100000000C945C000C946E000C946E000C946E00CA")

                mock_proc.communicate.return_value = (b"Compilation successful", b"")
            else:
                mock_proc.communicate.return_value = (b"", b"")
        else:
            mock_proc.communicate.return_value = (b"", b"")

        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_mock_exec) as mock_exec:
        yield mock_exec


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestLibraryValidation:
    """Tests for validating library names and URLs on /api/compile/cpp."""

    @pytest.mark.parametrize(
        "library",
        [
            "Servo",
            "Adafruit NeoPixel",
            "LiquidCrystal_I2C",
            "DHT-sensor-library",
            "Servo@1.2.1",
            "FastLED@3.5.0",
            "bblanchon/ArduinoJson",
            "adafruit/Adafruit NeoPixel",
            "bblanchon/ArduinoJson@6.21.3",
            "adafruit/DHT sensor library@1.4.4",
            "foo_bar.baz-123",
            "author_name-1.0/lib_name-2.0@3.0.0-beta.1",
        ],
    )
    def test_valid_library_names(self, client, mock_pio_subprocess, library):
        """Valid library names should pass validation and invoke platformio pkg install."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [library],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 200
        assert "hex" in response.json()

        # Verify platformio was called for pkg install with the library
        called_cmds = [call.args for call in mock_pio_subprocess.call_args_list]
        install_calls = [
            cmd
            for cmd in called_cmds
            if len(cmd) >= 5
            and cmd[0] == "platformio"
            and cmd[1] == "pkg"
            and cmd[2] == "install"
        ]
        assert len(install_calls) == 1
        assert install_calls[0][4] == library
        assert install_calls[0][6] == fqbn_to_board["arduino:avr:uno"]

    def test_multiple_valid_libraries(self, client, mock_pio_subprocess):
        """Multiple valid libraries should all be installed."""
        libs = ["Servo@1.2.1", "bblanchon/ArduinoJson@6.21.3", "Adafruit NeoPixel"]
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": libs,
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 200

        called_cmds = [call.args for call in mock_pio_subprocess.call_args_list]
        installed_libs = [
            cmd[4]
            for cmd in called_cmds
            if len(cmd) >= 5
            and cmd[0] == "platformio"
            and cmd[1] == "pkg"
            and cmd[2] == "install"
        ]
        assert installed_libs == libs

    def test_empty_libraries(self, client, mock_pio_subprocess):
        """Empty library list is valid and compiles without installing libraries."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 200

        called_cmds = [call.args for call in mock_pio_subprocess.call_args_list]
        install_calls = [
            cmd
            for cmd in called_cmds
            if len(cmd) >= 3
            and cmd[0] == "platformio"
            and cmd[1] == "pkg"
            and cmd[2] == "install"
        ]
        assert len(install_calls) == 0

    @pytest.mark.parametrize(
        "invalid_library",
        [
            "Servo; rm -rf /",
            "Servo && echo hacked",
            "Servo | cat /etc/passwd",
            "$(whoami)",
            "`whoami`",
            "Servo\nrm -rf /",
            "Servo > /tmp/out",
            "Servo < /tmp/in",
            "Servo$var",
            "Servo#branch",
            "Servo:version",
            "author/pkg/extra",
            "pkg@v1@v2",
            "/leading/slash",
            "@leading/at",
            "trailing/slash/",
            "trailing/at@",
            "author//package",
            "author/package@@1.0",
        ],
    )
    def test_invalid_library_names_rejected(
        self, client, mock_pio_subprocess, invalid_library
    ):
        """Invalid library patterns and injection attempts should return 422 and not call platformio."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [invalid_library],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 422
        assert mock_pio_subprocess.call_count == 0

    @pytest.mark.parametrize(
        "allowed_url",
        [
            "https://github.com/madhephaestus/ESP32Servo",
            "https://github.com/madhephaestus/ESP32Servo/archive/refs/tags/1.2.1.zip",
        ],
    )
    def test_allowlisted_url_library(self, client, mock_pio_subprocess, allowed_url):
        """Allowlisted URLs should pass validation and install via platformio."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [allowed_url],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 200

        called_cmds = [call.args for call in mock_pio_subprocess.call_args_list]
        install_calls = [
            cmd
            for cmd in called_cmds
            if len(cmd) >= 5
            and cmd[0] == "platformio"
            and cmd[1] == "pkg"
            and cmd[2] == "install"
        ]
        assert len(install_calls) == 1
        # HttpUrl string representation
        assert allowed_url in install_calls[0][4]

    @pytest.mark.parametrize(
        "disallowed_url",
        [
            "https://github.com/attacker/malicious-lib",
            "http://attacker.com/payload.zip",
            "https://github.com/other-user/ESP32Servo",
            "https://gitlab.com/madhephaestus/ESP32Servo",
        ],
    )
    def test_disallowed_url_library_rejected(
        self, client, mock_pio_subprocess, disallowed_url
    ):
        """Non-allowlisted URLs should return 422 and not install via platformio."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [disallowed_url],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 422
        assert (
            "Only libraries from the allowlist can be installed"
            in response.json()["detail"]
        )

        called_cmds = [call.args for call in mock_pio_subprocess.call_args_list]
        install_calls = [
            cmd
            for cmd in called_cmds
            if len(cmd) >= 3
            and cmd[0] == "platformio"
            and cmd[1] == "pkg"
            and cmd[2] == "install"
        ]
        assert len(install_calls) == 0

    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http://",
            "https://",
            "ftp://invalid-scheme.com/lib.zip",
            "http:///no-host",
        ],
    )
    def test_invalid_url_format_rejected(
        self, client, mock_pio_subprocess, invalid_url
    ):
        """Malformed URLs should return 422 validation error."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": [invalid_url],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 422
        assert mock_pio_subprocess.call_count == 0


class TestCompileEndpointExecution:
    """Tests for compilation flow and error handling with mocked PlatformIO."""

    def test_unsupported_board_rejected(self, client, mock_pio_subprocess):
        """Unsupported board fqbn should return 422."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "invalid:board:type",
            "libraries": ["Servo"],
        }

        response = client.post("/api/compile/cpp", json=payload)
        assert response.status_code == 422
        assert "Unsupported fqbn" in response.json()["detail"]

    def test_compile_error_returns_500(self, client):
        """If platformio run returns non-zero, compile_cpp should return 500 with error log."""

        async def _failing_compiler(*args, **kwargs):
            mock_proc = AsyncMock()
            cmd = list(args)
            if len(cmd) > 1 and cmd[1] == "run":
                mock_proc.returncode = 1
                mock_proc.communicate.return_value = (
                    b"",
                    b"Error: fatal error: Servo.h: No such file",
                )
            else:
                mock_proc.returncode = 0
                mock_proc.communicate.return_value = (b"", b"")
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=_failing_compiler):
            payload = {
                "source_code": "#include <Servo.h>\nvoid setup() {}",
                "board": "arduino:avr:uno",
                "libraries": [],
            }
            response = client.post("/api/compile/cpp", json=payload)
            assert response.status_code == 500
            assert "Servo.h: No such file" in response.json()["detail"]

    def test_caching_compiled_result(self, client, mock_pio_subprocess):
        """Subsequent request with same sketch should return cached result without calling pio."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:uno",
            "libraries": ["Servo@1.2.1"],
        }

        resp1 = client.post("/api/compile/cpp", json=payload)
        assert resp1.status_code == 200
        call_count_1 = mock_pio_subprocess.call_count
        assert call_count_1 > 0

        resp2 = client.post("/api/compile/cpp", json=payload)
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()
        assert mock_pio_subprocess.call_count == call_count_1

    def test_legacy_compile_cpp_route(self, client, mock_pio_subprocess):
        """The /compile/cpp alias route should work identically."""
        payload = {
            "source_code": "void setup() {} void loop() {}",
            "board": "arduino:avr:nano",
            "libraries": ["FastLED@3.5.0"],
        }

        response = client.post("/compile/cpp", json=payload)
        assert response.status_code == 200
        assert "hex" in response.json()
