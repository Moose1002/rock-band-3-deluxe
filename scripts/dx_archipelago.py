#!/usr/bin/env python3
import sys
import subprocess
import json
import time
import os
import configparser
import logging
from pathlib import Path
import requests
from datetime import datetime
import re
import argparse
import platform

def get_rpcs3_path():
    os_system = platform.system()
    default_data_path = None

    if os_system == 'Darwin':  # macOS
        default_data_path = Path.home() / "Library/Application Support/rpcs3"
    elif os_system == 'Windows':
        default_data_path = None
    elif os_system == 'Linux':
        default_data_paths = [
            Path.home() / ".config/rpcs3",
            Path.home() / ".rpcs3",
            Path("/usr/share/rpcs3"),
            Path("/usr/local/share/rpcs3")
        ]
        for path in default_data_paths:
            if path.exists():
                default_data_path = path
                break
    else:
        default_data_path = None

    while True:
        if default_data_path and default_data_path.exists():
            print(f"Default RPCS3 data directory detected: {default_data_path}")
            print(f"RPCS3 Directory must contain 'dev_hdd0' folder.\nWhere Rock Band 3 Deluxe is installed in /dev_hdd0/game/BLUS30463/.")
            rpcs3_path_str = input(f"Enter the path for RPCS3 data directory (leave empty to use default): ").strip()
            if not rpcs3_path_str:
                rpcs3_path = default_data_path
            else:
                rpcs3_path = Path(rpcs3_path_str)
        else:
            print(f"RPCS3 Directory must contain 'dev_hdd0' folder.\nWhere Rock Band 3 Deluxe is installed in /dev_hdd0/game/BLUS30463/.")
            rpcs3_path_str = input("Enter the path for RPCS3 base directory (e.g. C:\\games\\rpcs3): ").strip()
            rpcs3_path = Path(rpcs3_path_str)

        if rpcs3_path.exists() and rpcs3_path.is_dir():
            return rpcs3_path
        else:
            print("Invalid RPCS3 data directory path provided.")

def save_config(config_path: Path, rpcs3_path, xbox_console_ip, never_setup_rpcs3=False, never_setup_xbox=False):
    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)

    # Save Paths
    if 'Paths' not in config:
        config['Paths'] = {}
    config['Paths']['rpcs3_path'] = str(rpcs3_path) if rpcs3_path else ''
    config['Paths']['xbox_console_ip'] = xbox_console_ip

    # Save Settings for "Never" flags
    if 'Settings' not in config:
        config['Settings'] = {}
    config['Settings']['never_setup_rpcs3'] = str(never_setup_rpcs3)
    config['Settings']['never_setup_xbox'] = str(never_setup_xbox)

    with config_path.open('w') as configfile:
        config.write(configfile)

def load_config(config_path: Path):
    config = configparser.ConfigParser()
    if config_path.is_file():
        config.read(config_path)
    else:
        # Configuration file doesn't exist
        # Return default values
        return None, '', False, False, None, False  # Added never_setup_lastfm

    rpcs3_path = None
    xbox_console_ip = ''
    never_setup_rpcs3 = False
    never_setup_xbox = False

    # Read Paths
    if 'Paths' in config:
        rpcs3_path_str = config['Paths'].get('rpcs3_path', '').strip('"')
        if rpcs3_path_str:
            rpcs3_path = Path(rpcs3_path_str)
        xbox_console_ip = config['Paths'].get('xbox_console_ip', '').strip()

    # Read Settings for "Never" flags
    if 'Settings' in config:
        never_setup_rpcs3 = config['Settings'].getboolean('never_setup_rpcs3', fallback=False)
        never_setup_xbox = config['Settings'].getboolean('never_setup_xbox', fallback=False)


    return rpcs3_path, xbox_console_ip, never_setup_rpcs3, never_setup_xbox

