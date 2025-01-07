from data_types import MinerInput, MinerOutput
from model import ResponseQualityHandler
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import numpy as np
import random
from scipy.stats import spearmanr

class Challenge:
    """
    A class that sets up the challenge and scores the miner's performance.
    It provides the task to be completed and evaluates the output.
    """
    def __init__(self):
        self.model = ResponseQualityHandler()
        with open("questions.txt") as f:
            self.questions = f.readlines()

        self.stop_words = set(stopwords.words('english'))
        print(self.stop_words)
        

    def prepare_task(self) -> MinerInput:
        """
        Prepares the task by returning an instance of MinerInput,
        which contains the task description.
        """
        original_prompt = random.choice(self.questions)
        modified_prompt = self._generate_modified_prompt(original_prompt)
        return MinerInput(original_prompt=original_prompt, modified_prompt=modified_prompt)

    def score_task(self, miner_input: MinerInput, miner_output: MinerOutput) -> float:
        """
        Evaluates the output generated by the miner.
        """

        payload = {
            'inputs': [
                {
                    "instruction": miner_input.original_prompt,
                    "response": miner_output.response
                },
            ]
        }
        score = self.model(payload)[0]["response_quality"]

        return score

    def _generate_modified_prompt(self, original_prompt: str) -> str:
        """
        Generates a modified version of the original prompt by masking a key term.
        """
        words = word_tokenize(original_prompt)
        stop_word_index = [i for i, word in enumerate(words) if word.lower() in self.stop_words]
        mask_index = random.choice([i for i in range(0,len(words)) if i not in stop_word_index])
        modified_prompt = " ".join(
            word if i != mask_index else "BLANK" for i, word in enumerate(words)
        )
        return modified_prompt