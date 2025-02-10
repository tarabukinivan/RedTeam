import datetime
import numpy as np
import pandas as pd

from typing import List, Dict, Optional, Union
from pydantic import BaseModel
from cryptography.fernet import Fernet
from ..constants import constants


class MinerCommit(BaseModel):
    encrypted_commit: str
    timestamp: float
    docker_hub_id: Optional[str] = None
    key: Optional[str] = None

    def update(self, **kwargs) -> None:
        """
        Update the MinerCommit with new key if provided.
        """
        self.key = kwargs.get("key", self.key)

    def reveal(self) -> bool:
        """
        Decrypts the encrypted commit to reveal the docker_hub_id.
        Requires a valid encryption key to be set.
        Returns True if successful, False otherwise.
        """
        if not self.key:
            return False
        try:
            f = Fernet(self.key)
            decrypted_data = f.decrypt(self.encrypted_commit).decode()
            self.docker_hub_id = decrypted_data.split("---")[1]
            return True
        except Exception as e:
            # Consider logging the error
            return False


class ChallengeRecord(BaseModel):
    point: float = 0
    score: float = 0
    date: str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    scored_date: Optional[str] = None
    docker_hub_id: Optional[str] = None
    uid: Optional[int] = None


class ScoringLog(BaseModel):
    uid: int
    score: float
    miner_input: Optional[dict] = None
    miner_output: Optional[dict] = None
    miner_docker_image: str
    error: Optional[str] = None
    baseline_score: Optional[float] = None


class MinerManager:
    def __init__(self, challenge_name: str, challenge_incentive_weight: float):
        """
        Initializes the MinerManager to track scores and challenges.
        """
        self.challenge_name = challenge_name
        self.uids_to_commits: Dict[int, MinerCommit] = {}
        self.challenge_records: Dict[str, ChallengeRecord] = {}
        self.challenge_incentive_weight = challenge_incentive_weight

    def update_uid_to_commit(self, uids: List[int], commits: List[MinerCommit]) -> None:
        for uid, commit in zip(uids, commits):
            self.uids_to_commits[uid] = commit

    def update_scores(self, logs: List[ScoringLog]) -> None:
        """
        Updates the scores for miners based on new logs.
        Ensures daily records are maintained.
        """
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        if today in self.challenge_records:
            # No need to update if today's record already exists
            return

        prev_day = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        prev_day_record = self.challenge_records.get(prev_day)

        if prev_day_record is None:
            prev_day_record = (
                ChallengeRecord()
            )  # Default record for the previous day if not found

        logs_df = pd.DataFrame([log.model_dump() for log in logs])

        # Group by uid and mean the scores
        scores = logs_df.groupby("uid")["score"].mean().sort_values(ascending=False)

        best_uid = scores.index[0]
        best_score = scores.iloc[0]
        best_docker_hub_id = logs_df[logs_df["uid"] == best_uid]["miner_docker_image"].iloc[0]

        if best_score > prev_day_record.score:
            point = max(best_score - prev_day_record.score, 0) * 100
            today_record = ChallengeRecord(
                point=point,
                score=best_score,
                date=today,
                scored_date=today,
                docker_hub_id=best_docker_hub_id,
                uid=best_uid,
            )
            self.challenge_records[today] = today_record
        else:
            # Handle if no score improvement

            # Handle backward compatibility for scored_date
            prev_scored_date = getattr(prev_day_record, "scored_date", None)
            if prev_scored_date is None and prev_day_record.docker_hub_id:
                # Search backwards for the nearest record with same docker_hub_id and non-zero points
                current_date = datetime.datetime.strptime(prev_day, "%Y-%m-%d")
                while current_date.strftime("%Y-%m-%d") in self.challenge_records:
                    record = self.challenge_records[current_date.strftime("%Y-%m-%d")]
                    # If the docker_hub_id is the same and the point is greater than 0, then we have found the nearest scored date
                    if (record.docker_hub_id == prev_day_record.docker_hub_id and record.point > 0):
                        prev_scored_date = record.date
                        break
                    current_date -= datetime.timedelta(days=1)

            # If no matching record found, use the prev_day_record's date
            if prev_scored_date is None:
                prev_scored_date = prev_day_record.date
            today_record = ChallengeRecord(
                score=prev_day_record.score,
                date=today,
                scored_date=prev_scored_date,
                docker_hub_id=prev_day_record.docker_hub_id,
                # uid=prev_day_record.uid
            )
            # REMEMBER WE ARE HANDLING BACKWARD COMPATIBILITY USING POINT FIELD SO WAIT FOR THE NEW VERSION TO BE STABLE BEFORE ADDING THIS !!!
            # Do this if we want to explicitly save the decayed points.
            # scored_date = datetime.datetime.strptime(today_record.scored_date, "%Y-%m-%d")
            # days_passed = (today - scored_date).days
            # point = constants.decay_points(today_record.point, days_passed)
            # today_record.point = point
            self.challenge_records[today] = today_record

    def get_onchain_scores(self, n_uids: int) -> np.ndarray:
        """
        Returns a numpy array of scores using a hybrid approach:
        - 50% based on each miner's best score as a proportion of all best scores
        - 50% based on the original improvement-based scoring with decay
        """
        # Initialize arrays for both scoring components
        improvement_scores = np.zeros(n_uids)
        best_scores = np.zeros(n_uids)
        today = datetime.datetime.now(datetime.timezone.utc)

        # Track best score for each miner
        miner_best_scores = {}  # uid -> best_score mapping

        # Calculate improvement-based scores with decay (50% weight)
        total_improvement_points = 0
        for date_str, record in self.challenge_records.items():
            record_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            days_passed = (today - record_date).days
            point = constants.decay_points(record.point, days_passed)

            if record.uid is not None:
                improvement_scores[record.uid] += point
                total_improvement_points += point

                # Track best score for each miner
                current_score = record.score
                if record.uid not in miner_best_scores or current_score > miner_best_scores[record.uid]:
                    miner_best_scores[record.uid] = current_score

        # Normalize improvement scores if there are any points
        if total_improvement_points > 0:
            improvement_scores /= total_improvement_points

        # Calculate proportional scores based on best performances (50% weight)
        total_best_scores = sum(miner_best_scores.values())
        if total_best_scores > 0:
            for uid, best_score in miner_best_scores.items():
                best_scores[uid] = best_score / total_best_scores

        # Combine both scoring components with equal weights (50-50)
        final_scores = (improvement_scores * 0.5) + (best_scores * 0.5)

        return final_scores