def configure_logging(debug_mode):
    if debug_mode:
        logging_level = logging.DEBUG
    else:
        logging_level = logging.CRITICAL

    logging.basicConfig(level=logging_level, format='[%(levelname)s] %(message)s')
    logger = logging.getLogger(__name__)

    if not debug_mode:
        # Suppress logging from external libraries in non-debug mode
        logging.getLogger('urllib3').setLevel(logging.CRITICAL)
        logging.getLogger('requests').setLevel(logging.CRITICAL)
        logging.getLogger('asyncio').setLevel(logging.CRITICAL)
        logging.getLogger('pylast').setLevel(logging.CRITICAL)
        logging.getLogger('httpcore').setLevel(logging.CRITICAL)
        logging.getLogger('httpx').setLevel(logging.CRITICAL)
    else:
        # In debug mode, set them to INFO level
        logging.getLogger('urllib3').setLevel(logging.INFO)
        logging.getLogger('requests').setLevel(logging.INFO)
        logging.getLogger('asyncio').setLevel(logging.INFO)
        logging.getLogger('pylast').setLevel(logging.INFO)
        logging.getLogger('httpcore').setLevel(logging.INFO)
        logging.getLogger('httpx').setLevel(logging.INFO)

# Initialize logger
logger = logging.getLogger(__name__)

# Function to parse the raw input data
def parse_raw_input(raw_input, from_web=False):
    try:
        if isinstance(raw_input, dict):
            return raw_input

        # Extract JSON-like content
        start_idx = raw_input.find("{")
        end_idx = raw_input.rfind("}") + 1
        if start_idx == -1 or end_idx == 0:
            logger.error("No JSON-like content found in raw input.")
            return None
        parsed_input = raw_input[start_idx:end_idx]

        result = []
        inside_string = False
        i = 0
        length = len(parsed_input)

        while i < length:
            if parsed_input[i] == '\\' and i + 1 < length and parsed_input[i + 1] == 'q':
                if not inside_string:
                    result.append('"')
                    inside_string = True
                else:
                    next_i = i + 2
                    if next_i < length:
                        next_char = parsed_input[next_i]
                        if next_char in [',', '}', ':']:
                            result.append('"')
                            inside_string = False
                    else:
                        result.append('"')
                        inside_string = False
                i += 2  # Skip the \q
            else:
                result.append(parsed_input[i])
                i += 1

        final_json_str = ''.join(result)

        result = json.loads(final_json_str)
        return result
    except json.JSONDecodeError as e:
        logger.exception("Invalid JSON data after parsing: %s", e)
        return None
    except Exception as e:
        logger.exception("Error parsing raw input: %s", e)
        return None

# Function to load JSON data from parsed input
def load_json(parsed_input):
    try:
        if isinstance(parsed_input, str):
            data = json.loads(parsed_input)
            return data
        elif isinstance(parsed_input, dict):
            return parsed_input
        else:
            return None
    except json.JSONDecodeError as e:
        logger.exception("Invalid JSON data: %s", e)
        return None

# Function to simplify instrument names
def simplify_instrument_name(instrument_name):
    instrument_mapping = {
        'GUITAR': 'Guitar',
        'REAL_GUITAR': 'Pro Guitar',
        'KEYS': 'Keys',
        'DRUMS': 'Drums',
        'REAL_KEYS': 'Pro Keys',
        'REAL_BASS': 'Pro Bass',
        'BASS': 'Bass',
        'VOCALS': 'Vocals'
    }
    return instrument_mapping.get(instrument_name.upper(), instrument_name)

# Function to map instrument names to small_image names
def map_instrument_to_small_image(instrument_name):
    instrument_mapping = {
        'GUITAR': 'guitar',
        'REAL_GUITAR': 'real_guitar',
        'KEYS': 'keys',
        'DRUMS': 'drums',
        'REAL_KEYS': 'real_keys',
        'REAL_BASS': 'real_bass',
        'BASS': 'bass',
        'VOCALS': 'vocals'
    }
    return instrument_mapping.get(instrument_name.upper(), 'default_small_image_name')

# Function to clean up difficulty levels
def clean_difficulty(difficulty):
    difficulty_mapping = {
        '0': 'Warmup',
        '1': 'Apprentice',
        '2': 'Solid',
        '3': 'Moderate',
        '4': 'Challenging',
        '5': 'Nightmare',
        '6': 'Impossible'
    }
    return difficulty_mapping.get(difficulty, difficulty)

