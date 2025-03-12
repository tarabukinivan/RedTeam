import bittensor as bt
import requests

from redteam_core.constants import constants
from redteam_core.challenge_pool.comparer import Comparer
from redteam_core.validator.models import MinerChallengeCommit, ComparisonLog, ScoringLog

COMPARE_SCORE_THRESHOLD = 0.0


class HBComparer(Comparer):
    def __init__(
        self,
        challenge_name: str,
        challenge_info: dict,
        miner_commits: list[MinerChallengeCommit],
        compare_with_each_other: bool,
    ):
        super().__init__(
            challenge_name=challenge_name,
            challenge_info=challenge_info,
            miner_commits=miner_commits,
            compare_with_each_other=compare_with_each_other,
        )

    def _compare_within_batch(self, miner_commit: MinerChallengeCommit):
        """
        Compare commits within the same batch if compare_with_each_other is True.
        Only compares with commits that have smaller UIDs.
        Prioritizes comparing outputs that were generated from the same inputs.
        """
        # Skip if no scoring logs
        if not miner_commit.scoring_logs:
            return

        # Group scoring logs by input hash for efficient matching
        miner_logs_by_hash: dict[str, ScoringLog] = {}
        for log in miner_commit.scoring_logs:
            if log.input_hash and log.miner_output:
                miner_logs_by_hash[log.input_hash] = log

        for other_commit in self.miner_commits:
            ### Check if we should compare with this commit
            if (other_commit.commit_timestamp > miner_commit.commit_timestamp or # Newer commit
                other_commit.docker_hub_id in miner_commit.comparison_logs or  # Already compared
                not other_commit.scoring_logs or  # No scoring logs
                miner_commit.miner_hotkey == other_commit.miner_hotkey):  # Same miner
                continue


            # Find matching inputs between the two commits
            comparison_logs = []

            # Check each scoring log in the other commit to find matching inputs
            for other_log in other_commit.scoring_logs:
                if not other_log.input_hash or not other_log.miner_output:
                    continue

                # If we have a matching input hash, we can compare outputs
                if other_log.input_hash in miner_logs_by_hash:
                    miner_log = miner_logs_by_hash[other_log.input_hash]

                    try:
                        # Use the comparison API to compare outputs
                        similarity_score = self._compare_outputs(
                            miner_input=miner_log.miner_input,  # Both used the same input
                            miner_output=miner_log.miner_output,
                            reference_output=other_log.miner_output
                        )

                        # Create a comparison log with the inputs and outputs
                        comparison_log = ComparisonLog(
                            similarity_score=similarity_score,
                            miner_input=miner_log.miner_input,
                            miner_output=miner_log.miner_output,
                            reference_output=other_log.miner_output,
                            reference_hotkey=other_commit.miner_hotkey,
                        )
                        comparison_logs.append(comparison_log)

                    except Exception as e:
                        bt.logging.error(
                            f"Error comparing outputs with matching inputs for miner {miner_commit.miner_hotkey}: {str(e)}"
                        )
                        comparison_log = ComparisonLog(
                            error=str(e),
                            similarity_score=0.0,
                            miner_input=miner_log.miner_input,
                            miner_output=miner_log.miner_output,
                            reference_output=other_log.miner_output,
                            reference_hotkey=other_commit.miner_hotkey,
                        )
                        comparison_logs.append(comparison_log)

            # If we found any matching inputs, add the comparison logs
            if comparison_logs:
                miner_commit.comparison_logs[other_commit.docker_hub_id] = comparison_logs
                bt.logging.info(
                    f"[COMPARER] Added {len(comparison_logs)} comparison logs for commit {miner_commit.encrypted_commit} against docker_hub_id {other_commit.docker_hub_id}"
                )
            else:
                bt.logging.warning(
                    f"[COMPARER] No matching inputs found for comparison between commit {miner_commit.encrypted_commit} and docker_hub_id {other_commit.docker_hub_id}"
                )


    def _compare_outputs(
        self, miner_input: dict, miner_output: dict, reference_output: dict
    ) -> float:
        """
        Send comparison request to challenge container's /compare endpoint.

        Args:
            miner_input: The input used for both outputs
            miner_output: The output from the current miner
            reference_output: The output from the reference miner

        Returns:
            float: Comparison score between 0 and 1
        """
        _protocol, _ssl_verify = self._check_protocol(is_challenger=True)

        try:
            payload = {
                "miner_input": miner_input,
                "miner_output": miner_output,
                "reference_output": reference_output,
            }

            response = requests.post(
                f"{_protocol}://localhost:{constants.CHALLENGE_DOCKER_PORT}/compare",
                timeout=self.challenge_info.get("challenge_compare_timeout", 60),
                verify=_ssl_verify,
                json=payload,
            )

            response_data = response.json()
            data = response_data.get("data", {})
            similarity_score = data.get("similarity_score", 0.0)

            # Normalize score to float between 0 and 1
            if isinstance(similarity_score, int):
                similarity_score = float(similarity_score)
            elif not isinstance(similarity_score, float):
                similarity_score = 0.0

            return max(0.0, min(1.0, similarity_score))

        except Exception as e:
            bt.logging.error(f"Error in comparison request: {str(e)}")
            return 0.0