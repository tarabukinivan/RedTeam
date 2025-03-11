# -*- coding: utf-8 -*-

import os
import time
import json
import pathlib
from typing import List, Union, Dict, Tuple

import docker
from pydantic import validate_call
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

try:
    from modules.rt_hb_score import MetricsProcessor  # type: ignore
except ImportError:
    from rt_hb_score import MetricsProcessor  # type: ignore

from api.core.constants import ErrorCodeEnum
from api.config import config
from api.core.exceptions import BaseHTTPException
from api.helpers.crypto import asymmetric as asymmetric_helper
from api.endpoints.challenge.schemas import KeyPairPM, TaskPM, MinerInput, MinerOutput
from api.endpoints.challenge import utils
from api.logger import logger


_src_dir = pathlib.Path(__file__).parent.parent.parent.parent.resolve()


_TMP_ACTION_LIST: List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]] = (
    utils.gen_tasks_actions(
        n_task=1,
        window_width=config.challenge.window_width,
        window_height=config.challenge.window_height,
        n_checkboxes=config.challenge.n_checkboxes,
        min_distance=config.challenge.cb_min_distance,
        max_factor=config.challenge.cb_gen_max_factor,
        checkbox_size=config.challenge.cb_size,
        exclude_areas=config.challenge.cb_exclude_areas,
    )[0]
)


class TaskManager:

    _TASKS_ACTIONS: List[
        List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]]
    ] = utils.gen_tasks_actions(
        n_task=config.challenge.n_task_per_epoch,
        window_width=config.challenge.window_width,
        window_height=config.challenge.window_height,
        n_checkboxes=config.challenge.n_checkboxes,
        min_distance=config.challenge.cb_min_distance,
        max_factor=config.challenge.cb_gen_max_factor,
        checkbox_size=config.challenge.cb_size,
        exclude_areas=config.challenge.cb_exclude_areas,
        pre_tasks_actions=config.challenge.pre_tasks_actions,
    )

    @validate_call
    def __init__(self, uid: str = None):
        self.uid = uid
        self.reset_tasks()

    def reset_tasks(self) -> List[TaskPM]:
        self._actions_idx = 0
        self.tasks = []

        for _i in range(config.challenge.n_task_per_epoch):
            _key_pair: KeyPairPM = utils.gen_key_pair(
                key_size=config.api.security.asymmetric.key_size,
            )

            _actions = self._TASKS_ACTIONS[_i]
            _task = TaskPM(id=_i, key_pair=_key_pair, actions=_actions)
            self.tasks.append(_task)

        self.cur_task = None

        return self.tasks

    def pop_task(self) -> Union[TaskPM, None]:

        self.cur_task = None
        if self.tasks:
            self.cur_task = self.tasks.pop(0)

        return self.cur_task

    def get_actions(
        self,
    ) -> List[Dict[str, Union[int, str, Dict[str, Dict[str, int]]]]]:

        _actions = self._TASKS_ACTIONS[self._actions_idx]
        self._actions_idx += 1
        if config.challenge.n_task_per_epoch <= self._actions_idx:
            self._actions_idx = 0

        return _actions

    @property
    def cur_task(self) -> Union[TaskPM, None]:
        try:
            return self.__cur_task
        except AttributeError:
            self.__cur_task = None

        return self.__cur_task

    @cur_task.setter
    def cur_task(self, cur_task: Union[TaskPM, None]) -> None:
        if cur_task and (not isinstance(cur_task, TaskPM)):
            raise TypeError(
                f"`cur_task` attribute type {type(cur_task)} is invalid, it must be a <class 'TaskPM'>!"
            )

        self.__cur_task = cur_task


global tm
tm = TaskManager()


def get_task() -> MinerInput:

    _actions = tm.get_actions()
    _miner_input = MinerInput(actions=_actions)
    return _miner_input


