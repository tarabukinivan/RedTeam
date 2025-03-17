import logging
from logging.handlers import QueueListener
import requests
import traceback
import threading
import queue

import bittensor as bt
from redteam_core.constants import constants

class BittensorLogHandler(logging.Handler):
    def __init__(self, api_key, buffer_size=100, level=logging.INFO):
        super().__init__(level)
        self.api_key = api_key
        self.buffer_size = buffer_size
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()  # Used to stop the thread gracefully

        # Use the optimized JSON formatter for network logs
        self.setFormatter(bt.logging._file_formatter)

        # Start the daemon thread for sending logs
        self.sender_thread = threading.Thread(target=self.process_logs, daemon=True)
        self.sender_thread.start()

    def emit(self, record):
        """Capture log and enqueue it for asynchronous sending."""
        if record.levelno < self.level:
            return

        log_entry = self.format(record)  # Now JSON-formatted
        self.log_queue.put(log_entry)

    def process_logs(self):
        """Daemon thread function: Collect logs and send in batches."""
        buffer = []

        while not self.stop_event.is_set() or not self.log_queue.empty():
            try:
                log_entry = self.log_queue.get(timeout=3)  # Wait for logs
                buffer.append(log_entry)

                if len(buffer) >= self.buffer_size:
                    self.flush_logs(buffer)
                    buffer.clear()

            except queue.Empty:
                # If queue is empty, periodically flush remaining logs
                if buffer:
                    self.flush_logs(buffer)
                    buffer.clear()

    def flush_logs(self, logs):
        """Send logs to the logging server."""
        if not logs:
            return

        logging_endpoint = f"{constants.STORAGE_URL}/upload-log"
        payload = {"logs": logs}
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(logging_endpoint, json=payload, headers=headers)
            response.raise_for_status()
        except requests.RequestException:
            bt.logging.error(f"[LOG HANDLER] Failed to send logs: {traceback.format_exc()}")

    def close(self):
        """Stop the daemon thread and flush remaining logs."""
        self.stop_event.set()
        self.sender_thread.join(timeout=2)  # Allow time to finish processing
        super().close()


def start_bittensor_log_listener(api_key, buffer_size=100):
    """
    Starts a separate QueueListener that listens to Bittensor's logging queue.
    """
    bt_logger = bt.logging  # The Bittensor logging machine
    log_queue = bt_logger.get_queue()  # Get the shared log queue

    # Create our custom log handler
    custom_handler = BittensorLogHandler(api_key, buffer_size)

    # Create our own listener that listens to the same queue
    custom_listener = QueueListener(log_queue, custom_handler, respect_handler_level=True)

    # Start our custom listener
    custom_listener.start()

    bt.logging.success("Custom Bittensor log listener started!")
    return custom_listener  # Return the listener so we can stop it if needed