# Function to fetch JSON data from a web address
def fetch_json_from_web(address):
    try:
        response = requests.get(address, timeout=5)
        if response.status_code == 200:
            return response.text
        else:
            logger.error(f"Failed to fetch data from {address}, status code: {response.status_code}")
            return None
    except requests.RequestException as e:
        if logger.getEffectiveLevel() <= logging.DEBUG:
            logger.debug(f"Exception when fetching data from {address}: {e}")
        else:
            logger.error(f"Could not connect to {address}")
        return None

# Function to normalize data for comparison
def normalize_data(data):
    # Create a copy to avoid modifying the original
    data_copy = data.copy()
    # Remove dynamic fields that change every time
    dynamic_fields = ['timestamp', 'last_updated']  # Add any dynamic fields here
    for field in dynamic_fields:
        data_copy.pop(field, None)
    return data_copy

def extract_song_artist(loaded_song):
    if loaded_song and loaded_song != 'No song loaded':
        song_artist_parts = loaded_song.split(' - ', 1)
        if len(song_artist_parts) == 2:
            song_name = song_artist_parts[0].strip()
            artist_year = song_artist_parts[1].strip()
            artist_parts = artist_year.split(',', 1)
            artist_name = artist_parts[0].strip()
            return song_name, artist_name
        else:
            return loaded_song.strip(), ''
    else:
        return '', ''

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Discord Rich Presence and Last.fm Scrobbler for Rock Band 3 Deluxe")
    parser.add_argument('--debug', action='store_true', help='Enable debug mode with detailed logging')
    args = parser.parse_args()

    debug_mode = args.debug  # Store debug mode flag

    # Configure logging based on debug mode
    configure_logging(debug_mode)

    # Configurable parameters
    client_id = "1125571051607298190"
    idle_timeout = 900  # 15 minutes

    config_path = Path.cwd() / 'dx_config.ini'
    rpcs3_path, xbox_console_ip, never_setup_rpcs3, never_setup_xbox

    # Function to prompt setup with "Never" option
    def prompt_setup():
        nonlocal rpcs3_path, xbox_console_ip, never_setup_rpcs3, never_setup_xbox

        while True:
            print("\nNo RPCS3 data path or Xbox console IP configured.")
            print("Please select your setup:")
            print("1. Set up RPCS3")
            print("2. Set up Xbox")
            print("3. Set up both")
            choice = input("Enter the number corresponding to your setup (1-3): ").strip()

            if choice == '1':
                rpcs3_path = get_rpcs3_path()
                save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
                return
            elif choice == '2':
                xbox_console_ip_input = input("Enter the IP address of the Xbox console: ").strip()
                if xbox_console_ip_input:
                    xbox_console_ip = xbox_console_ip_input
                save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
                return
            elif choice == '3':
                rpcs3_path = get_rpcs3_path()
                xbox_console_ip_input = input("Enter the IP address of the Xbox console: ").strip()
                if xbox_console_ip_input:
                    xbox_console_ip = xbox_console_ip_input
                save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
                return
            else:
                print("Invalid choice. Please try again.")

    # Initial setup prompts based on existing configurations and "never" flags
    while (not rpcs3_path and not xbox_console_ip) and not (never_setup_rpcs3 and never_setup_xbox):
        prompt_setup()

    # Check if only one is missing and prompt accordingly, considering "never" flags
    if not rpcs3_path and not never_setup_rpcs3:
        print("\nRPCS3 data path not configured.")
        print("Do you want to set it up now?")
        print("1. Yes")
        print("2. Not Now")
        print("3. Never")
        choice = input("Enter your choice (1-3): ").strip()
        if choice == '1':
            rpcs3_path = get_rpcs3_path()
            save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
        elif choice == '2':
            pass
        elif choice == '3':
            never_setup_rpcs3 = True
            save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
            print("RPCS3 setup will not be prompted again.")
        else:
            print("Invalid choice. Please try again.")

    if not xbox_console_ip and not never_setup_xbox:
        print("\nXbox console IP not configured.")
        print("Do you want to set it up now?")
        print("1. Yes")
        print("2. Not Now")
        print("3. Never")
        choice = input("Enter your choice (1-3): ").strip()
        if choice == '1':
            xbox_console_ip = input("Enter the IP address of the Xbox console: ").strip()
            save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
        elif choice == '2':
            pass
        elif choice == '3':
            never_setup_xbox = True
            save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)
            print("Xbox setup will not be prompted again.")
        else:
            print("Invalid choice. Please try again.")

    # Save the updated configuration
    save_config(config_path, rpcs3_path or '', xbox_console_ip or '', never_setup_rpcs3, never_setup_xbox)

    large_text = "Rock Band 3 Deluxe"  # Default value for large_text

    try:
        presence_cleared = False
        previous_data = None
        last_data_change_time = time.time()
        last_data_receive_time = time.time()
        last_json_content = None
        xbox_connection_error_displayed = False
        screen_clear_delay_counter = 0  # Initialize the screen clear delay counter

        while True:
            current_time = time.time()
            data_changed = False
            json_data = None
            from_web = False
            data_source = None  # Initialize data_source variable

            # Determine whether to clear the screen
            should_clear_screen = screen_clear_delay_counter == 0

            # Attempt to fetch data from Xbox if configured
            if xbox_console_ip and not never_setup_xbox:
                web_address = f"http://{xbox_console_ip}:21070/jsonrpc"
                json_data = fetch_json_from_web(web_address)
                if json_data:
                    data_source = 'xbox'
                    from_web = True
                    xbox_connection_error_displayed = False  # Reset the error flag
                    screen_clear_delay_counter = 0  # Reset the counter when connection is successful
                else:
                    if not xbox_connection_error_displayed:
                        print(f"Error: Could not connect to Xbox at {xbox_console_ip}.")
                        print("Please ensure the Xbox IP is correct and the console is powered on.")
                        print("Rich Presence on Xbox requires Nightly RB3Enhanced installed and configured")
                        print("Consult the MiloHax Discord or online setup guide https://rb3pc.milohax.org/adv_discordrp")
                        print("Attempting to reconnect...")
                        xbox_connection_error_displayed = True

                        # Set the screen clear delay counter to delay clearing the screen
                        screen_clear_delay_counter = 1  # Number of cycles to delay
                    else:
                        # We can choose whether to reset the counter on subsequent failures
                        # For now, we'll only set the counter on the first failure
                        pass

                    # Xbox data not available, fall back to RPCS3 if configured
                    if rpcs3_path and not never_setup_rpcs3:
                        json_path = rpcs3_path / "dev_hdd0" / "game" / "BLUS30463" / "USRDIR" / "discordrp.json"
                        json_file = Path(json_path)
                        if json_file.is_file():
                            with json_file.open('r', encoding='utf-8') as file:
                                json_data = file.read()
                            if json_data:
                                data_source = 'local'
                                from_web = False
            else:
                # Xbox not configured or opted out, try RPCS3
                if rpcs3_path and not never_setup_rpcs3:
                    json_path = rpcs3_path / "dev_hdd0" / "game" / "BLUS30463" / "USRDIR" / "discordrp.json"
                    json_file = Path(json_path)
                    if json_file.is_file():
                        with json_file.open('r', encoding='utf-8') as file:
                            json_data = file.read()
                        if json_data:
                            data_source = 'local'
                            from_web = False

            # Set interval based on data source
            if data_source == 'xbox':
                interval = 5  # Check every 5 seconds when using Xbox
            else:
                interval = 2  # Check every 2 seconds when not using Xbox

            # If no data from any source, wait and retry
            if not json_data:
                # Check for idle timeout
                if current_time - last_data_receive_time > idle_timeout and previous_data:
                    if not presence_cleared:
                        presence_cleared = True
                        previous_data = None
                time.sleep(interval)
                continue

            # Process the fetched data
            parsed_input_data = parse_raw_input(json_data, from_web)
            if not parsed_input_data:
                logger.error("Failed to parse raw input data.")
                time.sleep(interval)
                continue

            # Decrement the screen clear delay counter if it's greater than zero
            if screen_clear_delay_counter > 0:
                screen_clear_delay_counter -= 1

            # Wait before checking again
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nDisconnected. Goodbye!", flush=True)

if __name__ == '__main__':
    main()
