import os
import subprocess
import time
import argparse
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('validator_autoupdate.log'),
        logging.StreamHandler()
    ]
)

def get_process_status(process_name):
    """Check if the validator process is running using pm2"""
    try:
        result = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True)
        if result.returncode == 0:
            import json
            processes = json.loads(result.stdout)
            for proc in processes:
                if proc['name'] == process_name:
                    return proc['pm2_env']['status']
        return None
    except Exception as e:
        logging.error(f"Error checking process status: {e}")
        return None

def restart_validator(process_name):
    """Restart the validator process using pm2"""
    try:
        subprocess.run(['pm2', 'restart', process_name], check=True)
        logging.info(f"Successfully restarted {process_name}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to restart {process_name}: {e}")

def should_update_local(local_commit, remote_commit):
    """Check if local repository needs updating"""
    return local_commit != remote_commit

def run_auto_updater(process_name, check_interval=300):
    """Main auto-updater function"""
    logging.info("Starting validator auto-updater...")

    while True:
        try:
            logging.info("Checking for updates...")

            # Get current branch and commit information
            current_branch = subprocess.getoutput("git rev-parse --abbrev-ref HEAD")
            local_commit = subprocess.getoutput("git rev-parse HEAD")

            # Fetch latest changes
            subprocess.run(["git", "fetch"], check=True)
            remote_commit = subprocess.getoutput(f"git rev-parse origin/{current_branch}")

            if should_update_local(local_commit, remote_commit):
                logging.info("Updates available. Starting update process...")

                # Stash any local changes
                subprocess.run(["git", "stash"], check=True)

                # Reset to latest commit
                subprocess.run(["git", "reset", "--hard", remote_commit], check=True)

                # Reinstall package
                subprocess.run(["pip", "install", "-e", "."], check=True)

                # Restart validator process
                restart_validator(process_name)

                # Check if process is running correctly
                time.sleep(15)  # Wait for process to stabilize
                status = get_process_status(process_name)
                if status == "errored":
                    logging.warning(f"{process_name} is in error state, attempting another restart...")
                    restart_validator(process_name)

                logging.info("Update completed successfully!")
            else:
                logging.info("Repository is up-to-date")

        except Exception as e:
            logging.error(f"Error during update process: {e}")

        time.sleep(check_interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-updater for RedTeam Subnet validator")
    parser.add_argument("--process-name", default="validator_snxxx",
                      help="PM2 process name of the validator")
    parser.add_argument("--interval", type=int, default=3600,
                      help="Check interval in seconds (default: 3600)")

    args = parser.parse_args()

    run_auto_updater(args.process_name, args.interval)