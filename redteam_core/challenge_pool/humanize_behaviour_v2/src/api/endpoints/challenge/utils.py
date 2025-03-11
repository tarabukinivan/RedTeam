# -*- coding: utf-8 -*-

import os
import re
import time
import shutil
import random
import requests
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Union, Tuple, Optional

import vault_unlock
import docker
from docker.models.networks import Network
from docker import DockerClient
from pydantic import validate_call

from api.core.constants import ErrorCodeEnum, ENV_PREFIX
from api.core import utils
from api.core.exceptions import BaseHTTPException
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM, MinerOutput
from api.logger import logger


@validate_call
def gen_key_pair(key_size: int) -> KeyPairPM:

    _key_pair: Tuple[str, str] = asymmetric_helper.gen_key_pair(
        key_size=key_size, as_str=True
    )
    _private_key, _public_key = _key_pair
    _nonce = utils.gen_random_string(length=32)
    _key_pair_pm = KeyPairPM(
        private_key=_private_key, public_key=_public_key, nonce=_nonce
    )
    return _key_pair_pm


@validate_call
def gen_tasks_actions(
    n_task: int = 10,
    window_width: int = 1420,
    window_height: int = 740,
    n_checkboxes: int = 5,
    min_distance: int = 300,
    max_factor: int = 10,
    checkbox_size: int = 20,  # Assuming checkbox size ~20px
    exclude_areas: Union[List[Dict[str, int]], None] = None,
    pre_tasks_actions: Union[
        List[List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]]], None
    ] = None,
) -> List[List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]]]:

    _max_attempt = n_checkboxes * max_factor  # Avoid infinite loops

    _tasks_actions = []
    for _ in range(n_task):
        _i = 0
        _n_attempt = 0

        _actions = []
        if pre_tasks_actions:
            _actions = pre_tasks_actions.pop(0)

        while len(_actions) < n_checkboxes:
            _x = random.randint(checkbox_size, window_width - checkbox_size)
            _y = random.randint(checkbox_size, window_height - checkbox_size)

            _is_near = False
            _i = len(_actions)
            for _action in _actions:
                if _action["type"] == "click":
                    ## Calculate distance between two points using Euclidean distance:
                    if (_x - _action["args"]["location"]["x"]) ** 2 + (
                        _y - _action["args"]["location"]["y"]
                    ) ** 2 < min_distance**2:
                        _is_near = True
                        break

            _is_in_area = False
            if exclude_areas:
                for _area in exclude_areas:
                    if (_area["x1"] <= _x <= _area["x2"]) and (
                        _area["y1"] <= _y <= _area["y2"]
                    ):
                        _is_in_area = True
                        break

            if (not _is_near) and (not _is_in_area):
                _action = {
                    "id": _i,
                    "type": "click",
                    "args": {"location": {"x": _x, "y": _y}},
                }
                _actions.append(_action)

                _n_attempt += 1

            if _max_attempt <= _n_attempt:
                logger.warning("Skipped generating positions due to max attempts!")
                break

        _tasks_actions.append(_actions)

    return _tasks_actions


@validate_call
def check_pip_requirements(pip_requirements: List[str], target_dt: datetime) -> None:

    for _package_name in pip_requirements:
        _package_name = re.split(r"[<>=\[!]", _package_name)[0].strip()

        _url = f"https://pypi.org/pypi/{_package_name}/json"
        _response = requests.get(_url)

        if _response.status_code != 200:
            logger.warning(f"Package '{_package_name}' not found on PyPi or API error!")
            raise BaseHTTPException(
                error_enum=ErrorCodeEnum.BAD_REQUEST,
                message=f"Package '{_package_name}' not found on PyPi or API error!",
            )

        _data = _response.json()

        _releases = _data.get("releases", {})
        _upload_dts = []
        for _, _files in _releases.items():
            for _file in _files:
                _upload_dt_str = _file.get("upload_time_iso_8601", "")
                if _upload_dt_str:
                    _upload_dt = datetime.fromisoformat(_upload_dt_str.rstrip("Z"))
                    if not _upload_dt.tzinfo:
                        _upload_dt = _upload_dt.replace(tzinfo=timezone.utc)
                    _upload_dts.append(_upload_dt)

        if _upload_dts:
            _package_created_dt = min(_upload_dts)
            if target_dt < _package_created_dt:
                logger.warning(
                    f"New package found created after '{target_dt}': '{_package_name}'!"
                )
                raise BaseHTTPException(
                    error_enum=ErrorCodeEnum.BAD_REQUEST,
                    message=f"We do not allow new packages like these: '{_package_name}'!",
                )

    return


@validate_call
def _copy_all_files(src_dir: str, dst_dir: str) -> None:
    try:
        utils.create_dir(dst_dir)
        for _file_name in os.listdir(src_dir):
            _src_path = os.path.join(src_dir, _file_name)
            _dst_path = os.path.join(dst_dir, _file_name)
            if os.path.isdir(_src_path):
                _copy_all_files(_src_path, _dst_path)
            else:
                shutil.copy2(_src_path, _dst_path)
    except Exception as err:
        logger.error(f"Failed to copy all files: {err}!")
        raise
    return


