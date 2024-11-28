import json
import time
import datetime
import threading

import numpy as np
import bittensor as bt
from cryptography.fernet import Fernet

from redteam_core import (
    Commit,
    BaseValidator,
    challenge_pool,
    constants,
    MinerManager,
    StorageManager,
    ScoringLog,
)


class Validator(BaseValidator):
    def __init__(self):
        """
        Initializes the Validator by setting up MinerManager instances for all active challenges.
        """
        super().__init__()
        self.active_challenges = challenge_pool.ACTIVE_CHALLENGES
        self.miner_managers = {
            challenge: MinerManager(challenge_name=challenge, challenge_incentive_weight=self.active_challenges[challenge]["challenge_incentive_weight"])
            for challenge in self.active_challenges.keys()
        }

        # Setup storage manager and publish public hf_repo_id for storage
        self.storage_manager = StorageManager(
            cache_dir=self.config.validator.cache_dir,
            hf_repo_id=self.config.validator.hf_repo_id,
            sync_on_init=True
        )
        self.commit_repo_id_to_chain(
            hf_repo_id=self.config.validator.hf_repo_id,
            max_retries=5   
        )
        # Start a thread to periodically commit the repo_id
        commit_thread = threading.Thread(
            target=self._commit_repo_id_to_chain_periodically,
            kwargs={"hf_repo_id": self.config.validator.hf_repo_id, "interval": 600},
            daemon=True
        )
        commit_thread.start()

        self.miner_submit = {}
        self.scoring_dates: list[str] = []

    def forward(self):
        """
        Executes the forward pass of the Validator:
        1. Updates miner commit information.
        2. Reveals commits.
        3. Runs challenges and updates scores.
        4. Store the commits.
        """
        self.update_miner_commit()
        bt.logging.success(f"[FORWARD] Miner submit: {self.miner_submit}")

        revealed_commits = self.get_revealed_commits()

        today = datetime.datetime.now()
        current_hour = today.hour
        today_key = today.strftime("%Y-%m-%d")
        validate_scoring_hour = current_hour >= constants.SCORING_HOUR
        validate_scoring_date = today_key not in self.scoring_dates
        if validate_scoring_hour and validate_scoring_date and revealed_commits:
            bt.logging.info(f"[FORWARD] Running scoring for {today_key}")
            for challenge, (commits, uids) in revealed_commits.items():
                bt.logging.info(f"[FORWARD] Running challenge: {challenge}")
                controller = self.active_challenges[challenge]["controller"](
                    challenge_name=challenge, miner_docker_images=commits, uids=uids, challenge_info=self.active_challenges[challenge]
                )
                logs = controller.start_challenge()
                logs = [ScoringLog(**log) for log in logs]
                self.miner_managers[challenge].update_scores(logs)
                bt.logging.info(f"[FORWARD] Scoring for challenge: {challenge} has been completed for {today_key}")
            bt.logging.info(f"[FORWARD] All tasks: Scoring completed for {today_key}")
            self.scoring_dates.append(today_key)
        else:
            bt.logging.warning(f"[FORWARD] Skipping scoring for {today_key}")
            bt.logging.info(
                f"[FORWARD] Current hour: {current_hour}, Scoring hour: {constants.SCORING_HOUR}"
            )
            bt.logging.info(f"[FORWARD] Scoring dates: {self.scoring_dates}")
            bt.logging.info(
                f"[FORWARD] Revealed commits: {str(revealed_commits)[:100]}..."
            )

        self.store_miner_output()

    def update_miner_commit(self):
        """
        Queries the axons for miner commit updates and decrypts them if the reveal interval has passed.
        """
        uids = [0]  # Change this to query multiple uids as needed
        # uids = self.metagraph.uids
        axons = [self.metagraph.axons[i] for i in uids]
        dendrite = bt.dendrite(wallet=self.wallet)
        synapse = Commit()

        responses: list[Commit] = dendrite.query(
            axons, synapse, timeout=constants.QUERY_TIMEOUT
        )

        for uid, response in zip(uids, responses):
            this_miner_submit = self.miner_submit.setdefault(uid, {})
            encrypted_commit_dockers = response.encrypted_commit_dockers
            keys = response.public_keys

            for challenge_name, encrypted_commit in encrypted_commit_dockers.items():
                # Update miner commit data if it's new
                if encrypted_commit != this_miner_submit.get(challenge_name, {}).get(
                    "encrypted_commit"
                ):
                    this_miner_submit[challenge_name] = {
                        "commit_timestamp": time.time(),
                        "encrypted_commit": encrypted_commit,
                        "key": keys.get(challenge_name),
                        "commit": "",
                    }

                # Reveal commit if the interval has passed
                commit_timestamp = this_miner_submit[challenge_name]["commit_timestamp"]
                encrypted_commit = this_miner_submit[challenge_name]["encrypted_commit"]
                key = this_miner_submit[challenge_name]["key"]
                if constants.is_commit_on_time(commit_timestamp):
                    try:
                        f = Fernet(key)
                        commit = f.decrypt(encrypted_commit).decode()
                        this_miner_submit[challenge_name]["commit"] = commit
                    except Exception as e:
                        bt.logging.error(f"Failed to decrypt commit: {e}")

    def get_revealed_commits(self) -> dict:
        """
        Collects all revealed commits from miners.

        Returns:
            A dictionary where the key is the challenge name and the value is a tuple:
            (list of docker_hub_ids, list of uids).
        """
        revealed_commits = {}
        for uid, commits in self.miner_submit.items():
            for challenge_name, commit in commits.items():
                bt.logging.info(f"- {uid} - {challenge_name} - {commit}")
                if commit["commit"]:
                    this_challenge_revealed_commits = revealed_commits.setdefault(
                        challenge_name, ([], [])
                    )
                    docker_hub_id = commit["commit"].split("---")[1]
                    this_challenge_revealed_commits[0].append(docker_hub_id)
                    this_challenge_revealed_commits[1].append(uid)
        return revealed_commits
    
    def store_miner_output(self):
        """
        Store miner commit to storage.
        """
        data_to_store: list[dict] = []

        for uid, commits in self.miner_submit.items():
            for challenge_name, commit in commits.items():
                miner_uid, validator_uid = uid, self.uid
                miner_ss58_address, validator_ss58_address = self.metagraph.hotkeys[miner_uid], self.metagraph.hotkeys[validator_uid]
                # Construct data
                data = {
                    "miner_uid": miner_uid,
                    "miner_ss58_address": miner_ss58_address,
                    "validator_uid": validator_uid,
                    "validator_ss58_address": validator_ss58_address,
                    "challenge_name": challenge_name,
                    "commit_timestamp": commit["commit_timestamp"],
                    "encrypted_commit": commit["encrypted_commit"], 
                    # encrypted_commit implicitly converted to string by FastAPI due to lack of annotation so no decode here
                    "key": commit["key"],
                    "commit": commit["commit"]
                }
                # Sign the submission
                self._sign_with_private_key(data=data)

                data_to_store.append(data)
        try:
            self.storage_manager.update_batch(records=data_to_store, async_update=True)
        except Exception as e:
            bt.logging.error(f"Failed to queue miner commit data for storage: {e}")

    def set_weights(self) -> None:
        """
        Sets the weights of the miners on-chain based on their accumulated scores.
        Accumulates scores from all challenges.
        """
        n_uids = len(self.metagraph.axons)
        uids = list(range(n_uids))
        weights = np.zeros(len(uids))

        # Accumulate scores from all challenges
        for challenge, miner_manager in self.miner_managers.items():
            scores = miner_manager.get_onchain_scores(n_uids)
            bt.logging.debug(f"[SET WEIGHTS] {challenge} scores: {scores}")
            weights += scores * miner_manager.challenge_incentive_weight

        # Set weights on-chain
        result, log = self.subtensor.set_weights(
            wallet=self.wallet,
            netuid=self.config.netuid,
            uids=uids,
            weights=weights,
            version_key=constants.SPEC_VERSION,
        )

        if result:
            bt.logging.success(f"[SET WEIGHTS]: {log}")
        else:
            bt.logging.error(f"[SET WEIGHTS]: {log}")

    def _sign_with_private_key(self, data: dict):
        """
        Signs JSON-serializable data with the validator's private key, adding "nonce" and "signature" fields.

        Args:
            data (dict): JSON-serializable input.

        Raises:
            ValueError: If data is not serializable.
        """
        keypair = self.wallet.hotkey

        # Ensure data is serializable
        try:
            serialized_data = json.dumps(data, sort_keys=True, separators=(',', ':'))
        except TypeError as e:
            raise ValueError(f"Data must be JSON serializable: {e}")
    
        nonce = str(time.time_ns())
        # Calculate validator 's signature
        message = f"{serialized_data}{keypair.ss58_address}{nonce}"
        signature = f"0x{keypair.sign(message).hex()}"

        # Add nonce and signature to the data
        data["nonce"] = nonce
        data["signature"] = signature

    def commit_repo_id_to_chain(self, hf_repo_id: str, max_retries: int = 5) -> None:
        """
        Commits the repository ID to the blockchain, ensuring the process succeeds with retries.

        Args:
            repo_id (str): The repository ID to commit.
            max_retries (int): Maximum number of retries in case of failure. Defaults to 5.

        Raises:
            RuntimeError: If the commitment fails after all retries.
        """
        message = f"{self.wallet.hotkey.ss58_address}---{hf_repo_id}"

        for attempt in range(1, max_retries + 1):
            try:
                bt.logging.info(f"Attempting to commit repo ID '{hf_repo_id}' to the blockchain (Attempt {attempt})...")
                self.subtensor.commit(
                    wallet=self.wallet,
                    netuid=self.config.netuid,
                    data=message,
                )
                bt.logging.success(f"Successfully committed repo ID '{hf_repo_id}' to the blockchain.")
                return
            except Exception as e:
                bt.logging.error(f"Error committing repo ID '{hf_repo_id}' on attempt {attempt}: {e}")
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Failed to commit repo ID '{hf_repo_id}' to the blockchain after {max_retries} attempts."
                    )
                
    def _commit_repo_id_to_chain_periodically(self, hf_repo_id: str, interval: int) -> None:
        """
        Periodically commits the repository ID to the blockchain.

        Args:
            interval (int): Time interval in seconds between consecutive commits.
        """
        while True:
            try:
                self.commit_repo_id_to_chain(hf_repo_id=hf_repo_id)
                bt.logging.info("Periodic commit HF repo id to chain completed successfully.")
            except Exception as e:
                bt.logging.error(f"Error in periodic commit for repo ID '{self.config.validator.hf_repo_id}': {e}")
            time.sleep(interval)

if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info("Validator is running...")
            time.sleep(constants.EPOCH_LENGTH // 4)