@validate_call
def score(miner_output: MinerOutput, reset: bool) -> float:

    _container_name = "bot_container"
    utils.stop_container(container_name=_container_name)

    if reset:
        tm.reset_tasks()

    tm.pop_task()

    if not tm.cur_task:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
            message=f"Not initialized tasks or out of tasks!",
        )

    _score = 0.0

    logger.debug(f"Current task actions: {tm.cur_task.actions}")
    logger.debug("Scoring the miner output...")
    try:
        if miner_output.pip_requirements:
            utils.check_pip_requirements(
                pip_requirements=miner_output.pip_requirements,
                target_dt=config.challenge.allowed_pip_pkg_dt,
            )

        _build_dir = os.path.join(config.api.paths.tmp_dir, "bot")
        utils.copy_bot_files(
            miner_output=miner_output, src_dir=str(_src_dir / "bot"), dst_dir=_build_dir
        )

        _docker_client = docker.from_env()
        _image_name = "bot:latest"
        utils.build_bot_image(
            docker_client=_docker_client,
            build_dir=_build_dir,
            system_deps=miner_output.system_deps,
            image_name=_image_name,
        )
        utils.run_bot_container(
            docker_client=_docker_client,
            actions=tm.cur_task.actions,
            image_name=_image_name,
            container_name=_container_name,
            ulimit=config.challenge.docker_ulimit,
        )

        _i = 0
        while True:
            if tm.cur_task.score is not None:
                _score = tm.cur_task.score
                tm.cur_task.score = None
                break

            logger.debug("Waiting for the bot to finish...")
            time.sleep(1)
            _i += 1

            if config.challenge.bot_timeout < _i:
                raise BaseHTTPException(
                    error_enum=ErrorCodeEnum.BAD_REQUEST,
                    message=f"Timeout error: Bot running too long or failed to finish!",
                )

        logger.debug("Successfully scored the miner output.")
    except Exception as err:
        if isinstance(err, BaseHTTPException):
            raise

        logger.error(f"Failed to score the miner output: {str(err)}!")
        raise

    return _score


@validate_call(config={"arbitrary_types_allowed": True})
def get_web(request: Request) -> HTMLResponse:

    _nonce = None
    if tm.cur_task:
        _nonce = tm.cur_task.key_pair.nonce
    else:
        _tmp_key_pair: KeyPairPM = utils.gen_key_pair(
            key_size=config.api.security.asymmetric.key_size
        )
        _nonce = _tmp_key_pair.nonce
        logger.warning(
            "Not initialized key pair, this endpoint is shouldn't be called directly!"
        )

    _actions = []
    if tm.cur_task:
        _actions = tm.cur_task.actions
    else:
        _actions = _TMP_ACTION_LIST
        logger.warning(
            "Not initialized actions, this endpoint is shouldn't be called directly!"
        )

    _key_pair: Tuple[str, str] = asymmetric_helper.gen_key_pair(
        key_size=config.api.security.asymmetric.key_size, as_str=True
    )
    _, _public_key = _key_pair
    _templates = Jinja2Templates(directory=(_src_dir / "./templates/html"))
    _html_response = _templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "nonce": _nonce,
            "public_key": _public_key,
            "actions_list": _actions,
        },
    )
    return _html_response


@validate_call
def get_random_val(nonce: str) -> str:

    if not tm.cur_task:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.BAD_REQUEST,
            message=f"Not initialized task or out of task, this endpoint is shouldn't be called directly!",
        )

    if tm.cur_task.key_pair.nonce != nonce:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.UNAUTHORIZED,
            message=f"Invalid nonce value!",
        )

    if not tm.cur_task.key_pair.public_key:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
            message=f"Nonce is already retrieved!",
        )

    _nonce_key: str = tm.cur_task.key_pair.public_key
    tm.cur_task.key_pair.public_key = None
    tm.cur_task.key_pair.nonce = None

    return _nonce_key


@validate_call
def eval_bot(data: str) -> None:

    if not tm.cur_task:
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.BAD_REQUEST,
            message=f"Not initialized task or out of task, this endpoint is shouldn't be called directly!",
        )

    _private_key: str = tm.cur_task.key_pair.private_key
    tm.cur_task.key_pair = None

    logger.debug("Evaluating the bot...")
    try:
        _plaintext = utils.decrypt(ciphertext=data, private_key=_private_key)
        _metrics_processor = MetricsProcessor(config={"actions": tm.cur_task.actions})
        _plain_data = json.loads(_plaintext)
        _result = _metrics_processor(data=_plain_data)
        tm.cur_task.actions = None
        logger.info(f"Bot evaluation result: {_result}")
        tm.cur_task.score = _result["analysis"]["score"]
        logger.info(f"Final score: {tm.cur_task.score}")

        logger.debug("Successfully evaluated the bot.")
    except Exception as err:
        if isinstance(err, BaseHTTPException):
            raise

        logger.error(f"Failed to evaluate the bot: {str(err)}!")
        raise

    return


__all__ = [
    "get_task",
    "get_web",
    "get_random_val",
    "score",
    "eval_bot",
]
