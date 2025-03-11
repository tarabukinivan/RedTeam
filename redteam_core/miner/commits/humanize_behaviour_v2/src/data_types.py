# -*- coding: utf-8 -*-

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field, constr


class MinerFilePM(BaseModel):
    fname: constr(strip_whitespace=True) = Field(  # type: ignore
        ...,
        min_length=4,
        max_length=64,
        title="File Name",
        description="Name of the file.",
        examples=["config.py"],
    )
    content: constr(strip_whitespace=True) = Field(  # type: ignore
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
        examples=["threshold = 0.5"],
    )


class MinerInput(BaseModel):
    actions: List[Dict[str, Any]] = Field(
        ...,
        title="Actions",
        min_length=1,
        description="List of actions to be performed.",
        examples=[
            [
                {
                    "id": 1,
                    "type": "click",
                    "args": {"location": {"x": 100, "y": 200}},
                }
            ]
        ],
    )


class MinerOutput(BaseModel):
    bot_py: str = Field(
        ...,
        title="bot.py",
        min_length=2,
        description="The main bot.py source code for the challenge.",
        examples=["def run_bot(driver):\n    print('Hello, World!')"],
    )
    system_deps: Optional[constr(strip_whitespace=True, min_length=2, max_length=2048)] = Field(  # type: ignore
        default=None,
        title="System Dependencies",
        description="System dependencies (Debian/Ubuntu) that needs to be installed as space-separated string.",
        examples=[None, "python3 python3-pip"],
    )
    pip_requirements: Optional[
        List[constr(min_length=2, max_length=128)]  # type: ignore
    ] = Field(
        default=None,
        title="Pip Requirements",
        description="Dependencies required for the bot.py as a list of strings.",
        examples=[
            ["pydantic[email,timezone]>=2.0.0,<3.0.0", "selenium>=4.16.0,<5.0.0"]
        ],
    )
    # extra_files: Optional[List[MinerFilePM]] = Field(
    #     default=None,
    #     title="Extra Files",
    #     description="List of extra files to support the bot.py.",
    #     examples=[
    #         [
    #             {
    #                 "fname": "config.py",
    #                 "content": "threshold = 0.5",
    #             }
    #         ]
    #     ],
    # )


__all__ = [
    "MinerInput",
    "MinerOutput",
]
