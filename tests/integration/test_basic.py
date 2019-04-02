import pytest
import time
import threading

WINDOWS_DATA = {
    "signal": None,
    "stacktraces": [
        {
            "registers": {"eip": "0x0000000001509530"},
            "frames": [{"instruction_addr": "0x749e8630"}],
        }
    ],
    "modules": [
        {
            "type": "symbolic",
            "debug_id": "ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1",
            "code_id": None,
            "code_file": "C:\\Windows\\System32\\kernel32.dll",
            "debug_file": "C:\\Windows\\System32\\wkernel32.pdb",
            "image_addr": "0x749d0000",
            "image_size": 851_968,
        }
    ],
}

SUCCESS_WINDOWS = {
    "signal": None,
    "stacktraces": [
        {
            "frames": [
                {
                    "status": "symbolicated",
                    "original_index": 0,
                    "instruction_addr": "0x749e8630",
                    "filename": None,
                    "lang": None,
                    "lineno": 0,
                    "abs_path": None,
                    "package": "C:\\Windows\\System32\\kernel32.dll",
                    "function": "@BaseThreadInitThunk@12",
                    "symbol": "@BaseThreadInitThunk@12",
                    "sym_addr": "0x749e8630",
                }
            ]
        }
    ],
    "modules": [dict(status="found", **WINDOWS_DATA["modules"][0])],
    "status": "completed",
}


def _make_unsuccessful_result(status):
    return {
        "signal": None,
        "stacktraces": [
            {
                "frames": [
                    {
                        "status": status,
                        "original_index": 0,
                        "instruction_addr": "0x749e8630",
                        "abs_path": None,
                        "filename": None,
                        "function": None,
                        "lang": None,
                        "lineno": None,
                        "package": None,
                        "sym_addr": None,
                        "symbol": None,
                    }
                ]
            }
        ],
        "modules": [dict(status=status, **WINDOWS_DATA["modules"][0])],
        "status": "completed",
    }


MISSING_DEBUG_FILE = _make_unsuccessful_result("missing_debug_file")
MALFORMED_DEBUG_FILE = _make_unsuccessful_result("malformed_debug_file")


@pytest.fixture(params=[True, False])
def cache_dir_param(tmpdir, request):
    if request.param:
        return tmpdir.mkdir("caches")


@pytest.mark.parametrize("is_public", [True, False])
def test_basic(symbolicator, cache_dir_param, is_public, hitcounter):
    scope = "myscope"

    input = dict(
        **WINDOWS_DATA,
        sources=[
            {
                "type": "http",
                "id": "microsoft",
                "layout": "symstore",
                "filetypes": ["pdb", "pe"],
                "url": f"{hitcounter.url}/msdl/",
                "is_public": is_public,
            }
        ],
    )

    # i = 0: Cache miss
    # i = 1: Cache hit
    # i = 2: Assert that touching the file during cache hit did not destroy the cache
    for i in range(3):
        service = symbolicator(cache_dir=cache_dir_param)
        service.wait_healthcheck()

        response = service.post(f"/symbolicate?scope={scope}", json=input)
        response.raise_for_status()

        assert response.json() == SUCCESS_WINDOWS

        if cache_dir_param:
            stored_in_scope = "global" if is_public else scope
            assert {
                o.basename: o.size()
                for o in cache_dir_param.join("objects").join(stored_in_scope).listdir()
            } == {
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__pdb": 846_848,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__pe": 0,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__breakpad": 0,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__elf-code": 0,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__elf-debug": 0,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__mach-code": 0,
                "microsoft_ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__mach-debug": 0,
            }

            symcache, = (
                cache_dir_param.join("symcaches").join(stored_in_scope).listdir()
            )
            assert (
                symcache.basename
                == "ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1__s:microsoft"
            )
            assert symcache.size() > 0

        assert hitcounter.hits == {
            "/msdl/wkernel32.pdb/FF9F9F7841DB88F0CDEDA9E1E9BFF3B51/wkernel32.pdb": 1
            if cache_dir_param
            else (i + 1)
        }