@validate_call
def copy_bot_files(miner_output: MinerOutput, src_dir: str, dst_dir: str) -> None:

    logger.info("Copying bot files...")
    try:
        _copy_all_files(src_dir=src_dir, dst_dir=dst_dir)
        _bot_core_dir = os.path.join(dst_dir, "src", "core")

        # if miner_output.extra_files:
        #     for _extra_file_pm in miner_output.extra_files:
        #         _extra_file_path = os.path.join(_bot_core_dir, _extra_file_pm.fname)
        #         with open(_extra_file_path, "w") as _extra_file:
        #             _extra_file.write(_extra_file_pm.content)

        if miner_output.pip_requirements:
            _requirements_txt_path = os.path.join(dst_dir, "requirements.txt")
            with open(_requirements_txt_path, "w") as _requirements_txt_file:
                for _package_name in miner_output.pip_requirements:
                    _requirements_txt_file.write(f"{_package_name}\n")

        _bot_path = os.path.join(_bot_core_dir, "bot.py")
        with open(_bot_path, "w") as _bot_file:
            _bot_file.write(miner_output.bot_py)

        logger.success("Successfully copied bot files.")
    except Exception as err:
        logger.error(f"Failed to copy bot files: {err}!")
        raise

    return


@validate_call
def stop_container(container_name: str = "bot_container") -> None:

    logger.info(f"Stopping container '{container_name}' ...")
    try:
        subprocess.run(["sudo", "docker", "rm", "-f", container_name])
        logger.success(f"Successfully stopped container '{container_name}'.")
    except Exception:
        logger.debug(f"Failed to stop container '{container_name}'!")
        pass

    return


@validate_call(config={"arbitrary_types_allowed": True})
def build_bot_image(
    docker_client: DockerClient,
    build_dir: str,
    system_deps: Optional[str] = None,
    image_name: str = "bot:latest",
) -> None:

    logger.info("Building bot docker image...")
    try:
        _kwargs = {}
        if system_deps:
            _kwargs["buildargs"] = {"APT_PACKAGES": system_deps}

        _, _logs = docker_client.images.build(
            path=build_dir, tag=image_name, rm=True, **_kwargs
        )

        for _log in _logs:
            if "stream" in _log:
                _log_stream = _log["stream"].strip()
                logger.info(_log_stream)

        logger.success("Successfully built bot docker image.")
    except Exception as err:
        logger.error(f"Failed to build bot docker: {str(err)}!")
        raise

    return


@validate_call(config={"arbitrary_types_allowed": True})
def run_bot_container(
    docker_client: DockerClient,
    actions: List[Dict],
    image_name: str = "bot:latest",
    container_name: str = "bot_container",
    network_name: str = "local_network",
    ulimit: int = 32768,
    **kwargs,
) -> None:

    logger.info("Running bot docker container...")
    try:
        _networks = docker_client.networks.list(names=[network_name])
        _network: Union[Network, None] = None
        if not _networks:
            _network: Network = docker_client.networks.create(
                name=network_name, driver="bridge"
            )
        else:
            _network: Network = docker_client.networks.get(network_name)

        _network_info = docker_client.api.inspect_network(_network.id)
        _subnet = _network_info["IPAM"]["Config"][0]["Subnet"]

        # fmt: off
        subprocess.run(["sudo", "iptables", "-I", "FORWARD", "-s", _subnet, "!", "-d", _subnet, "-j", "DROP"])
        subprocess.run(["sudo", "iptables", "-t", "nat", "-I", "POSTROUTING", "-s", _subnet, "-j", "RETURN"])
        # fmt: on

        stop_container(container_name=container_name)
        # try:
        #     _containers = docker_client.containers.list(all=True)
        #     if _containers:
        #         for _container in _containers:
        #             _container.stop()
        #             _container.remove(force=True)
        # except Exception:
        #     pass

        time.sleep(1)

        _ulimit_nofile = docker.types.Ulimit(name="nofile", soft=ulimit, hard=ulimit)
        _container = docker_client.containers.run(
            image=image_name,
            name=container_name,
            ulimits=[_ulimit_nofile],
            environment={
                "TZ": "UTC",
                f"{ENV_PREFIX}ACTION_LIST": actions,
            },
            network=network_name,
            detach=True,
            **kwargs,
        )

        for _log in _container.logs(stream=True):
            logger.info(_log.decode().strip())

        logger.info(
            f"Container '{container_name}' exited with code - {_container.wait()}."
        )

        _container.remove(force=True)

        time.sleep(1)

        logger.info("Successfully ran bot docker container.")
    except Exception as err:
        logger.error(f"Failed to run bot docker: {str(err)}!")
        raise

    return


@validate_call
def decrypt(ciphertext: str, private_key: str) -> str:

    _plaintext: str = vault_unlock.decrypt_payload(
        encrypted_text=ciphertext, private_key_pem=private_key
    )
    return _plaintext


__all__ = [
    "gen_key_pair",
    "gen_tasks_actions",
    "check_pip_requirements",
    "copy_bot_files",
    "build_bot_image",
    "run_bot_container",
    "decrypt",
]
