# -*- coding: utf-8 -*-

import os
import sys
import logging
import pathlib
from typing import Union, List

from fastapi import FastAPI, Body, HTTPException

from data_types import MinerInput, MinerOutput


logger = logging.getLogger(__name__)
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S %z",
    format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
)


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve", response_model=MinerOutput)
def solve(miner_input: MinerInput = Body(...)) -> MinerOutput:

    logger.info(f"Retrieving bot.py and related files...")
    _miner_output: MinerOutput
    try:
        _src_dir = pathlib.Path(__file__).parent.resolve()
        _bot_dir = _src_dir / "bot"

        _bot_py_path = str(_bot_dir / "bot.py")
        _bot_py = "def run_bot(driver):\n    print('Hello, World!')"
        with open(_bot_py_path, "r") as _bot_py_file:
            _bot_py = _bot_py_file.read()

        _requirements_txt_path = str(_bot_dir / "requirements.txt")
        _pip_requirements: Union[List[str], None] = None
        if os.path.exists(_requirements_txt_path):
            with open(_requirements_txt_path, "r") as _requirements_txt_file:
                _pip_requirements = [_line.strip() for _line in _requirements_txt_file]

        _system_deps_path = str(_bot_dir / "system_deps.txt")
        _system_deps: Union[str, None] = None
        if os.path.exists(_system_deps_path):
            with open(_system_deps_path, "r") as _system_deps_file:
                _system_deps = _system_deps_file.read()
                if _system_deps:
                    _system_deps = None

        _miner_output = MinerOutput(
            bot_py=_bot_py, system_deps=_system_deps, pip_requirements=_pip_requirements
        )
        logger.info(f"Successfully retrieved bot.py and related files.")
    except Exception as err:
        logger.error(f"Failed to retrieve bot.py and related files: {err}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve bot.py and related files."
        )

    return _miner_output


___all___ = ["app"]
