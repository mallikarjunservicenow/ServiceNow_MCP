import sys
import os
import subprocess
import platform


def get_scripts_directory():
    """Finds the scripts directory for the current Python environment."""
    # sys.executable is the path to the python.exe interpreter
    python_dir = os.path.dirname(sys.executable)

    # On Windows, scripts are typically in the 'Scripts' subdirectory
    if platform.system() == "Windows":
        return os.path.join(python_dir, "Scripts")
    else:
        # On Linux/macOS, they are in the 'bin' subdirectory
        return os.path.join(python_dir, "bin")


def add_to_path_windows(path_to_add):
    """
    Permanently adds a directory to the User's PATH on Windows.
    """
    print(f"Attempting to add '{path_to_add}' to your PATH.")

    try:
        # Get the current user PATH. The 'reg query' command is reliable.
        output = subprocess.check_output(
            'reg query "HKEY_CURRENT_USER\\Environment" /v Path', shell=True
        ).decode()

        # The output is messy, so we find the line with "Path" and get the value
        current_path = ""
        for line in output.splitlines():
            if "Path" in line and "REG_SZ" in line:
                current_path = line.split("REG_SZ", 1)[1].strip()
                break

        if path_to_add in current_path.split(";"):
            print(f"\n✅ Success! The directory is already in your PATH.")
            print("No changes were needed.")
            return

        print(f"Directory not found in PATH. Adding it now...")

        # Use setx to permanently set the user PATH.
        # We append the new path to the existing one.
        # Using shell=True is necessary for setx.
        subprocess.run(
            f'setx Path "{current_path};{path_to_add}"',
            shell=True,
            check=True,
            capture_output=True,
        )

        print(f"\n✅ Success! '{path_to_add}' has been added to your PATH.")
        print(
            "\nIMPORTANT: You must close and reopen your terminal (e.g., Command Prompt, PowerShell, VS Code terminal) for this change to take effect."
        )

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error: Failed to modify the PATH.")
        print(f"   Command failed with error: {e.stderr.decode()}")
        print(
            "\nPlease try running your terminal as an Administrator and run the command again."
        )
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
        print("Please add the directory to your PATH manually.")


def main():
    """
    Main function for the post-install script.
    """
    print("--- MCP ServiceNow Exporter Post-Install Setup ---")

    if platform.system() != "Windows":
        print("This script is intended for Windows. No action needed on this OS.")
        return

    scripts_dir = get_scripts_directory()

    if not os.path.isdir(scripts_dir):
        print(f"Error: Could not find the Python Scripts directory at '{scripts_dir}'.")
        return

    add_to_path_windows(scripts_dir)


if __name__ == "__main__":
    main()