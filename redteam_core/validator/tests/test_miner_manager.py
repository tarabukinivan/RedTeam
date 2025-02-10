import pytest

import datetime
from redteam_core.validator.miner_manager import MinerManager, ScoringLog, ChallengeRecord

@pytest.fixture
def miner_manager():
    return MinerManager(challenge_name="webui_auto", challenge_incentive_weight=0.4)

def test_initialization(miner_manager):
    assert miner_manager.challenge_name == "webui_auto"
    assert miner_manager.challenge_incentive_weight == 0.4
    assert miner_manager.uids_to_commits == {}
    assert miner_manager.challenge_records == {}

def test_update_scores(miner_manager):
    logs = [
        ScoringLog(uid=1, score=10.0, miner_input={}, miner_output=None, miner_docker_image="image1"),
        ScoringLog(uid=2, score=20.0, miner_input={}, miner_output=None, miner_docker_image="image2"),
    ]
    miner_manager.update_scores(logs)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert today in miner_manager.challenge_records
    assert miner_manager.challenge_records[today].score == 20.0
    assert miner_manager.challenge_records[today].uid == 2

def test_get_onchain_scores(miner_manager):
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # Set up test data
    # UID 1: High best score but no recent improvements
    # UID 2: Lower best score but recent improvements
    # UID 3: Multiple entries to test best score selection
    # UID 0: No entries (control)

    miner_manager.challenge_records[today] = ChallengeRecord(point=50, score=80, date=today, uid=2)
    miner_manager.challenge_records[yesterday] = ChallengeRecord(point=100, score=100, date=yesterday, uid=1)
    miner_manager.challenge_records[(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%d")] = ChallengeRecord(point=30, score=70, date=yesterday, uid=3)
    miner_manager.challenge_records[(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)).strftime("%Y-%m-%d")] = ChallengeRecord(point=20, score=60, date=yesterday, uid=3)

    n_uids = 4
    scores = miner_manager.get_onchain_scores(n_uids)

    # Test proportional scoring component (50%)
    assert scores[1] > 0, "UID 1 should get points for highest best score (100)"
    assert scores[2] > 0, "UID 2 should get points for good best score (80)"

    # Test improvement scoring component (50%)
    assert scores[2] > 0, "UID 2 should get points for recent improvement"

    # Test overall hybrid scoring
    assert scores[0] == 0, "UID 0 should have zero score (no entries)"
    assert scores[1] + scores[2] > 0, "Both UIDs 1 and 2 should have non-zero total scores"

    # Verify scores are normalized
    assert abs(sum(scores) - 1.0) < 1e-6, "Scores should sum to approximately 1"