def test_no_sources(symbolicator, cache_dir_param):
    input = dict(**WINDOWS_DATA, sources=[])

    service = symbolicator(cache_dir=cache_dir_param)
    service.wait_healthcheck()

    response = service.post("/symbolicate", json=input)
    response.raise_for_status()

    assert response.json() == MISSING_DEBUG_FILE

    if cache_dir_param:
        assert not cache_dir_param.join("objects/global").exists()
        symcache, = cache_dir_param.join("symcaches/global").listdir()
        assert symcache.basename == "ff9f9f78-41db-88f0-cded-a9e1e9bff3b5-1_"
        assert symcache.size() == 0


@pytest.mark.parametrize("is_public", [True, False])
def test_lookup_deduplication(symbolicator, hitcounter, is_public):
    input = dict(
        **WINDOWS_DATA,
        sources=[
            {
                "type": "http",
                "id": "microsoft",
                "filetypes": ["pdb", "pe"],
                "layout": "symstore",
                "url": f"{hitcounter.url}/msdl/",
                "is_public": is_public,
            }
        ],
    )

    service = symbolicator(cache_dir=None)
    service.wait_healthcheck()
    responses = []

    def f():
        response = service.post("/symbolicate", json=input)
        response.raise_for_status()
        responses.append(response.json())

    ts = []
    for _ in range(20):
        t = threading.Thread(target=f)
        t.start()
        ts.append(t)

    for t in ts:
        t.join()

    assert responses == [SUCCESS_WINDOWS] * 20

    assert hitcounter.hits == {
        "/msdl/wkernel32.pdb/FF9F9F7841DB88F0CDEDA9E1E9BFF3B51/wkernel32.pdb": 1
    }


def test_sources_without_filetypes(symbolicator, hitcounter):
    input = dict(
        sources=[
            {
                "type": "http",
                "id": "microsoft",
                "filetypes": [],
                "layout": "symstore",
                "url": f"{hitcounter.url}/msdl/",
            }
        ],
        **WINDOWS_DATA,
    )

    service = symbolicator()
    service.wait_healthcheck()

    response = service.post("/symbolicate", json=input)
    response.raise_for_status()

    assert response.json() == MISSING_DEBUG_FILE
    assert not hitcounter.hits


def test_timeouts(symbolicator, hitcounter):
    hitcounter.before_request = lambda: time.sleep(3)

    request_id = None

    responses = []

    service = symbolicator()
    service.wait_healthcheck()

    for _ in range(10):
        if request_id:
            response = service.get("/requests/{}?timeout=1".format(request_id))
        else:
            input = dict(
                sources=[
                    {
                        "type": "http",
                        "id": "microsoft",
                        "filetypes": ["pdb", "pe"],
                        "layout": "symstore",
                        "url": f"{hitcounter.url}/msdl/",
                    }
                ],
                **WINDOWS_DATA,
            )
            response = service.post("/symbolicate?timeout=1", json=input)

        response.raise_for_status()
        response = response.json()
        responses.append(response)
        if response["status"] == "completed":
            break
        elif response["status"] == "pending":
            request_id = response["request_id"]
        else:
            assert False

    for response in responses[:-1]:
        assert response["status"] == "pending"
        assert response["request_id"] == request_id

    assert responses[-1] == SUCCESS_WINDOWS
    assert len(responses) > 1

    assert hitcounter.hits == {
        "/msdl/wkernel32.pdb/FF9F9F7841DB88F0CDEDA9E1E9BFF3B51/wkernel32.pdb": 1
    }


@pytest.mark.parametrize("statuscode", [400, 500, 404])
def test_unreachable_bucket(symbolicator, hitcounter, statuscode):
    input = dict(
        sources=[
            {
                "type": "http",
                "id": "broken",
                "layout": "symstore",
                "url": f"{hitcounter.url}/respond_statuscode/{statuscode}/",
            }
        ],
        **WINDOWS_DATA,
    )

    service = symbolicator()
    service.wait_healthcheck()

    response = service.post("/symbolicate", json=input)
    response.raise_for_status()
    response = response.json()
    # TODO(markus): Better error reporting
    assert response == MISSING_DEBUG_FILE


def test_malformed_objects(symbolicator, hitcounter):
    input = dict(
        sources=[
            {
                "type": "http",
                "id": "broken",
                "layout": "symstore",
                "url": f"{hitcounter.url}/garbage_data/",
            }
        ],
        **WINDOWS_DATA,
    )

    service = symbolicator()
    service.wait_healthcheck()

    response = service.post("/symbolicate", json=input)
    response.raise_for_status()
    response = response.json()
    assert response == MALFORMED_DEBUG_FILE
