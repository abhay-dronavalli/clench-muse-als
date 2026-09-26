"""Shared setup for the Muse 2 / BrainFlow test scripts.

Every script here starts the same way:

    args  = build_parser("what this script does").parse_args()
    board = get_board(args)          # a BoardShim, not yet prepared

Then the script is responsible for `prepare_session()` / `release_session()`,
ideally in a try/finally so the Bluetooth connection is always handed back.

Flags handled for you:
    --synthetic        use BoardIds.SYNTHETIC_BOARD (fake data, no hardware)
    --name Muse-XXXX   BrainFlowInputParams.serial_number (the BLE device name)
    --mac AA:BB:...    BrainFlowInputParams.mac_address
    --debug            turn on BrainFlow's dev logger (very chatty)

The env var USE_SYNTHETIC=1 does the same thing as --synthetic, so a teammate
without a headset can export it once and forget about it.
"""

import argparse
import os
import platform

from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets


# ---------------------------------------------------------------- CLI plumbing

def build_parser(description: str) -> argparse.ArgumentParser:
    """An ArgumentParser pre-loaded with the flags every script shares."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        default=os.environ.get("USE_SYNTHETIC", "") in ("1", "true", "yes"),
        help="use BrainFlow's synthetic board instead of a real Muse 2 "
             "(also enabled by USE_SYNTHETIC=1)",
    )
    parser.add_argument(
        "--name",
        default=None,
        metavar="Muse-XXXX",
        help="Muse device name, e.g. Muse-1234. Speeds up / disambiguates discovery.",
    )
    parser.add_argument(
        "--mac",
        default=None,
        metavar="AA:BB:CC:DD:EE:FF",
        help="Muse MAC address, if you know it.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable BrainFlow's dev logger (prints the native BLE chatter)",
    )
    return parser


def get_board(args: argparse.Namespace) -> BoardShim:
    """Build the BoardShim described by the parsed args. Does NOT prepare it."""
    if args.debug:
        BoardShim.enable_dev_board_logger()
    else:
        # Keep the native layer quiet so our own prints are readable.
        BoardShim.disable_board_logger()

    params = BrainFlowInputParams()
    # Both fields are optional. Leave them empty and BrainFlow grabs the first
    # Muse it can see, which is what you want with exactly one headband around.
    if args.name:
        params.serial_number = args.name
    if args.mac:
        params.mac_address = args.mac

    board_id = BoardIds.SYNTHETIC_BOARD if args.synthetic else BoardIds.MUSE_2_BOARD
    # MUSE_2_BOARD talks over the laptop's own Bluetooth radio.
    # MUSE_2_BLED_BOARD is the one that needs a BLED112 USB dongle -- not us.
    return BoardShim(board_id, params)


# ------------------------------------------------------------------- utilities

def board_label(board: BoardShim) -> str:
    """Human-readable name of the board we ended up with."""
    return "SYNTHETIC_BOARD" if board.board_id == BoardIds.SYNTHETIC_BOARD else "MUSE_2_BOARD"


def available_presets(board_id: int) -> list[BrainFlowPresets]:
    """Presets this board actually has, in a stable order (default, aux, ancillary).

    The Muse 2 has all three; the synthetic board only has default + auxiliary,
    so every script iterates over this instead of assuming.
    """
    present = set(BoardShim.get_board_presets(board_id))
    order = [
        BrainFlowPresets.DEFAULT_PRESET,     # EEG @ 256 Hz: TP9, AF7, AF8, TP10
        BrainFlowPresets.AUXILIARY_PRESET,   # accelerometer + gyro (on by default)
        BrainFlowPresets.ANCILLARY_PRESET,   # PPG (needs config_board("p50"))
    ]
    return [p for p in order if int(p) in present]


def eeg_channels_and_names(board_id: int) -> tuple[list[int], list[str]]:
    """EEG row indices + electrode names for the default preset.

    The Muse 2 gives exactly four: TP9, AF7, AF8, TP10. The synthetic board
    gives sixteen, which would swamp the output, so we trim it to the first four
    and every script below behaves the same in both modes.
    """
    channels = BoardShim.get_eeg_channels(board_id)
    names = BoardShim.get_eeg_names(board_id)
    if board_id == BoardIds.SYNTHETIC_BOARD:
        channels, names = channels[:4], names[:4]
    return channels, names


def enable_ppg(board: BoardShim, verbose: bool = True) -> bool:
    """Ask a real Muse to start streaming PPG on the ancillary preset.

    Must be called after prepare_session() and before start_stream().
    Side effect documented by BrainFlow: this also turns on a 5th EEG channel.
    Returns True if PPG should now be flowing.
    """
    if board.board_id == BoardIds.SYNTHETIC_BOARD:
        if verbose:
            print("  (synthetic board: no PPG, skipping config_board('p50'))")
        return False
    try:
        board.config_board("p50")
        if verbose:
            print("  PPG enabled via config_board('p50')")
        return True
    except Exception as exc:  # non-fatal: everything else still works
        if verbose:
            print(f"  could not enable PPG: {exc}")
        return False


def preset_name(preset: BrainFlowPresets) -> str:
    return BrainFlowPresets(int(preset)).name


# ------------------------------------------------------- platform sanity check

def warn_about_platform() -> None:
    """Print the known BrainFlow-over-BLE gotchas for whatever OS this is."""
    system = platform.system()
    print(f"Platform: {system} {platform.release()} ({platform.machine()})")

    if system == "Windows":
        # BrainFlow's BLE transport needs the WinRT BLE APIs from 2004/20H1.
        build = 0
        try:
            build = int(platform.version().split(".")[-1])
        except (ValueError, IndexError):
            pass
        if build and build < 19041:
            print("  !! Windows build 19041 (2004) or newer is required for BLE.")
            print(f"     This looks like build {build}. Update Windows or use --synthetic.")
        else:
            print(f"  OK: Windows build {build or 'unknown'} supports native BLE.")
        print("  Tip: pair/forget the Muse in Windows Settings only if discovery fails;")
        print("       BrainFlow connects directly and does not need it paired.")

    elif system == "Darwin":
        version = platform.mac_ver()[0]
        major_minor = ".".join(version.split(".")[:2])
        if major_minor in ("12.0", "12.1", "12.2"):
            print(f"  !! macOS {version} has known BLE scanning bugs. Update to 12.3+.")
        print("  Grant Bluetooth permission to your terminal / IDE:")
        print("     System Settings > Privacy & Security > Bluetooth")

    elif system == "Linux":
        print("  You may need: sudo apt install libdbus-1-dev")
        print("  and a BrainFlow built from source with BLE enabled (pip wheels often lack it).")

    print("  Only ONE app may hold the Muse: close the Muse phone app, muselsl, other scripts.")
    print()


def _smoke_test() -> None:
    """`python config.py [--synthetic]` prints the board layout and exits."""
    warn_about_platform()
    args = build_parser("print the board configuration and exit").parse_args()
    board = get_board(args)
    print(f"Board: {board_label(board)} (id {int(board.board_id)})")
    for preset in available_presets(board.board_id):
        print(
            f"  {preset_name(preset):<18} fs={BoardShim.get_sampling_rate(board.board_id, preset):>4} Hz"
            f"  rows={BoardShim.get_num_rows(board.board_id, preset)}"
        )
    channels, names = eeg_channels_and_names(board.board_id)
    print(f"  EEG channels: {channels} {names}")


if __name__ == "__main__":
    _smoke_test()
