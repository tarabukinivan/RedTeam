import traceback

import bittensor as bt
import numpy as np

from redteam_core.challenge_pool.controller import Controller
from redteam_core.validator.models import (
    MinerChallengeCommit,
    ComparisonLog,
    ScoringLog,
)
from redteam_core.constants import constants
from redteam_core.challenge_pool import docker_utils


class HBController(Controller):
    # Class-level cache for baseline reference comparison commits
    _baseline_reference_cache: dict[
        str, MinerChallengeCommit
    ] = {}  # {docker_hub_id: MinerChallengeCommit}

    """
    A specialized controller for the 'humanize_behaviour_v2' challenge.
    Inherits from the base Controller and modifies specific logic.
    """

    def __init__(
        self,
        challenge_name: str,
        challenge_info: dict,
        miner_commits: list[MinerChallengeCommit],
        reference_comparison_commits: list[MinerChallengeCommit],
        seed_inputs: list[dict] = [],
    ):
        """
        Initializes the HBController, extending the original Controller.
        """
        super().__init__(
            challenge_name,
            challenge_info,
            miner_commits,
            reference_comparison_commits,
            seed_inputs,
        )

        self.behavior_scaling_factor = self.challenge_info.get(
            "behavior_scaling_factor", 0.1
        )

        # Get baseline reference comparison docker hub IDs from challenge info
        baseline_reference_comparison_docker_hub_ids = self.challenge_info.get(
            "baseline_reference_comparison_docker_hub_ids", []
        )

        # Initialize local storage for this instance
        self.baseline_reference_comparison_commits_to_score: list[MinerChallengeCommit] = []

        for docker_hub_id in baseline_reference_comparison_docker_hub_ids:
            # Check if this docker_hub_id is already in the class cache
            if docker_hub_id in HBController._baseline_reference_cache:
                cached_commit = HBController._baseline_reference_cache[docker_hub_id]
                # Verify it has scoring logs (i.e., has been successfully scored)
                if cached_commit.scoring_logs:
                    bt.logging.info(
                        f"[CONTROLLER - HBController] Reference commit {docker_hub_id} has already been scored, skipping"
                    )
                    continue

            # If not in cache or not scored, add to list of commits to score
            self.baseline_reference_comparison_commits_to_score.append(docker_hub_id)

            # Create a new commit object
            new_commit = MinerChallengeCommit(
                miner_uid=-1,
                miner_hotkey="baseline",
                challenge_name=self.challenge_name,
                docker_hub_id=docker_hub_id,
            )

            # Add to our instance list and to the class cache
            self.baseline_reference_comparison_commits_to_score.append(new_commit)

    def start_challenge(self):
        """
        Starts the challenge by building and running the challenge Docker container.
        Then, for each miner image, it runs the miner in a separate container and submits the challenge.
        It collects the challenge inputs, outputs, and the scores for each miner.

        Returns:
            A tuple containing:
            - miner_scores: A dictionary mapping each miner Docker image to their scores.
            - logs: A dictionary of logs for each miner, detailing the input, output, and score.
        """
        # Setup challenge, get challenge container and network ready
        self._setup_challenge()

        # Generate new input to score miners
        num_task = self.challenge_info.get(
            "num_tasks", constants.N_CHALLENGES_PER_EPOCH
        )
        # Start with seed inputs and generate more if needed to reach num_task
        challenge_inputs = self.seed_inputs.copy()
        remaining_tasks = max(0, num_task - len(challenge_inputs))
        if remaining_tasks > 0:
            challenge_inputs.extend(
                [self._get_challenge_from_container() for _ in range(remaining_tasks)]
            )

        # Score baseline first if it exists
        if self.baseline_commit.docker_hub_id:
            try:
                self._setup_miner_container(self.baseline_commit)
                self._score_miner_with_new_inputs(
                    self.baseline_commit, challenge_inputs
                )
                docker_utils.remove_container_by_port(
                    client=self.docker_client,
                    port=constants.MINER_DOCKER_PORT,
                )
                docker_utils.clean_docker_resources(
                    client=self.docker_client,
                    remove_containers=True,
                    remove_images=True,
                )
            except Exception as e:
                bt.logging.error(f"Error scoring baseline: {e}")
                bt.logging.error(traceback.format_exc())

        # Score baseline reference comparisons (only those that need scoring)
        for reference_commit in self.baseline_reference_comparison_commits_to_score:
            try:
                bt.logging.info(
                    f"[CONTROLLER - HBController] Scoring baseline reference: {reference_commit.docker_hub_id}"
                )
                self._setup_miner_container(reference_commit)
                self._score_miner_with_new_inputs(reference_commit, challenge_inputs)

                docker_utils.remove_container_by_port(
                    client=self.docker_client,
                    port=constants.MINER_DOCKER_PORT,
                )
                docker_utils.clean_docker_resources(
                    client=self.docker_client,
                    remove_containers=True,
                    remove_images=True,
                )

                # Update the class cache with the scored commit
                HBController._baseline_reference_cache[reference_commit.docker_hub_id] = reference_commit

            except Exception as e:
                bt.logging.error(
                    f"Error scoring baseline reference comparison, docker_hub_id: {reference_commit.docker_hub_id}: {e}"
                )
                bt.logging.error(traceback.format_exc())

        # Score commits with new input and collect comparison logs
        for miner_commit in self.miner_commits:
            uid, hotkey = miner_commit.miner_uid, miner_commit.miner_hotkey

            try:
                # 1. Validate and setup miner container
                self._setup_miner_container(miner_commit)

                # 2. Score with new inputs
                self._score_miner_with_new_inputs(miner_commit, challenge_inputs)

                # 3. Run reference comparisons
                self._run_reference_comparison_inputs(miner_commit)

            except Exception as e:
                bt.logging.error(f"Error while processing miner {uid} - {hotkey}: {e}")
                bt.logging.error(traceback.format_exc())
                if uid != self.baseline_commit.miner_uid:
                    miner_commit.scoring_logs.append(
                        ScoringLog(
                            miner_input=None,
                            miner_output=None,
                            score=0,
                            error=str(e),
                        )
                    )

            # Clean up miner container
            docker_utils.remove_container_by_port(
                client=self.docker_client,
                port=constants.MINER_DOCKER_PORT,
            )
            docker_utils.clean_docker_resources(
                client=self.docker_client,
                remove_containers=True,
                remove_images=True,
            )

        # Clean up challenge container
        docker_utils.remove_container(
            client=self.docker_client,
            container_name=self.challenge_name,
            stop_timeout=60,
            force=True,
            remove_volumes=True,
        )
        docker_utils.clean_docker_resources(
            client=self.docker_client,
            remove_containers=True,
            remove_images=True,
        )

    def _run_reference_comparison_inputs(self, miner_commit: MinerChallengeCommit):
        # Skip for baseline commit since it's used as reference
        if miner_commit.miner_uid == self.baseline_commit.miner_uid:
            return

        all_reference_comparison_commits = (
            self.reference_comparison_commits
            + list(HBController._baseline_reference_cache.values())
        )

        for reference_commit in all_reference_comparison_commits:
            bt.logging.info(
                f"[CONTROLLER - HBController] Running comparison with reference commit {reference_commit.miner_uid}"
            )

            miner_mean_score = np.mean(
                [scoring_log.score for scoring_log in miner_commit.scoring_logs]
            ).item()

            # Skip if already compared, or if mean score is less than behavior scaling factor, or if miner is the same
            if (
                reference_commit.docker_hub_id in miner_commit.comparison_logs
                or miner_mean_score < self.behavior_scaling_factor
                or miner_commit.miner_hotkey == reference_commit.miner_hotkey
            ):
                continue
            else:
                miner_commit.comparison_logs[reference_commit.docker_hub_id] = []

            # Process each input from the reference commit's scoring logs
            for i, reference_log in enumerate(reference_commit.scoring_logs):
                if reference_log.miner_input is None:
                    continue

                # Submit the same input to current miner
                miner_output, error_message = self._submit_challenge_to_miner(
                    reference_log.miner_input
                )

                # Create comparison log
                comparison_log = ComparisonLog(
                    miner_input=reference_log.miner_input,
                    miner_output=miner_output,
                    reference_output=reference_log.miner_output,
                    error=error_message,
                )

                # Add to comparison logs
                miner_commit.comparison_logs[reference_commit.docker_hub_id].append(
                    comparison_log
                )
